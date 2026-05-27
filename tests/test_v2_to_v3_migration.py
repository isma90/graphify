# Tests for Stage 3 schema additions in graphify/detect.py and build.py.
#
# Design note: Stage 3 introduces two separate versioning axes:
#   1. SCHEMA_VERSION (graph node/edge schema) — bumped to 3 in detect.py.
#      Tracks sleep-cycle fields: weight, last_used, uses, created_at,
#      max_observed_degree.  Applied lazily at graph-load time via build_merge().
#   2. Manifest schema_version — stays at 2.  The manifest only tracks file
#      mtimes, hashes, and origin.  Its format did not change in Stage 3.
#
# Tests 1-2 cover the manifest migration (v1→v2 remains correct, SCHEMA_VERSION
# constant is 3). Tests 3-7 cover lazy graph-level field population.
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _v1_manifest(files: dict[str, dict] | None = None) -> dict:
    """A manifest with no schema_version key (v1 legacy)."""
    m: dict = {}
    if files:
        m.update(files)
    return m


def _v2_manifest(files: dict[str, dict] | None = None) -> dict:
    """A manifest explicitly at schema_version=2."""
    m: dict = {"schema_version": 2}
    if files:
        m.update(files)
    return m


def _write_graph_json(path: Path, nodes: list[dict], edges: list[dict]) -> None:
    """Write a minimal NetworkX node-link JSON graph file (no sleep-cycle fields)."""
    payload = {
        "directed": True,
        "multigraph": False,
        "graph": {},
        "nodes": [{"id": n["id"], **{k: v for k, v in n.items() if k != "id"}} for n in nodes],
        "links": edges,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _base_node(node_id: str, **extra: Any) -> dict:
    base: dict = {"id": node_id, "label": node_id, "file_type": "concept", "source_file": "a.py"}
    base.update(extra)
    return base


def _base_edge(src: str, tgt: str, **extra: Any) -> dict:
    base: dict = {
        "source": src, "target": tgt,
        "relation": "uses", "source_file": "a.py",
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# 1. v1 manifest migrates to manifest schema_version=2; SCHEMA_VERSION=3
# ---------------------------------------------------------------------------


def test_manifest_schema_version_bumped_on_load() -> None:
    """_migrate_manifest raises manifest schema v1 → v2 (manifest format unchanged
    in Stage 3). The graph SCHEMA_VERSION constant is separately at 3."""
    from graphify.detect import _migrate_manifest, SCHEMA_VERSION

    # Graph schema version must be 3 (Stage 3 bump)
    assert SCHEMA_VERSION == 3, f"SCHEMA_VERSION must be 3, got {SCHEMA_VERSION}"

    data = _v1_manifest(
        files={"src/a.py": {"mtime": 1_700_000_000.0, "ast_hash": "abc", "semantic_hash": ""}}
    )
    result = _migrate_manifest(data)

    # Manifest schema stays at 2 — manifest format is a separate versioning axis
    assert result["schema_version"] == 2, (
        f"_migrate_manifest must set manifest schema_version=2 (manifest axis), "
        f"got {result['schema_version']}"
    )
    # v1→v2 step stamps origin on file entries
    assert result["src/a.py"]["origin"] == "private"


# ---------------------------------------------------------------------------
# 2. manifest migration is idempotent (v2 in → v2 out)
# ---------------------------------------------------------------------------


def test_manifest_v2_migration_idempotent() -> None:
    """Calling _migrate_manifest twice on a v2 manifest leaves it unchanged."""
    from graphify.detect import _migrate_manifest

    data = _v2_manifest(
        files={"src/b.py": {"mtime": 1_700_000_001.0, "ast_hash": "def", "semantic_hash": "",
                            "origin": "shared"}}
    )
    result1 = _migrate_manifest(data)
    assert result1["schema_version"] == 2

    # Call a second time — must remain 2, no mutation of file entries
    result2 = _migrate_manifest(result1)
    assert result2["schema_version"] == 2
    assert result2["src/b.py"]["origin"] == "shared"


# ---------------------------------------------------------------------------
# 3. legacy graph.json gets all 5 fields populated; created_at uses file mtime
# ---------------------------------------------------------------------------


def test_v0_9_graph_to_v0_10_lazy_field_population(tmp_path: Path) -> None:
    from graphify.build import build_merge

    graph_file = tmp_path / "graphify-out" / "graph.json"
    _write_graph_json(
        graph_file,
        nodes=[_base_node("n1"), _base_node("n2")],
        edges=[_base_edge("n1", "n2", confidence="INFERRED")],
    )
    file_mtime = graph_file.stat().st_mtime

    G = build_merge([], graph_path=graph_file)

    # All 5 fields must be present
    for nid, attrs in G.nodes(data=True):
        assert "created_at" in attrs, f"Node {nid} missing created_at"
        assert "max_observed_degree" in attrs, f"Node {nid} missing max_observed_degree"
        # created_at should be the file mtime (conservative fallback for old nodes)
        assert attrs["created_at"] == pytest.approx(file_mtime, abs=1.0), (
            f"Node {nid} created_at {attrs['created_at']!r} != file mtime {file_mtime!r}"
        )

    for u, v, attrs in G.edges(data=True):
        assert "weight" in attrs, f"Edge ({u},{v}) missing weight"
        assert "last_used" in attrs, f"Edge ({u},{v}) missing last_used"
        assert "uses" in attrs, f"Edge ({u},{v}) missing uses"


# ---------------------------------------------------------------------------
# 4. EXTRACTED edges get weight=1.0
# ---------------------------------------------------------------------------


def test_v0_9_graph_extracted_edges_get_weight_1_0(tmp_path: Path) -> None:
    from graphify.build import build_merge

    graph_file = tmp_path / "graphify-out" / "graph.json"
    _write_graph_json(
        graph_file,
        nodes=[_base_node("a"), _base_node("b")],
        edges=[_base_edge("a", "b", confidence="EXTRACTED")],
    )

    G = build_merge([], graph_path=graph_file)

    for u, v, attrs in G.edges(data=True):
        assert attrs["weight"] == pytest.approx(1.0), (
            f"EXTRACTED edge ({u},{v}) should have weight=1.0, got {attrs['weight']}"
        )


# ---------------------------------------------------------------------------
# 5. INFERRED edges get weight=0.6
# ---------------------------------------------------------------------------


def test_v0_9_graph_inferred_edges_get_weight_0_6(tmp_path: Path) -> None:
    from graphify.build import build_merge

    graph_file = tmp_path / "graphify-out" / "graph.json"
    _write_graph_json(
        graph_file,
        nodes=[_base_node("a"), _base_node("b")],
        edges=[_base_edge("a", "b", confidence="INFERRED")],
    )

    G = build_merge([], graph_path=graph_file)

    for u, v, attrs in G.edges(data=True):
        assert attrs["weight"] == pytest.approx(0.6), (
            f"INFERRED edge ({u},{v}) should have weight=0.6, got {attrs['weight']}"
        )


# ---------------------------------------------------------------------------
# 6. AMBIGUOUS edges get weight=0.3
# ---------------------------------------------------------------------------


def test_v0_9_graph_ambiguous_edges_get_weight_0_3(tmp_path: Path) -> None:
    from graphify.build import build_merge

    graph_file = tmp_path / "graphify-out" / "graph.json"
    _write_graph_json(
        graph_file,
        nodes=[_base_node("a"), _base_node("b")],
        edges=[_base_edge("a", "b", confidence="AMBIGUOUS")],
    )

    G = build_merge([], graph_path=graph_file)

    for u, v, attrs in G.edges(data=True):
        assert attrs["weight"] == pytest.approx(0.3), (
            f"AMBIGUOUS edge ({u},{v}) should have weight=0.3, got {attrs['weight']}"
        )


# ---------------------------------------------------------------------------
# 7. Missing confidence falls back to AMBIGUOUS weight (0.3)
# ---------------------------------------------------------------------------


def test_v0_9_graph_missing_confidence_falls_back_to_ambiguous_weight(tmp_path: Path) -> None:
    from graphify.build import build_merge

    graph_file = tmp_path / "graphify-out" / "graph.json"
    # Edge without confidence key — raw JSON, bypassing validate_extraction
    payload = {
        "directed": True,
        "multigraph": False,
        "graph": {},
        "nodes": [
            {"id": "a", "label": "a", "file_type": "concept", "source_file": "a.py"},
            {"id": "b", "label": "b", "file_type": "concept", "source_file": "a.py"},
        ],
        "links": [
            {"source": "a", "target": "b", "relation": "uses", "source_file": "a.py"},
        ],
    }
    graph_file.parent.mkdir(parents=True, exist_ok=True)
    graph_file.write_text(json.dumps(payload), encoding="utf-8")

    G = build_merge([], graph_path=graph_file)

    for u, v, attrs in G.edges(data=True):
        assert attrs["weight"] == pytest.approx(0.3), (
            f"Edge without confidence should fall back to weight=0.3, got {attrs['weight']}"
        )
