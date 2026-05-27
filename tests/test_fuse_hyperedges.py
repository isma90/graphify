"""graphify fuse — hyperedge rewrite tests (Stage 3 Sprint 3 — S3.A).

Covers the critical hyperedge-rewrite case from /autoplan Eng Decision #5:
after alias→canonical contraction, hyperedge member IDs must be rewritten
through the final fixed-point mapping before contractions happen.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Shared helpers (mirror test_fuse.py pattern)
# ---------------------------------------------------------------------------

def _make_repo_with_graph(tmp_path, nodes, edges, hyperedges=None):
    """Create a tmp git repo with graph.json including optional hyperedges."""
    repo = tmp_path
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(repo), check=True)
    out = repo / "graphify-out"
    out.mkdir(exist_ok=True)
    graph_block: dict = {}
    if hyperedges is not None:
        graph_block["hyperedges"] = hyperedges
    graph_data = {
        "directed": False,
        "multigraph": False,
        "graph": graph_block,
        "nodes": nodes,
        "links": edges,
    }
    (out / "graph.json").write_text(json.dumps(graph_data))
    subprocess.run(["git", "add", "graphify-out/graph.json"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "initial graph"], cwd=str(repo), check=True)
    return repo


def _make_mock_llm_module(tmp_path, response_json: str) -> Path:
    patch_dir = tmp_path / "_llm_patch"
    patch_dir.mkdir(exist_ok=True)
    (patch_dir / "sitecustomize.py").write_text(
        f"""
import graphify.llm as _llm_mod
_RESP = {response_json!r}
def _patched_call_llm(prompt, *, backend, max_tokens=200):
    return _RESP
_llm_mod._call_llm = _patched_call_llm
"""
    )
    return patch_dir


def _run_fuse_with_mock_llm(tmp_path, repo, mock_response_json: str, *extra_args):
    patch_dir = _make_mock_llm_module(tmp_path, mock_response_json)
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("MOONSHOT_API_KEY", None)
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    env["GRAPHIFY_BACKEND"] = "kimi"
    env["MOONSHOT_API_KEY"] = "fake-test-key"
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(patch_dir) + (os.pathsep + existing_pp if existing_pp else "")
    return subprocess.run(
        [sys.executable, "-m", "graphify", "fuse", *extra_args, str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _hub_edges(hub_id: str, targets: list[str]) -> list[dict]:
    """Return INFERRED edges from hub to each target (gives hub degree > 3)."""
    return [
        {
            "source": hub_id,
            "target": t,
            "relation": "calls",
            "confidence": "INFERRED",
            "weight": 0.6,
            "source_file": "x.py",
        }
        for t in targets
    ]


# ---------------------------------------------------------------------------
# Test 1 — hyperedge member IDs are rewritten after fusion
# ---------------------------------------------------------------------------

def test_fuse_rewrites_hyperedge_member_ids(tmp_path):
    """Graph has hyperedge {"nodes": ["a","b","c"]}; b→b_canonical; after fuse member becomes b_canonical."""
    targets = ["x1", "x2", "x3", "x4"]
    nodes = [
        {"id": "a", "label": "A", "file_type": "code", "source_file": "a.py"},
        {"id": "b", "label": "B", "file_type": "code", "source_file": "b.py"},
        {"id": "b_canonical", "label": "B Canonical", "file_type": "code", "source_file": "bc.py"},
        {"id": "c", "label": "C", "file_type": "code", "source_file": "c.py"},
    ] + [
        {"id": t, "label": t.upper(), "file_type": "code", "source_file": f"{t}.py"}
        for t in targets
    ]
    # Give b and b_canonical degree > 3 so they're candidates
    edges = _hub_edges("b", targets) + _hub_edges("b_canonical", targets)
    hyperedges = [{"nodes": ["a", "b", "c"]}]
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges, hyperedges=hyperedges)

    # LLM proposes b → b_canonical
    mock_response = json.dumps({
        "clusters": [
            {
                "canonical_id": "b_canonical",
                "canonical_label": "B Canonical",
                "alias_ids": ["b"],
            }
        ]
    })
    r = _run_fuse_with_mock_llm(tmp_path, repo, mock_response, "--max-fusions=10")
    assert r.returncode == 0, r.stderr

    raw = json.loads((repo / "graphify-out" / "graph.json").read_text())
    # Retrieve hyperedges from graph metadata
    graph_meta = raw.get("graph", {})
    he_list = graph_meta.get("hyperedges", [])
    assert len(he_list) == 1, f"Expected 1 hyperedge, got {he_list}"
    members = he_list[0].get("nodes") or he_list[0].get("members") or []
    assert "b" not in members, f"Stale 'b' still in hyperedge members: {members}"
    assert "b_canonical" in members, f"'b_canonical' missing from hyperedge members: {members}"
    assert "a" in members
    assert "c" in members


# ---------------------------------------------------------------------------
# Test 2 — multiple aliases in same hyperedge dedup to canonical once
# ---------------------------------------------------------------------------

def test_fuse_dedups_hyperedge_members_when_multiple_aliases_in_same_he(tmp_path):
    """Hyperedge has both b and b_alias_2 both pointing to b_canonical; result has b_canonical ONCE."""
    targets = ["x1", "x2", "x3", "x4"]
    nodes = [
        {"id": "b", "label": "B", "file_type": "code", "source_file": "b.py"},
        {"id": "b_alias_2", "label": "B Alias 2", "file_type": "code", "source_file": "ba2.py"},
        {"id": "b_canonical", "label": "B Canonical", "file_type": "code", "source_file": "bc.py"},
        {"id": "other", "label": "Other", "file_type": "code", "source_file": "o.py"},
    ] + [
        {"id": t, "label": t.upper(), "file_type": "code", "source_file": f"{t}.py"}
        for t in targets
    ]
    edges = (
        _hub_edges("b", targets)
        + _hub_edges("b_alias_2", targets)
        + _hub_edges("b_canonical", targets)
    )
    # Hyperedge contains both aliases and canonical
    hyperedges = [{"nodes": ["b", "b_alias_2", "b_canonical", "other"]}]
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges, hyperedges=hyperedges)

    # LLM proposes both b and b_alias_2 → b_canonical
    mock_response = json.dumps({
        "clusters": [
            {
                "canonical_id": "b_canonical",
                "canonical_label": "B Canonical",
                "alias_ids": ["b", "b_alias_2"],
            }
        ]
    })
    r = _run_fuse_with_mock_llm(tmp_path, repo, mock_response, "--max-fusions=10")
    assert r.returncode == 0, r.stderr

    raw = json.loads((repo / "graphify-out" / "graph.json").read_text())
    graph_meta = raw.get("graph", {})
    he_list = graph_meta.get("hyperedges", [])
    assert len(he_list) == 1
    members = he_list[0].get("nodes") or he_list[0].get("members") or []

    assert "b" not in members, f"Stale 'b' in members: {members}"
    assert "b_alias_2" not in members, f"Stale 'b_alias_2' in members: {members}"
    # b_canonical appears exactly ONCE (deduplicated)
    assert members.count("b_canonical") == 1, (
        f"b_canonical should appear exactly once; members={members}"
    )
    assert "other" in members


# ---------------------------------------------------------------------------
# Test 3 — chained alias in hyperedge resolves to terminal
# ---------------------------------------------------------------------------

def test_fuse_chained_alias_in_hyperedge_resolves_to_terminal(tmp_path):
    """LLM proposes a→b and b→c. Hyperedge {"nodes": ["a", "x"]} becomes {"nodes": ["c", "x"]}."""
    targets = ["x1", "x2", "x3", "x4"]
    nodes = [
        {"id": "node_a", "label": "A", "file_type": "code", "source_file": "a.py"},
        {"id": "node_b", "label": "B", "file_type": "code", "source_file": "b.py"},
        {"id": "node_c", "label": "C", "file_type": "code", "source_file": "c.py"},
        {"id": "node_x", "label": "X", "file_type": "code", "source_file": "x.py"},
    ] + [
        {"id": t, "label": t.upper(), "file_type": "code", "source_file": f"{t}.py"}
        for t in targets
    ]
    # Give a, b, c degree > 3
    edges = (
        _hub_edges("node_a", targets)
        + _hub_edges("node_b", targets)
        + _hub_edges("node_c", targets)
    )
    hyperedges = [{"nodes": ["node_a", "node_x"]}]
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges, hyperedges=hyperedges)

    # LLM proposes a→b (in one cluster) and b→c (in another)
    mock_response = json.dumps({
        "clusters": [
            {
                "canonical_id": "node_b",
                "canonical_label": "B",
                "alias_ids": ["node_a"],
            },
            {
                "canonical_id": "node_c",
                "canonical_label": "C",
                "alias_ids": ["node_b"],
            },
        ]
    })
    r = _run_fuse_with_mock_llm(tmp_path, repo, mock_response, "--max-fusions=10")
    assert r.returncode == 0, r.stderr

    raw = json.loads((repo / "graphify-out" / "graph.json").read_text())
    graph_meta = raw.get("graph", {})
    he_list = graph_meta.get("hyperedges", [])
    assert len(he_list) == 1
    members = he_list[0].get("nodes") or he_list[0].get("members") or []

    # node_a must have resolved all the way to node_c (not just node_b)
    assert "node_a" not in members, f"node_a not resolved; members={members}"
    assert "node_b" not in members, f"node_b not resolved; members={members}"
    assert "node_c" in members, f"terminal node_c missing; members={members}"
    assert "node_x" in members, f"node_x should be unchanged; members={members}"


# ---------------------------------------------------------------------------
# Test 4 — both "nodes" and "members" keys are handled
# ---------------------------------------------------------------------------

def test_fuse_handles_both_nodes_and_members_keys(tmp_path):
    """Some hyperedges use 'nodes' key, others use 'members' key — both must be rewritten."""
    targets = ["x1", "x2", "x3", "x4"]
    nodes = [
        {"id": "alpha", "label": "Alpha", "file_type": "code", "source_file": "a.py"},
        {"id": "alpha_canon", "label": "Alpha Canonical", "file_type": "code", "source_file": "ac.py"},
        {"id": "beta", "label": "Beta", "file_type": "code", "source_file": "b.py"},
        {"id": "beta_canon", "label": "Beta Canonical", "file_type": "code", "source_file": "bc.py"},
        {"id": "other", "label": "Other", "file_type": "code", "source_file": "o.py"},
    ] + [
        {"id": t, "label": t.upper(), "file_type": "code", "source_file": f"{t}.py"}
        for t in targets
    ]
    edges = (
        _hub_edges("alpha", targets)
        + _hub_edges("alpha_canon", targets)
        + _hub_edges("beta", targets)
        + _hub_edges("beta_canon", targets)
    )
    # First hyperedge uses "nodes" key; second uses "members" key
    hyperedges = [
        {"nodes": ["alpha", "other"]},
        {"members": ["beta", "other"]},
    ]
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges, hyperedges=hyperedges)

    # alpha → alpha_canon, beta → beta_canon
    mock_response = json.dumps({
        "clusters": [
            {
                "canonical_id": "alpha_canon",
                "canonical_label": "Alpha Canonical",
                "alias_ids": ["alpha"],
            },
            {
                "canonical_id": "beta_canon",
                "canonical_label": "Beta Canonical",
                "alias_ids": ["beta"],
            },
        ]
    })
    r = _run_fuse_with_mock_llm(tmp_path, repo, mock_response, "--max-fusions=10")
    assert r.returncode == 0, r.stderr

    raw = json.loads((repo / "graphify-out" / "graph.json").read_text())
    graph_meta = raw.get("graph", {})
    he_list = graph_meta.get("hyperedges", [])
    assert len(he_list) == 2, f"Expected 2 hyperedges, got {he_list}"

    # Find hyperedge that originally used "nodes"
    he_nodes = next(
        (he for he in he_list if "nodes" in he),
        None,
    )
    # Find hyperedge that originally used "members"
    he_members = next(
        (he for he in he_list if "members" in he),
        None,
    )

    # Both must be rewritten
    if he_nodes is not None:
        members_n = he_nodes.get("nodes", [])
        assert "alpha" not in members_n, f"Stale 'alpha' in nodes-key HE: {members_n}"
        assert "alpha_canon" in members_n, f"'alpha_canon' missing from nodes-key HE: {members_n}"

    if he_members is not None:
        members_m = he_members.get("members", [])
        assert "beta" not in members_m, f"Stale 'beta' in members-key HE: {members_m}"
        assert "beta_canon" in members_m, f"'beta_canon' missing from members-key HE: {members_m}"
