"""Auto-register graphify sleep-cycle cron jobs in Hermes.

Idempotent: skips jobs already present. Called from GraphifyMemoryProvider.on_session_start.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("graphify_plugin.auto_register")


def _auto_register_crons(manifest: dict) -> dict:
    """Register the manifest's cron jobs in Hermes.

    Returns: {"created": [job_names...], "skipped": [...], "errors": [...]}
    """
    summary = {"created": [], "skipped": [], "errors": []}

    # Defensive Hermes imports
    try:
        from cron.jobs import create_job, load_jobs
    except ImportError as e:
        summary["errors"].append(f"cron.jobs not importable: {e}")
        return summary

    try:
        existing = load_jobs()
    except Exception as e:
        summary["errors"].append(f"load_jobs failed: {e}")
        return summary

    existing_names = {j.get("name") for j in existing}

    for job_spec in manifest.get("jobs", []):
        name = job_spec.get("name")
        if not name:
            summary["errors"].append("job_spec missing name")
            continue
        if name in existing_names:
            summary["skipped"].append(name)
            continue
        try:
            prompt = (
                f"Run the {job_spec['template']} template from the sleep-cycle skill bundle. "
                f"Brain root: {manifest.get('brain_root', '.')}. "
                f"Upstream context_from: {job_spec.get('context_from') or 'none (first phase)'}."
            )
            create_job(
                name=name,
                schedule=job_spec["schedule"],
                prompt=prompt,
                skill="sleep-cycle",
                enabled_toolsets=["execute_code", "memory", "session_search", "send_message", "delegate_task"],
                context_from=job_spec.get("context_from"),
            )
            summary["created"].append(name)
        except Exception as e:
            summary["errors"].append(f"{name}: {e}")

    # Stamp registered_at
    state_dir = Path.home() / ".hermes" / "plugins" / "memory" / "graphify" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    if summary["created"]:
        from datetime import datetime
        (state_dir / "registered_at.txt").write_text(
            f"{datetime.utcnow().isoformat(timespec='seconds')}Z\n"
            + "\n".join(summary["created"])
        )

    return summary


if __name__ == "__main__":
    # CLI for testing: python -m graphify_plugin.auto_register
    import json
    import os
    manifest_path = Path.home() / ".hermes" / "state" / "graphify-sleep.manifest.json"
    if not manifest_path.is_file():
        print(f"[error] manifest not found at {manifest_path}", flush=True)
        raise SystemExit(1)
    manifest = json.loads(manifest_path.read_text())
    result = _auto_register_crons(manifest)
    print(json.dumps(result, indent=2))
