# Tests for Stage 3 sleep-cycle schema additions in graphify/validate.py and build.py.
# Covers: weight/last_used/uses edge fields, created_at/max_observed_degree node fields,
# extended origin enum, grounded_in relation, and lazy default population via build_merge.
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(node_id: str, source_file: str = "src/a.py", **extra: Any) -> dict:
    base: dict = {
        "id": node_id,
        "label": node_id,
        "file_type": "concept",
        "source_file": source_file,
    }
    base.update(extra)
    return base


def _edge(src: str, tgt: str, **extra: Any) -> dict:
    base: dict = {
        "source": src,
        "target": tgt,
        "relation": "uses",
        "confidence": "INFERRED",
        "source_file": "src/a.py",
    }
    base.update(extra)
    return base


def _extraction(nodes: list[dict], edges: list[dict]) -> dict:
    return {"nodes": nodes, "edges": edges}


def _write_graph_json(path: Path, nodes: list[dict], edges: list[dict]) -> None:
    """Write a minimal NetworkX node-link JSON graph file."""
    payload = {
        "directed": True,
        "multigraph": False,
        "graph": {},
        "nodes": [{"id": n["id"], **{k: v for k, v in n.items() if k != "id"}} for n in nodes],
        "links": edges,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. weight field accepted on edge
# ---------------------------------------------------------------------------


def test_weight_field_accepted_on_edge() -> None:
    from graphify.validate import validate_extraction

    nodes = [_node("a"), _node("b")]
    edges = [_edge("a", "b", weight=0.5)]
    errors = validate_extraction(_extraction(nodes, edges))
    assert errors == [], f"Unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# 2. weight out of range rejected
# ---------------------------------------------------------------------------


def test_weight_out_of_range_rejected() -> None:
    from graphify.validate import validate_extraction

    nodes = [_node("a"), _node("b")]

    errors_high = validate_extraction(_extraction(nodes, [_edge("a", "b", weight=1.5)]))
    assert any("weight" in e and "out of range" in e for e in errors_high), (
        f"Expected weight-range error for 1.5, got: {errors_high}"
    )

    errors_low = validate_extraction(_extraction(nodes, [_edge("a", "b", weight=-0.1)]))
    assert any("weight" in e and "out of range" in e for e in errors_low), (
        f"Expected weight-range error for -0.1, got: {errors_low}"
    )


# ---------------------------------------------------------------------------
# 3. last_used and uses accepted as optional
# ---------------------------------------------------------------------------


def test_last_used_uses_accepted() -> None:
    from graphify.validate import validate_extraction

    nodes = [_node("a"), _node("b")]
    edges = [_edge("a", "b", last_used=1_700_000_000.0, uses=3)]
    errors = validate_extraction(_extraction(nodes, edges))
    assert errors == [], f"Unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# 4. created_at and max_observed_degree accepted on node
# ---------------------------------------------------------------------------


def test_created_at_max_observed_degree_accepted_on_node() -> None:
    from graphify.validate import validate_extraction

    nodes = [_node("a", created_at=1_700_000_000.0, max_observed_degree=5), _node("b")]
    edges = [_edge("a", "b")]
    errors = validate_extraction(_extraction(nodes, edges))
    assert errors == [], f"Unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# 5. extended origin enum
# ---------------------------------------------------------------------------


def test_origin_extended_enum() -> None:
    from graphify.validate import validate_extraction

    nodes_replay = [_node("a", origin="replay"), _node("b")]
    assert validate_extraction(_extraction(nodes_replay, [_edge("a", "b")])) == []

    nodes_rem = [_node("a", origin="rem_dream"), _node("b")]
    assert validate_extraction(_extraction(nodes_rem, [_edge("a", "b")])) == []

    nodes_hypo = [_node("a", origin="hypothesis"), _node("b")]
    assert validate_extraction(_extraction(nodes_hypo, [_edge("a", "b")])) == []

    # origin='unknown' should be rejected
    nodes_bad = [_node("a", origin="unknown"), _node("b")]
    errors = validate_extraction(_extraction(nodes_bad, [_edge("a", "b")]))
    assert any("origin" in e for e in errors), (
        f"Expected origin error for 'unknown', got: {errors}"
    )

    # edge origins
    edges_replay = [_edge("a", "b", origin="replay")]
    assert validate_extraction(_extraction([_node("a"), _node("b")], edges_replay)) == []

    edges_bad = [_edge("a", "b", origin="unknown")]
    errors_edge = validate_extraction(_extraction([_node("a"), _node("b")], edges_bad))
    assert any("origin" in e for e in errors_edge), (
        f"Expected origin error for edge with 'unknown' origin, got: {errors_edge}"
    )


# ---------------------------------------------------------------------------
# 6. grounded_in relation accepted
# ---------------------------------------------------------------------------


def test_grounded_in_relation_accepted() -> None:
    from graphify.validate import validate_extraction, VALID_RELATIONS

    # grounded_in must be in the informational VALID_RELATIONS constant
    assert "grounded_in" in VALID_RELATIONS

    nodes = [_node("h_node"), _node("src_node")]
    edges = [_edge("h_node", "src_node", relation="grounded_in", confidence="INFERRED")]
    errors = validate_extraction(_extraction(nodes, edges))
    assert errors == [], f"Unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# 7. legacy graph lazy defaults applied
# ---------------------------------------------------------------------------


def test_legacy_graph_lazy_defaults_applied(tmp_path: Path) -> None:
    from graphify.build import build_merge

    graph_file = tmp_path / "graphify-out" / "graph.json"
    # Write a graph.json with no sleep-cycle fields
    _write_graph_json(
        graph_file,
        nodes=[{"id": "n1", "label": "Node1", "file_type": "concept", "source_file": "a.py"}],
        edges=[{
            "source": "n1", "target": "n1",
            "relation": "uses", "confidence": "EXTRACTED", "source_file": "a.py",
        }],
    )

    G = build_merge([], graph_path=graph_file)

    # All 5 sleep-cycle fields must be present after load
    for nid, attrs in G.nodes(data=True):
        assert "created_at" in attrs, f"Node {nid} missing created_at"
        assert "max_observed_degree" in attrs, f"Node {nid} missing max_observed_degree"

    for u, v, attrs in G.edges(data=True):
        assert "weight" in attrs, f"Edge ({u},{v}) missing weight"
        assert "last_used" in attrs, f"Edge ({u},{v}) missing last_used"
        assert "uses" in attrs, f"Edge ({u},{v}) missing uses"


# ---------------------------------------------------------------------------
# 8. existing field values preserved (no overwrite)
# ---------------------------------------------------------------------------


def test_existing_field_values_preserved(tmp_path: Path) -> None:
    from graphify.build import build_merge

    graph_file = tmp_path / "graphify-out" / "graph.json"
    _write_graph_json(
        graph_file,
        nodes=[{"id": "n1", "label": "N1", "file_type": "concept", "source_file": "a.py",
                "created_at": 1_600_000_000.0, "max_observed_degree": 42}],
        edges=[{
            "source": "n1", "target": "n1",
            "relation": "uses", "confidence": "INFERRED", "source_file": "a.py",
            "weight": 0.7, "last_used": 1_600_000_001.0, "uses": 99,
        }],
    )

    G = build_merge([], graph_path=graph_file)

    for nid, attrs in G.nodes(data=True):
        assert attrs["created_at"] == pytest.approx(1_600_000_000.0), (
            f"created_at was overwritten on node {nid}"
        )
        assert attrs["max_observed_degree"] == 42, (
            f"max_observed_degree was overwritten on node {nid}"
        )

    for u, v, attrs in G.edges(data=True):
        assert attrs["weight"] == pytest.approx(0.7), "weight was overwritten"
        assert attrs["last_used"] == pytest.approx(1_600_000_001.0), "last_used was overwritten"
        assert attrs["uses"] == 99, "uses was overwritten"


# ---------------------------------------------------------------------------
# 9. build_merge round-trip preserves new fields
# ---------------------------------------------------------------------------


def test_build_merge_preserves_new_fields(tmp_path: Path) -> None:
    from graphify.build import build_merge

    graph_file = tmp_path / "graphify-out" / "graph.json"
    # First merge — creates graph.json with sleep-cycle defaults
    G1 = build_merge(
        [_extraction(
            [_node("x"), _node("y")],
            [_edge("x", "y", confidence="EXTRACTED")],
        )],
        graph_path=graph_file,
    )

    # weight for EXTRACTED should be 1.0
    for u, v, attrs in G1.edges(data=True):
        assert attrs["weight"] == pytest.approx(1.0), (
            f"Expected EXTRACTED default weight 1.0, got {attrs['weight']}"
        )
        assert "last_used" in attrs
        assert attrs["uses"] == 0
