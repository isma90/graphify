"""Tests for build_merge(prune_ids=...) and three_way_merge_nodes().

The build_merge extension and three_way_merge_nodes function are being added
in parallel by another agent; these tests are written against the documented
Stage-1 spec.
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest

from graphify.build import build, build_merge, three_way_merge_nodes
from graphify.export import to_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_extraction(
    nodes_with_attrs: list[dict],
    edges: list[dict] | None = None,
) -> dict:
    """Produce the extraction dict shape that build/build_merge expects."""
    return {
        "nodes": [dict(n) for n in nodes_with_attrs],
        "edges": [dict(e) for e in (edges or [])],
        "input_tokens": 0,
        "output_tokens": 0,
    }


def _make_graph(nodes: list[tuple[str, dict]], edges: list[tuple[str, str, dict]]) -> nx.Graph:
    """Construct a plain undirected nx.Graph from explicit node/edge tuples."""
    G = nx.Graph()
    for nid, attrs in nodes:
        G.add_node(nid, **attrs)
    for u, v, attrs in edges:
        G.add_edge(u, v, **attrs)
    return G


def _save_graph(G: nx.Graph, path: Path) -> None:
    """Persist a graph through the project's normal JSON export."""
    assert to_json(G, {}, str(path), force=True)


def _five_node_extraction() -> dict:
    """Five-node extraction with a small chain of edges n1-n2-n3-n4-n5."""
    nodes = [
        {"id": f"n{i}", "label": f"Node{i}", "file_type": "code", "source_file": "x.py"}
        for i in range(1, 6)
    ]
    edges = [
        {"source": "n1", "target": "n2", "relation": "calls",
         "confidence": "EXTRACTED", "source_file": "x.py", "weight": 1.0},
        {"source": "n2", "target": "n3", "relation": "calls",
         "confidence": "EXTRACTED", "source_file": "x.py", "weight": 1.0},
        {"source": "n3", "target": "n4", "relation": "calls",
         "confidence": "EXTRACTED", "source_file": "x.py", "weight": 1.0},
        {"source": "n4", "target": "n5", "relation": "calls",
         "confidence": "EXTRACTED", "source_file": "x.py", "weight": 1.0},
    ]
    return _make_extraction(nodes, edges)


# ---------------------------------------------------------------------------
# build_merge(prune_ids=...)
# ---------------------------------------------------------------------------


def test_build_merge_accepts_prune_ids_kwarg(tmp_path):
    """Smoke check: passing prune_ids=[] is accepted and changes nothing."""
    ext = _five_node_extraction()
    G_initial = build([ext], dedup=False)
    graph_path = tmp_path / "graph.json"
    _save_graph(G_initial, graph_path)

    G = build_merge([], graph_path, dedup=False, prune_ids=[])
    assert G.number_of_nodes() == 5


def test_build_merge_prune_ids_removes_named_nodes(tmp_path):
    """prune_ids=['n2','n4'] strips those nodes from the merged result."""
    ext = _five_node_extraction()
    G_initial = build([ext], dedup=False)
    graph_path = tmp_path / "graph.json"
    _save_graph(G_initial, graph_path)

    G = build_merge([], graph_path, dedup=False, prune_ids=["n2", "n4"])
    assert G.number_of_nodes() == 3
    assert "n2" not in G.nodes
    assert "n4" not in G.nodes
    assert {"n1", "n3", "n5"} == set(G.nodes())


def test_build_merge_prune_ids_removes_incident_edges(tmp_path):
    """Edges incident to pruned nodes must also be removed."""
    ext = _five_node_extraction()
    G_initial = build([ext], dedup=False)
    graph_path = tmp_path / "graph.json"
    _save_graph(G_initial, graph_path)

    G = build_merge([], graph_path, dedup=False, prune_ids=["n3"])
    # Edges n2-n3 and n3-n4 must be gone; n1-n2 and n4-n5 must remain.
    for u, v in G.edges():
        assert "n3" not in (u, v)
    assert G.number_of_edges() == 2


def test_build_merge_prune_ids_combinable_with_prune_sources(tmp_path):
    """Both prune_sources and prune_ids may be passed together."""
    nodes = [
        {"id": "n1", "label": "A", "file_type": "code", "source_file": "keep.py"},
        {"id": "n2", "label": "B", "file_type": "code", "source_file": "delete.py"},
        {"id": "n3", "label": "C", "file_type": "code", "source_file": "keep.py"},
        {"id": "n4", "label": "D", "file_type": "code", "source_file": "keep.py"},
    ]
    ext = _make_extraction(nodes, [])
    G_initial = build([ext], dedup=False)
    graph_path = tmp_path / "graph.json"
    _save_graph(G_initial, graph_path)

    G = build_merge(
        [],
        graph_path,
        prune_sources=["delete.py"],
        dedup=False,
        prune_ids=["n4"],
    )
    # n2 removed by prune_sources, n4 removed by prune_ids.
    assert set(G.nodes()) == {"n1", "n3"}


def test_build_merge_prune_ids_does_not_trigger_shrink_guard(tmp_path):
    """prune_ids skips the shrink-guard even when the result has fewer nodes."""
    # Initial graph has 5 nodes.
    ext = _five_node_extraction()
    G_initial = build([ext], dedup=False)
    graph_path = tmp_path / "graph.json"
    _save_graph(G_initial, graph_path)

    # Pass no new chunks but ask to prune 3 of them → shrink from 5 to 2.
    # Must NOT raise the shrink-guard ValueError.
    G = build_merge(
        [],
        graph_path,
        dedup=False,
        prune_ids=["n2", "n3", "n4"],
    )
    assert G.number_of_nodes() == 2


def test_build_merge_backwards_compat_without_prune_ids(tmp_path):
    """Calling build_merge with no prune_ids yields the v0.8.18 result."""
    ext = _five_node_extraction()
    G_initial = build([ext], dedup=False)
    graph_path = tmp_path / "graph.json"
    _save_graph(G_initial, graph_path)

    # Legacy invocation (no prune_ids).
    G_legacy = build_merge([], graph_path, dedup=False)
    # Equivalent invocation with prune_ids=None.
    G_explicit = build_merge([], graph_path, dedup=False, prune_ids=None)

    assert set(G_legacy.nodes()) == set(G_initial.nodes())
    assert set(G_explicit.nodes()) == set(G_legacy.nodes())
    assert G_legacy.number_of_edges() == G_initial.number_of_edges()


# ---------------------------------------------------------------------------
# three_way_merge_nodes()
# ---------------------------------------------------------------------------


def test_three_way_merge_fast_forward_ours_only_changed():
    """If only ours diverges from base, merged ≈ ours and no conflicts."""
    base = _make_graph([("n1", {"label": "A"})], [])
    ours = _make_graph(
        [("n1", {"label": "A"}), ("n2", {"label": "B"})],
        [("n1", "n2", {"relation": "calls"})],
    )
    theirs = _make_graph([("n1", {"label": "A"})], [])

    merged, conflicts = three_way_merge_nodes(base, ours, theirs, dedup=False)
    assert set(merged.nodes()) == set(ours.nodes())
    assert conflicts == []


def test_three_way_merge_fast_forward_theirs_only_changed():
    """If only theirs diverges from base, merged ≈ theirs and no conflicts."""
    base = _make_graph([("n1", {"label": "A"})], [])
    ours = _make_graph([("n1", {"label": "A"})], [])
    theirs = _make_graph(
        [("n1", {"label": "A"}), ("n2", {"label": "B"})],
        [("n1", "n2", {"relation": "calls"})],
    )

    merged, conflicts = three_way_merge_nodes(base, ours, theirs, dedup=False)
    assert set(merged.nodes()) == set(theirs.nodes())
    assert conflicts == []


def test_three_way_merge_non_overlapping_changes_auto_merge():
    """ours adds n5, theirs adds n6 → merged contains both, no conflicts."""
    base = _make_graph([("n1", {"label": "A"})], [])
    ours = _make_graph(
        [("n1", {"label": "A"}), ("n5", {"label": "E"})],
        [],
    )
    theirs = _make_graph(
        [("n1", {"label": "A"}), ("n6", {"label": "F"})],
        [],
    )

    merged, conflicts = three_way_merge_nodes(base, ours, theirs, dedup=False)
    assert "n5" in merged.nodes
    assert "n6" in merged.nodes
    assert conflicts == []


def test_three_way_merge_overlapping_conflict_picks_ours_default():
    """When both sides modify the same node, ours wins and a conflict is recorded."""
    base = _make_graph([("n1", {"label": "base-A"})], [])
    ours = _make_graph([("n1", {"label": "ours-A"})], [])
    theirs = _make_graph([("n1", {"label": "theirs-A"})], [])

    merged, conflicts = three_way_merge_nodes(base, ours, theirs, dedup=False)
    assert merged.nodes["n1"]["label"] == "ours-A"
    assert len(conflicts) == 1
    entry = conflicts[0]
    assert entry["id"] == "n1"
    assert entry["ours"]["label"] == "ours-A"
    assert entry["theirs"]["label"] == "theirs-A"
    # base may be the base attrs or None — both are allowed by the spec.
    assert entry["base"] is None or entry["base"].get("label") == "base-A"


def test_three_way_merge_ignores_community_attr_in_conflicts():
    """Differing community attrs alone must not register as a conflict."""
    base = _make_graph([("n1", {"label": "A", "community": 0})], [])
    ours = _make_graph([("n1", {"label": "A", "community": 1})], [])
    theirs = _make_graph([("n1", {"label": "A", "community": 2})], [])

    merged, conflicts = three_way_merge_nodes(base, ours, theirs, dedup=False)
    assert conflicts == []
    assert "n1" in merged.nodes


def test_three_way_merge_ignores_built_at_commit_attr():
    """built_at_commit differences alone are not real conflicts either."""
    base = _make_graph([("n1", {"label": "A", "built_at_commit": "aaaa"})], [])
    ours = _make_graph([("n1", {"label": "A", "built_at_commit": "bbbb"})], [])
    theirs = _make_graph([("n1", {"label": "A", "built_at_commit": "cccc"})], [])

    merged, conflicts = three_way_merge_nodes(base, ours, theirs, dedup=False)
    assert conflicts == []
    assert "n1" in merged.nodes


def test_three_way_merge_raises_when_exceeds_max_nodes():
    """A merged result above max_nodes must raise a clear ValueError."""
    base = nx.Graph()
    ours = _make_graph(
        [(f"a{i}", {"label": f"A{i}"}) for i in range(6)],
        [],
    )
    theirs = _make_graph(
        [(f"b{i}", {"label": f"B{i}"}) for i in range(6)],
        [],
    )

    with pytest.raises(ValueError):
        three_way_merge_nodes(base, ours, theirs, dedup=False, max_nodes=5)


def test_three_way_merge_returns_conflicts_in_documented_shape():
    """Each conflict dict has exactly the keys id/base/ours/theirs."""
    base = _make_graph([("n1", {"label": "base"})], [])
    ours = _make_graph([("n1", {"label": "ours"})], [])
    theirs = _make_graph([("n1", {"label": "theirs"})], [])

    _, conflicts = three_way_merge_nodes(base, ours, theirs, dedup=False)
    assert len(conflicts) == 1
    entry = conflicts[0]
    assert set(entry.keys()) == {"id", "base", "ours", "theirs"}
    assert isinstance(entry["id"], str)
    # base may be None (when not present) or a dict — both acceptable per spec.
    assert entry["base"] is None or isinstance(entry["base"], dict)
    assert isinstance(entry["ours"], dict)
    assert isinstance(entry["theirs"], dict)
