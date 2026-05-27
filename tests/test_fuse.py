"""graphify fuse subcommand tests (Stage 3 Sprint 3 — S3.A)."""
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
    graph_data: dict = {"directed": False, "multigraph": False, "graph": {}, "nodes": nodes, "links": edges}
    if hyperedges is not None:
        graph_data["graph"]["hyperedges"] = hyperedges
    if graph_meta:
        graph_data["graph"].update(graph_meta)
    (out / "graph.json").write_text(json.dumps(graph_data))
    subprocess.run(["git", "add", "graphify-out/graph.json"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "initial graph"], cwd=str(repo), check=True)
    return repo


def _run_fuse(repo, *args, extra_env=None):
    env = os.environ.copy()
    # Ensure no real LLM key leaks in — tests always monkeypatch via env
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("MOONSHOT_API_KEY", None)
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "graphify", "fuse", *args, str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _make_mock_llm_module(tmp_path, response_json: str) -> Path:
    """Write a small sitecustomize-style patch module that monkey-patches
    graphify.llm._call_llm to return the given JSON string.
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


def _run_fuse_with_mock_llm(tmp_path, repo, mock_response_json: str, *extra_args):
    """Run graphify fuse with _call_llm mocked to return mock_response_json."""
    patch_dir = _make_mock_llm_module(tmp_path, mock_response_json)
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("MOONSHOT_API_KEY", None)
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    # Use a fake key so backend detection doesn't abort before reaching mock
    env["GRAPHIFY_BACKEND"] = "kimi"
    env["MOONSHOT_API_KEY"] = "fake-test-key"
    # Prepend patch dir so sitecustomize fires before graphify.llm is used
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(patch_dir) + (os.pathsep + existing_pp if existing_pp else "")
    return subprocess.run(
        [sys.executable, "-m", "graphify", "fuse", *extra_args, str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


# ---------------------------------------------------------------------------
# Test 1 — dry-run does not mutate graph.json
# ---------------------------------------------------------------------------

def test_fuse_dry_run_does_not_mutate(tmp_path):
    """--dry-run must report clusters to stderr and leave graph.json unchanged."""
    nodes = [
        {"id": "auth_service", "label": "Auth Service", "file_type": "code", "source_file": "a.py"},
        {"id": "authservice", "label": "AuthService", "file_type": "code", "source_file": "b.py"},
        {"id": "x1", "label": "X1", "file_type": "code", "source_file": "c.py"},
        {"id": "x2", "label": "X2", "file_type": "code", "source_file": "d.py"},
        {"id": "x3", "label": "X3", "file_type": "code", "source_file": "e.py"},
        {"id": "x4", "label": "X4", "file_type": "code", "source_file": "f.py"},
    ]
    # edges with INFERRED confidence so neither node is EXTRACTED-safe
    edges = [
        {"source": "auth_service", "target": "x1", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "a.py"},
        {"source": "auth_service", "target": "x2", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "a.py"},
        {"source": "auth_service", "target": "x3", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "a.py"},
        {"source": "auth_service", "target": "x4", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "a.py"},
        {"source": "authservice", "target": "x1", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "b.py"},
        {"source": "authservice", "target": "x2", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "b.py"},
        {"source": "authservice", "target": "x3", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "b.py"},
        {"source": "authservice", "target": "x4", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "b.py"},
    ]
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)
    original_text = (repo / "graphify-out" / "graph.json").read_text()

    mock_response = json.dumps({
        "clusters": [
            {
                "canonical_id": "auth_service",
                "canonical_label": "Auth Service",
                "alias_ids": ["authservice"],
            }
        ]
    })
    r = _run_fuse_with_mock_llm(tmp_path, repo, mock_response, "--dry-run")

    # dry-run exits 0
    assert r.returncode == 0, r.stderr

    # graph.json must be untouched
    assert (repo / "graphify-out" / "graph.json").read_text() == original_text

    # clusters reported to stderr
    assert "dry-run" in r.stderr.lower() or "canonical" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Test 2 — EXTRACTED-safe nodes are never fused
# ---------------------------------------------------------------------------

def test_fuse_never_merges_extracted(tmp_path):
    """Node with an EXTRACTED incident edge stays in the graph even if LLM proposes fusion."""
    # auth_service has an EXTRACTED edge → it is EXTRACTED-safe
    # authservice is also EXTRACTED-safe (same edge touches it)
    # Both have degree > 3 via extra INFERRED edges so they'd normally be candidates
    nodes = [
        {"id": "auth_service", "label": "Auth Service", "file_type": "code", "source_file": "a.py"},
        {"id": "authservice", "label": "AuthService", "file_type": "code", "source_file": "b.py"},
        {"id": "x1", "label": "X1", "file_type": "code", "source_file": "c.py"},
        {"id": "x2", "label": "X2", "file_type": "code", "source_file": "d.py"},
        {"id": "x3", "label": "X3", "file_type": "code", "source_file": "e.py"},
        {"id": "x4", "label": "X4", "file_type": "code", "source_file": "f.py"},
    ]
    edges = [
        # EXTRACTED edge — makes auth_service and authservice EXTRACTED-safe
        {"source": "auth_service", "target": "authservice", "relation": "imports",
         "confidence": "EXTRACTED", "weight": 1.0, "source_file": "a.py"},
        # Extra edges so degree > 3
        {"source": "auth_service", "target": "x1", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "a.py"},
        {"source": "auth_service", "target": "x2", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "a.py"},
        {"source": "auth_service", "target": "x3", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "a.py"},
        {"source": "auth_service", "target": "x4", "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "a.py"},
    ]
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)

    # LLM proposes to merge authservice into auth_service — but both are EXTRACTED-safe
    mock_response = json.dumps({
        "clusters": [
            {
                "canonical_id": "auth_service",
                "canonical_label": "Auth Service",
                "alias_ids": ["authservice"],
            }
        ]
    })
    r = _run_fuse_with_mock_llm(tmp_path, repo, mock_response)
    assert r.returncode == 0, r.stderr

    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    node_ids = {n["id"] for n in g["nodes"]}
    # Both nodes must still exist — EXTRACTED-safe cannot be fused
    assert "auth_service" in node_ids
    assert "authservice" in node_ids


# ---------------------------------------------------------------------------
# Test 3 — safety gate aborts when fusions exceed max-fusions
# ---------------------------------------------------------------------------

def test_fuse_safety_gate_aborts_above_max_fusions(tmp_path):
    """With --max-fusions=2, when LLM proposes >2 fusions → exit 2, graph NOT modified."""
    # Build 12 nodes with INFERRED edges so they have degree > 3
    n = 12
    nodes = [
        {"id": f"node_{i}", "label": f"Node {i}", "file_type": "code", "source_file": f"f{i}.py"}
        for i in range(n)
    ]
    # Connect node_0..node_5 so each has degree > 3 (they'll be candidates)
    edges = []
    for i in range(6):
        for j in range(i + 1, min(i + 5, 6)):
            edges.append({
                "source": f"node_{i}", "target": f"node_{j}",
                "relation": "calls", "confidence": "INFERRED",
                "weight": 0.6, "source_file": "x.py",
            })
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)
    original_text = (repo / "graphify-out" / "graph.json").read_text()

    # LLM proposes 5 fusions: node_1→node_0, node_2→node_0, node_3→node_0,
    # node_4→node_0, node_5→node_0
    mock_response = json.dumps({
        "clusters": [
            {
                "canonical_id": "node_0",
                "canonical_label": "Node 0",
                "alias_ids": ["node_1", "node_2", "node_3", "node_4", "node_5"],
            }
        ]
    })
    r = _run_fuse_with_mock_llm(tmp_path, repo, mock_response, "--max-fusions=2")

    # Must exit 2
    assert r.returncode == 2, r.stderr

    # Safety gate message on stderr
    assert "safety gate" in r.stderr.lower()
    assert "max-fusions" in r.stderr.lower()

    # Graph must be unmodified
    assert (repo / "graphify-out" / "graph.json").read_text() == original_text


# ---------------------------------------------------------------------------
# Test 4 — chained mapping resolves to fixed point
# ---------------------------------------------------------------------------

def test_fuse_chained_mapping_resolves_to_fixed_point(tmp_path):
    """LLM proposes a→b in batch 1 and b→c in batch 2; after fuse both a and b are gone."""
    # Need nodes with degree > 3 to be candidates. Use a hub pattern.
    # c is the canonical; b aliases to c; a aliases to b.
    # We put many edges on each so they're selected as candidates.
    nodes = [
        {"id": "node_a", "label": "Node A", "file_type": "code", "source_file": "a.py"},
        {"id": "node_b", "label": "Node B", "file_type": "code", "source_file": "b.py"},
        {"id": "node_c", "label": "Node C", "file_type": "code", "source_file": "c.py"},
        {"id": "x1", "label": "X1", "file_type": "code", "source_file": "x1.py"},
        {"id": "x2", "label": "X2", "file_type": "code", "source_file": "x2.py"},
        {"id": "x3", "label": "X3", "file_type": "code", "source_file": "x3.py"},
        {"id": "x4", "label": "X4", "file_type": "code", "source_file": "x4.py"},
    ]
    # Give each of a, b, c degree > 3 via INFERRED edges
    edges = []
    for hub in ("node_a", "node_b", "node_c"):
        for xi in ("x1", "x2", "x3", "x4"):
            edges.append({
                "source": hub, "target": xi,
                "relation": "calls", "confidence": "INFERRED",
                "weight": 0.6, "source_file": "x.py",
            })

    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)

    # Two-batch response: batch 1 says a→b, batch 2 says b→c.
    # We simulate two batches by having batch_size=1 for each hub node.
    # The mock LLM will receive two calls and alternate responses.
    # Simpler approach: single response with two clusters (a→b and b→c).
    # Fixed-point logic must resolve a→c transitively.
    mock_response = json.dumps({
        "clusters": [
            {
                "canonical_id": "node_b",
                "canonical_label": "Node B",
                "alias_ids": ["node_a"],
            },
            {
                "canonical_id": "node_c",
                "canonical_label": "Node C",
                "alias_ids": ["node_b"],
            },
        ]
    })
    r = _run_fuse_with_mock_llm(tmp_path, repo, mock_response, "--max-fusions=10")
    assert r.returncode == 0, r.stderr

    g = json.loads((repo / "graphify-out" / "graph.json").read_text())
    node_ids = {n["id"] for n in g["nodes"]}

    # Both node_a and node_b must be gone — contracted into node_c
    assert "node_a" not in node_ids, f"node_a still present; nodes={node_ids}"
    assert "node_b" not in node_ids, f"node_b still present; nodes={node_ids}"
    assert "node_c" in node_ids, f"node_c missing; nodes={node_ids}"


# ---------------------------------------------------------------------------
# Test 5 — commit touches only graph.json
# ---------------------------------------------------------------------------

def test_fuse_commits_only_graph_json(tmp_path):
    """An unstaged change to another file survives after fuse (only graph.json committed)."""
    nodes = [
        {"id": "n1", "label": "N1", "file_type": "code", "source_file": "a.py"},
        {"id": "n2", "label": "N2", "file_type": "code", "source_file": "b.py"},
        {"id": "x1", "label": "X1", "file_type": "code", "source_file": "x1.py"},
        {"id": "x2", "label": "X2", "file_type": "code", "source_file": "x2.py"},
        {"id": "x3", "label": "X3", "file_type": "code", "source_file": "x3.py"},
        {"id": "x4", "label": "X4", "file_type": "code", "source_file": "x4.py"},
    ]
    edges = [
        {"source": "n1", "target": xi, "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "x.py"}
        for xi in ("x1", "x2", "x3", "x4")
    ] + [
        {"source": "n2", "target": xi, "relation": "calls", "confidence": "INFERRED",
         "weight": 0.6, "source_file": "x.py"}
        for xi in ("x1", "x2", "x3", "x4")
    ]
    repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)

    # Write an unstaged file
    other = repo / "OTHER.txt"
    other.write_text("user-edit-in-progress")

    # LLM proposes no fusions so graph changes are trivial
    mock_response = json.dumps({"clusters": []})
    r = _run_fuse_with_mock_llm(tmp_path, repo, mock_response)
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
# Test 6 — split mode refuses without --force
# ---------------------------------------------------------------------------

def test_fuse_split_mode_refuses(tmp_path):
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

    r = _run_fuse(repo)
    assert r.returncode == 1
    assert "split mode" in r.stderr.lower()
    assert "force-split-mode-unsafe" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Test 7 — split mode with --force proceeds
# ---------------------------------------------------------------------------

def test_fuse_refuses_split_mode_force_proceeds(tmp_path):
    """--force-split-mode-unsafe allows the run even with .graphifyshared."""
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
    subprocess.run(["git", "add", "graphify-out/graph.json"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "initial graph"], cwd=str(repo), check=True)
    (repo / ".graphifyshared").write_text("src/**\n")

    # With no real LLM key and no candidates the run should succeed (0 candidates → 0 fusions)
    r = _run_fuse(repo, "--force-split-mode-unsafe")
    # Should not exit 1 (split-mode refusal). May exit 0 or 1 for missing backend,
    # but must NOT be the split-mode error specifically.
    if r.returncode == 1:
        assert "split mode" not in r.stderr.lower(), (
            f"Should have bypassed split-mode check but got: {r.stderr}"
        )
    # Warn message about force flag should appear
    assert "force-split-mode-unsafe" in r.stderr.lower() or "split-mode" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Test 8 — missing graph.json errors cleanly
# ---------------------------------------------------------------------------

def test_fuse_missing_graph_errors(tmp_path):
    """No graphify-out/graph.json → clean error message, non-zero exit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)

    r = _run_fuse(repo)
    assert r.returncode != 0
    assert "graph.json" in r.stderr.lower() or "does not exist" in r.stderr.lower()
