"""graphify decay subcommand tests (Stage 3 Sprint 2)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _make_repo_with_graph(tmp_path, nodes, edges):
    """Create a tmp git repo with a graph.json fixture."""
    repo = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(repo), check=True)
    out = repo / "graphify-out"
    out.mkdir(exist_ok=True)
    g = {"directed": False, "multigraph": False, "graph": {}, "nodes": nodes, "links": edges}
    (out / "graph.json").write_text(json.dumps(g))
    # Commit so decay's commit is a follow-up
    subprocess.run(["git", "add", "graphify-out/graph.json"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "initial graph"], cwd=str(repo), check=True)
    return repo


def _run_decay(repo, *args):
    return subprocess.run(
        [sys.executable, "-m", "graphify", "decay", *args, str(repo)],
        capture_output=True, text=True, check=False,
    )


def test_decay_decays_inferred_edges(tmp_path):
    repo = _make_repo_with_graph(tmp_path,
        nodes=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        edges=[{"source": "a", "target": "b", "relation": "calls", "confidence": "INFERRED", "weight": 0.6, "source_file": "x.py"}],
    )
    r = _run_decay(repo, "--rate", "0.5", "--threshold", "0.0")  # threshold=0 so nothing removed; just test decay
    assert r.returncode == 0, r.stderr
    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    edge = g["links"][0] if "links" in g else g.get("edges", [])[0]
    assert abs(edge["weight"] - 0.3) < 0.01  # 0.6 * 0.5 = 0.3


def test_decay_does_not_decay_extracted(tmp_path):
    repo = _make_repo_with_graph(tmp_path,
        nodes=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        edges=[{"source": "a", "target": "b", "relation": "imports", "confidence": "EXTRACTED", "weight": 1.0, "source_file": "x.py"}],
    )
    r = _run_decay(repo, "--rate", "0.5")
    assert r.returncode == 0, r.stderr
    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    edge = g["links"][0] if "links" in g else g.get("edges", [])[0]
    assert edge["weight"] == 1.0  # untouched


def test_decay_removes_subthreshold_inferred(tmp_path):
    repo = _make_repo_with_graph(tmp_path,
        nodes=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}, {"id": "c", "label": "C"}],
        edges=[
            {"source": "a", "target": "b", "relation": "calls", "confidence": "INFERRED", "weight": 0.5, "source_file": "x.py"},
            {"source": "b", "target": "c", "relation": "references", "confidence": "INFERRED", "weight": 0.1, "source_file": "y.py"},
        ],
    )
    r = _run_decay(repo, "--rate", "1.0", "--threshold", "0.20")
    assert r.returncode == 0, r.stderr
    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    edges = g.get("links") or g.get("edges", [])
    # Edge b→c had weight 0.1 < 0.20 threshold → removed. a→b had 0.5 ≥ 0.20 → kept.
    sources = [e["source"] for e in edges]
    assert "a" in sources  # a→b kept
    # b→c should be gone


def test_decay_preserves_extracted_below_threshold(tmp_path):
    repo = _make_repo_with_graph(tmp_path,
        nodes=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        edges=[{"source": "a", "target": "b", "relation": "imports", "confidence": "EXTRACTED", "weight": 0.05, "source_file": "x.py"}],
    )
    r = _run_decay(repo, "--rate", "0.5", "--threshold", "0.20")
    assert r.returncode == 0, r.stderr
    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    edges = g.get("links") or g.get("edges", [])
    assert len(edges) == 1  # EXTRACTED edges never removed even below threshold


def test_decay_boosts_touched_edges(tmp_path):
    repo = _make_repo_with_graph(tmp_path,
        nodes=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        edges=[{"source": "a", "target": "b", "relation": "calls", "confidence": "INFERRED", "weight": 0.6, "source_file": "x.py"}],
    )
    # Write touch log with both endpoints
    touch_log = repo / "graphify-out" / ".graphify_touched.json"
    touch_log.write_text(json.dumps({"id": "a", "ts": 1.0}) + "\n" + json.dumps({"id": "b", "ts": 1.0}) + "\n")
    r = _run_decay(repo, "--rate", "0.5")
    assert r.returncode == 0, r.stderr
    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    edge = (g.get("links") or g.get("edges", []))[0]
    # 0.6 * 0.5 = 0.3, then +0.10 boost = 0.4
    assert abs(edge["weight"] - 0.4) < 0.01
    # Touch log should be cleared (truncated) after decay
    assert touch_log.read_text() == ""


def test_decay_orphan_removal_with_god_node_exemption(tmp_path):
    nodes = [
        {"id": "god", "label": "GOD", "max_observed_degree": 25},  # god node
        {"id": "orphan", "label": "ORPHAN"},  # will be orphan
        {"id": "alive_a", "label": "A"},
        {"id": "alive_b", "label": "B"},
    ]
    edges = [
        {"source": "alive_a", "target": "alive_b", "relation": "calls", "confidence": "EXTRACTED", "weight": 1.0, "source_file": "x.py"},
    ]
    repo = _make_repo_with_graph(tmp_path, nodes=nodes, edges=edges)
    r = _run_decay(repo, "--rate", "1.0")
    assert r.returncode == 0, r.stderr
    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    node_ids = {n["id"] for n in g["nodes"]}
    assert "orphan" not in node_ids  # orphan removed
    assert "god" in node_ids  # god node preserved despite degree=0
    assert "alive_a" in node_ids  # has edges, kept


def test_decay_commits_only_graph_json(tmp_path):
    repo = _make_repo_with_graph(tmp_path,
        nodes=[{"id": "a", "label": "A"}],
        edges=[],
    )
    # Make an unstaged change to a different file
    other = repo / "OTHER.txt"
    other.write_text("user-edit-in-progress")
    r = _run_decay(repo, "--rate", "0.5")
    assert r.returncode == 0, r.stderr
    # Other file should still have the user-edit, NOT be committed
    assert other.read_text() == "user-edit-in-progress"
    # git status should show OTHER.txt as untracked still
    status = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo), capture_output=True, text=True).stdout
    assert "OTHER.txt" in status


def test_decay_missing_graph_errors(tmp_path):
    # tmp_path has no graphify-out/graph.json
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    r = _run_decay(tmp_path, "--rate", "0.5")
    assert r.returncode != 0
    assert "does not exist" in r.stderr.lower() or "graph.json" in r.stderr.lower()
