"""git_integration.py — Stage 2 git plumbing helpers for graphify team sync.

Pure-stdlib wrappers around the ``git`` CLI.  All public functions take a
``root: Path`` (the working-tree root) and shell out via ``subprocess.run``.
They never touch the user's working tree index (``<root>/.git/index``) or
HEAD; all index work uses a throw-away temp file via ``GIT_INDEX_FILE``.

Intended callers: ``graphify push``, ``graphify pull``, ``graphify
install-merge-driver`` (D4/D5 CLI layer).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _git(
    root: Path,
    *args: str,
    check: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in *root* and return the completed process.

    Args:
        root: Absolute path to the git working tree (resolved by callers).
        *args: Arguments forwarded to ``git``.
        check: When ``True`` (default) and the exit code is non-zero, raise
            ``RuntimeError`` with the captured stderr.
        extra_env: Optional extra environment variables merged on top of
            ``os.environ`` for this invocation (used for ``GIT_INDEX_FILE``).

    Returns:
        The ``subprocess.CompletedProcess`` instance.

    Raises:
        RuntimeError: When *check* is ``True`` and the subprocess exits
            non-zero.
    """
    env = {**os.environ, **(extra_env or {})}
    result = subprocess.run(
        ["git", *args],
        cwd=str(root),
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result


# ---------------------------------------------------------------------------
# Merge-driver lifecycle
# ---------------------------------------------------------------------------


def install_merge_driver(
    root: Path,
    *,
    shared_path: str = "graphify-out/graph-shared.json",
) -> bool:
    """Idempotently register the ``graphify-shared`` git merge driver.

    Sets two ``git config`` keys in the local repo config and appends an
    entry to ``.gitattributes`` so git invokes the driver when merging
    ``graph-shared.json``.

    Args:
        root: Absolute path to the git working-tree root.
        shared_path: Repo-relative path to the shared-graph file that the
            merge driver should govern.

    Returns:
        ``True`` when *anything* was newly written (at least one config key
        or the gitattributes line was missing); ``False`` for a fully
        idempotent no-op.
    """
    root = root.resolve()
    wrote_anything = False

    # --- git config keys ---
    _driver_name = "graphify shared-graph merge"
    _driver_cmd = "graphify merge-driver %O %A %B"

    for key, value in [
        ("merge.graphify-shared.name", _driver_name),
        ("merge.graphify-shared.driver", _driver_cmd),
    ]:
        result = _git(root, "config", "--get", key, check=False)
        if result.returncode != 0 or result.stdout.strip() != value:
            _git(root, "config", key, value)
            wrote_anything = True

    # --- .gitattributes line ---
    attrs_path = root / ".gitattributes"
    target_line = f"{shared_path} merge=graphify-shared"

    existing_lines: list[str] = []
    if attrs_path.exists():
        existing_lines = attrs_path.read_text(encoding="utf-8").splitlines(keepends=True)

    already_present = any(line.rstrip("\n\r") == target_line for line in existing_lines)
    if not already_present:
        with attrs_path.open("a", encoding="utf-8") as fh:
            # Ensure the new line starts on its own line.
            if existing_lines and not existing_lines[-1].endswith(("\n", "\r")):
                fh.write("\n")
            fh.write(target_line + "\n")
        wrote_anything = True

    return wrote_anything


def uninstall_merge_driver(
    root: Path,
    *,
    shared_path: str = "graphify-out/graph-shared.json",
) -> bool:
    """Remove the ``graphify-shared`` git merge driver registration.

    Symmetric counterpart to :func:`install_merge_driver`.  Idempotent:
    calling it on a repo that never had the driver installed returns ``False``.

    Args:
        root: Absolute path to the git working-tree root.
        shared_path: Repo-relative path used to find the gitattributes line.

    Returns:
        ``True`` when something was actually removed; ``False`` otherwise.
    """
    root = root.resolve()
    removed_anything = False

    for key in ("merge.graphify-shared.name", "merge.graphify-shared.driver"):
        # git config --unset exits non-zero when key is already absent — treat
        # that as success (idempotent).
        result = _git(root, "config", "--unset", key, check=False)
        if result.returncode == 0:
            removed_anything = True

    # --- .gitattributes line ---
    attrs_path = root / ".gitattributes"
    target_line = f"{shared_path} merge=graphify-shared"

    if attrs_path.exists():
        original = attrs_path.read_text(encoding="utf-8")
        kept = [
            line
            for line in original.splitlines(keepends=True)
            if line.rstrip("\n\r") != target_line
        ]
        if len(kept) != len(original.splitlines(keepends=True)):
            attrs_path.write_text("".join(kept), encoding="utf-8")
            removed_anything = True

    return removed_anything


def is_merge_driver_installed(root: Path) -> bool:
    """Return ``True`` iff the graphify merge driver is fully registered.

    Both the ``git config`` driver key *and* the ``.gitattributes`` line must
    be present (and the config value must be non-empty).

    Args:
        root: Absolute path to the git working-tree root.

    Returns:
        ``True`` when the driver is fully installed; ``False`` otherwise.
    """
    root = root.resolve()

    result = _git(root, "config", "--get", "merge.graphify-shared.driver", check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return False

    attrs_path = root / ".gitattributes"
    if not attrs_path.exists():
        return False

    lines = attrs_path.read_text(encoding="utf-8").splitlines()
    return any("merge=graphify-shared" in line for line in lines)


# ---------------------------------------------------------------------------
# Ref / branch helpers
# ---------------------------------------------------------------------------


def current_shared_head(
    root: Path,
    *,
    branch: str,
    remote_name: str = "origin",
) -> str | None:
    """Return the current SHA of *branch*, checking local then remote tracking.

    Args:
        root: Absolute path to the git working-tree root.
        branch: Local branch name (e.g. ``"graphify/shared"``).
        remote_name: Name of the configured remote (default ``"origin"``).

    Returns:
        The full 40-character SHA string, or ``None`` when neither the local
        branch nor the remote-tracking ref exists.
    """
    root = root.resolve()

    # Try local branch first.
    result = _git(root, "rev-parse", "--verify", "--quiet", branch, check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    # Fall back to the remote-tracking ref.
    remote_ref = f"{remote_name}/{branch}"
    result = _git(root, "rev-parse", "--verify", "--quiet", remote_ref, check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    return None


def fetch_shared(
    root: Path,
    *,
    branch: str,
    remote_name: str = "origin",
) -> str | None:
    """Fetch *branch* from *remote_name* and return the resulting SHA.

    Args:
        root: Absolute path to the git working-tree root.
        branch: Remote branch to fetch (e.g. ``"graphify/shared"``).
        remote_name: Name of the configured remote (default ``"origin"``).

    Returns:
        The SHA of the fetched tip, or ``None`` when the remote branch does
        not exist.

    Raises:
        RuntimeError: On any git failure other than "remote ref not found".
    """
    root = root.resolve()
    remote_ref = f"refs/remotes/{remote_name}/{branch}"
    result = _git(
        root,
        "fetch",
        "--quiet",
        remote_name,
        f"{branch}:{remote_ref}",
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.lower()
        if "couldn't find remote ref" in stderr:
            return None
        raise RuntimeError(f"git fetch failed: {result.stderr.strip()}")

    sha_result = _git(root, "rev-parse", remote_ref)
    return sha_result.stdout.strip()


def read_shared_at_ref(
    root: Path,
    ref: str,
    *,
    shared_path: str,
) -> dict | None:  # type: ignore[type-arg]
    """Return the parsed JSON content of *shared_path* at *ref*.

    Args:
        root: Absolute path to the git working-tree root.
        ref: Any git ref or SHA (e.g. a commit SHA or branch name).
        shared_path: Repo-relative path to the JSON file inside the tree.

    Returns:
        Parsed dict when the file exists at *ref*, ``None`` when the path is
        absent at that ref.

    Raises:
        RuntimeError: On unexpected git errors (not "file absent" errors).
        json.JSONDecodeError: When the file exists but contains invalid JSON
            (intentionally not swallowed so callers detect corruption).
    """
    root = root.resolve()
    result = _git(root, "show", f"{ref}:{shared_path}", check=False)
    if result.returncode != 0:
        stderr_lower = result.stderr.lower()
        # git returns non-zero with one of these messages when the path is
        # absent at the given ref — treat both as "not found".
        if (
            "does not exist" in stderr_lower
            or "exists on disk, but not in" in stderr_lower
            or "path not in the working tree" in stderr_lower
        ):
            return None
        raise RuntimeError(f"git show failed: {result.stderr.strip()}")
    # json.JSONDecodeError is intentionally propagated to the caller.
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Plumbing-based commit (no working-tree / HEAD mutation)
# ---------------------------------------------------------------------------


def commit_shared_via_plumbing(
    root: Path,
    *,
    branch: str,
    shared_path: str,
    message: str,
    parent_sha: str | None,
) -> str:
    """Commit *shared_path* to *branch* without touching the working tree.

    Uses ``git hash-object``, ``git update-index``, ``git write-tree``, and
    ``git commit-tree`` exclusively — no porcelain commands that would switch
    branches or modify ``<root>/.git/index``.  A temporary index file is used
    for all index operations so the user's staging area is never touched.

    Args:
        root: Absolute path to the git working-tree root.
        branch: Target branch name (e.g. ``"graphify/shared"``).  Will be
            created if it does not already exist.
        shared_path: Repo-relative path to the JSON file to commit (must
            exist on the filesystem under *root*).
        message: Commit message.
        parent_sha: SHA of the parent commit, or ``None`` for an initial
            commit (no parent).

    Returns:
        The SHA of the newly created commit.

    Raises:
        RuntimeError: On git failures, including a friendlier message when
            ``user.email`` / ``user.name`` are not configured.
    """
    root = root.resolve()

    # 1. Write the blob object.
    blob_result = _git(root, "hash-object", "-w", shared_path)
    blob_sha = blob_result.stdout.strip()

    # 2. Build a new tree using a throw-away index file.
    with tempfile.NamedTemporaryFile(suffix=".graphify_idx", delete=False) as tmp_idx:
        tmp_index_path = tmp_idx.name

    try:
        idx_env = {"GIT_INDEX_FILE": tmp_index_path}

        if parent_sha is not None:
            # Seed the temp index from the parent commit's tree so existing
            # files in that tree are preserved.
            _git(root, "read-tree", parent_sha, extra_env=idx_env)
        else:
            # Initialise a valid empty index.  An on-disk empty file is NOT a
            # valid git index (git rejects it as "smaller than expected").
            # ``git read-tree --empty`` writes the correct 12-byte header.
            _git(root, "read-tree", "--empty", extra_env=idx_env)

        # Add / overwrite the entry for shared_path in the temp index.
        _git(
            root,
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{blob_sha},{shared_path}",
            extra_env=idx_env,
        )

        # 3. Write the tree object.
        tree_result = _git(root, "write-tree", extra_env=idx_env)
        tree_sha = tree_result.stdout.strip()
    finally:
        try:
            os.unlink(tmp_index_path)
        except OSError:
            pass

    # 4. Create the commit object.
    parents_args: list[str] = []
    if parent_sha:
        parents_args = ["-p", parent_sha]

    try:
        commit_result = _git(
            root,
            "commit-tree",
            tree_sha,
            *parents_args,
            "-m",
            message,
        )
    except RuntimeError as exc:
        err_text = str(exc).lower()
        if "user.email" in err_text or "user.name" in err_text or "author" in err_text or "committer" in err_text:
            raise RuntimeError(
                "git user.email and user.name must be configured "
                "(run: git config --global user.email <you@example.com> "
                "and git config --global user.name <Your Name>)"
            ) from exc
        raise

    commit_sha = commit_result.stdout.strip()

    # 5. Atomically advance (or create) the branch ref.
    #    The third argument to update-ref is the expected old value;
    #    empty string means "create — assert it doesn't exist" when
    #    parent_sha is None, which is exactly what we want.
    _git(
        root,
        "update-ref",
        f"refs/heads/{branch}",
        commit_sha,
        parent_sha or "",
    )

    return commit_sha


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------


def push_branch(
    root: Path,
    *,
    branch: str,
    remote_name: str = "origin",
) -> None:
    """Push *branch* to *remote_name*.

    Args:
        root: Absolute path to the git working-tree root.
        branch: Local branch to push (e.g. ``"graphify/shared"``).
        remote_name: Name of the configured remote (default ``"origin"``).

    Raises:
        RuntimeError: When the push fails for any reason.
    """
    root = root.resolve()
    _git(root, "push", "--quiet", remote_name, branch)
