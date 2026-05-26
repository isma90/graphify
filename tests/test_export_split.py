"""Tests for ``graphify.export.to_json_split`` and the extended
``backup_if_protected`` behaviour around the split graph files.

Stage 1.2 spec:
* ``to_json_split`` writes ``graph-private.json`` and ``graph-shared.json``
  into ``out_dir`` and returns a ``(private_written, shared_written)`` tuple.
* In split mode the legacy ``graph.json`` must NOT be written by the
  splitter.
* ``backup_if_protected`` must snapshot both split files as well as the
  legacy ``graph.json`` when present (covering the transition period).
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph(nodes: list[tuple[str, dict]], edges: list[tuple[str, str, dict]]) -> nx.Graph:
    G = nx.Graph()
    for nid, data in nodes:
        G.add_node(nid, **data)
    for u, v, data in edges:
        G.add_edge(u, v, **data)
    return G


def _trivial_graphs() -> tuple[nx.Graph, dict, nx.Graph, dict]:
    G_private = _make_graph(
        [("p1", {"label": "p1"}), ("p2", {"label": "p2"})],
        [("p1", "p2", {"relation": "calls", "confidence": "EXTRACTED"})],
    )
    G_shared = _make_graph(
        [("s1", {"label": "s1"}), ("s2", {"label": "s2"})],
        [("s1", "s2", {"relation": "calls", "confidence": "EXTRACTED"})],
    )
    return G_private, {0: ["p1", "p2"]}, G_shared, {0: ["s1", "s2"]}


# ---------------------------------------------------------------------------
# to_json_split
# ---------------------------------------------------------------------------


def test_to_json_split_writes_two_files(tmp_path: Path) -> None:
    """``to_json_split`` must create graph-private.json and graph-shared.json
    in the supplied out_dir."""
    from graphify.export import to_json_split

    G_private, communities_private, G_shared, communities_shared = _trivial_graphs()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    to_json_split(
        G_private,
        communities_private,
        G_shared,
        communities_shared,
        out_dir,
        force=True,
        built_at_commit="deadbeef",
    )

    assert (out_dir / "graph-private.json").exists(), (
        "graph-private.json must be written by to_json_split"
    )
    assert (out_dir / "graph-shared.json").exists(), (
        "graph-shared.json must be written by to_json_split"
    )

    private_data = json.loads((out_dir / "graph-private.json").read_text(encoding="utf-8"))
    shared_data = json.loads((out_dir / "graph-shared.json").read_text(encoding="utf-8"))
    assert "nodes" in private_data and "nodes" in shared_data
    assert len(private_data["nodes"]) >= 2
    assert len(shared_data["nodes"]) >= 2


def test_to_json_split_does_not_write_legacy_graph_json(tmp_path: Path) -> None:
    """In split mode, ``to_json_split`` must not also write graph.json."""
    from graphify.export import to_json_split

    G_private, communities_private, G_shared, communities_shared = _trivial_graphs()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    to_json_split(
        G_private,
        communities_private,
        G_shared,
        communities_shared,
        out_dir,
        force=True,
        built_at_commit=None,
    )

    assert not (out_dir / "graph.json").exists(), (
        "to_json_split must NOT write legacy graph.json"
    )


# ---------------------------------------------------------------------------
# backup_if_protected — split files
# ---------------------------------------------------------------------------


def test_backup_if_protected_includes_both_split_files(tmp_path: Path) -> None:
    """When both split files coexist (split mode), backup_if_protected must
    snapshot both into the dated backup folder."""
    from graphify.export import backup_if_protected

    # graph.json acts as the trigger (existing implementation gates on it).
    # In split mode, ``graphify extract`` should still write or leave a
    # placeholder graph.json to drive the backup trigger; if the spec is
    # that graph-private/shared also drives backup, the assertion below
    # still checks the split files are copied.
    (tmp_path / "graph.json").write_text('{"nodes":[],"links":[]}', encoding="utf-8")
    (tmp_path / "graph-private.json").write_text(
        '{"nodes":[{"id":"p"}],"links":[]}', encoding="utf-8"
    )
    (tmp_path / "graph-shared.json").write_text(
        '{"nodes":[{"id":"s"}],"links":[]}', encoding="utf-8"
    )
    (tmp_path / ".graphify_semantic_marker").write_text("{}", encoding="utf-8")

    backup_dir = backup_if_protected(tmp_path)
    assert backup_dir is not None, "Backup must run when semantic marker is present"
    assert (backup_dir / "graph-private.json").exists(), (
        "backup must include graph-private.json"
    )
    assert (backup_dir / "graph-shared.json").exists(), (
        "backup must include graph-shared.json"
    )


def test_backup_preserves_legacy_graph_json_when_present(tmp_path: Path) -> None:
    """During the legacy -> split transition all three files may coexist;
    backup must include the legacy graph.json alongside the split files."""
    from graphify.export import backup_if_protected

    (tmp_path / "graph.json").write_text(
        '{"nodes":[{"id":"legacy"}],"links":[]}', encoding="utf-8"
    )
    (tmp_path / "graph-private.json").write_text(
        '{"nodes":[{"id":"p"}],"links":[]}', encoding="utf-8"
    )
    (tmp_path / "graph-shared.json").write_text(
        '{"nodes":[{"id":"s"}],"links":[]}', encoding="utf-8"
    )
    (tmp_path / ".graphify_semantic_marker").write_text("{}", encoding="utf-8")

    backup_dir = backup_if_protected(tmp_path)
    assert backup_dir is not None
    assert (backup_dir / "graph.json").exists(), (
        "backup must preserve legacy graph.json when present"
    )
    assert (backup_dir / "graph-private.json").exists()
    assert (backup_dir / "graph-shared.json").exists()
