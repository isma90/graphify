"""Unit tests for ``graphify.extract.split_extraction_by_origin``.

Covers the pure data-shape contract: given an extraction dict and a
privacy_map mapping ``source_file`` -> ``"private" | "shared"``, the
function returns a 2-tuple ``(private_dict, shared_dict)`` where:

* Nodes are bucketed by the origin of their source file.
* Intra-bucket edges go to their bucket.
* Cross-bucket edges go to the private bucket only ("private wins").
* Hyperedges with any private member go to the private bucket;
  all-shared hyperedges go to the shared bucket.
* Top-level extraction metadata is preserved across both buckets.
"""
from __future__ import annotations

from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_extraction_dict(
    nodes: list[dict],
    edges: list[dict],
    hyperedges: list[dict] | None = None,
    **extra: Any,
) -> dict:
    """Build the extraction dict shape that ``extract()`` returns."""
    out: dict = {
        "nodes": list(nodes),
        "edges": list(edges),
        "input_tokens": 0,
        "output_tokens": 0,
    }
    if hyperedges is not None:
        out["hyperedges"] = list(hyperedges)
    out.update(extra)
    return out


def _node(node_id: str, source_file: str, **extra: Any) -> dict:
    base = {"id": node_id, "source_file": source_file, "label": node_id}
    base.update(extra)
    return base


def _edge(src: str, tgt: str, source_file: str = "", **extra: Any) -> dict:
    base = {
        "source": src,
        "target": tgt,
        "relation": "calls",
        "source_file": source_file,
        "confidence": "EXTRACTED",
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_split_with_no_privacy_map_returns_empty_shared() -> None:
    """An empty privacy_map should send every node to the private bucket
    (safety default) and leave the shared bucket empty."""
    from graphify.extract import split_extraction_by_origin

    nodes = [
        _node("a", "src/a.py"),
        _node("b", "scratch/b.py"),
    ]
    extraction = _make_extraction_dict(nodes, [])
    private_dict, shared_dict = split_extraction_by_origin(extraction, {})

    private_ids = {n["id"] for n in private_dict.get("nodes", [])}
    shared_nodes = shared_dict.get("nodes", [])
    assert private_ids == {"a", "b"}, (
        f"Expected all nodes in private bucket, got private={private_ids}"
    )
    assert shared_nodes == [], (
        f"Expected shared bucket empty when no privacy_map entries, got {shared_nodes}"
    )


def test_split_basic_node_distribution() -> None:
    """Nodes are bucketed by ``privacy_map[source_file]`` — counts must match."""
    from graphify.extract import split_extraction_by_origin

    privacy_map = {
        "src/a.py": "shared",
        "src/b.py": "shared",
        "scratch/c.py": "private",
        "scratch/d.py": "private",
        "scratch/e.py": "private",
    }
    nodes = [
        _node("a1", "src/a.py"),
        _node("b1", "src/b.py"),
        _node("c1", "scratch/c.py"),
        _node("d1", "scratch/d.py"),
        _node("e1", "scratch/e.py"),
    ]
    extraction = _make_extraction_dict(nodes, [])
    private_dict, shared_dict = split_extraction_by_origin(extraction, privacy_map)

    assert len(private_dict["nodes"]) == 3, (
        f"Expected 3 private nodes, got {len(private_dict['nodes'])}"
    )
    assert len(shared_dict["nodes"]) == 2, (
        f"Expected 2 shared nodes, got {len(shared_dict['nodes'])}"
    )


def test_split_edges_intra_shared() -> None:
    """Edges where both endpoints are shared go to the shared bucket."""
    from graphify.extract import split_extraction_by_origin

    privacy_map = {"src/a.py": "shared", "src/b.py": "shared"}
    nodes = [_node("a", "src/a.py"), _node("b", "src/b.py")]
    edges = [_edge("a", "b", source_file="src/a.py")]
    extraction = _make_extraction_dict(nodes, edges)
    private_dict, shared_dict = split_extraction_by_origin(extraction, privacy_map)

    shared_edge_pairs = {(e["source"], e["target"]) for e in shared_dict["edges"]}
    private_edge_pairs = {(e["source"], e["target"]) for e in private_dict["edges"]}
    assert ("a", "b") in shared_edge_pairs, "Intra-shared edge must be in shared bucket"
    assert ("a", "b") not in private_edge_pairs, (
        "Intra-shared edge must NOT also be in private bucket"
    )


def test_split_edges_intra_private() -> None:
    """Edges where both endpoints are private go to the private bucket."""
    from graphify.extract import split_extraction_by_origin

    privacy_map = {"scratch/a.py": "private", "scratch/b.py": "private"}
    nodes = [_node("a", "scratch/a.py"), _node("b", "scratch/b.py")]
    edges = [_edge("a", "b", source_file="scratch/a.py")]
    extraction = _make_extraction_dict(nodes, edges)
    private_dict, shared_dict = split_extraction_by_origin(extraction, privacy_map)

    private_edge_pairs = {(e["source"], e["target"]) for e in private_dict["edges"]}
    shared_edge_pairs = {(e["source"], e["target"]) for e in shared_dict["edges"]}
    assert ("a", "b") in private_edge_pairs, (
        "Intra-private edge must be in private bucket"
    )
    assert ("a", "b") not in shared_edge_pairs, (
        "Intra-private edge must NOT leak into shared bucket"
    )


def test_split_edges_cross_bucket_go_to_private() -> None:
    """Cross-bucket edges (one endpoint shared, the other private) must go
    to the private bucket only — private wins."""
    from graphify.extract import split_extraction_by_origin

    privacy_map = {"src/a.py": "shared", "scratch/b.py": "private"}
    nodes = [_node("a", "src/a.py"), _node("b", "scratch/b.py")]
    edges = [
        _edge("a", "b", source_file="src/a.py"),  # shared -> private
        _edge("b", "a", source_file="scratch/b.py"),  # private -> shared
    ]
    extraction = _make_extraction_dict(nodes, edges)
    private_dict, shared_dict = split_extraction_by_origin(extraction, privacy_map)

    private_pairs = {(e["source"], e["target"]) for e in private_dict["edges"]}
    shared_pairs = {(e["source"], e["target"]) for e in shared_dict["edges"]}

    assert {("a", "b"), ("b", "a")}.issubset(private_pairs), (
        f"Both cross-bucket edges must be in private, got {private_pairs}"
    )
    assert not (shared_pairs & {("a", "b"), ("b", "a")}), (
        f"Cross-bucket edges must NOT leak to shared, got {shared_pairs}"
    )


def test_split_hyperedges_all_shared() -> None:
    """A hyperedge whose every member is a shared node goes to shared."""
    from graphify.extract import split_extraction_by_origin

    privacy_map = {"src/a.py": "shared", "src/b.py": "shared"}
    nodes = [_node("a", "src/a.py"), _node("b", "src/b.py")]
    hyperedges = [{"id": "h1", "nodes": ["a", "b"], "label": "all-shared"}]
    extraction = _make_extraction_dict(nodes, [], hyperedges=hyperedges)
    private_dict, shared_dict = split_extraction_by_origin(extraction, privacy_map)

    shared_ids = {h.get("id") for h in shared_dict.get("hyperedges", [])}
    private_ids = {h.get("id") for h in private_dict.get("hyperedges", [])}
    assert "h1" in shared_ids, "All-shared hyperedge must be in shared bucket"
    assert "h1" not in private_ids, (
        "All-shared hyperedge must NOT leak into private bucket"
    )


def test_split_hyperedges_mixed_go_to_private() -> None:
    """A hyperedge with any private member goes to the private bucket only."""
    from graphify.extract import split_extraction_by_origin

    privacy_map = {
        "src/a.py": "shared",
        "src/b.py": "shared",
        "scratch/c.py": "private",
    }
    nodes = [
        _node("a", "src/a.py"),
        _node("b", "src/b.py"),
        _node("c", "scratch/c.py"),
    ]
    hyperedges = [{"id": "h_mixed", "nodes": ["a", "b", "c"], "label": "mixed"}]
    extraction = _make_extraction_dict(nodes, [], hyperedges=hyperedges)
    private_dict, shared_dict = split_extraction_by_origin(extraction, privacy_map)

    private_ids = {h.get("id") for h in private_dict.get("hyperedges", [])}
    shared_ids = {h.get("id") for h in shared_dict.get("hyperedges", [])}
    assert "h_mixed" in private_ids, "Mixed hyperedge must be in private bucket"
    assert "h_mixed" not in shared_ids, "Mixed hyperedge must NOT be in shared bucket"


def test_split_preserves_extraction_metadata() -> None:
    """Top-level extraction metadata (input_tokens / output_tokens) must
    survive the split — either copied to both buckets or preserved in both,
    so downstream consumers can compute cost-per-graph correctly."""
    from graphify.extract import split_extraction_by_origin

    privacy_map = {"src/a.py": "shared", "scratch/b.py": "private"}
    nodes = [_node("a", "src/a.py"), _node("b", "scratch/b.py")]
    extraction = _make_extraction_dict(nodes, [], input_tokens=123, output_tokens=456)
    private_dict, shared_dict = split_extraction_by_origin(extraction, privacy_map)

    for bucket_name, bucket in (("private", private_dict), ("shared", shared_dict)):
        assert "input_tokens" in bucket, (
            f"{bucket_name} bucket missing input_tokens metadata"
        )
        assert "output_tokens" in bucket, (
            f"{bucket_name} bucket missing output_tokens metadata"
        )
