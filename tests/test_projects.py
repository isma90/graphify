"""Unit tests for the central knowledge-group registry (graphify/projects.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from graphify import projects


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point GRAPHIFY_HOME + GRAPHIFY_PROJECTS at a throwaway dir."""
    home = tmp_path / "graphify-home"
    home.mkdir()
    monkeypatch.setenv("GRAPHIFY_HOME", str(home))
    monkeypatch.setenv("GRAPHIFY_PROJECTS", str(home / "projects.toml"))
    return home


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Name sanitization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("agrosuper", "agrosuper"),
        ("Agro Super", "agro-super"),
        ("  Cliente/Plataforma  ", "cliente-plataforma"),
        ("db-corpus", "db-corpus"),
        ("a__b--c", "a__b-c"),  # only dash-runs collapse; underscores preserved
        ("Café_Niño", "cafe_nino"),
    ],
)
def test_sanitize_group_name_ok(raw, expected):
    assert projects.sanitize_group_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "/", "..", ".", "---", "///"])
def test_sanitize_group_name_rejects(raw):
    with pytest.raises(ValueError):
        projects.sanitize_group_name(raw)


@pytest.mark.parametrize("raw", ["../../etc/passwd", "a/../b", "..\\..\\win", "/abs/path"])
def test_sanitize_blocks_path_traversal(raw):
    # Result is always a single safe path component: no separators, never '.'/'..'.
    safe = projects.sanitize_group_name(raw)
    assert "/" not in safe and "\\" not in safe
    assert safe not in (".", "..")
    # group_root therefore stays directly under graphify_home (no escape).
    root = projects.group_root(raw)
    assert root.parent == projects.graphify_home()


# ---------------------------------------------------------------------------
# Registry round-trip
# ---------------------------------------------------------------------------


def test_upsert_and_find_roundtrip():
    g = projects.upsert_group("Agrosuper")
    assert g.name == "agrosuper"
    assert g.dir == projects.group_root("agrosuper")
    assert g.created_at  # stamped

    found = projects.find_group("AGROSUPER")  # case-insensitive via sanitize
    assert found is not None
    assert found.name == "agrosuper"
    assert projects.find_group("nope") is None


def test_add_source_dedup_and_lookup(tmp_path):
    projects.upsert_group("agrosuper")
    src = tmp_path / "repoA"
    src.mkdir()
    projects.add_source("agrosuper", src, contributor="me@x.com")
    projects.add_source("agrosuper", src, contributor="me@x.com")  # dedup
    g = projects.find_group("agrosuper")
    assert len(g.sources) == 1
    assert g.sources[0].path == src.resolve()
    assert g.sources[0].contributor == "me@x.com"
    assert projects.find_group_for_source(src).name == "agrosuper"


def test_add_source_requires_existing_group(tmp_path):
    with pytest.raises(ValueError):
        projects.add_source("ghost", tmp_path)


def test_rename_group_moves_dir():
    g = projects.upsert_group("old")
    g.dir.mkdir(parents=True, exist_ok=True)
    (g.dir / "marker.txt").write_text("x")
    renamed = projects.rename_group("old", "new")
    assert renamed.name == "new"
    assert renamed.dir == projects.group_root("new")
    assert (renamed.dir / "marker.txt").read_text() == "x"
    assert projects.find_group("old") is None
    assert projects.find_group("new") is not None


def test_rename_into_existing_rejected():
    projects.upsert_group("a")
    projects.upsert_group("b")
    with pytest.raises(ValueError):
        projects.rename_group("a", "b")


def test_remove_group():
    projects.upsert_group("temp")
    assert projects.remove_group("temp") is True
    assert projects.remove_group("temp") is False
    assert projects.find_group("temp") is None


# ---------------------------------------------------------------------------
# Remote URL handling
# ---------------------------------------------------------------------------


def test_require_remote_url_raises_when_unset():
    projects.upsert_group("agrosuper")
    assert projects.get_remote_url("agrosuper") is None
    with pytest.raises(ValueError, match="no remote configured"):
        projects.require_remote_url("agrosuper")


def test_set_remote_persists_and_registers_git_remote():
    g = projects.upsert_group("agrosuper")
    projects.git_init_group(g.dir)
    projects.set_remote("agrosuper", "git@example.com:me/agrosuper.git")
    assert projects.get_remote_url("agrosuper") == "git@example.com:me/agrosuper.git"
    assert projects.require_remote_url("agrosuper") == "git@example.com:me/agrosuper.git"
    # registered in the group's git repo
    out = subprocess.run(
        ["git", "remote", "-v"], cwd=str(g.dir), capture_output=True, text=True
    )
    assert "agrosuper.git" in out.stdout


def test_set_remote_rejects_empty():
    projects.upsert_group("agrosuper")
    with pytest.raises(ValueError):
        projects.set_remote("agrosuper", "  ")


# ---------------------------------------------------------------------------
# Git wrappers — commit/push surface errors
# ---------------------------------------------------------------------------


def test_git_init_group_idempotent():
    d = projects.group_root("agrosuper")
    projects.git_init_group(d)
    assert (d / ".git").exists()
    projects.git_init_group(d)  # no-op, no raise


def test_commit_group_success_and_noop():
    g = projects.upsert_group("agrosuper")
    projects.git_init_group(g.dir)
    _git(g.dir, "config", "user.email", "t@t.com")
    _git(g.dir, "config", "user.name", "t")
    (g.dir / "graph.json").write_text('{"nodes": []}')

    r1 = projects.commit_group(g.dir, "init", ["graph.json"])
    assert r1.ok and r1.detail not in ("nothing to commit",)

    r2 = projects.commit_group(g.dir, "again", ["graph.json"])
    assert r2.ok and r2.detail == "nothing to commit"


def test_commit_group_not_a_repo(tmp_path):
    r = projects.commit_group(tmp_path, "x", ["graph.json"])
    assert not r.ok
    assert "not a git repo" in r.detail


def test_push_group_requires_remote():
    g = projects.upsert_group("agrosuper")
    projects.git_init_group(g.dir)
    with pytest.raises(ValueError, match="no remote configured"):
        projects.push_group("agrosuper")


def test_push_group_error_is_surfaced(tmp_path):
    """A bad remote yields ok=False with git's stderr, not an exception."""
    g = projects.upsert_group("agrosuper")
    projects.git_init_group(g.dir)
    _git(g.dir, "config", "user.email", "t@t.com")
    _git(g.dir, "config", "user.name", "t")
    (g.dir / "graph.json").write_text('{"nodes": []}')
    projects.commit_group(g.dir, "init", ["graph.json"])
    # Point origin at a non-existent local path → push must fail cleanly.
    projects.set_remote("agrosuper", str(tmp_path / "does-not-exist.git"))
    r = projects.push_group("agrosuper")
    assert not r.ok
    assert r.detail  # carries git's stderr
    # local commit survives (retry-safe)
    log = subprocess.run(["git", "log", "--oneline"], cwd=str(g.dir),
                         capture_output=True, text=True)
    assert "init" in log.stdout


def test_push_group_succeeds_to_local_bare_remote(tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    g = projects.upsert_group("agrosuper")
    projects.git_init_group(g.dir)
    _git(g.dir, "config", "user.email", "t@t.com")
    _git(g.dir, "config", "user.name", "t")
    (g.dir / "graph.json").write_text('{"nodes": []}')
    projects.commit_group(g.dir, "init", ["graph.json"])
    projects.set_remote("agrosuper", str(bare))
    r = projects.push_group("agrosuper")
    assert r.ok, r.detail


# ---------------------------------------------------------------------------
# Assisted migration
# ---------------------------------------------------------------------------


def _make_repo_with_graphify_out(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    out = repo / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text('{"nodes": [{"id": "a"}]}')
    (out / "GRAPH_REPORT.md").write_text("# report")
    (out / "cache").mkdir()
    (out / "cache" / "x.json").write_text("{}")


def test_import_no_graphify_out(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    res = projects.import_repo_into_group(repo, "agrosuper")
    assert res["status"] == "no_graphify_out"


def test_import_moves_and_is_idempotent(tmp_path):
    repo = tmp_path / "repo"
    _make_repo_with_graphify_out(repo)

    res = projects.import_repo_into_group(repo, "Agrosuper")
    assert res["status"] == "imported"
    assert res["group"] == "agrosuper"
    dest = projects.group_root("agrosuper") / "graphify-out" / "graph.json"
    assert dest.exists()
    # marker left behind, source registered
    assert (repo / "graphify-out" / ".graphify_group").read_text().strip() == "agrosuper"
    assert projects.find_group("agrosuper").sources[0].path == repo.resolve()

    # second run is a no-op
    res2 = projects.import_repo_into_group(repo, "agrosuper")
    assert res2["status"] == "already_imported"


def test_import_merge_into_existing_registers_source(tmp_path):
    projects.upsert_group("agrosuper")
    repo = tmp_path / "repo2"
    _make_repo_with_graphify_out(repo)
    res = projects.import_repo_into_group(repo, "ignored", merge_into="agrosuper")
    assert res["status"] == "merged"
    # files NOT moved (merge accumulates via extract instead)
    assert (repo / "graphify-out" / "graph.json").exists()
    assert projects.find_group("agrosuper").sources[0].path == repo.resolve()
