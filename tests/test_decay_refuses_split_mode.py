"""graphify decay refuses split-mode repos without --force-split-mode-unsafe."""
import json
import subprocess
import sys
from pathlib import Path

import pytest


def _make_split_mode_repo(tmp_path, with_overlay=True, with_config_remote=False):
    repo = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(repo), check=True)
    out = repo / "graphify-out"
    out.mkdir(exist_ok=True)
    (out / "graph.json").write_text(json.dumps({"directed": False, "multigraph": False, "graph": {}, "nodes": [], "links": []}))
    if with_overlay:
        (repo / ".graphifyshared").write_text("src/**\n")
    return repo


def test_decay_refuses_split_mode_with_overlay(tmp_path):
    repo = _make_split_mode_repo(tmp_path, with_overlay=True)
    r = subprocess.run(
        [sys.executable, "-m", "graphify", "decay", str(repo)],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "split mode" in r.stderr.lower()
    assert "force-split-mode-unsafe" in r.stderr.lower()


def test_decay_force_split_mode_unsafe_proceeds(tmp_path):
    repo = _make_split_mode_repo(tmp_path, with_overlay=True)
    r = subprocess.run(
        [sys.executable, "-m", "graphify", "decay", "--force-split-mode-unsafe", str(repo)],
        capture_output=True, text=True,
    )
    # Should proceed (return 0) and warn on stderr
    assert r.returncode == 0, r.stderr
    assert "force-split-mode-unsafe" in r.stderr.lower() or "split-mode" in r.stderr.lower()
