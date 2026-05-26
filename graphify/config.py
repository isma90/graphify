"""TOML-backed config for graphify remote entries.

Manages ``~/.graphify/config.toml`` (or the path given by the
``GRAPHIFY_CONFIG`` environment variable).  Each ``[[remote]]`` section
records how a local repo root maps to a shared-graph git branch.

Public API
----------
- ``RemoteConfig``       — frozen dataclass representing one ``[[remote]]`` entry.
- ``config_path()``      — canonical path to the config file.
- ``load_config()``      — parse and return all entries.
- ``find_remote()``      — look up the entry for a specific repo root.
- ``upsert_remote()``    — create or update the entry for a repo root (atomic).
- ``remove_remote()``    — delete the entry for a repo root (atomic).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import]
else:
    import tomli as tomllib  # type: ignore[import-not-found]

__all__ = [
    "RemoteConfig",
    "config_path",
    "load_config",
    "find_remote",
    "upsert_remote",
    "remove_remote",
]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

_DEFAULT_BRANCH = "graphify/shared"
_DEFAULT_SHARED_PATH = "graphify-out/graph-shared.json"
_DEFAULT_REMOTE_NAME = "origin"

_CONFIG_HEADER = (
    "# graphify config — managed by `graphify remote` subcommands."
    " Hand-editing is OK if you preserve the [[remote]] schema.\n"
)


@dataclass(frozen=True)
class RemoteConfig:
    """One ``[[remote]]`` entry in ``~/.graphify/config.toml``.

    Args:
        repo_path: Absolute, resolved path to the local repository root.
        url: Git remote URL (informational; git is the source of truth).
        branch: Branch name used for the shared graph.
        shared_path: Repo-relative path to ``graph-shared.json``.
        remote_name: Name of the git remote to push/pull from.
    """

    repo_path: Path
    url: str | None = None
    branch: str = _DEFAULT_BRANCH
    shared_path: str = _DEFAULT_SHARED_PATH
    remote_name: str = _DEFAULT_REMOTE_NAME


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def config_path() -> Path:
    """Return the path to the graphify config file.

    Respects the ``GRAPHIFY_CONFIG`` environment variable; falls back to
    ``~/.graphify/config.toml``.

    Returns:
        Absolute path to the config file (file need not exist).
    """
    return Path(
        os.environ.get(
            "GRAPHIFY_CONFIG",
            str(Path.home() / ".graphify" / "config.toml"),
        )
    )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def load_config(path: Path | None = None) -> list[RemoteConfig]:
    """Parse the config file and return all remote entries.

    Args:
        path: Config file path.  Defaults to ``config_path()``.

    Returns:
        List of ``RemoteConfig`` objects (empty when the file is missing or
        contains no ``[[remote]]`` sections).

    Raises:
        ValueError: If the TOML is syntactically invalid or a required field
            has the wrong type.
    """
    resolved_path = path if path is not None else config_path()

    if not resolved_path.exists():
        return []

    raw = resolved_path.read_bytes()
    if not raw.strip():
        return []

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except Exception as exc:  # tomllib raises TOMLDecodeError (subclass of ValueError)
        raise ValueError(f"malformed config.toml: {exc}") from exc

    remotes = data.get("remote", [])
    if not isinstance(remotes, list):
        raise ValueError("malformed config.toml: 'remote' must be an array of tables")

    result: list[RemoteConfig] = []
    for i, entry in enumerate(remotes):
        if not isinstance(entry, dict):
            raise ValueError(f"malformed config.toml: remote[{i}] is not a table")
        result.append(_parse_entry(entry, index=i))

    return result


def _parse_entry(entry: dict, *, index: int) -> RemoteConfig:
    """Convert a raw TOML table to a ``RemoteConfig``.

    Args:
        entry: Raw dict from the TOML parser.
        index: Position in the ``[[remote]]`` array (for error messages).

    Returns:
        Validated ``RemoteConfig``.

    Raises:
        ValueError: On a missing or wrong-typed required field.
    """
    if "repo_path" not in entry:
        raise ValueError(
            f"malformed config.toml: remote[{index}] is missing required field 'repo_path'"
        )
    repo_path_raw = entry["repo_path"]
    if not isinstance(repo_path_raw, str):
        raise ValueError(
            f"malformed config.toml: remote[{index}].repo_path must be a string"
        )

    url = entry.get("url", None)
    if url is not None and not isinstance(url, str):
        raise ValueError(f"malformed config.toml: remote[{index}].url must be a string")

    branch = entry.get("branch", _DEFAULT_BRANCH)
    if not isinstance(branch, str):
        raise ValueError(f"malformed config.toml: remote[{index}].branch must be a string")

    shared_path = entry.get("shared_path", _DEFAULT_SHARED_PATH)
    if not isinstance(shared_path, str):
        raise ValueError(
            f"malformed config.toml: remote[{index}].shared_path must be a string"
        )

    remote_name = entry.get("remote_name", _DEFAULT_REMOTE_NAME)
    if not isinstance(remote_name, str):
        raise ValueError(
            f"malformed config.toml: remote[{index}].remote_name must be a string"
        )

    return RemoteConfig(
        repo_path=Path(repo_path_raw),
        url=url,
        branch=branch,
        shared_path=shared_path,
        remote_name=remote_name,
    )


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def find_remote(root: Path, path: Path | None = None) -> RemoteConfig | None:
    """Return the config entry whose ``repo_path`` matches *root*.

    Args:
        root: Local repository root to look up.  Resolved via
            ``Path.resolve(strict=False)`` before comparison.
        path: Config file path.  Defaults to ``config_path()``.

    Returns:
        The matching ``RemoteConfig``, or ``None`` if no entry is found.
    """
    resolved_root = root.resolve(strict=False)
    for entry in load_config(path):
        if entry.repo_path.resolve(strict=False) == resolved_root:
            return entry
    return None


# ---------------------------------------------------------------------------
# Writing helpers
# ---------------------------------------------------------------------------


def _escape_toml_string(value: str) -> str:
    """Return *value* as a TOML basic-string payload (without surrounding quotes).

    Escapes backslashes and double-quotes only — sufficient for filesystem paths
    and git URLs.

    Args:
        value: Raw string to escape.

    Returns:
        Escaped string suitable for embedding between ``"..."`` delimiters.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _serialize_config(entries: list[RemoteConfig]) -> str:
    """Render the full config file content as a TOML string.

    Args:
        entries: All remote entries to write.

    Returns:
        Complete TOML file content including the header comment.
    """
    lines: list[str] = [_CONFIG_HEADER]
    for entry in entries:
        lines.append("[[remote]]")
        lines.append(f'repo_path     = "{_escape_toml_string(str(entry.repo_path))}"')
        if entry.url is not None:
            lines.append(f'url           = "{_escape_toml_string(entry.url)}"')
        lines.append(f'branch        = "{_escape_toml_string(entry.branch)}"')
        lines.append(f'shared_path   = "{_escape_toml_string(entry.shared_path)}"')
        lines.append(f'remote_name   = "{_escape_toml_string(entry.remote_name)}"')
        lines.append("")  # blank line between entries
    return "\n".join(lines)


def _atomic_write(target: Path, content: str) -> None:
    """Write *content* to *target* atomically using a tmp file + os.replace.

    Args:
        target: Destination path.
        content: Text to write (UTF-8).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def upsert_remote(
    root: Path,
    url: str | None,
    *,
    branch: str | None = None,
    shared_path: str | None = None,
    remote_name: str | None = None,
    path: Path | None = None,
) -> RemoteConfig:
    """Create or update the config entry for *root*.

    When updating an existing entry, any kwarg left as ``None`` preserves the
    stored value.  When creating a new entry, ``None`` kwargs use the dataclass
    defaults.

    Args:
        root: Local repository root (resolved before storage).
        url: Git remote URL, or ``None`` to clear it.
        branch: Branch name override.  ``None`` preserves existing or uses default.
        shared_path: Shared-graph file path override.  ``None`` preserves or defaults.
        remote_name: Git remote name override.  ``None`` preserves or defaults.
        path: Config file path.  Defaults to ``config_path()``.

    Returns:
        The upserted ``RemoteConfig`` as it now appears in the file.
    """
    resolved_path = path if path is not None else config_path()
    resolved_root = root.resolve(strict=False)

    entries = load_config(resolved_path)

    existing_index: int | None = None
    for i, entry in enumerate(entries):
        if entry.repo_path.resolve(strict=False) == resolved_root:
            existing_index = i
            break

    if existing_index is not None:
        existing = entries[existing_index]
        new_entry = RemoteConfig(
            repo_path=resolved_root,
            url=url,
            branch=branch if branch is not None else existing.branch,
            shared_path=shared_path if shared_path is not None else existing.shared_path,
            remote_name=remote_name if remote_name is not None else existing.remote_name,
        )
        entries = [new_entry if i == existing_index else e for i, e in enumerate(entries)]
    else:
        new_entry = RemoteConfig(
            repo_path=resolved_root,
            url=url,
            branch=branch if branch is not None else _DEFAULT_BRANCH,
            shared_path=shared_path if shared_path is not None else _DEFAULT_SHARED_PATH,
            remote_name=remote_name if remote_name is not None else _DEFAULT_REMOTE_NAME,
        )
        entries = [*entries, new_entry]

    _atomic_write(resolved_path, _serialize_config(entries))
    return new_entry


def remove_remote(root: Path, path: Path | None = None) -> bool:
    """Remove the config entry for *root*.

    Args:
        root: Local repository root to remove.  Resolved before lookup.
        path: Config file path.  Defaults to ``config_path()``.

    Returns:
        ``True`` if an entry was found and removed, ``False`` otherwise.
    """
    resolved_path = path if path is not None else config_path()
    resolved_root = root.resolve(strict=False)

    entries = load_config(resolved_path)
    filtered = [e for e in entries if e.repo_path.resolve(strict=False) != resolved_root]

    if len(filtered) == len(entries):
        return False

    _atomic_write(resolved_path, _serialize_config(filtered))
    return True
