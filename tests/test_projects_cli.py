"""End-to-end CLI tests for central knowledge groups (graphify project ...).

Uses AST-only extraction (tiny Python corpora) so no LLM backend is required.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(*args, home: Path, cwd: Path | None = None):
    env = {**os.environ}
    env["GRAPHIFY_HOME"] = str(home)
    env["GRAPHIFY_PROJECTS"] = str(home / "projects.toml")
    env["HOME"] = str(home)  # isolate hermes/skill checks
    return subprocess.run(
        [sys.executable, "-m", "graphify", *args],
        cwd=str(cwd or home),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "ghome"
    h.mkdir()
    return h


def _corpus(root: Path, prefix: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{prefix}.py").write_text(
        f"def {prefix}_one():\n    return {prefix}_two()\n"
        f"def {prefix}_two():\n    return 1\n",
        encoding="utf-8",
    )
    return root


def _node_count(home: Path, name: str) -> int:
    gj = home / name / "graphify-out" / "graph.json"
    return len(json.loads(gj.read_text(encoding="utf-8")).get("nodes", []))


# ---------------------------------------------------------------------------
# create + git repo
# ---------------------------------------------------------------------------


def test_create_makes_group_git_repo(home):
    r = _run("project", "create", "agrosuper", home=home)
    assert r.returncode == 0, r.stderr
    gdir = home / "agrosuper"
    assert (gdir / ".git").is_dir()
    assert (gdir / ".gitignore").is_file()
    # registry persisted
    reg = (home / "projects.toml").read_text()
    assert 'name        = "agrosuper"' in reg


def test_create_rejects_duplicate(home):
    _run("project", "create", "dup", home=home)
    r = _run("project", "create", "dup", home=home)
    assert r.returncode != 0
    assert "already exists" in r.stderr


def test_create_from_path_extracts(home, tmp_path):
    src = _corpus(tmp_path / "svc", "login")
    r = _run("project", "create", "demo", "--from", str(src), home=home)
    assert r.returncode == 0, r.stderr
    assert (home / "demo" / "graphify-out" / "graph.json").exists()
    assert _node_count(home, "demo") >= 1


# ---------------------------------------------------------------------------
# accumulation — one growing graph
# ---------------------------------------------------------------------------


def test_add_accumulates_into_one_graph(home, tmp_path):
    a = _corpus(tmp_path / "repoA", "alpha")
    b = _corpus(tmp_path / "repoB", "beta")
    assert _run("project", "create", "grp", home=home).returncode == 0
    assert _run("project", "add", str(a), "--group", "grp", home=home).returncode == 0
    n_after_a = _node_count(home, "grp")
    assert _run("project", "add", str(b), "--group", "grp", home=home).returncode == 0
    n_after_b = _node_count(home, "grp")
    assert n_after_b > n_after_a, "second source must grow the graph, not replace it"

    ids = [
        n["id"]
        for n in json.loads(
            (home / "grp" / "graphify-out" / "graph.json").read_text()
        )["nodes"]
    ]
    assert any("alpha" in i for i in ids) and any("beta" in i for i in ids)
    # two commits in the group repo (one per source)
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=str(home / "grp"), capture_output=True, text=True
    )
    assert log.stdout.count("\n") >= 2


def test_add_to_missing_group_errors(home, tmp_path):
    src = _corpus(tmp_path / "x", "x")
    r = _run("project", "add", str(src), "--group", "ghost", home=home)
    assert r.returncode != 0
    assert "does not exist" in r.stderr


# ---------------------------------------------------------------------------
# query against a group
# ---------------------------------------------------------------------------


def test_query_against_group(home, tmp_path):
    src = _corpus(tmp_path / "svc", "login")
    _run("project", "create", "demo", "--from", str(src), home=home)
    r = _run("query", "how does login work", "--group", "demo", home=home)
    assert r.returncode == 0, r.stderr
    assert "login" in r.stdout.lower()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_shows_groups(home, tmp_path):
    src = _corpus(tmp_path / "svc", "login")
    _run("project", "create", "demo", "--from", str(src), home=home)
    r = _run("project", "list", "--verbose", home=home)
    assert r.returncode == 0
    assert "demo" in r.stdout
    assert "sources" in r.stdout


# ---------------------------------------------------------------------------
# import (assisted migration)
# ---------------------------------------------------------------------------


def test_import_moves_and_is_idempotent(home, tmp_path):
    # First build a normal repo-local graphify-out via plain extract.
    repo = _corpus(tmp_path / "legacy", "core")
    assert _run("extract", str(repo), home=home, cwd=repo).returncode == 0
    assert (repo / "graphify-out" / "graph.json").exists()

    r = _run("project", "import", str(repo), "--group", "legacy-kb", home=home)
    assert r.returncode == 0, r.stderr
    moved = home / "legacy-kb" / "graphify-out" / "graph.json"
    assert moved.exists()
    assert (repo / "graphify-out" / ".graphify_group").read_text().strip() == "legacy-kb"
    assert (home / "legacy-kb" / ".git").is_dir()

    # second import is a no-op
    r2 = _run("project", "import", str(repo), "--group", "legacy-kb", home=home)
    assert r2.returncode == 0
    assert "already imported" in r2.stdout


# ---------------------------------------------------------------------------
# push fallback + remote
# ---------------------------------------------------------------------------


def test_push_without_remote_falls_back_to_local_commit(home, tmp_path):
    src = _corpus(tmp_path / "svc", "login")
    _run("project", "create", "demo", "--from", str(src), home=home)
    r = _run("project", "push", "demo", home=home)
    # No remote → informative, NOT a hard failure; work stays committed locally.
    assert r.returncode == 0, r.stderr
    assert "remote" in (r.stdout + r.stderr).lower()
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=str(home / "demo"), capture_output=True, text=True
    )
    assert log.stdout.strip()  # at least one commit exists


def test_push_to_local_bare_remote_succeeds(home, tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    src = _corpus(tmp_path / "svc", "login")
    _run("project", "create", "demo", "--from", str(src), home=home)
    _run("project", "remote", "demo", str(bare), home=home)
    r = _run("project", "push", "demo", home=home)
    assert r.returncode == 0, r.stderr + r.stdout


# ---------------------------------------------------------------------------
# group dir is a valid sleep-cycle brain-root (no sleep code changes needed)
# ---------------------------------------------------------------------------


def test_out_and_group_are_mutually_exclusive(home, tmp_path):
    src = _corpus(tmp_path / "svc", "login")
    r = _run("extract", str(src), "--group", "g", "--out", str(tmp_path / "o"), home=home)
    assert r.returncode == 2
    assert "mutually exclusive" in r.stderr


def test_explicit_graph_overrides_group(home, tmp_path):
    # Build a standalone graph and a separate group; --graph must win over --group.
    standalone = _corpus(tmp_path / "solo", "solo")
    _run("extract", str(standalone), home=home, cwd=standalone)
    standalone_graph = standalone / "graphify-out" / "graph.json"
    src = _corpus(tmp_path / "svc", "login")
    _run("project", "create", "demo", "--from", str(src), home=home)
    # --graph points at the standalone graph → should find 'solo', not the group's 'login'
    r = _run("query", "solo", "--group", "demo", "--graph", str(standalone_graph), home=home)
    assert r.returncode == 0, r.stderr
    assert "solo" in r.stdout.lower()


def test_project_rename_moves_dir_and_updates_registry(home, tmp_path):
    src = _corpus(tmp_path / "svc", "login")
    _run("project", "create", "old", "--from", str(src), home=home)
    r = _run("project", "rename", "old", "new", home=home)
    assert r.returncode == 0, r.stderr
    assert (home / "new" / "graphify-out" / "graph.json").exists()
    assert not (home / "old").exists()
    lst = _run("project", "list", home=home)
    assert "new" in lst.stdout and "old" not in lst.stdout.split("\n")[0]


def test_group_dir_is_valid_sleep_brain_root(tmp_path):
    # HOME must contain the brain-root; put GRAPHIFY_HOME under HOME.
    home = tmp_path
    ghome = home / ".graphify"
    ghome.mkdir()
    _run("project", "create", "kb", home=ghome)  # creates ghome/kb (git repo)
    group_dir = ghome / "kb"
    assert (group_dir / ".git").is_dir()
    r = subprocess.run(
        [sys.executable, "-m", "graphify", "sleep", "install", "--brain-root", str(group_dir)],
        cwd=str(home),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": str(home), "GRAPHIFY_HOME": str(ghome)},
    )
    assert r.returncode == 0, r.stderr
    manifest = home / ".hermes" / "state" / "graphify-sleep.manifest.json"
    assert manifest.is_file()
    assert json.loads(manifest.read_text())["brain_root"] == str(group_dir)
