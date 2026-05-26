"""Tests for graphify.config — TOML config reader/writer for remote entries.

Uses pytest's ``tmp_path`` fixture and ``monkeypatch.setenv`` to isolate each
test from the real ``~/.graphify/config.toml``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from graphify.config import (
    RemoteConfig,
    config_path,
    find_remote,
    load_config,
    remove_remote,
    upsert_remote,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(tmp_path: Path) -> Path:
    """Return a deterministic config path inside *tmp_path*."""
    return tmp_path / "config.toml"


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    """A config file that doesn't exist yields an empty list."""
    p = _config(tmp_path)
    assert not p.exists()
    assert load_config(p) == []


def test_load_empty_file_returns_empty(tmp_path: Path) -> None:
    """A zero-byte (or whitespace-only) file yields an empty list."""
    p = _config(tmp_path)
    p.write_text("", encoding="utf-8")
    assert load_config(p) == []

    p.write_text("   \n\n  ", encoding="utf-8")
    assert load_config(p) == []


def test_malformed_toml_raises_valueerror(tmp_path: Path) -> None:
    """Syntactically invalid TOML raises ValueError with a helpful prefix."""
    p = _config(tmp_path)
    p.write_text("[[remote\nbroken", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed config.toml"):
        load_config(p)


def test_missing_required_field_raises(tmp_path: Path) -> None:
    """A [[remote]] entry without repo_path raises ValueError."""
    p = _config(tmp_path)
    p.write_text('[[remote]]\nurl = "x"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="malformed config.toml"):
        load_config(p)


# ---------------------------------------------------------------------------
# upsert_remote / find_remote
# ---------------------------------------------------------------------------


def test_upsert_creates_file_and_entry(tmp_path: Path) -> None:
    """upsert_remote creates parent dirs, file, and a recoverable entry."""
    cfg = tmp_path / "sub" / "config.toml"
    root = tmp_path / "myrepo"
    root.mkdir()

    entry = upsert_remote(root, "git@github.com:org/repo.git", path=cfg)

    assert cfg.exists()
    assert entry.repo_path == root.resolve(strict=False)
    assert entry.url == "git@github.com:org/repo.git"
    assert entry.branch == "graphify/shared"
    assert entry.shared_path == "graphify-out/graph-shared.json"
    assert entry.remote_name == "origin"

    found = find_remote(root, cfg)
    assert found == entry


def test_upsert_replaces_existing(tmp_path: Path) -> None:
    """Upserting the same root twice results in exactly one entry with the latest url."""
    cfg = _config(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()

    upsert_remote(root, "git@github.com:org/first.git", path=cfg)
    upsert_remote(root, "git@github.com:org/second.git", path=cfg)

    entries = load_config(cfg)
    assert len(entries) == 1
    assert entries[0].url == "git@github.com:org/second.git"


def test_upsert_preserves_other_repos(tmp_path: Path) -> None:
    """Upserting root B does not remove root A's entry."""
    cfg = _config(tmp_path)
    root_a = tmp_path / "repo_a"
    root_b = tmp_path / "repo_b"
    root_a.mkdir()
    root_b.mkdir()

    upsert_remote(root_a, "url_a", path=cfg)
    upsert_remote(root_b, "url_b", path=cfg)

    assert find_remote(root_a, cfg) is not None
    assert find_remote(root_b, cfg) is not None
    assert len(load_config(cfg)) == 2


def test_upsert_partial_update_preserves_fields(tmp_path: Path) -> None:
    """When only url is supplied on a second upsert, branch/shared_path/remote_name are kept."""
    cfg = _config(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()

    upsert_remote(
        root,
        "url_v1",
        branch="custom/branch",
        shared_path="out/custom.json",
        remote_name="upstream",
        path=cfg,
    )

    # Second call with only url; all other kwargs default to None → preserve
    upsert_remote(root, "url_v2", path=cfg)

    entry = find_remote(root, cfg)
    assert entry is not None
    assert entry.url == "url_v2"
    assert entry.branch == "custom/branch"
    assert entry.shared_path == "out/custom.json"
    assert entry.remote_name == "upstream"


# ---------------------------------------------------------------------------
# remove_remote
# ---------------------------------------------------------------------------


def test_remove_returns_false_when_missing(tmp_path: Path) -> None:
    """remove_remote returns False when no entry exists for the given root."""
    cfg = _config(tmp_path)
    root = tmp_path / "ghost"
    assert remove_remote(root, cfg) is False


def test_remove_returns_true_and_persists(tmp_path: Path) -> None:
    """After removal the entry is gone from the file and find_remote returns None."""
    cfg = _config(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()

    upsert_remote(root, "url", path=cfg)
    assert remove_remote(root, cfg) is True
    assert find_remote(root, cfg) is None


def test_remove_does_not_touch_other_repos(tmp_path: Path) -> None:
    """Removing root A leaves root B's entry intact."""
    cfg = _config(tmp_path)
    root_a = tmp_path / "repo_a"
    root_b = tmp_path / "repo_b"
    root_a.mkdir()
    root_b.mkdir()

    upsert_remote(root_a, "url_a", path=cfg)
    upsert_remote(root_b, "url_b", path=cfg)
    remove_remote(root_a, cfg)

    assert find_remote(root_a, cfg) is None
    assert find_remote(root_b, cfg) is not None


# ---------------------------------------------------------------------------
# GRAPHIFY_CONFIG env override
# ---------------------------------------------------------------------------


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GRAPHIFY_CONFIG env var redirects config_path() and all default-path operations."""
    custom = tmp_path / "custom" / "graphify.toml"
    monkeypatch.setenv("GRAPHIFY_CONFIG", str(custom))

    assert config_path() == custom

    root = tmp_path / "repo"
    root.mkdir()
    upsert_remote(root, "url_env")

    assert custom.exists()
    found = find_remote(root)
    assert found is not None
    assert found.url == "url_env"


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_resolve_normalizes_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """./foo/../bar and bar from the same cwd refer to the same entry."""
    monkeypatch.chdir(tmp_path)
    cfg = _config(tmp_path)

    # Create the actual directory so resolve(strict=False) has something to work with
    bar = tmp_path / "bar"
    bar.mkdir()

    upsert_remote(Path("./foo/../bar"), "url", path=cfg)
    found = find_remote(Path("bar"), cfg)
    assert found is not None
    assert found.url == "url"
    # Exactly one entry should exist (same resolved path)
    assert len(load_config(cfg)) == 1


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def test_atomic_write_no_tmp_leftovers(tmp_path: Path) -> None:
    """After a successful upsert no *.tmp.* files remain in the config directory."""
    cfg = _config(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()

    upsert_remote(root, "url", path=cfg)

    leftover = list(cfg.parent.glob("*.tmp.*"))
    assert leftover == [], f"Unexpected tmp files: {leftover}"


# ---------------------------------------------------------------------------
# Round-trip with unicode paths
# ---------------------------------------------------------------------------


def test_round_trip_unicode_path(tmp_path: Path) -> None:
    """Paths with accented characters survive a write/read round-trip."""
    cfg = _config(tmp_path)
    cafe = tmp_path / "café"
    cafe.mkdir()

    upsert_remote(cafe, "url_unicode", path=cfg)
    entry = find_remote(cafe, cfg)

    assert entry is not None
    assert entry.repo_path == cafe.resolve(strict=False)
    assert entry.url == "url_unicode"


# ---------------------------------------------------------------------------
# Defaults on new entry
# ---------------------------------------------------------------------------


def test_new_entry_uses_defaults(tmp_path: Path) -> None:
    """A newly created entry uses the documented defaults for optional fields."""
    cfg = _config(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()

    entry = upsert_remote(root, None, path=cfg)

    assert entry.url is None
    assert entry.branch == "graphify/shared"
    assert entry.shared_path == "graphify-out/graph-shared.json"
    assert entry.remote_name == "origin"


# ---------------------------------------------------------------------------
# url=None omits the line in the file
# ---------------------------------------------------------------------------


def test_url_none_omitted_from_file(tmp_path: Path) -> None:
    """When url is None the 'url = ...' line must not appear in the config file."""
    cfg = _config(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()

    upsert_remote(root, None, path=cfg)
    content = cfg.read_text(encoding="utf-8")
    # Check that none of the lines start with 'url' (the TOML key)
    url_lines = [ln for ln in content.splitlines() if ln.strip().startswith("url")]
    assert url_lines == [], f"Unexpected url lines: {url_lines}"


def test_url_present_in_file(tmp_path: Path) -> None:
    """When url is set the 'url = ...' line appears in the config file."""
    cfg = _config(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()

    upsert_remote(root, "git@example.com:repo.git", path=cfg)
    content = cfg.read_text(encoding="utf-8")
    assert "url" in content
    assert "git@example.com:repo.git" in content


# ---------------------------------------------------------------------------
# File header comment
# ---------------------------------------------------------------------------


def test_config_file_has_header_comment(tmp_path: Path) -> None:
    """The written file starts with the expected header comment."""
    cfg = _config(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()

    upsert_remote(root, "url", path=cfg)
    content = cfg.read_text(encoding="utf-8")
    assert content.startswith("# graphify config")
