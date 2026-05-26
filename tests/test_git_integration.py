"""Tests for graphify.git_integration — Stage 2 git plumbing helpers.

Each test is fully independent and uses its own ``tmp_path``.  Tests that
need a remote create a sibling bare repo and wire it as ``origin``.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from graphify.git_integration import (
    commit_shared_via_plumbing,
    current_shared_head,
    fetch_shared,
    install_merge_driver,
    is_merge_driver_installed,
    push_branch,
    read_shared_at_ref,
    uninstall_merge_driver,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SHARED_PATH = "graphify-out/graph-shared.json"
BRANCH = "graphify/shared"


def _git_init(tmp_path: Path) -> Path:
    """Initialise an empty git repo at *tmp_path* with a local identity.

    Returns *tmp_path* for convenience.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return tmp_path


def _make_initial_commit(root: Path, filename: str = "README.md") -> str:
    """Create a file and make an initial commit; return the commit SHA."""
    (root / filename).write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _make_bare_remote(tmp_path: Path) -> Path:
    """Create a bare repo sibling and return its path."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    return bare


def _add_remote(root: Path, bare: Path) -> None:
    """Wire *bare* as ``origin`` in *root*."""
    subprocess.run(
        ["git", "remote", "add", "origin", str(bare)],
        cwd=root,
        check=True,
    )


def _write_shared_json(root: Path, data: dict) -> Path:
    """Write *data* as JSON to the shared-graph path under *root*."""
    out = root / SHARED_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


def _git_config_get(root: Path, key: str) -> str | None:
    """Return the value of *key* from git config, or None if absent."""
    result = subprocess.run(
        ["git", "config", "--get", key],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _git_status_porcelain(root: Path) -> str:
    """Return ``git status --porcelain`` output for *root*."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


# ---------------------------------------------------------------------------
# install_merge_driver / uninstall_merge_driver / is_merge_driver_installed
# ---------------------------------------------------------------------------


def test_install_merge_driver_idempotent(tmp_path: Path) -> None:
    """Second call to install_merge_driver returns False; gitattributes has exactly one line."""
    root = _git_init(tmp_path)
    first = install_merge_driver(root, shared_path=SHARED_PATH)
    second = install_merge_driver(root, shared_path=SHARED_PATH)

    assert first is True
    assert second is False

    attrs = (root / ".gitattributes").read_text(encoding="utf-8")
    lines = [ln for ln in attrs.splitlines() if "merge=graphify-shared" in ln]
    assert len(lines) == 1


def test_install_writes_gitconfig_keys(tmp_path: Path) -> None:
    """After install, both git config keys hold the expected values."""
    root = _git_init(tmp_path)
    install_merge_driver(root, shared_path=SHARED_PATH)

    assert _git_config_get(root, "merge.graphify-shared.name") == "graphify shared-graph merge"
    assert _git_config_get(root, "merge.graphify-shared.driver") == "graphify merge-driver %O %A %B"


def test_install_creates_gitattributes_when_missing(tmp_path: Path) -> None:
    """install_merge_driver creates .gitattributes when it doesn't exist."""
    root = _git_init(tmp_path)
    assert not (root / ".gitattributes").exists()

    install_merge_driver(root, shared_path=SHARED_PATH)

    attrs_path = root / ".gitattributes"
    assert attrs_path.exists()
    assert f"{SHARED_PATH} merge=graphify-shared" in attrs_path.read_text(encoding="utf-8")


def test_install_appends_to_existing_gitattributes(tmp_path: Path) -> None:
    """install_merge_driver preserves pre-existing gitattributes content."""
    root = _git_init(tmp_path)
    existing_line = "*.png binary\n"
    (root / ".gitattributes").write_text(existing_line, encoding="utf-8")

    install_merge_driver(root, shared_path=SHARED_PATH)

    content = (root / ".gitattributes").read_text(encoding="utf-8")
    assert "*.png binary" in content
    assert f"{SHARED_PATH} merge=graphify-shared" in content


def test_uninstall_idempotent(tmp_path: Path) -> None:
    """uninstall on clean repo → False; install then uninstall → True; second uninstall → False."""
    root = _git_init(tmp_path)

    assert uninstall_merge_driver(root, shared_path=SHARED_PATH) is False

    install_merge_driver(root, shared_path=SHARED_PATH)
    assert uninstall_merge_driver(root, shared_path=SHARED_PATH) is True
    assert uninstall_merge_driver(root, shared_path=SHARED_PATH) is False


def test_is_merge_driver_installed_false_when_clean(tmp_path: Path) -> None:
    """Fresh repo with no install → is_merge_driver_installed returns False."""
    root = _git_init(tmp_path)
    assert is_merge_driver_installed(root) is False


def test_is_merge_driver_installed_true_after_install(tmp_path: Path) -> None:
    """After install → is_merge_driver_installed returns True."""
    root = _git_init(tmp_path)
    install_merge_driver(root, shared_path=SHARED_PATH)
    assert is_merge_driver_installed(root) is True


# ---------------------------------------------------------------------------
# current_shared_head
# ---------------------------------------------------------------------------


def test_current_shared_head_none_when_branch_missing(tmp_path: Path) -> None:
    """current_shared_head returns None when the branch doesn't exist."""
    root = _git_init(tmp_path)
    _make_initial_commit(root)

    result = current_shared_head(root, branch=BRANCH)
    assert result is None


def test_current_shared_head_returns_sha(tmp_path: Path) -> None:
    """current_shared_head returns the correct SHA for an existing branch."""
    root = _git_init(tmp_path)
    head_sha = _make_initial_commit(root)

    # Manually create the branch pointing at HEAD.
    subprocess.run(["git", "branch", BRANCH], cwd=root, check=True)

    result = current_shared_head(root, branch=BRANCH)
    assert result == head_sha


# ---------------------------------------------------------------------------
# commit_shared_via_plumbing
# ---------------------------------------------------------------------------


def test_commit_shared_via_plumbing_no_parent(tmp_path: Path) -> None:
    """Plumbing commit with no parent creates a valid commit on the branch."""
    root = _git_init(tmp_path)
    _make_initial_commit(root)  # need at least one commit for git config to work

    data = {"nodes": [{"id": "A"}], "links": []}
    _write_shared_json(root, data)

    status_before = _git_status_porcelain(root)

    sha = commit_shared_via_plumbing(
        root,
        branch=BRANCH,
        shared_path=SHARED_PATH,
        message="test: initial shared",
        parent_sha=None,
    )

    assert sha and len(sha) == 40

    # Branch must point to the new commit.
    ref_result = subprocess.run(
        ["git", "rev-parse", f"refs/heads/{BRANCH}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert ref_result.stdout.strip() == sha

    # The file content must be readable from that commit.
    show_result = subprocess.run(
        ["git", "show", f"{sha}:{SHARED_PATH}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(show_result.stdout) == data

    # Working tree must be byte-identical before and after.
    status_after = _git_status_porcelain(root)
    assert status_after == status_before


def test_commit_shared_via_plumbing_with_parent(tmp_path: Path) -> None:
    """Two chained plumbing commits produce a two-commit history on the branch."""
    root = _git_init(tmp_path)
    _make_initial_commit(root)

    _write_shared_json(root, {"nodes": [{"id": "A"}], "links": []})
    sha1 = commit_shared_via_plumbing(
        root,
        branch=BRANCH,
        shared_path=SHARED_PATH,
        message="commit 1",
        parent_sha=None,
    )

    _write_shared_json(root, {"nodes": [{"id": "A"}, {"id": "B"}], "links": []})
    sha2 = commit_shared_via_plumbing(
        root,
        branch=BRANCH,
        shared_path=SHARED_PATH,
        message="commit 2",
        parent_sha=sha1,
    )

    # git log should show both commits.
    log_result = subprocess.run(
        ["git", "log", "--format=%H", f"refs/heads/{BRANCH}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    shas = [s.strip() for s in log_result.stdout.strip().splitlines() if s.strip()]
    assert len(shas) == 2
    assert shas[0] == sha2
    assert shas[1] == sha1


def test_commit_shared_does_not_touch_user_index(tmp_path: Path) -> None:
    """Plumbing commit must not affect files outside shared_path."""
    root = _git_init(tmp_path)
    _make_initial_commit(root)

    # Create an unstaged modification to a different file.
    other_file = root / "other.py"
    other_file.write_text("# unstaged change\n", encoding="utf-8")
    mutation_content = "# modified but not staged\n"
    other_file.write_text(mutation_content, encoding="utf-8")

    _write_shared_json(root, {"nodes": [], "links": []})

    commit_shared_via_plumbing(
        root,
        branch=BRANCH,
        shared_path=SHARED_PATH,
        message="should not touch index",
        parent_sha=None,
    )

    # The other file must still have the unstaged mutation.
    assert other_file.read_text(encoding="utf-8") == mutation_content

    # It must still appear as untracked/modified in git status.
    status = _git_status_porcelain(root)
    assert "other.py" in status


# ---------------------------------------------------------------------------
# fetch_shared
# ---------------------------------------------------------------------------


def test_fetch_shared_returns_none_when_remote_branch_absent(tmp_path: Path) -> None:
    """fetch_shared returns None when the remote branch doesn't exist."""
    root = _git_init(tmp_path)
    _make_initial_commit(root)
    bare = _make_bare_remote(tmp_path)
    _add_remote(root, bare)

    result = fetch_shared(root, branch=BRANCH)
    assert result is None


def test_fetch_shared_returns_sha_when_present(tmp_path: Path) -> None:
    """fetch_shared returns the correct SHA after the branch exists on the remote."""
    # Set up the "upstream" clone that pushes the branch.
    upstream_dir = tmp_path / "upstream"
    upstream_dir.mkdir()
    _git_init(upstream_dir)
    _make_initial_commit(upstream_dir)
    bare = _make_bare_remote(tmp_path)
    _add_remote(upstream_dir, bare)

    # Push main so the remote has at least one ref.
    subprocess.run(
        ["git", "push", "-q", "origin", "HEAD:main"],
        cwd=upstream_dir,
        check=True,
    )

    # Create and push the shared branch from upstream.
    _write_shared_json(upstream_dir, {"nodes": [{"id": "X"}], "links": []})
    expected_sha = commit_shared_via_plumbing(
        upstream_dir,
        branch=BRANCH,
        shared_path=SHARED_PATH,
        message="initial shared",
        parent_sha=None,
    )
    subprocess.run(
        ["git", "push", "-q", "origin", BRANCH],
        cwd=upstream_dir,
        check=True,
    )

    # Fresh clone that will fetch.
    consumer_dir = tmp_path / "consumer"
    consumer_dir.mkdir()
    _git_init(consumer_dir)
    _make_initial_commit(consumer_dir)
    _add_remote(consumer_dir, bare)

    fetched_sha = fetch_shared(consumer_dir, branch=BRANCH)
    assert fetched_sha == expected_sha


# ---------------------------------------------------------------------------
# read_shared_at_ref
# ---------------------------------------------------------------------------


def test_read_shared_at_ref_returns_dict(tmp_path: Path) -> None:
    """read_shared_at_ref parses and returns the JSON at the given ref."""
    root = _git_init(tmp_path)
    _make_initial_commit(root)

    data = {"nodes": [{"id": "N1"}], "links": []}
    _write_shared_json(root, data)
    sha = commit_shared_via_plumbing(
        root,
        branch=BRANCH,
        shared_path=SHARED_PATH,
        message="add shared",
        parent_sha=None,
    )

    result = read_shared_at_ref(root, sha, shared_path=SHARED_PATH)
    assert result == data


def test_read_shared_at_ref_returns_none_when_missing(tmp_path: Path) -> None:
    """read_shared_at_ref returns None when the path doesn't exist at the ref."""
    root = _git_init(tmp_path)
    commit_sha = _make_initial_commit(root)

    # The initial commit has only README.md; shared_path is absent.
    result = read_shared_at_ref(root, commit_sha, shared_path=SHARED_PATH)
    assert result is None


# ---------------------------------------------------------------------------
# push_branch
# ---------------------------------------------------------------------------


def test_push_branch_to_bare_remote(tmp_path: Path) -> None:
    """push_branch pushes the local branch to the bare remote successfully."""
    root = _git_init(tmp_path)
    _make_initial_commit(root)
    bare = _make_bare_remote(tmp_path)
    _add_remote(root, bare)

    # Push main first so the remote is not completely empty.
    subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=root, check=True)

    _write_shared_json(root, {"nodes": [{"id": "P1"}], "links": []})
    local_sha = commit_shared_via_plumbing(
        root,
        branch=BRANCH,
        shared_path=SHARED_PATH,
        message="push test",
        parent_sha=None,
    )

    push_branch(root, branch=BRANCH)

    # Verify the bare repo has the branch pointing to the same SHA.
    bare_result = subprocess.run(
        ["git", "rev-parse", BRANCH],
        cwd=bare,
        capture_output=True,
        text=True,
        check=True,
    )
    assert bare_result.stdout.strip() == local_sha
