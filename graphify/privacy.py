"""Path-based privacy classifier for graphify v0.9.0.

Classifies file paths into one of two buckets — "private" or "shared" — by
reading optional overlay files:

  .graphifyshared   — patterns for files that may be shared (committed to a
                      shared/public graph output)
  .graphifyprivate  — patterns for files that are explicitly private (takes
                      precedence over .graphifyshared when both match)

Overlay inheritance follows the same VCS-ancestry walk used by
.graphifyignore in detect.py (_load_graphifyignore, lines 618-653):
  * Inside a VCS root (.git, .hg, …): parent overlay files walk up to the
    VCS root ceiling and apply to subdirectory scans.
  * Outside any VCS root: only the overlay file closest to *root* is loaded
    (hermetic — no leakage from unrelated parent projects).

ASSUMPTION: The `paths` argument passed to classify_paths() has already been
filtered through the .graphifyignore machinery (detect._is_ignored). This
module does NOT apply ignore rules; it only assigns a privacy bucket to paths
that the upstream pipeline has already decided to include.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Re-exported primitives from detect.py (imported by name to avoid duplication)
# ---------------------------------------------------------------------------
from graphify.detect import (  # noqa: F401  (re-used, not re-exported publicly)
    _VCS_MARKERS,
    _find_vcs_root,
    _parse_gitignore_line,
)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

Bucket = Literal["private", "shared"]

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_SHARED_FILE = ".graphifyshared"
_PRIVATE_FILE = ".graphifyprivate"

_log = logging.getLogger(__name__)

# Sentinel so we warn only once per process when git is unavailable.
_git_warned: bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _OverlayEntry:
    """A single (pattern, base_dir) pair from an overlay file."""

    pattern: str
    base_dir: Path


def _load_overlay_file(
    root: Path,
    filename: str,
) -> list[tuple[str, Path]]:
    """Load patterns from *filename* (.graphifyshared or .graphifyprivate).

    Uses the same VCS-ancestry walk as detect._load_graphifyignore
    (detect.py lines 618-653):

    * Inside VCS: walk from the VCS-root ceiling down to *root*, collecting
      all overlay files found along the path (outer → inner, so inner rules
      override via last-match-wins).
    * Outside VCS: load only the file at *root* itself (hermetic).

    Args:
        root: The scan root directory (resolved absolute path expected).
        filename: Either ``".graphifyshared"`` or ``".graphifyprivate"``.

    Returns:
        List of ``(pattern, base_dir)`` tuples, ceiling-first (outer) to
        root-last (inner).  Empty list when no files are found.
    """
    root = root.resolve()
    ceiling = _find_vcs_root(root) or root

    # Build ordered list of directories: ceiling → … → root
    dirs: list[Path] = []
    current = root
    while True:
        dirs.append(current)
        if current == ceiling:
            break
        current = current.parent
    dirs.reverse()  # ceiling first, scan root last

    patterns: list[tuple[str, Path]] = []
    for d in dirs:
        overlay_file = d / filename
        if overlay_file.exists():
            text = overlay_file.read_text(encoding="utf-8", errors="ignore")
            for raw in text.splitlines():
                line = _parse_gitignore_line(raw)
                if line:
                    patterns.append((line, d))
    return patterns


def _matches_overlay(
    path: Path,
    root: Path,
    patterns: list[tuple[str, Path]],
) -> bool:
    """Return True if *path* matches at least one effective pattern in *patterns*.

    Implements gitignore last-match-wins semantics with negation support,
    mirroring the logic in detect._is_ignored (detect.py lines 656-731).

    Args:
        path: The candidate file path (absolute, resolved).
        root: The scan root used as the fallback relativity anchor.
        patterns: List of ``(pattern, base_dir)`` pairs, outer-to-inner.

    Returns:
        ``True`` if the final matching pattern is a positive match.
    """
    if not patterns:
        return False

    def _fnmatch_multi(target: Path, anchor: Path, p: str) -> bool:
        """Try multiple relative representations against *p*."""
        try:
            rel = str(target.relative_to(anchor)).replace(os.sep, "/")
        except ValueError:
            return False
        parts = rel.split("/")
        if fnmatch.fnmatch(rel, p):
            return True
        if fnmatch.fnmatch(target.name, p):
            return True
        for i, part in enumerate(parts):
            if fnmatch.fnmatch(part, p):
                return True
            if fnmatch.fnmatch("/".join(parts[: i + 1]), p):
                return True
        return False

    def _eval_single(target: Path) -> bool:
        result = False
        for pattern, base_dir in patterns:
            negated = pattern.startswith("!")
            raw = pattern[1:] if negated else pattern
            anchored = raw.startswith("/")
            p = raw.strip("/")
            if not p:
                continue

            matched = False
            if anchored:
                matched = _fnmatch_multi(target, base_dir, p)
            else:
                # Try relative to scan root first, then relative to base_dir
                matched = _fnmatch_multi(target, root, p)
                if not matched and base_dir != root:
                    matched = _fnmatch_multi(target, base_dir, p)

            if matched:
                result = not negated  # last match wins; ! flips result
        return result

    # Gitignore parent-exclusion rule: a negation cannot re-include a file
    # whose ancestor directory is already positively matched.
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return _eval_single(path)

    ancestor = root
    for part in rel_parts[:-1]:
        ancestor = ancestor / part
        if _eval_single(ancestor):
            return True
    return _eval_single(path)


def _git_log_author(vcs_root: Path, path: Path) -> dict | None:
    """Invoke ``git log -1`` to retrieve the last-commit author for *path*.

    This is a low-level helper kept separate so it can be memoized at the
    ``resolve_author`` level.  It is the **only** place in this module that
    spawns a subprocess, and it always uses a list-of-args form (no
    ``shell=True``).

    Args:
        vcs_root: Absolute path to the repository root (contains ``.git``).
        path: Absolute path to the file being queried.

    Returns:
        ``{"email": str, "display_name": str}`` on success, ``None`` on any
        error (git not in PATH, file untracked, empty output, etc.).
    """
    global _git_warned  # noqa: PLW0603

    try:
        rel = str(path.relative_to(vcs_root))
    except ValueError:
        return None

    try:
        result = subprocess.run(
            ["git", "-C", str(vcs_root), "log", "-1", "--format=%ae|%an", "--", rel],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        # git binary not found
        if not _git_warned:
            _git_warned = True
            print(
                "graphify privacy: git not found in PATH; resolve_author will return None",
                file=sys.stderr,
            )
        return None
    except subprocess.CalledProcessError:
        # Non-zero exit (e.g. not a git repo, or other git error)
        if not _git_warned:
            _git_warned = True
            print(
                "graphify privacy: git command failed; resolve_author will return None",
                file=sys.stderr,
            )
        return None

    line = result.stdout.strip()
    if not line or "|" not in line:
        # File is untracked or has no commits
        return None

    email, _, display_name = line.partition("|")
    email = email.strip()
    display_name = display_name.strip()
    if not email and not display_name:
        return None

    return {"email": email, "display_name": display_name}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_overlays(root: Path) -> dict[str, list[tuple[str, Path]]]:
    """Load .graphifyshared and .graphifyprivate overlay patterns for *root*.

    Walks VCS ancestry (same engine as ``.graphifyignore`` in
    detect._load_graphifyignore, lines 618-653).  Each bucket's list is
    ordered outer-first (ceiling directory) to inner-last (scan root), so
    inner rules override outer ones via last-match-wins semantics.

    Outside a VCS root the walk is hermetic: only the overlay file at *root*
    itself is loaded.

    Args:
        root: Directory from which the scan originates.

    Returns:
        A dict with exactly two keys::

            {
                "private": [(pattern, base_dir), ...],
                "shared":  [(pattern, base_dir), ...],
            }

        Both lists are empty when the corresponding overlay file is absent.
    """
    root = root.resolve()
    return {
        "private": _load_overlay_file(root, _PRIVATE_FILE),
        "shared": _load_overlay_file(root, _SHARED_FILE),
    }


def classify_paths(paths: list[Path], root: Path) -> dict[str, Bucket]:
    """Classify each path in *paths* as ``"private"`` or ``"shared"``.

    Classification rules (in priority order):

    1. If ``.graphifyshared`` does NOT exist anywhere in the overlay chain:
       every path is ``"private"`` (conservative default).
    2. Path matches ``.graphifyshared`` only → ``"shared"``.
    3. Path matches ``.graphifyprivate`` only → ``"private"``.
    4. Path matches **both** overlays → ``"private"`` (private wins).
    5. Path matches ``.graphifyshared`` and ``.graphifyprivate`` does not
       exist → ``"shared"``.

    Negation patterns (``!pattern``) follow gitignore semantics within each
    overlay independently.  Matching is relative to the ``base_dir`` where
    each overlay file lives.

    Absolute input paths are converted to relative paths against *root*
    before matching; paths that cannot be made relative are matched as-is.

    Note:
        This function assumes *paths* have already been filtered by the
        upstream ``.graphifyignore`` pipeline in ``detect.py``.  It does not
        apply ignore rules itself.

    Args:
        paths: File paths to classify (may be absolute or relative).
        root: The scan root directory; used as the relativity anchor.

    Returns:
        ``{str(path): "private" | "shared"}`` for every path in *paths*.
    """
    root = root.resolve()
    overlays = load_overlays(root)

    shared_patterns = overlays["shared"]
    private_patterns = overlays["private"]

    has_shared_overlay = bool(shared_patterns)
    has_private_overlay = bool(private_patterns)

    result: dict[str, Bucket] = {}

    for p in paths:
        # Normalise to absolute resolved path for matching
        abs_path = p.resolve() if not p.is_absolute() else p.resolve()

        key = str(p)  # preserve the caller's original representation as the key

        if not has_shared_overlay:
            # Rule 1: no .graphifyshared anywhere → everything is private
            result[key] = "private"
            continue

        in_shared = _matches_overlay(abs_path, root, shared_patterns)
        in_private = _matches_overlay(abs_path, root, private_patterns) if has_private_overlay else False

        if in_shared and in_private:
            # Rule 4: both match → private wins
            result[key] = "private"
        elif in_private:
            # Rule 3: only private matches
            result[key] = "private"
        elif in_shared:
            # Rule 2 / Rule 5: shared matches (private overlay absent or non-matching)
            result[key] = "shared"
        else:
            # No match in either overlay: default to private (conservative)
            result[key] = "private"

    return result


def is_legacy_mode(root: Path) -> bool:
    """Return True when the repo should behave as graphify v0.8.18 (single graph).

    Legacy mode is active when **all** of the following are true:

    * No ``.graphifyshared`` file exists in the overlay chain for *root*.
    * No ``.graphifyprivate`` file exists in the overlay chain for *root*.
    * No remote is configured in ``~/.graphify/config.toml`` for this repo
      path.

    Args:
        root: The scan root directory.

    Returns:
        ``True`` if legacy mode is active; ``False`` otherwise.
    """
    root = root.resolve()
    overlays = load_overlays(root)
    has_overlays = bool(overlays["shared"]) or bool(overlays["private"])

    if has_overlays:
        return False

    has_remote = _has_remote_configured(root)

    return not has_remote


def _has_remote_configured(root: Path) -> bool:
    """Return True iff `root` has a configured remote in ~/.graphify/config.toml.

    Used by is_legacy_mode() to detect Stage-2 config-driven remotes
    (in addition to overlay files).

    Args:
        root: Repository root path.

    Returns:
        ``True`` when a matching ``[[remote]]`` entry exists in the config file.
    """
    from graphify.config import find_remote
    return find_remote(root) is not None


@lru_cache(maxsize=512)
def _cached_git_log_author(vcs_root: Path, path: Path) -> dict | None:
    """Memoised wrapper around _git_log_author.

    Caches by ``(vcs_root, path)`` so repeated calls for the same file
    within a session do not spawn additional subprocesses.

    Args:
        vcs_root: Absolute resolved VCS root directory.
        path: Absolute resolved file path.

    Returns:
        ``{"email": str, "display_name": str}`` or ``None``.
    """
    return _git_log_author(vcs_root, path)


def resolve_author(path: Path, vcs_root: Path | None = None) -> dict | None:
    """Resolve the last-commit author for *path* via ``git log -1``.

    Author information is memoised by ``(vcs_root, path)`` so that bulk
    classification calls do not spawn one subprocess per file.

    If *vcs_root* is ``None``, it is inferred by walking upward from
    ``path.parent`` looking for a ``.git/`` directory (or any other VCS
    marker in ``_VCS_MARKERS``).

    Args:
        path: The file whose git authorship is being queried.
        vcs_root: Optional explicit VCS root.  If ``None``, inferred
            automatically.

    Returns:
        ``{"email": str, "display_name": str}`` with the author of the most
        recent commit touching *path*, or ``None`` when:

        * git is not available in ``PATH``.
        * The file is untracked (no commits reference it).
        * *path* lies outside any git repository.
        * Any ``subprocess.CalledProcessError`` is raised.
    """
    abs_path = path.resolve()

    if vcs_root is None:
        vcs_root = _find_vcs_root(abs_path.parent)

    if vcs_root is None:
        return None

    vcs_root = vcs_root.resolve()
    return _cached_git_log_author(vcs_root, abs_path)
