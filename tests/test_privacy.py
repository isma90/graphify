"""Tests for graphify.privacy — path classification, overlay inheritance,
legacy-mode detection, and git-author resolution.

The module under test is created in parallel by another agent; these tests
are written against the documented Stage-1 spec.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from graphify.privacy import (
    classify_paths,
    is_legacy_mode,
    load_overlays,
    resolve_author,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(
    tmp_path: Path,
    *,
    with_git: bool = True,
    shared_content: str | None = None,
    private_content: str | None = None,
) -> Path:
    """Set up a temporary repo with optional overlays and an optional .git dir.

    Returns the repo root (a subdirectory of tmp_path) with:
      * an empty .git/ marker directory (when with_git=True)
      * .graphifyshared (when shared_content is not None)
      * .graphifyprivate (when private_content is not None)
    """
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    if with_git:
        (root / ".git").mkdir(exist_ok=True)
    if shared_content is not None:
        (root / ".graphifyshared").write_text(shared_content, encoding="utf-8")
    if private_content is not None:
        (root / ".graphifyprivate").write_text(private_content, encoding="utf-8")
    return root


def _touch(path: Path) -> Path:
    """Create the parent directories and an empty file at *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def _git_init_with_commit(root: Path, file_rel: str = "tracked.py") -> Path:
    """Initialise a real git repo at *root* with a single committed file.

    Returns the absolute path of the committed file.
    """
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(root)],
        check=True,
        capture_output=True,
    )
    # Configure local identity so commits do not require global config.
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "tester@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test Author"],
        check=True,
        capture_output=True,
    )
    tracked = root / file_rel
    _touch(tracked)
    subprocess.run(
        ["git", "-C", str(root), "add", file_rel],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "initial"],
        check=True,
        capture_output=True,
        env={"GIT_COMMITTER_NAME": "Test Author",
             "GIT_COMMITTER_EMAIL": "tester@example.com",
             "GIT_AUTHOR_NAME": "Test Author",
             "GIT_AUTHOR_EMAIL": "tester@example.com",
             "PATH": _safe_path_env()},
    )
    return tracked


def _safe_path_env() -> str:
    """Return the current PATH so git's subcommands can locate helpers."""
    import os
    return os.environ.get("PATH", "/usr/bin:/bin")


# ---------------------------------------------------------------------------
# classify_paths
# ---------------------------------------------------------------------------


def test_no_overlays_returns_all_private(tmp_path):
    """Without .graphifyshared / .graphifyprivate, every path is private."""
    root = _make_repo(tmp_path)
    files = [_touch(root / "a.py"), _touch(root / "src" / "b.py")]
    result = classify_paths(files, root)
    assert all(v == "private" for v in result.values())
    assert len(result) == 2


def test_shared_only_matches_pattern(tmp_path):
    """Paths matching .graphifyshared are 'shared'; non-matches are 'private'."""
    root = _make_repo(tmp_path, shared_content="src/**\n")
    in_src = _touch(root / "src" / "foo.py")
    outside = _touch(root / "docs" / "guide.md")
    result = classify_paths([in_src, outside], root)
    assert result[str(in_src)] == "shared"
    assert result[str(outside)] == "private"


def test_private_wins_when_both_match(tmp_path):
    """Path matching both overlays must end up 'private' (private wins)."""
    root = _make_repo(
        tmp_path,
        shared_content="src/**\n",
        private_content="src/**\n",
    )
    target = _touch(root / "src" / "thing.py")
    result = classify_paths([target], root)
    assert result[str(target)] == "private"


def test_negation_works_in_shared(tmp_path):
    """!pattern inside .graphifyshared excludes paths that would otherwise share.

    Uses flat file patterns to avoid the gitignore ancestor-exclusion rule
    (an ancestor positively matched cannot be re-included by a leaf negation).
    """
    root = _make_repo(
        tmp_path,
        shared_content="*.py\n!secret_y.py\n",
    )
    public = _touch(root / "foo_x.py")
    secret = _touch(root / "secret_y.py")
    result = classify_paths([public, secret], root)
    assert result[str(public)] == "shared"
    assert result[str(secret)] == "private"


def test_negation_works_in_private(tmp_path):
    """!pattern inside .graphifyprivate excludes paths from the private overlay.

    Same ancestor-exclusion caveat as test_negation_works_in_shared.
    """
    root = _make_repo(
        tmp_path,
        shared_content="*.py\n",
        private_content="*.py\n!public_x.py\n",
    )
    # public_x.py is excluded from private overlay → only shared matches.
    public = _touch(root / "public_x.py")
    # internal_y.py matches both shared and private → private wins.
    internal = _touch(root / "internal_y.py")
    result = classify_paths([public, internal], root)
    assert result[str(public)] == "shared"
    assert result[str(internal)] == "private"


def test_inheritance_from_vcs_root(tmp_path):
    """An overlay at the VCS root applies to subdirectory scans (with .git/)."""
    vcs_root = tmp_path / "repo"
    vcs_root.mkdir()
    (vcs_root / ".git").mkdir()
    (vcs_root / ".graphifyshared").write_text("src/**\n", encoding="utf-8")
    subdir = vcs_root / "subdir"
    subdir.mkdir()
    target = _touch(vcs_root / "subdir" / "src" / "x.py")
    result = classify_paths([target], subdir)
    assert result[str(target)] == "shared"


def test_no_inheritance_outside_vcs(tmp_path):
    """Without .git in the ancestry, parent-overlay does not leak in."""
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / ".graphifyshared").write_text("src/**\n", encoding="utf-8")
    subdir = parent / "subdir"
    subdir.mkdir()
    # No .git anywhere → subdir is hermetic, overlay does not apply.
    target = _touch(subdir / "src" / "x.py")
    result = classify_paths([target], subdir)
    # Spec rule 1: missing .graphifyshared in the (hermetic) chain → private.
    assert result[str(target)] == "private"


def test_is_legacy_mode_true_when_no_overlays(tmp_path):
    """is_legacy_mode is True when neither overlay file exists."""
    root = _make_repo(tmp_path)
    assert is_legacy_mode(root) is True


def test_is_legacy_mode_false_when_shared_exists(tmp_path):
    """is_legacy_mode is False as soon as .graphifyshared is present."""
    root = _make_repo(tmp_path, shared_content="src/**\n")
    assert is_legacy_mode(root) is False


def test_load_overlays_returns_empty_when_files_missing(tmp_path):
    """load_overlays returns {private: [], shared: []} when no overlays exist."""
    root = _make_repo(tmp_path)
    overlays = load_overlays(root)
    assert set(overlays.keys()) == {"private", "shared"}
    assert overlays["private"] == []
    assert overlays["shared"] == []


def test_load_overlays_parses_patterns(tmp_path):
    """load_overlays returns (pattern, base_dir) tuples from the overlay files."""
    root = _make_repo(
        tmp_path,
        shared_content="src/**\n# comment ignored\n!src/secret/**\n",
        private_content="secrets/**\n",
    )
    overlays = load_overlays(root)
    shared_patterns = [p for p, _ in overlays["shared"]]
    private_patterns = [p for p, _ in overlays["private"]]
    assert "src/**" in shared_patterns
    assert "!src/secret/**" in shared_patterns
    assert "secrets/**" in private_patterns
    # Comments should be filtered out by _parse_gitignore_line.
    assert not any(p.startswith("#") for p in shared_patterns)
    # base_dir values should be Path instances pointing at root (or an ancestor).
    for _, base in overlays["shared"]:
        assert isinstance(base, Path)


def test_classify_handles_absolute_paths(tmp_path):
    """Absolute Path inputs are normalised correctly against root."""
    root = _make_repo(tmp_path, shared_content="src/**\n")
    target = _touch(root / "src" / "main.py").resolve()
    assert target.is_absolute()
    result = classify_paths([target], root)
    assert result[str(target)] == "shared"


def test_resolve_author_returns_none_when_git_unavailable(tmp_path, monkeypatch):
    """When git is not on PATH, resolve_author must return None (no raise)."""
    # Initialise a real repo first so _find_vcs_root has something to find.
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    target = _touch(root / "file.py")
    # Force the subprocess.run call to fail as if git was not in PATH.
    import graphify.privacy as priv

    def _raise_filenotfound(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(priv.subprocess, "run", _raise_filenotfound)
    # Clear the lru_cache to avoid pollution from earlier tests.
    priv._cached_git_log_author.cache_clear()
    result = resolve_author(target, vcs_root=root)
    assert result is None


def test_resolve_author_returns_none_for_untracked_file(tmp_path):
    """A file that is not committed has no git author → None."""
    root = tmp_path / "repo"
    root.mkdir()
    _git_init_with_commit(root, file_rel="tracked.py")
    untracked = _touch(root / "untracked.py")
    # Clear cache so a previous (vcs_root, untracked) None entry from another
    # test does not mask a real lookup.
    import graphify.privacy as priv
    priv._cached_git_log_author.cache_clear()
    result = resolve_author(untracked, vcs_root=root)
    assert result is None


def test_resolve_author_returns_email_and_name_for_tracked_file(tmp_path):
    """A committed file yields {"email": ..., "display_name": ...}."""
    # Skip cleanly when git is unavailable in this environment.
    import shutil
    if shutil.which("git") is None:  # pragma: no cover — environment guard
        pytest.skip("git binary not available")

    root = tmp_path / "repo"
    root.mkdir()
    tracked = _git_init_with_commit(root, file_rel="tracked.py")

    import graphify.privacy as priv
    priv._cached_git_log_author.cache_clear()
    result = resolve_author(tracked, vcs_root=root)
    assert isinstance(result, dict)
    assert result.get("email") == "tester@example.com"
    assert result.get("display_name") == "Test Author"


# ---------------------------------------------------------------------------
# D2 — is_legacy_mode with config.toml remote (Stage 2)
# ---------------------------------------------------------------------------


def test_is_legacy_mode_false_when_config_has_remote(tmp_path, monkeypatch):
    """is_legacy_mode is False when a remote is registered in config.toml,
    even with no overlay files."""
    import graphify.config as cfg

    cfg_file = tmp_path / "cfg.toml"
    monkeypatch.setenv("GRAPHIFY_CONFIG", str(cfg_file))

    root = _make_repo(tmp_path, with_git=True)
    # No overlays — legacy mode would normally be True here.
    assert not (root / ".graphifyshared").exists()
    assert not (root / ".graphifyprivate").exists()

    cfg.upsert_remote(root, "git@example.com:foo/bar.git", path=cfg_file)

    assert is_legacy_mode(root) is False


def test_is_legacy_mode_true_when_other_repo_in_config(tmp_path, monkeypatch):
    """Config has entry for repo B only: A is legacy-mode, B is not."""
    import graphify.config as cfg

    cfg_file = tmp_path / "cfg.toml"
    monkeypatch.setenv("GRAPHIFY_CONFIG", str(cfg_file))

    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    (repo_a / ".git").mkdir()

    repo_b = tmp_path / "repo_b"
    repo_b.mkdir()
    (repo_b / ".git").mkdir()

    # Register only repo B.
    cfg.upsert_remote(repo_b, "git@example.com:org/b.git", path=cfg_file)

    assert is_legacy_mode(repo_a) is True
    assert is_legacy_mode(repo_b) is False


def test_is_legacy_mode_false_when_overlay_and_config_both_present(tmp_path, monkeypatch):
    """Sanity: both a .graphifyshared overlay and a config remote → not legacy."""
    import graphify.config as cfg

    cfg_file = tmp_path / "cfg.toml"
    monkeypatch.setenv("GRAPHIFY_CONFIG", str(cfg_file))

    root = _make_repo(tmp_path, with_git=True, shared_content="src/**\n")
    cfg.upsert_remote(root, "git@example.com:org/repo.git", path=cfg_file)

    assert is_legacy_mode(root) is False


def test_is_legacy_mode_true_when_no_overlay_no_config(tmp_path, monkeypatch):
    """Baseline (unchanged from Stage 1): no overlay + no config → legacy."""
    cfg_file = tmp_path / "cfg.toml"
    monkeypatch.setenv("GRAPHIFY_CONFIG", str(cfg_file))

    root = _make_repo(tmp_path, with_git=True)
    # cfg_file intentionally not written — no remote configured.

    assert is_legacy_mode(root) is True
