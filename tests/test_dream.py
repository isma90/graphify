"""graphify dream subcommand tests (Stage 3 Sprint 3 — S3.B)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_repo_with_graph(tmp_path, nodes, edges, hyperedges=None, graph_meta=None):
    """Create a tmp git repo with a graph.json fixture."""
    repo = tmp_path
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(repo), check=True)
    out = repo / "graphify-out"
    out.mkdir(exist_ok=True)
    graph_data: dict = {
        "directed": False,
        "multigraph": False,
        "graph": {},
        "nodes": nodes,
        "links": edges,
    }
    if hyperedges is not None:
        graph_data["graph"]["hyperedges"] = hyperedges
    if graph_meta:
        graph_data["graph"].update(graph_meta)
    (out / "graph.json").write_text(json.dumps(graph_data))
    subprocess.run(["git", "add", "graphify-out/graph.json"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "initial graph"], cwd=str(repo), check=True)
    return repo


def _make_mock_llm_module(tmp_path, response_json: str) -> Path:
    """Write a sitecustomize patch that monkey-patches graphify.llm._call_llm.
    Returns the directory to prepend to PYTHONPATH."""
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


def _run_dream(repo, *args, extra_env=None):
    """Run graphify dream without an LLM mock (for tests that don't reach LLM)."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("MOONSHOT_API_KEY", None)
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "graphify", "dream", *args, str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _run_dream_with_mock_llm(tmp_path, repo, mock_response_json: str, *extra_args):
    """Run graphify dream with _call_llm mocked to return mock_response_json."""
    patch_dir = _make_mock_llm_module(tmp_path, mock_response_json)
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("MOONSHOT_API_KEY", None)
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    # Fake key so backend detection doesn't abort before the mock fires
    env["GRAPHIFY_BACKEND"] = "kimi"
    env["MOONSHOT_API_KEY"] = "fake-test-key"
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(patch_dir) + (os.pathsep + existing_pp if existing_pp else "")
    return subprocess.run(
        [sys.executable, "-m", "graphify", "dream", *extra_args, str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _two_community_nodes_no_cross_edges():
    """8 nodes in 2 communities (4 each), no edges between communities."""
    # Community 0: nodes a0..a3 each with degree >3 via intra-community edges
    # Community 1: nodes b0..b3 each with degree >3 via intra-community edges
    nodes = [
        {"id": "a0", "label": "Alpha Zero", "community": 0},
        {"id": "a1", "label": "Alpha One",  "community": 0},
        {"id": "a2", "label": "Alpha Two",  "community": 0},
        {"id": "a3", "label": "Alpha Three","community": 0},
        {"id": "b0", "label": "Beta Zero",  "community": 1},
        {"id": "b1", "label": "Beta One",   "community": 1},
        {"id": "b2", "label": "Beta Two",   "community": 1},
        {"id": "b3", "label": "Beta Three", "community": 1},
    ]
    # Intra-community edges only — communities are fully disconnected
    edges = []
    for hub in ("a0", "a1", "a2", "a3"):
        for spoke in ("a0", "a1", "a2", "a3"):
            if hub != spoke:
                edges.append({
                    "source": hub, "target": spoke,
                    "relation": "calls", "confidence": "INFERRED",
                    "weight": 0.6, "source_file": "x.py",
                })
    for hub in ("b0", "b1", "b2", "b3"):
        for spoke in ("b0", "b1", "b2", "b3"):
            if hub != spoke:
                edges.append({
                    "source": hub, "target": spoke,
                    "relation": "calls", "confidence": "INFERRED",
                    "weight": 0.6, "source_file": "x.py",
                })
    return nodes, edges


# ---------------------------------------------------------------------------
# Test 1 — adds edge between disconnected communities
# ---------------------------------------------------------------------------

def test_dream_adds_edges_between_disconnected_communities(tmp_path):
    """LLM returns 1 high-confidence edge; dream adds it with correct metadata."""
    nodes, edges = _two_community_nodes_no_cross_edges()
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)

    mock_response = json.dumps({
        "edges": [
            {"source": "a0", "target": "b0", "relation": "depends_on", "confidence": 0.9},
        ]
    })
    r = _run_dream_with_mock_llm(tmp_path, repo, mock_response)
    assert r.returncode == 0, r.stderr

    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    links = g.get("links") or g.get("edges", [])
    dream_edges = [e for e in links if e.get("origin") == "rem_dream"]
    assert len(dream_edges) == 1, f"Expected 1 dream edge; got {dream_edges}"
    e = dream_edges[0]
    assert e["weight"] == 0.5
    assert e["confidence"] == "INFERRED"
    assert e["relation"] == "depends_on"
    assert set([e["source"], e["target"]]) == {"a0", "b0"}


# ---------------------------------------------------------------------------
# Test 2 — skips pairs that already have edges
# ---------------------------------------------------------------------------

def test_dream_skips_pairs_with_existing_edges(tmp_path):
    """A pair with an existing cross-edge is skipped; LLM must NOT be called for it."""
    nodes, edges = _two_community_nodes_no_cross_edges()
    # Add a cross-community edge so the pair is NOT disconnected
    edges.append({
        "source": "a0", "target": "b0",
        "relation": "already_connected", "confidence": "INFERRED",
        "weight": 0.8, "source_file": "x.py",
    })
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)

    # The mock would add an edge — but it should never be called for this pair
    mock_response = json.dumps({
        "edges": [
            {"source": "a1", "target": "b1", "relation": "calls", "confidence": 0.95},
        ]
    })
    r = _run_dream_with_mock_llm(tmp_path, repo, mock_response)
    assert r.returncode == 0, r.stderr

    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    links = g.get("links") or g.get("edges", [])
    dream_edges = [e for e in links if e.get("origin") == "rem_dream"]
    # Since the only pair has an existing edge it must be skipped — 0 dream edges
    assert len(dream_edges) == 0, f"Expected 0 dream edges for pre-connected pair; got {dream_edges}"


# ---------------------------------------------------------------------------
# Test 3 — filters edges below threshold
# ---------------------------------------------------------------------------

def test_dream_filters_below_threshold(tmp_path):
    """LLM proposes 3 edges at [0.9, 0.6, 0.4]; --threshold 0.7 keeps only the first."""
    nodes, edges = _two_community_nodes_no_cross_edges()
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)

    mock_response = json.dumps({
        "edges": [
            {"source": "a0", "target": "b0", "relation": "depends_on",   "confidence": 0.9},
            {"source": "a1", "target": "b1", "relation": "analogous_to", "confidence": 0.6},
            {"source": "a2", "target": "b2", "relation": "produces",     "confidence": 0.4},
        ]
    })
    r = _run_dream_with_mock_llm(tmp_path, repo, mock_response, "--threshold", "0.7")
    assert r.returncode == 0, r.stderr

    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    links = g.get("links") or g.get("edges", [])
    dream_edges = [e for e in links if e.get("origin") == "rem_dream"]
    assert len(dream_edges) == 1, f"Expected 1 edge above threshold=0.7; got {dream_edges}"
    assert dream_edges[0]["confidence_score"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Test 4 — drops hallucinated IDs
# ---------------------------------------------------------------------------

def test_dream_drops_hallucinated_ids(tmp_path):
    """Edge with source='bogus_id' not in prompt is dropped; valid edges are kept."""
    nodes, edges = _two_community_nodes_no_cross_edges()
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)

    mock_response = json.dumps({
        "edges": [
            {"source": "bogus_id",  "target": "b0", "relation": "calls",      "confidence": 0.95},
            {"source": "a0",        "target": "b0", "relation": "depends_on",  "confidence": 0.9},
        ]
    })
    r = _run_dream_with_mock_llm(tmp_path, repo, mock_response, "--threshold", "0.7")
    assert r.returncode == 0, r.stderr

    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    links = g.get("links") or g.get("edges", [])
    dream_edges = [e for e in links if e.get("origin") == "rem_dream"]
    # Only the valid edge is kept
    assert len(dream_edges) == 1, f"Expected 1 valid edge; got {dream_edges}"
    assert set([dream_edges[0]["source"], dream_edges[0]["target"]]) == {"a0", "b0"}
    # Hallucination warning must appear on stderr
    assert "bogus_id" in r.stderr or "hallucination" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Test 5 — --max-triplets cap rolls back excess
# ---------------------------------------------------------------------------

def test_dream_max_triplets_cap_rolls_back(tmp_path):
    """LLM proposes 6 edges above threshold; --max-triplets 3 → exactly 3 in final graph."""
    # Use 6 nodes per community, each with degree > 3 (connected to 4 other intra-community nodes)
    # so the sampling always picks 3 reps with degree > 3 from each side.
    nodes = (
        [{"id": f"a{i}", "label": f"Alpha {i}", "community": 0} for i in range(6)]
        + [{"id": f"b{i}", "label": f"Beta {i}", "community": 1} for i in range(6)]
    )
    edges = []
    # Fully connect community 0: every node gets degree 5 > 3
    for i in range(6):
        for j in range(i + 1, 6):
            edges.append({
                "source": f"a{i}", "target": f"a{j}",
                "relation": "calls", "confidence": "INFERRED",
                "weight": 0.6, "source_file": "x.py",
            })
    # Fully connect community 1
    for i in range(6):
        for j in range(i + 1, 6):
            edges.append({
                "source": f"b{i}", "target": f"b{j}",
                "relation": "calls", "confidence": "INFERRED",
                "weight": 0.6, "source_file": "x.py",
            })
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)

    # The sampler picks nodes with degree > 3; in a 6-node clique every node has degree 5.
    # It takes the first 3 from each set (a0,a1,a2 and b0,b1,b2).
    # Propose 6 unique cross-community edges using those reps only.
    mock_response = json.dumps({
        "edges": [
            {"source": "a0", "target": "b0", "relation": "depends_on",   "confidence": 0.95},
            {"source": "a0", "target": "b1", "relation": "analogous_to", "confidence": 0.95},
            {"source": "a1", "target": "b0", "relation": "calls",        "confidence": 0.95},
            {"source": "a1", "target": "b2", "relation": "produces",     "confidence": 0.95},
            {"source": "a2", "target": "b1", "relation": "validates",    "confidence": 0.95},
            {"source": "a2", "target": "b2", "relation": "extends",      "confidence": 0.95},
        ]
    })
    r = _run_dream_with_mock_llm(tmp_path, repo, mock_response, "--max-triplets", "3")
    assert r.returncode == 0, r.stderr

    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    links = g.get("links") or g.get("edges", [])
    dream_edges = [e for e in links if e.get("origin") == "rem_dream"]
    assert len(dream_edges) == 3, f"Expected exactly 3 dream edges after cap; got {len(dream_edges)}"
    # Cap notice on stderr
    assert "capped" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Test 6 — safety gate aborts above 50
# ---------------------------------------------------------------------------

def test_dream_safety_gate_aborts_above_50(tmp_path):
    """LLM floods 51+ unique edges above threshold → exit 2, graph.json unchanged.

    Strategy: use 5 communities of 6 nodes each (all cliques so degree=5 > 3).
    Top-5 picks all 5 communities → 10 distinct disconnected pairs.
    The mock inspects the prompt to extract the valid IDs for each call and
    returns 6 unique edges using those IDs, giving 10 × 6 = 60 > 50 total.
    """
    # 5 communities × 6 nodes = 30 nodes total, each community a clique (degree 5)
    num_communities = 5
    nodes_per_comm = 6
    nodes = [
        {"id": f"c{c}n{i}", "label": f"Comm{c} Node{i}", "community": c}
        for c in range(num_communities)
        for i in range(nodes_per_comm)
    ]
    edges = []
    for c in range(num_communities):
        comm_nodes = [f"c{c}n{i}" for i in range(nodes_per_comm)]
        for i in range(nodes_per_comm):
            for j in range(i + 1, nodes_per_comm):
                edges.append({
                    "source": comm_nodes[i], "target": comm_nodes[j],
                    "relation": "calls", "confidence": "INFERRED",
                    "weight": 0.6, "source_file": "x.py",
                })
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)
    original_text = (repo / "graphify-out" / "graph.json").read_text()

    # Use a stateful mock that parses the prompt to discover valid IDs, then
    # returns 6 unique edges using those IDs.  This ensures no edge is ever
    # dropped as a hallucination, giving 10 pairs × 6 edges = 60 > 50.
    patch_dir = tmp_path / "_llm_patch_sg"
    patch_dir.mkdir(exist_ok=True)
    (patch_dir / "sitecustomize.py").write_text(
        """
import re
import json
import graphify.llm as _llm_mod

def _smart_mock(prompt, *, backend, max_tokens=200):
    # Extract all node IDs from the prompt lines: "- <id>: <label>"
    ids = re.findall(r'^- (\\S+):', prompt, re.MULTILINE)
    # Community A reps appear before "Community B", B reps after
    split = prompt.find("Community B representatives:")
    before = prompt[:split] if split != -1 else prompt
    after  = prompt[split:] if split != -1 else ""
    ids_a = re.findall(r'^- (\\S+):', before, re.MULTILINE)
    ids_b = re.findall(r'^- (\\S+):', after, re.MULTILINE)
    if not ids_a or not ids_b:
        return json.dumps({"edges": []})
    # Produce 6 unique edges crossing A→B
    result = []
    for a in ids_a:
        for b in ids_b:
            if len(result) < 6:
                result.append({"source": a, "target": b, "relation": "depends_on", "confidence": 0.95})
    return json.dumps({"edges": result})

_llm_mod._call_llm = _smart_mock
"""
    )
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

    r = subprocess.run(
        [sys.executable, "-m", "graphify", "dream", "--max-triplets", "100", str(repo)],
        capture_output=True, text=True, check=False, env=env,
    )
    assert r.returncode == 2, (
        f"Expected exit 2 for safety gate; got {r.returncode}\nstderr={r.stderr}\nstdout={r.stdout}"
    )
    assert "safety gate" in r.stderr.lower()
    # graph.json must be unchanged (safety gate aborts before write)
    assert (repo / "graphify-out" / "graph.json").read_text() == original_text


# ---------------------------------------------------------------------------
# Test 7 — --dry-run does not mutate
# ---------------------------------------------------------------------------

def test_dream_dry_run_does_not_mutate(tmp_path):
    """--dry-run reports proposed edges to stderr; graph.json and git history unchanged."""
    nodes, edges = _two_community_nodes_no_cross_edges()
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)
    original_text = (repo / "graphify-out" / "graph.json").read_text()

    mock_response = json.dumps({
        "edges": [
            {"source": "a0", "target": "b0", "relation": "depends_on", "confidence": 0.9},
        ]
    })
    r = _run_dream_with_mock_llm(tmp_path, repo, mock_response, "--dry-run")
    assert r.returncode == 0, r.stderr

    # graph.json must be untouched
    assert (repo / "graphify-out" / "graph.json").read_text() == original_text

    # Proposed edges reported on stderr
    assert "dry-run" in r.stderr.lower() or "proposed" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Test 8 — split-mode refuses without --force
# ---------------------------------------------------------------------------

def test_dream_split_mode_refuses(tmp_path):
    """Repo with .graphifyshared overlay → exit 1 with split-mode error."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(repo), check=True)
    out = repo / "graphify-out"
    out.mkdir(exist_ok=True)
    (out / "graph.json").write_text(
        json.dumps({"directed": False, "multigraph": False, "graph": {}, "nodes": [], "links": []})
    )
    (repo / ".graphifyshared").write_text("src/**\n")

    r = _run_dream(repo)
    assert r.returncode == 1
    assert "split mode" in r.stderr.lower()
    assert "force-split-mode-unsafe" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Test 9 — --force-split-mode-unsafe allows the run
# ---------------------------------------------------------------------------

def test_dream_force_split_mode_unsafe_proceeds(tmp_path):
    """--force-split-mode-unsafe bypasses the split-mode check."""
    nodes, edges = _two_community_nodes_no_cross_edges()
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)
    (repo / ".graphifyshared").write_text("src/**\n")

    mock_response = json.dumps({"edges": []})
    r = _run_dream_with_mock_llm(tmp_path, repo, mock_response, "--force-split-mode-unsafe")

    # Must NOT be the split-mode refusal (exit 1 with that specific message)
    if r.returncode == 1:
        assert "split mode" not in r.stderr.lower(), (
            f"Should have bypassed split-mode but got: {r.stderr}"
        )
    # The force warning or no-communities message should appear
    assert (
        "force-split-mode-unsafe" in r.stderr.lower()
        or "split-mode" in r.stderr.lower()
        or "not enough" in r.stdout.lower()
        or r.returncode == 0
    )


# ---------------------------------------------------------------------------
# Test 10 — missing graph.json errors cleanly
# ---------------------------------------------------------------------------

def test_dream_missing_graph_errors(tmp_path):
    """No graphify-out/graph.json → clean error message, non-zero exit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)

    r = _run_dream(repo)
    assert r.returncode != 0
    assert "graph.json" in r.stderr.lower() or "does not exist" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Test 11 — commit touches only graph.json
# ---------------------------------------------------------------------------

def test_dream_commits_only_graph_json(tmp_path):
    """An unstaged change to another file survives after dream (only graph.json committed)."""
    nodes, edges = _two_community_nodes_no_cross_edges()
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)

    # Write an unstaged file
    other = repo / "OTHER.txt"
    other.write_text("user-edit-in-progress")

    mock_response = json.dumps({
        "edges": [
            {"source": "a0", "target": "b0", "relation": "depends_on", "confidence": 0.9},
        ]
    })
    r = _run_dream_with_mock_llm(tmp_path, repo, mock_response)
    assert r.returncode == 0, r.stderr

    # OTHER.txt must still have the user edit
    assert other.read_text() == "user-edit-in-progress"

    # git status must show OTHER.txt as untracked
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    ).stdout
    assert "OTHER.txt" in status


# ---------------------------------------------------------------------------
# Test 12 — not enough communities exits cleanly
# ---------------------------------------------------------------------------

def test_dream_not_enough_communities_exits_cleanly(tmp_path):
    """Fixture with only 1 viable community → exit 0 with informative message."""
    # All nodes in community 0 only
    nodes = [
        {"id": "a0", "label": "Alpha Zero",  "community": 0},
        {"id": "a1", "label": "Alpha One",   "community": 0},
        {"id": "a2", "label": "Alpha Two",   "community": 0},
        {"id": "a3", "label": "Alpha Three", "community": 0},
    ]
    edges = [
        {"source": "a0", "target": "a1", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "x.py"},
        {"source": "a1", "target": "a2", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "x.py"},
        {"source": "a2", "target": "a3", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "x.py"},
    ]
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)

    r = _run_dream(repo)
    assert r.returncode == 0, r.stderr
    # Informative message about not enough communities
    combined = r.stdout + r.stderr
    assert "not enough" in combined.lower() or "communities" in combined.lower()
