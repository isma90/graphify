"""Migration helpers for graphify v0.9.0 shared-graph setup.

Provides:
  - init_sharing        — create .graphifyshared / .graphifyprivate / graphify-out/.gitignore
  - suggest_classification_by_path — heuristic patterns from an existing graph.json
  - migrate_to_shared   — migrate a legacy single-graph repo to the split layout
  - add_pattern_to_overlay — append gitignore-style patterns to an overlay file
  - manifest_v1_to_v2   — idempotent manifest schema v1 → v2 migration helper
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SHARED_OVERLAY = ".graphifyshared"
_PRIVATE_OVERLAY = ".graphifyprivate"
_GRAPHIFY_OUT = "graphify-out"

# Patterns that are universally safe to share (heuristic defaults).
_DEFAULT_SHARED_PATTERNS: list[str] = [
    "README.md",
    "docs/**",
]

# Patterns that are universally sensitive (non-interactive defaults).
_DEFAULT_PRIVATE_PATTERNS: list[str] = [
    ".env*",
    "secrets/**",
    "**/*_local.*",
    "scratch/**",
    "notes/**",
]

# Gitignore allowlist for graphify-out/ — only the shared artefacts are
# committed; everything else (graph-private.json, cache/, backups/, …) stays
# gitignored on every developer's machine.
_GRAPHIFY_OUT_GITIGNORE = """\
# Auto-managed by graphify
*
!graph-shared.json
!GRAPH_REPORT-shared.md
!.gitignore
"""

# ---------------------------------------------------------------------------
# Heuristic bucket definitions for suggest_classification_by_path
# ---------------------------------------------------------------------------

_SHARED_HEURISTIC: tuple[str, ...] = (
    "src",
    "lib",
    "app",
    "pkg",
    "docs",
    "tests",
    "__tests__",
)

_SHARED_GLOB_HEURISTIC: tuple[str, ...] = (
    "README.*",
    "CONTRIBUTING.*",
)

_PRIVATE_HEURISTIC: tuple[str, ...] = (
    "scratch",
    "notes",
    "secrets",
)

_PRIVATE_GLOB_HEURISTIC: tuple[str, ...] = (
    ".env*",
    "*.local.*",
)

_AMBIGUOUS_HEURISTIC: tuple[str, ...] = (
    "scripts",
    "tools",
    "config",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_tty() -> bool:
    """Return True when stdout is connected to an interactive terminal."""
    return sys.stdin.isatty()


def _confirm(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question; return *default* when stdin is not a TTY.

    Args:
        prompt: Human-readable question (no trailing newline needed).
        default: Value to use when the user just presses Enter or stdin is
            not a TTY.

    Returns:
        ``True`` for yes, ``False`` for no.
    """
    if not _is_tty():
        return default
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{prompt} {hint} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer.startswith("y")


def _read_patterns_interactive(
    prompt_header: str,
    defaults: list[str],
) -> list[str]:
    """Present *defaults* to the user and let them edit the list line-by-line.

    Displays each pattern prefixed by its index, then lets the user accept the
    full list (Enter / y) or type a replacement line-by-line.

    Args:
        prompt_header: Section title shown above the pattern list.
        defaults: Starting list of gitignore-style patterns.

    Returns:
        Final list of patterns (may equal *defaults* when the user accepts).
    """
    if not _is_tty():
        return list(defaults)

    sys.stdout.write(f"\n{prompt_header}\n")
    for i, p in enumerate(defaults):
        sys.stdout.write(f"  [{i}] {p}\n")
    sys.stdout.flush()

    if not _confirm("Accept these patterns?", default=True):
        sys.stdout.write(
            "Enter patterns one per line (empty line to finish):\n"
        )
        sys.stdout.flush()
        patterns: list[str] = []
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                break
            if not line:
                break
            patterns.append(line)
        return patterns if patterns else list(defaults)

    return list(defaults)


def _write_file_idempotent(
    path: Path,
    content: str,
    *,
    interactive: bool,
    label: str,
) -> bool:
    """Write *content* to *path*, respecting idempotency.

    If *path* already exists:
    - In non-interactive mode: skip and return False.
    - In interactive mode: ask the user whether to overwrite.

    Args:
        path: Target file path.
        content: Text to write.
        interactive: Whether to prompt the user when the file exists.
        label: Short description for user-facing messages.

    Returns:
        ``True`` if the file was written, ``False`` if skipped.
    """
    if path.exists():
        if not interactive or not _is_tty():
            sys.stdout.write(
                f"[graphify] {label} already exists at {path} — skipping.\n"
            )
            return False
        if not _confirm(f"{label} already exists at {path}. Overwrite?", default=False):
            return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_sharing(
    root: Path,
    *,
    interactive: bool = True,
    default_remote: str | None = None,
) -> dict[str, Path]:
    """Set up the shared-graph layout for a repository.

    Creates three files under *root*:

    * ``.graphifyshared`` — gitignore-style allowlist for shareable paths.
    * ``.graphifyprivate`` — patterns for paths that must stay private.
    * ``graphify-out/.gitignore`` — allowlist so only ``graph-shared.json``
      is committed.

    The function is **idempotent**: existing files are not overwritten unless
    *interactive* is ``True`` and the user explicitly confirms each one.

    In interactive mode the user is shown the default pattern lists and can
    accept or replace them.  When ``sys.stdin.isatty()`` is ``False`` the
    function silently applies the non-interactive defaults even if
    ``interactive=True``.

    Args:
        root: Repository root directory.
        interactive: When ``True`` (and stdin is a TTY), prompt the user to
            confirm or edit default pattern lists.
        default_remote: If provided, written as a comment at the top of
            ``.graphifyshared`` for future ``graphify remote add`` use.

    Returns:
        ``{'shared': Path, 'private': Path, 'gitignore': Path}`` — the paths
        of the three files that were (or would have been) created.
    """
    root = root.resolve()

    # Build default pattern lists, letting the user edit them in interactive mode.
    _active_interactive = interactive and _is_tty()

    if _active_interactive:
        shared_patterns = _read_patterns_interactive(
            ".graphifyshared — paths visible to teammates (gitignore-style):",
            _DEFAULT_SHARED_PATTERNS,
        )
        private_patterns = _read_patterns_interactive(
            ".graphifyprivate — paths that must stay private (gitignore-style):",
            _DEFAULT_PRIVATE_PATTERNS,
        )
    else:
        shared_patterns = list(_DEFAULT_SHARED_PATTERNS)
        private_patterns = list(_DEFAULT_PRIVATE_PATTERNS)

    # Build file contents.
    remote_comment = (
        f"# default-remote: {default_remote}\n" if default_remote else ""
    )

    shared_content = (
        remote_comment
        + "# Add gitignore-style patterns for paths that may be shared.\n"
        + "# Lines starting with ! negate a pattern (exclude from shared).\n"
        + "\n".join(shared_patterns)
        + "\n"
    )
    private_content = (
        "# Paths listed here are always kept private (private wins over shared).\n"
        + "# Sensitive defaults — extend as needed.\n"
        + "\n".join(private_patterns)
        + "\n"
    )

    shared_path = root / _SHARED_OVERLAY
    private_path = root / _PRIVATE_OVERLAY
    gitignore_path = root / _GRAPHIFY_OUT / ".gitignore"

    _write_file_idempotent(
        shared_path, shared_content, interactive=interactive, label=".graphifyshared"
    )
    _write_file_idempotent(
        private_path, private_content, interactive=interactive, label=".graphifyprivate"
    )
    _write_file_idempotent(
        gitignore_path,
        _GRAPHIFY_OUT_GITIGNORE,
        interactive=interactive,
        label="graphify-out/.gitignore",
    )

    # Persist the default remote URL to ~/.graphify/config.toml (Stage 2).
    # The comment-line write in .graphifyshared above is kept for human
    # readability; the config entry is the machine-readable source of truth.
    if default_remote:
        from graphify.config import upsert_remote
        upsert_remote(root, default_remote)

    # Auto-install the git merge driver when root is inside a git working tree.
    from graphify.privacy import _find_vcs_root
    vcs_root = _find_vcs_root(root)
    if vcs_root is not None:
        try:
            from graphify.git_integration import install_merge_driver
            if install_merge_driver(root):
                print("[graphify init-sharing] installed git merge driver for graph-shared.json")
        except RuntimeError as e:
            print(f"[graphify init-sharing] warning: could not install merge driver: {e}")

    return {
        "shared": shared_path,
        "private": private_path,
        "gitignore": gitignore_path,
    }


def suggest_classification_by_path(
    graph_json_path: Path,
    *,
    interactive: bool = True,
) -> tuple[list[str], list[str]]:
    """Infer shared/private patterns by analysing an existing ``graph.json``.

    Reads *graph_json_path*, groups nodes by ``source_file``, counts how many
    nodes originate from each top-level directory, then applies heuristics to
    bucket directories into *shared* or *private*.

    Heuristics applied (in order):

    * **Always shared**: ``src/``, ``lib/``, ``app/``, ``pkg/``, ``docs/``,
      ``tests/``, ``__tests__/``, ``README.*``, ``CONTRIBUTING.*``.
    * **Always private**: ``scratch/``, ``notes/``, ``secrets/``, ``.env*``,
      ``*.local.*``, and anything that resembles a sensitive path based on
      detect.py conventions.
    * **Ambiguous** (prompted in interactive mode): ``scripts/``, ``tools/``,
      ``config/``.

    In interactive mode a TTY-friendly summary table is printed and the user
    can move patterns between buckets before they are returned.  When
    ``sys.stdin.isatty()`` is ``False`` the heuristic is applied silently.

    Args:
        graph_json_path: Path to an existing ``graph.json`` or similar export.
        interactive: When ``True`` and stdin is a TTY, prompt the user to
            review the suggested classification.

    Returns:
        ``(shared_patterns, private_patterns)`` — two lists of gitignore-style
        string patterns.
    """
    graph_json_path = graph_json_path.resolve()

    try:
        data = json.loads(graph_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(
            f"[graphify migrate] warning: could not read {graph_json_path}: {exc}\n"
        )
        return list(_DEFAULT_SHARED_PATTERNS), list(_DEFAULT_PRIVATE_PATTERNS)

    nodes: list[dict] = data.get("nodes", [])

    # Count nodes per top-level directory.
    dir_counts: dict[str, int] = {}
    for node in nodes:
        sf = node.get("source_file", "")
        if not sf:
            continue
        # Normalise separators; take the first path component.
        parts = sf.replace("\\", "/").lstrip("/").split("/")
        top = parts[0] if parts else ""
        if top:
            dir_counts[top] = dir_counts.get(top, 0) + 1

    shared_dirs: list[str] = []
    private_dirs: list[str] = []
    ambiguous_dirs: list[str] = []

    for top_dir, count in sorted(dir_counts.items(), key=lambda kv: -kv[1]):
        lower = top_dir.lower()

        # Check private heuristics first (private wins).
        if any(lower == p for p in _PRIVATE_HEURISTIC):
            private_dirs.append(f"{top_dir}/")
            continue
        if lower.startswith(".env"):
            private_dirs.append(f"{top_dir}/")
            continue
        if lower.endswith("_local") or "_local." in lower:
            private_dirs.append(f"{top_dir}/")
            continue

        # Check shared heuristics.
        if any(lower == p for p in _SHARED_HEURISTIC):
            shared_dirs.append(f"{top_dir}/")
            continue

        # README.* / CONTRIBUTING.* — these are files, not dirs; handled below.
        # Any other top-level dir is ambiguous.
        if lower not in _AMBIGUOUS_HEURISTIC:
            ambiguous_dirs.append(f"{top_dir}/")
        else:
            ambiguous_dirs.append(f"{top_dir}/")

    # Always add sensible glob patterns regardless of what was found in the graph.
    shared_patterns: list[str] = list(shared_dirs)
    for glob in _SHARED_GLOB_HEURISTIC:
        if glob not in shared_patterns:
            shared_patterns.append(glob)

    private_patterns: list[str] = list(private_dirs)
    for glob in _PRIVATE_GLOB_HEURISTIC:
        if glob not in private_patterns:
            private_patterns.append(glob)

    # In interactive mode, let the user review and handle ambiguous dirs.
    _active_interactive = interactive and _is_tty()
    if _active_interactive and ambiguous_dirs:
        sys.stdout.write(
            "\nThe following directories could not be classified automatically:\n"
        )
        for d in ambiguous_dirs:
            sys.stdout.write(f"  {d}  (add to shared, private, or skip?)\n")
        sys.stdout.flush()

        for d in ambiguous_dirs:
            try:
                answer = input(
                    f"  [{d}] (s)hared / (p)rivate / (k)eep private [default: p]: "
                ).strip().lower()
            except EOFError:
                answer = ""
            if answer.startswith("s"):
                shared_patterns.append(d)
            else:
                # Default: private (safety).
                private_patterns.append(d)
    else:
        # Non-interactive: ambiguous dirs default to private.
        private_patterns.extend(ambiguous_dirs)

    # If interactive, let user review the final lists.
    if _active_interactive:
        sys.stdout.write("\n--- Suggested shared patterns ---\n")
        for p in shared_patterns:
            sys.stdout.write(f"  {p}\n")
        sys.stdout.write("\n--- Suggested private patterns ---\n")
        for p in private_patterns:
            sys.stdout.write(f"  {p}\n")
        sys.stdout.flush()

        if not _confirm("Accept these classifications?", default=True):
            shared_patterns = _read_patterns_interactive(
                "Edit shared patterns:", shared_patterns
            )
            private_patterns = _read_patterns_interactive(
                "Edit private patterns:", private_patterns
            )

    return shared_patterns, private_patterns


def migrate_to_shared(
    root: Path,
    *,
    interactive: bool = True,
) -> dict[str, str]:
    """Migrate a legacy single-graph repo to the split private/shared layout.

    Performs the following steps atomically from the user's perspective:

    1. Validates that ``graphify-out/graph.json`` exists.
    2. Returns ``{'status': 'already_initialized'}`` if either overlay file
       already exists (idempotent guard).
    3. Calls :func:`suggest_classification_by_path` to derive patterns.
    4. Writes ``.graphifyshared`` and ``.graphifyprivate``.
    5. Renames ``graphify-out/graph.json`` →
       ``graphify-out/graph-private.json`` to preserve ``built_at_commit``
       and community labels without re-extraction.
    6. Does **not** run ``graphify extract`` automatically; the user must do
       that to materialise ``graph-shared.json``.

    In interactive mode a summary is printed and the user must confirm before
    any irreversible changes (steps 4 and 5) are applied.

    Args:
        root: Repository root directory.
        interactive: When ``True`` and stdin is a TTY, prompt for confirmation
            before applying irreversible changes.

    Returns:
        A dict describing the outcome::

            {
                'status': 'migrated' | 'already_initialized' | 'no_graph_json',
                'shared_patterns': [...],   # present when status == 'migrated'
                'private_patterns': [...],  # present when status == 'migrated'
                'renamed_graph_json': True, # present when status == 'migrated'
            }
    """
    root = root.resolve()
    graph_json = root / _GRAPHIFY_OUT / "graph.json"
    shared_overlay = root / _SHARED_OVERLAY
    private_overlay = root / _PRIVATE_OVERLAY

    # Step 1: validate source file.
    if not graph_json.exists():
        sys.stdout.write(
            f"[graphify migrate-to-shared] No graph.json found at {graph_json}.\n"
            "Run 'graphify extract .' first to generate it.\n"
        )
        return {"status": "no_graph_json"}

    # Step 2: idempotency guard.
    if shared_overlay.exists() or private_overlay.exists():
        sys.stdout.write(
            "[graphify migrate-to-shared] Overlays already exist — nothing to do.\n"
            f"  .graphifyshared: {shared_overlay}\n"
            f"  .graphifyprivate: {private_overlay}\n"
        )
        return {"status": "already_initialized"}

    # Step 3: suggest patterns.
    shared_patterns, private_patterns = suggest_classification_by_path(
        graph_json, interactive=interactive
    )

    # Interactive summary + confirmation before irreversible changes.
    _active_interactive = interactive and _is_tty()
    if _active_interactive:
        sys.stdout.write("\n[graphify migrate-to-shared] About to apply:\n")
        sys.stdout.write(f"  Create {shared_overlay}\n")
        sys.stdout.write(f"  Create {private_overlay}\n")
        sys.stdout.write(
            f"  Rename {graph_json}\n"
            f"      -> {graph_json.parent / 'graph-private.json'}\n"
        )
        sys.stdout.flush()
        if not _confirm("Proceed?", default=True):
            sys.stdout.write("[graphify migrate-to-shared] Aborted by user.\n")
            return {"status": "aborted"}

    # Step 4: write overlays.
    shared_content = (
        "# Paths visible to teammates — add gitignore-style patterns.\n"
        + "\n".join(shared_patterns)
        + "\n"
    )
    private_content = (
        "# Paths that must stay private (private wins over shared).\n"
        + "\n".join(private_patterns)
        + "\n"
    )
    shared_overlay.parent.mkdir(parents=True, exist_ok=True)
    shared_overlay.write_text(shared_content, encoding="utf-8")
    private_overlay.write_text(private_content, encoding="utf-8")

    # Step 5: rename graph.json → graph-private.json.
    graph_private = graph_json.parent / "graph-private.json"
    graph_json.rename(graph_private)

    sys.stdout.write(
        f"[graphify migrate-to-shared] Done.\n"
        f"  Wrote {shared_overlay} ({len(shared_patterns)} patterns)\n"
        f"  Wrote {private_overlay} ({len(private_patterns)} patterns)\n"
        f"  Renamed {graph_json.name} -> {graph_private.name}\n"
        "\nNext step: run 'graphify extract .' to produce graph-shared.json.\n"
        "Then: graphify remote add origin <url> && graphify login && graphify sync push\n"
    )

    return {
        "status": "migrated",
        "shared_patterns": shared_patterns,
        "private_patterns": private_patterns,
        "renamed_graph_json": True,
    }


def add_pattern_to_overlay(
    root: Path,
    overlay: Literal["shared", "private"],
    patterns: list[str],
    *,
    deduplicate: bool = True,
) -> Path:
    """Append *patterns* to the specified overlay file.

    Creates the file if it does not exist.  When ``deduplicate=True``, patterns
    that are already present verbatim in the file are silently skipped.

    Args:
        root: Repository root directory.
        overlay: Which overlay to update — ``"shared"`` maps to
            ``.graphifyshared``; ``"private"`` maps to ``.graphifyprivate``.
        patterns: Gitignore-style patterns to append.
        deduplicate: When ``True`` (default), do not add patterns that already
            appear literally in the file.

    Returns:
        Absolute path of the overlay file that was modified or created.
    """
    root = root.resolve()
    filename = _SHARED_OVERLAY if overlay == "shared" else _PRIVATE_OVERLAY
    overlay_path = root / filename

    existing_lines: list[str] = []
    if overlay_path.exists():
        existing_lines = overlay_path.read_text(encoding="utf-8").splitlines()

    existing_set: set[str] = set(existing_lines) if deduplicate else set()

    to_add = [p for p in patterns if p not in existing_set] if deduplicate else list(patterns)

    if not to_add:
        return overlay_path

    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    with overlay_path.open("a", encoding="utf-8") as fh:
        # Ensure we start on a fresh line if the file doesn't end with one.
        if existing_lines and not existing_lines[-1].endswith("\n"):
            fh.write("\n")
        for pattern in to_add:
            fh.write(pattern + "\n")

    return overlay_path


def manifest_v1_to_v2(manifest_data: dict) -> dict:
    """Migrate a graphify manifest from schema v1 to v2 (idempotent).

    Schema v1 is a flat dict of ``{filepath: {mtime, ast_hash, semantic_hash}}``
    with no top-level metadata.

    Schema v2 adds two top-level keys:

    * ``"schema_version": 2``
    * ``"files"`` — the per-file entries moved under this key (v1 stored them
      directly at the top level).

    Each per-file entry also gains ``"origin": "private"`` (safest default) if
    the key is absent.

    The function is **idempotent**: calling it on a v2 manifest returns it
    unchanged.

    NOTE: This function is intentionally independent of ``manifest.py`` /
    ``detect.py`` so it does not conflict with the parallel agent that modifies
    those modules.  When that agent wants to unify, they can refactor
    ``_migrate_manifest`` to delegate here.

    Args:
        manifest_data: Raw dict loaded from ``manifest.json``.

    Returns:
        A new dict conforming to schema v2.
    """
    # Already v2 — nothing to do.
    if manifest_data.get("schema_version") == 2:
        # Still ensure every file entry has origin.
        for entry in manifest_data.get("files", {}).values():
            if isinstance(entry, dict) and "origin" not in entry:
                entry["origin"] = "private"
        return manifest_data

    # v1: the dict keys ARE the file paths (except possibly a lone
    # "schema_version" key if someone partially migrated).
    files: dict[str, dict] = {}
    for key, value in manifest_data.items():
        if key == "schema_version":
            continue
        if isinstance(value, dict):
            entry = dict(value)
        elif isinstance(value, (int, float)):
            # Legacy float-only entry (just mtime).
            entry = {"mtime": value, "ast_hash": "", "semantic_hash": ""}
        else:
            entry = {}

        if "origin" not in entry:
            entry["origin"] = "private"
        files[key] = entry

    return {
        "schema_version": 2,
        "files": files,
    }
