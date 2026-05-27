"""Hippocampal-to-neocortical drain: MEMORY.md → graphify.

Two modes:
- nightly: drain all Cortex entries older than --age-days (default 7) during Phase 0 cron
- pressure: drain only the OLDEST 1-2 Cortex entries (intra-day safety net)

Only touches entries whose first line matches `^Cortex YYYY-MM-DD HH:MM`.
User-curated entries (any other prefix) are NEVER drained.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("graphify_plugin.drain")

CORTEX_HEADER_RE = re.compile(r"^Cortex (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})", re.MULTILINE)
ENTRY_DELIMITER = "\n§\n"  # Per Hermes memory tool convention
HERMES_HOME = Path.home() / ".hermes"
MEMORY_FILE = HERMES_HOME / "memories" / "MEMORY.md"


def _parse_entries(text: str) -> list[str]:
    """Split MEMORY.md content by entry delimiter."""
    if not text.strip():
        return []
    return [e.strip() for e in text.split(ENTRY_DELIMITER) if e.strip()]


def _cortex_age_days(entry: str) -> float | None:
    """Return age in days if entry has a Cortex header; else None."""
    m = CORTEX_HEADER_RE.search(entry)
    if not m:
        return None
    try:
        entry_dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        return (datetime.utcnow() - entry_dt).total_seconds() / 86400.0
    except Exception:
        return None


def _archive_to_graphify(entry: str, brain_root: Path) -> bool:
    """Push one entry to graphify via `graphify add` stdin. Returns success bool."""
    # Compose archive content with metadata
    archived_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    header_match = CORTEX_HEADER_RE.search(entry)
    original_ts = f"{header_match.group(1)} {header_match.group(2)}" if header_match else "unknown"

    content = (
        f"# Hippocampal drain — {original_ts}\n\n"
        f"_Archived from MEMORY.md at {archived_at}_\n\n"
        f"---\n\n"
        f"{entry}\n"
    )

    # Write to tmpfile (graphify add reads paths, not stdin per current CLI)
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md",
        prefix=f"hippocampal_{archived_at.replace(':', '-')}_",
        delete=False,
    ) as tf:
        tf.write(content)
        tmp_path = tf.name

    try:
        result = subprocess.run(
            ["graphify", "add", tmp_path, "--contributor", "hermes_hippocampal_drain"],
            cwd=str(brain_root), capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode == 0:
            return True
        logger.warning(f"[drain] graphify add failed: {result.stderr.strip()}")
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _remove_from_memory(full_entry: str) -> bool:
    """Remove an entry from MEMORY.md. Direct file edit (memory tool RPC not accessible from subprocess)."""
    if not MEMORY_FILE.is_file():
        return False
    current = MEMORY_FILE.read_text()
    if full_entry not in current:
        return False

    # Remove entry and surrounding delimiter (best-effort cleanup)
    new_content = current.replace(full_entry, "")
    # Collapse multiple delimiters
    while ENTRY_DELIMITER + ENTRY_DELIMITER in new_content:
        new_content = new_content.replace(ENTRY_DELIMITER + ENTRY_DELIMITER, ENTRY_DELIMITER)
    new_content = new_content.strip()

    MEMORY_FILE.write_text(new_content)
    return True


def _drain_old_cortex_entries(brain_root: Path, age_threshold_days: int = 7) -> dict:
    """Drain all Cortex entries older than threshold. Returns summary dict."""
    if not MEMORY_FILE.is_file():
        return {"drained": 0, "skipped_user_curated": 0, "errors": [], "remaining_chars": 0}

    content = MEMORY_FILE.read_text()
    entries = _parse_entries(content)
    drained_count = 0
    skipped_user_curated = 0
    errors = []

    for entry in entries:
        age = _cortex_age_days(entry)
        if age is None:
            skipped_user_curated += 1
            continue
        if age < age_threshold_days:
            continue
        # Archive then remove
        if _archive_to_graphify(entry, brain_root):
            if _remove_from_memory(entry):
                drained_count += 1
            else:
                errors.append(f"archived but failed to remove: {entry[:50]}...")
        else:
            errors.append(f"failed to archive: {entry[:50]}...")

    remaining_chars = len(MEMORY_FILE.read_text()) if MEMORY_FILE.is_file() else 0
    return {
        "drained": drained_count,
        "skipped_user_curated": skipped_user_curated,
        "errors": errors,
        "remaining_chars": remaining_chars,
    }


def _memory_pressure_drain(brain_root: Path, max_entries: int = 2) -> dict:
    """Drain only the oldest `max_entries` Cortex entries — safety net for intra-day pressure."""
    if not MEMORY_FILE.is_file():
        return {"drained": 0, "skipped_user_curated": 0, "errors": [], "remaining_chars": 0}

    content = MEMORY_FILE.read_text()
    entries = _parse_entries(content)

    # Filter to Cortex entries with age; sort oldest first
    cortex_entries = []
    skipped = 0
    for e in entries:
        age = _cortex_age_days(e)
        if age is None:
            skipped += 1
            continue
        cortex_entries.append((age, e))
    cortex_entries.sort(key=lambda x: x[0], reverse=True)  # oldest first

    drained = 0
    errors = []
    for age, entry in cortex_entries[:max_entries]:
        if _archive_to_graphify(entry, brain_root):
            if _remove_from_memory(entry):
                drained += 1
            else:
                errors.append(f"archived but failed to remove (age {age:.1f}d)")
        else:
            errors.append(f"failed to archive (age {age:.1f}d)")

    remaining_chars = len(MEMORY_FILE.read_text()) if MEMORY_FILE.is_file() else 0
    return {
        "drained": drained,
        "skipped_user_curated": skipped,
        "errors": errors,
        "remaining_chars": remaining_chars,
    }


def _main():
    parser = argparse.ArgumentParser(prog="graphify_plugin.drain")
    parser.add_argument("--mode", choices=["nightly", "pressure"], default="nightly")
    parser.add_argument("--brain-root", required=True, help="Brain root directory (where graph.json lives)")
    parser.add_argument("--age-days", type=int, default=7, help="Age threshold for nightly mode")
    parser.add_argument("--max-entries", type=int, default=2, help="Max entries in pressure mode")
    args = parser.parse_args()

    brain_root = Path(args.brain_root).expanduser().resolve()
    if not brain_root.is_dir():
        print(f"[error] brain root not found: {brain_root}", file=sys.stderr)
        sys.exit(1)

    if args.mode == "nightly":
        result = _drain_old_cortex_entries(brain_root, age_threshold_days=args.age_days)
    else:
        result = _memory_pressure_drain(brain_root, max_entries=args.max_entries)

    print(json.dumps(result, indent=2))
    if result["errors"]:
        sys.exit(2)


if __name__ == "__main__":
    _main()
