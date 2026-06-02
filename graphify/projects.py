"""TOML-backed registry for graphify knowledge groups (central "projects").

A *group* is a named knowledge base stored centrally at ``~/.graphify/<name>/``
— its own git repo — that accumulates knowledge from one or more source
repos/dirs into a single growing graph at
``~/.graphify/<name>/graphify-out/graph.json``.

This is intentionally separate from :mod:`graphify.config` (``config.toml``),
which is keyed by ``repo_path`` (one repo → one shared-graph remote).  Groups
are keyed by a semantic *name* chosen by the user (a client / project /
platform) and own a *list* of sources — a different cardinality and lifecycle.

Storage: ``~/.graphify/projects.toml`` (override via ``$GRAPHIFY_PROJECTS``).
The group directories live under ``~/.graphify`` (override the whole root via
``$GRAPHIFY_HOME`` — used by tests and worktrees).

Public API
----------
- ``Group`` / ``GroupSource``      — frozen dataclasses for one registry entry.
- ``graphify_home`` / ``projects_path`` / ``group_root`` — path helpers.
- ``sanitize_group_name``          — filesystem-safe, traversal-proof name.
- ``load_groups`` / ``find_group`` / ``find_group_for_source`` — reads.
- ``upsert_group`` / ``add_source`` / ``rename_group`` / ``remove_group`` — mutations.
- ``set_remote`` / ``get_remote_url`` / ``require_remote_url`` — remote URL handling.
- ``git_init_group`` / ``commit_group`` / ``push_group`` — git wrappers that
  surface errors instead of swallowing them.
- ``import_repo_into_group``       — assisted migration of a legacy graphify-out/.
"""

from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import]
else:
    import tomli as tomllib  # type: ignore[import-not-found]

from graphify.git_integration import _git, push_branch

__all__ = [
    "Group",
    "GroupSource",
    "GitOpResult",
    "graphify_home",
    "projects_path",
    "group_root",
    "sanitize_group_name",
    "load_groups",
    "find_group",
    "find_group_for_source",
    "upsert_group",
    "add_source",
    "rename_group",
    "remove_group",
    "set_remote",
    "get_remote_url",
    "require_remote_url",
    "git_init_group",
    "commit_group",
    "push_group",
    "import_repo_into_group",
]

_PROJECTS_HEADER = (
    "# graphify projects — managed by `graphify project` subcommands."
    " Hand-editing is OK if you preserve the [[group]] schema.\n"
)

_MAX_NAME_LEN = 64
_GROUP_MARKER = ".graphify_group"  # written into a repo's graphify-out/ on import


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupSource:
    """One source (repo/dir) feeding a group's graph.

    Args:
        path: Absolute, resolved path to the source repo/dir.
        contributor: Contributor tag stamped on nodes from this source.
        added_at: ISO-8601 UTC timestamp of when the source was added.
    """

    path: Path
    contributor: str | None = None
    added_at: str = ""


@dataclass(frozen=True)
class Group:
    """One ``[[group]]`` entry in ``~/.graphify/projects.toml``.

    Args:
        name: Semantic, sanitized, unique group name (a path-safe component).
        dir: Absolute path to the group dir (``~/.graphify/<name>``, a git repo).
        created_at: ISO-8601 UTC timestamp of group creation.
        parent: Optional parent group name (composition / hierarchy); ``None`` = top-level.
        remote_url: Optional dedicated git remote URL for push (``None`` = local-only).
        sources: Tuple of :class:`GroupSource` feeding this group.
    """

    name: str
    dir: Path
    created_at: str = ""
    parent: str | None = None
    remote_url: str | None = None
    sources: tuple[GroupSource, ...] = ()


@dataclass(frozen=True)
class GitOpResult:
    """Result of a commit/push operation.

    ``detail`` carries the new SHA / branch on success, or git's stderr on
    failure — it is always meant to be shown to the user.
    """

    ok: bool
    detail: str


# ---------------------------------------------------------------------------
# Time helper
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (second resolution)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def graphify_home() -> Path:
    """Return the graphify central home dir.

    Respects ``$GRAPHIFY_HOME``; falls back to ``~/.graphify``.  All group
    directories live directly under this path.
    """
    return Path(os.environ.get("GRAPHIFY_HOME", str(Path.home() / ".graphify")))


def projects_path() -> Path:
    """Return the path to the projects registry file.

    Respects ``$GRAPHIFY_PROJECTS``; falls back to ``<graphify_home>/projects.toml``.
    """
    return Path(
        os.environ.get(
            "GRAPHIFY_PROJECTS",
            str(graphify_home() / "projects.toml"),
        )
    )


def group_root(name: str) -> Path:
    """Return the absolute group directory for *name* (``<graphify_home>/<name>``).

    The name is sanitized first, guaranteeing a single safe path component.
    """
    return graphify_home() / sanitize_group_name(name)


# ---------------------------------------------------------------------------
# Name sanitization
# ---------------------------------------------------------------------------


def sanitize_group_name(raw: str) -> str:
    """Return a filesystem-safe, traversal-proof group name.

    Lowercases, NFKD-normalizes, replaces every char outside ``[a-z0-9._-]``
    with ``-``, collapses repeats, strips leading/trailing ``-._``, caps length.
    Rejects empty results and the reserved names ``.`` / ``..``.

    Args:
        raw: User-supplied group name.

    Returns:
        A safe single path component.

    Raises:
        ValueError: When *raw* sanitizes to an empty or reserved name.
    """
    if raw is None:
        raise ValueError("group name is required")
    norm = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    norm = norm.strip().lower()
    out_chars: list[str] = []
    for ch in norm:
        out_chars.append(ch if (ch.isalnum() or ch in "._-") else "-")
    cleaned = "".join(out_chars)
    # collapse runs of '-'
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-._")
    cleaned = cleaned[:_MAX_NAME_LEN].strip("-._")
    if not cleaned or cleaned in (".", ".."):
        raise ValueError(
            f"group name {raw!r} is not usable as a folder name; "
            "use letters, digits, '.', '_' or '-'"
        )
    return cleaned


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def load_groups(path: Path | None = None) -> list[Group]:
    """Parse the registry file and return all group entries.

    Args:
        path: Registry path.  Defaults to :func:`projects_path`.

    Returns:
        List of :class:`Group` (empty when the file is missing or has no
        ``[[group]]`` sections).

    Raises:
        ValueError: On malformed TOML or wrong-typed required fields.
    """
    resolved_path = path if path is not None else projects_path()

    if not resolved_path.exists():
        return []
    raw = resolved_path.read_bytes()
    if not raw.strip():
        return []

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"malformed projects.toml: {exc}") from exc

    groups = data.get("group", [])
    if not isinstance(groups, list):
        raise ValueError("malformed projects.toml: 'group' must be an array of tables")

    result: list[Group] = []
    for i, entry in enumerate(groups):
        if not isinstance(entry, dict):
            raise ValueError(f"malformed projects.toml: group[{i}] is not a table")
        result.append(_parse_group(entry, index=i))
    return result


def _opt_str(entry: dict, key: str, index: int) -> str | None:
    """Read an optional string field; empty string is normalized to ``None``."""
    val = entry.get(key, None)
    if val is None:
        return None
    if not isinstance(val, str):
        raise ValueError(f"malformed projects.toml: group[{index}].{key} must be a string")
    return val or None


def _parse_group(entry: dict, *, index: int) -> Group:
    """Convert a raw TOML table to a :class:`Group`."""
    if "name" not in entry:
        raise ValueError(
            f"malformed projects.toml: group[{index}] is missing required field 'name'"
        )
    name_raw = entry["name"]
    if not isinstance(name_raw, str):
        raise ValueError(f"malformed projects.toml: group[{index}].name must be a string")

    dir_raw = entry.get("dir")
    if dir_raw is not None and not isinstance(dir_raw, str):
        raise ValueError(f"malformed projects.toml: group[{index}].dir must be a string")
    group_dir = Path(dir_raw) if dir_raw else group_root(name_raw)

    sources_raw = entry.get("source", [])
    if not isinstance(sources_raw, list):
        raise ValueError(
            f"malformed projects.toml: group[{index}].source must be an array of tables"
        )
    sources: list[GroupSource] = []
    for j, s in enumerate(sources_raw):
        if not isinstance(s, dict):
            raise ValueError(
                f"malformed projects.toml: group[{index}].source[{j}] is not a table"
            )
        spath = s.get("path")
        if not isinstance(spath, str) or not spath:
            raise ValueError(
                f"malformed projects.toml: group[{index}].source[{j}].path is required"
            )
        contributor = s.get("contributor")
        if contributor is not None and not isinstance(contributor, str):
            raise ValueError(
                f"malformed projects.toml: group[{index}].source[{j}].contributor must be a string"
            )
        added_at = s.get("added_at", "")
        if not isinstance(added_at, str):
            raise ValueError(
                f"malformed projects.toml: group[{index}].source[{j}].added_at must be a string"
            )
        sources.append(
            GroupSource(path=Path(spath), contributor=contributor or None, added_at=added_at)
        )

    created_at = entry.get("created_at", "")
    if not isinstance(created_at, str):
        raise ValueError(f"malformed projects.toml: group[{index}].created_at must be a string")

    return Group(
        name=name_raw,
        dir=group_dir,
        created_at=created_at,
        parent=_opt_str(entry, "parent", index),
        remote_url=_opt_str(entry, "remote_url", index),
        sources=tuple(sources),
    )


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def find_group(name: str, path: Path | None = None) -> Group | None:
    """Return the group whose (sanitized) name matches *name*, or ``None``."""
    try:
        target = sanitize_group_name(name)
    except ValueError:
        return None
    for g in load_groups(path):
        try:
            if sanitize_group_name(g.name) == target:
                return g
        except ValueError:
            continue
    return None


def find_group_for_source(src_path: Path, path: Path | None = None) -> Group | None:
    """Return the group that already contains *src_path* as a source, or ``None``."""
    resolved = src_path.resolve(strict=False)
    for g in load_groups(path):
        for s in g.sources:
            if s.path.resolve(strict=False) == resolved:
                return g
    return None


# ---------------------------------------------------------------------------
# Writing helpers (mirrors config.py)
# ---------------------------------------------------------------------------


def _escape_toml_string(value: str) -> str:
    """Escape backslashes and double-quotes for a TOML basic string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _serialize_groups(groups: list[Group]) -> str:
    """Render the full registry file content as a TOML string."""
    lines: list[str] = [_PROJECTS_HEADER]
    for g in groups:
        lines.append("[[group]]")
        lines.append(f'name        = "{_escape_toml_string(g.name)}"')
        lines.append(f'dir         = "{_escape_toml_string(str(g.dir))}"')
        lines.append(f'created_at  = "{_escape_toml_string(g.created_at)}"')
        lines.append(f'parent      = "{_escape_toml_string(g.parent or "")}"')
        lines.append(f'remote_url  = "{_escape_toml_string(g.remote_url or "")}"')
        for s in g.sources:
            lines.append("")
            lines.append("[[group.source]]")
            lines.append(f'path        = "{_escape_toml_string(str(s.path))}"')
            lines.append(f'contributor = "{_escape_toml_string(s.contributor or "")}"')
            lines.append(f'added_at    = "{_escape_toml_string(s.added_at)}"')
        lines.append("")
    return "\n".join(lines)


def _atomic_write(target: Path, content: str) -> None:
    """Write *content* to *target* atomically (tmp file + os.replace)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _save(groups: list[Group], path: Path | None) -> None:
    _atomic_write(path if path is not None else projects_path(), _serialize_groups(groups))


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def upsert_group(
    name: str,
    *,
    dir: Path | None = None,
    parent: str | None = None,
    remote_url: str | None = None,
    path: Path | None = None,
) -> Group:
    """Create or update the registry entry for the group *name*.

    On update, ``None`` kwargs preserve the stored value.  ``sources`` are never
    touched here (use :func:`add_source`).

    Args:
        name: Group name (sanitized before storage and used as the key).
        dir: Group directory.  Defaults to :func:`group_root` on create.
        parent: Parent group name (composition).  ``None`` preserves on update.
        remote_url: Dedicated remote URL.  ``None`` preserves on update.
        path: Registry path.  Defaults to :func:`projects_path`.

    Returns:
        The upserted :class:`Group`.
    """
    safe = sanitize_group_name(name)
    groups = load_groups(path)

    idx: int | None = None
    for i, g in enumerate(groups):
        if sanitize_group_name(g.name) == safe:
            idx = i
            break

    if idx is not None:
        existing = groups[idx]
        new = Group(
            name=safe,
            dir=(dir.resolve() if dir is not None else existing.dir),
            created_at=existing.created_at or _now_iso(),
            parent=parent if parent is not None else existing.parent,
            remote_url=remote_url if remote_url is not None else existing.remote_url,
            sources=existing.sources,
        )
        groups = [new if i == idx else g for i, g in enumerate(groups)]
    else:
        new = Group(
            name=safe,
            dir=(dir.resolve() if dir is not None else group_root(safe)),
            created_at=_now_iso(),
            parent=parent,
            remote_url=remote_url,
            sources=(),
        )
        groups = [*groups, new]

    _save(groups, path)
    return new


def add_source(
    name: str,
    src_path: Path,
    *,
    contributor: str | None = None,
    path: Path | None = None,
) -> Group:
    """Register *src_path* as a source of group *name* (dedup by resolved path).

    Args:
        name: Existing group name.
        src_path: Source repo/dir to add.
        contributor: Contributor tag for nodes from this source.
        path: Registry path.  Defaults to :func:`projects_path`.

    Returns:
        The updated :class:`Group`.

    Raises:
        ValueError: When the group does not exist.
    """
    safe = sanitize_group_name(name)
    groups = load_groups(path)
    idx = next(
        (i for i, g in enumerate(groups) if sanitize_group_name(g.name) == safe), None
    )
    if idx is None:
        raise ValueError(f"group {name!r} does not exist; create it first")

    existing = groups[idx]
    resolved = src_path.resolve(strict=False)
    kept = [s for s in existing.sources if s.path.resolve(strict=False) != resolved]
    kept.append(
        GroupSource(path=resolved, contributor=contributor, added_at=_now_iso())
    )
    new = Group(
        name=existing.name,
        dir=existing.dir,
        created_at=existing.created_at,
        parent=existing.parent,
        remote_url=existing.remote_url,
        sources=tuple(kept),
    )
    groups = [new if i == idx else g for i, g in enumerate(groups)]
    _save(groups, path)
    return new


def rename_group(old: str, new: str, path: Path | None = None) -> Group:
    """Rename group *old* to *new* and move its directory.

    Args:
        old: Existing group name.
        new: New group name (must be free).
        path: Registry path.  Defaults to :func:`projects_path`.

    Returns:
        The renamed :class:`Group` (with updated ``dir``).

    Raises:
        ValueError: When *old* is unknown or *new* already exists.
    """
    safe_old = sanitize_group_name(old)
    safe_new = sanitize_group_name(new)
    groups = load_groups(path)
    idx = next(
        (i for i, g in enumerate(groups) if sanitize_group_name(g.name) == safe_old), None
    )
    if idx is None:
        raise ValueError(f"group {old!r} does not exist")
    if safe_new != safe_old and any(
        sanitize_group_name(g.name) == safe_new for g in groups
    ):
        raise ValueError(f"group {new!r} already exists")

    existing = groups[idx]
    new_dir = group_root(safe_new)
    if existing.dir.exists() and existing.dir.resolve() != new_dir.resolve():
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(existing.dir, new_dir)

    renamed = Group(
        name=safe_new,
        dir=new_dir,
        created_at=existing.created_at,
        parent=existing.parent,
        remote_url=existing.remote_url,
        sources=existing.sources,
    )
    groups = [renamed if i == idx else g for i, g in enumerate(groups)]
    _save(groups, path)
    return renamed


def remove_group(name: str, path: Path | None = None) -> bool:
    """Remove the registry entry for *name* (does NOT delete files on disk).

    Returns ``True`` if an entry was found and removed.
    """
    try:
        safe = sanitize_group_name(name)
    except ValueError:
        return False
    groups = load_groups(path)
    filtered = [g for g in groups if sanitize_group_name(g.name) != safe]
    if len(filtered) == len(groups):
        return False
    _save(filtered, path)
    return True


# ---------------------------------------------------------------------------
# Remote URL handling
# ---------------------------------------------------------------------------


def set_remote(name: str, url: str, *, path: Path | None = None) -> Group:
    """Provide / update the dedicated git remote URL for group *name*.

    Persists ``remote_url`` in the registry AND registers (or updates) the
    ``origin`` remote in the group's git repo so ``push`` works.

    Args:
        name: Existing group name.
        url: Non-empty git remote URL.
        path: Registry path.  Defaults to :func:`projects_path`.

    Returns:
        The updated :class:`Group`.

    Raises:
        ValueError: When *url* is empty or the group does not exist.
    """
    if not url or not url.strip():
        raise ValueError("remote URL must be a non-empty string")
    url = url.strip()
    grp = find_group(name, path)
    if grp is None:
        raise ValueError(f"group {name!r} does not exist; create it first")

    # Register the remote in the group's git repo (idempotent: add-or-set-url).
    if (grp.dir / ".git").exists():
        existing = _git(grp.dir, "remote", check=False)
        remotes = (existing.stdout or "").split()
        if "origin" in remotes:
            _git(grp.dir, "remote", "set-url", "origin", url, check=False)
        else:
            _git(grp.dir, "remote", "add", "origin", url, check=False)

    return upsert_group(grp.name, remote_url=url, path=path)


def get_remote_url(name: str, path: Path | None = None) -> str | None:
    """Return the configured remote URL for group *name*, or ``None``."""
    grp = find_group(name, path)
    return grp.remote_url if grp else None


def require_remote_url(name: str, path: Path | None = None) -> str:
    """Return the group's remote URL, or raise an actionable error when unset.

    This is the "the remote URL must be provided" contract: callers that need
    to push must go through here so the failure message tells the user exactly
    how to fix it.

    Raises:
        ValueError: When the group has no remote configured.
    """
    url = get_remote_url(name, path)
    if not url:
        raise ValueError(
            f"group {name!r} has no remote configured; "
            f"run 'graphify project remote {name} <url>' to set one"
        )
    return url


# ---------------------------------------------------------------------------
# Git wrappers — surface errors, never swallow them
# ---------------------------------------------------------------------------


def git_init_group(group_dir: Path) -> None:
    """Create *group_dir* and ``git init`` it if not already a repo (idempotent)."""
    group_dir.mkdir(parents=True, exist_ok=True)
    if not (group_dir / ".git").exists():
        _git(group_dir, "init", "--quiet")


def commit_group(group_dir: Path, message: str, paths: list[str]) -> GitOpResult:
    """Stage *paths* and commit them in the group repo.

    Never raises on git failure — returns a :class:`GitOpResult` whose ``detail``
    carries git's stderr (on failure) or the new short SHA (on success).  A
    no-op ("nothing to commit") is reported as ``ok=True``.

    Args:
        group_dir: Group repo root.
        message: Commit message.
        paths: Repo-relative paths to stage (existing paths only are added).
    """
    if not (group_dir / ".git").exists():
        return GitOpResult(False, f"{group_dir} is not a git repo (run 'git init' first)")

    # Stage only the paths that exist; ignore missing ones quietly.
    existing = [p for p in paths if (group_dir / p).exists()]
    if existing:
        add = _git(group_dir, "add", "--", *existing, check=False)
        if add.returncode != 0:
            return GitOpResult(False, f"git add failed: {(add.stderr or '').strip()}")

    # Nothing staged → nothing to commit (not an error).
    staged = _git(group_dir, "diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        return GitOpResult(True, "nothing to commit")

    commit = _git(group_dir, "commit", "--quiet", "-m", message, check=False)
    if commit.returncode != 0:
        return GitOpResult(False, f"git commit failed: {(commit.stderr or '').strip()}")

    head = _git(group_dir, "rev-parse", "--short", "HEAD", check=False)
    sha = (head.stdout or "").strip() or "HEAD"
    return GitOpResult(True, sha)


def push_group(name: str, *, path: Path | None = None) -> GitOpResult:
    """Push the group repo's current branch to its configured remote.

    Requires a remote (via :func:`require_remote_url`).  Errors are returned in
    the :class:`GitOpResult`, never swallowed.

    Raises:
        ValueError: When the group is unknown or has no remote configured
            (the caller turns this into a clear CLI message + fallback).
    """
    grp = find_group(name, path)
    if grp is None:
        raise ValueError(f"group {name!r} does not exist")
    require_remote_url(name, path)  # raises with an actionable message when unset

    if not (grp.dir / ".git").exists():
        return GitOpResult(False, f"{grp.dir} is not a git repo")

    # Current branch name (HEAD); fall back to pushing HEAD if detached.
    head = _git(grp.dir, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    branch = (head.stdout or "").strip() or "HEAD"
    try:
        # -u sets upstream on first push; harmless thereafter.
        result = _git(grp.dir, "push", "-u", "origin", branch, check=False)
    except RuntimeError as exc:  # defensive; check=False shouldn't raise
        return GitOpResult(False, str(exc))
    if result.returncode != 0:
        return GitOpResult(False, f"git push failed: {(result.stderr or '').strip()}")
    return GitOpResult(True, f"pushed {branch} → origin")


# ---------------------------------------------------------------------------
# Assisted migration
# ---------------------------------------------------------------------------


def import_repo_into_group(
    repo: Path,
    name: str,
    *,
    merge_into: str | None = None,
    out_dirname: str = "graphify-out",
    path: Path | None = None,
) -> dict:
    """Move an existing ``<repo>/graphify-out`` into a central group.

    Idempotent and conservative (modeled on ``migrate.migrate_to_shared``):

    * Returns ``{"status": "no_graphify_out"}`` when there is nothing to import.
    * Returns ``{"status": "already_imported", ...}`` when the repo already
      carries the ``.graphify_group`` marker.
    * ``merge_into`` accumulates into an existing group instead of moving files
      (the caller runs the extract/accumulate flow); this function only records
      the source and writes the marker.
    * Otherwise creates the group dir (caller ``git_init``s it), moves the
      artifacts in, registers the group, and leaves a ``.graphify_group`` marker
      pointing at the group.

    Args:
        repo: Source repo whose ``graphify-out/`` should be imported.
        name: Target group name (for a brand-new group).
        merge_into: Existing group name to accumulate into instead of moving.
        out_dirname: Output dir name inside the repo (default ``graphify-out``).
        path: Registry path.  Defaults to :func:`projects_path`.

    Returns:
        A status dict.  Possible ``status`` values: ``no_graphify_out``,
        ``already_imported``, ``merged``, ``imported``.
    """
    repo = repo.resolve(strict=False)
    src_out = repo / out_dirname
    marker = src_out / _GROUP_MARKER

    # Marker check first: after a successful import graph.json has been moved
    # out, so the no_graphify_out guard below would otherwise mask the
    # already-imported state.
    if marker.exists():
        return {
            "status": "already_imported",
            "repo": str(repo),
            "group": marker.read_text(encoding="utf-8").strip(),
        }

    if not src_out.exists() or not (src_out / "graph.json").exists():
        return {"status": "no_graphify_out", "repo": str(repo), "src_out": str(src_out)}

    # Merge-into-existing: do NOT move files (ids would collide). The caller
    # runs the accumulate flow; here we just register the source + marker.
    if merge_into:
        target = find_group(merge_into, path)
        if target is None:
            raise ValueError(f"group {merge_into!r} does not exist")
        add_source(target.name, repo, path=path)
        marker.write_text(target.name + "\n", encoding="utf-8")
        return {"status": "merged", "repo": str(repo), "group": target.name}

    # Brand-new group: create dir, move artifacts in.
    safe = sanitize_group_name(name)
    dest_root = group_root(safe)
    dest_out = dest_root / out_dirname
    if dest_out.exists() and any(dest_out.iterdir()):
        raise ValueError(
            f"target {dest_out} already exists and is not empty; "
            f"use --merge-into {safe} to accumulate instead"
        )
    dest_out.mkdir(parents=True, exist_ok=True)

    for entry in list(src_out.iterdir()):
        if entry.name == _GROUP_MARKER:
            continue
        target_path = dest_out / entry.name
        try:
            os.replace(entry, target_path)  # fast path: same filesystem
        except OSError:
            # Cross-filesystem (e.g. $HOME vs repo mount): copy then remove.
            if entry.is_dir():
                shutil.copytree(entry, target_path, dirs_exist_ok=True)
                shutil.rmtree(entry)
            else:
                shutil.copy2(entry, target_path)
                entry.unlink()

    upsert_group(safe, dir=dest_root, path=path)
    add_source(safe, repo, path=path)
    # Leave a pointer so future extract/update from the repo route to the group,
    # and so installed hooks that test graphify-out/ still find the marker dir.
    src_out.mkdir(parents=True, exist_ok=True)
    marker.write_text(safe + "\n", encoding="utf-8")

    return {
        "status": "imported",
        "repo": str(repo),
        "group": safe,
        "dir": str(dest_root),
    }
