"""graphify stats --json subcommand tests (Stage 3 Sprint 1)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run_graphify_stats(tmp_path, *args):
    """Helper: run `graphify stats <args>` via python -m, capture output."""
    return subprocess.run(
        [sys.executable, "-m", "graphify", "stats", *args],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )


def _make_minimal_graph(tmp_path):
    """Build a tiny graphify-out/graph.json fixture with 3 nodes, 2 edges, 1 community."""
    out = tmp_path / "graphify-out"
    out.mkdir()
    g = {
        "directed": False,
        "multigraph": False,
        "graph": {},
        "nodes": [
            {"id": "a", "label": "A", "community": 0, "source_file": "a.py", "confidence": "EXTRACTED", "origin": "private"},
            {"id": "b", "label": "B", "community": 0, "source_file": "b.py", "confidence": "EXTRACTED", "origin": "shared"},
            {"id": "c", "label": "C", "community": 1, "source_file": "c.py", "confidence": "INFERRED", "origin": "private"},
        ],
        "links": [
            {"source": "a", "target": "b", "relation": "imports", "confidence": "EXTRACTED", "source_file": "a.py", "weight": 1.0},
            {"source": "b", "target": "c", "relation": "references", "confidence": "INFERRED", "source_file": "b.py", "weight": 0.6},
        ],
    }
    (out / "graph.json").write_text(json.dumps(g))


def test_stats_json_basic(tmp_path):
    _make_minimal_graph(tmp_path)
    r = _run_graphify_stats(tmp_path, "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["nodes"] == 3
    assert data["edges"] == 2
    assert data["schema_version"] == 3
    assert "confidence_breakdown" in data
    assert "origin_breakdown" in data


def test_stats_json_confidence_breakdown(tmp_path):
    _make_minimal_graph(tmp_path)
    r = _run_graphify_stats(tmp_path, "--json")
    data = json.loads(r.stdout)
    cb = data["confidence_breakdown"]
    # 2 EXTRACTED, 1 INFERRED across 3 unique-confidence-bearing-edges (well, 2 edges total)
    # confidence on EDGES counts: a-b EXTRACTED, b-c INFERRED → {EXTRACTED: 0.5, INFERRED: 0.5}
    assert abs(cb.get("EXTRACTED", 0) - 0.5) < 0.001
    assert abs(cb.get("INFERRED", 0) - 0.5) < 0.001


def test_stats_human_readable_default(tmp_path):
    _make_minimal_graph(tmp_path)
    r = _run_graphify_stats(tmp_path)
    assert r.returncode == 0
    # Default (non-JSON) should be human-readable key/value lines
    assert "nodes" in r.stdout.lower()
    assert "3" in r.stdout


def test_stats_avg_degree_correct(tmp_path):
    _make_minimal_graph(tmp_path)
    r = _run_graphify_stats(tmp_path, "--json")
    data = json.loads(r.stdout)
    # 3 nodes, 2 edges → degrees: a=1, b=2, c=1 → sum=4, avg = 4/3
    assert abs(data["avg_degree"] - 4 / 3) < 0.01


def test_stats_no_graph_errors_cleanly(tmp_path):
    r = _run_graphify_stats(tmp_path, "--json")
    # Should exit non-zero with a clear error, not crash with traceback
    assert r.returncode != 0
    assert "not found" in r.stderr.lower() or "no graph" in r.stderr.lower() or "graph.json" in r.stderr.lower()
