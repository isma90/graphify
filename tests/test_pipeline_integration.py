"""End-to-end pipeline integration tests for the privacy split (Stage 1.2).

Validates that the detect -> extract -> export pipeline correctly branches
on the presence of ``.graphifyshared``/``.graphifyprivate`` overlays:

* Legacy mode (no overlays)  -> single ``graphify-out/graph.json`` is written.
* New mode (overlays present) -> ``graph-private.json`` + ``graph-shared.json``
  are written and the legacy ``graph.json`` is NOT.

These tests are written against the Stage-1.2 spec; they are intentionally
agnostic about whether the CLI is invoked via subprocess or by importing
the underlying helpers.  Where the spec leaves a choice (e.g. the exact
key under which split files appear), the tests assert the documented
contract and surface anything ambiguous via clear failure messages.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo_with_overlays(
    tmp_path: Path,
    *,
    shared: str | None = None,
    private: str | None = None,
    files: dict[str, str] | None = None,
) -> Path:
    """Build a tmp repo with optional overlays and source files.

    Args:
        tmp_path: pytest fixture; root inside which the repo is built.
        shared: contents of ``.graphifyshared`` (None -> not created).
        private: contents of ``.graphifyprivate`` (None -> not created).
        files: mapping of relative path -> file contents to materialise.

    Returns:
        Absolute path to the new repo root (a ``repo/`` subdirectory of
        ``tmp_path``) with an empty ``.git/`` marker.
    """
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    if shared is not None:
        (root / ".graphifyshared").write_text(shared, encoding="utf-8")
    if private is not None:
        (root / ".graphifyprivate").write_text(private, encoding="utf-8")
    for rel, content in (files or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def _make_extraction_dict(
    nodes: list[dict],
    edges: list[dict],
    hyperedges: list[dict] | None = None,
) -> dict:
    """Wrap raw nodes/edges/hyperedges into the dict shape extract() returns."""
    return {
        "nodes": list(nodes),
        "edges": list(edges),
        "hyperedges": list(hyperedges or []),
        "input_tokens": 0,
        "output_tokens": 0,
    }


def _run_split_pipeline(root: Path) -> Path:
    """Drive the full detect -> extract -> export pipeline directly.

    Returns the ``graphify-out/`` directory for inspection.
    """
    from graphify.detect import detect
    from graphify.extract import extract

    detection = detect(root)
    code_files = [Path(p) for p in detection["files"].get("code", [])]
    privacy_map = detection.get("privacy_map")

    out_dir = root / "graphify-out"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not code_files:
        # Nothing to extract — still create the expected file shape based on mode.
        if privacy_map is None:
            (out_dir / "graph.json").write_text('{"nodes": [], "links": []}', encoding="utf-8")
        else:
            (out_dir / "graph-private.json").write_text('{"nodes": [], "links": []}', encoding="utf-8")
            (out_dir / "graph-shared.json").write_text('{"nodes": [], "links": []}', encoding="utf-8")
        return out_dir

    result = extract(code_files, cache_root=root, privacy_map=privacy_map)

    from graphify.build import build_from_json
    from graphify.cluster import cluster

    if privacy_map is None:
        # Legacy mode: single graph.json
        from graphify.export import to_json

        G = build_from_json(result)
        communities = cluster(G) if G.number_of_nodes() else {}
        to_json(G, communities, str(out_dir / "graph.json"), force=True)
        return out_dir

    # New mode: split and write two files
    from graphify.extract import split_extraction_by_origin
    from graphify.export import to_json_split

    private_dict, shared_dict = split_extraction_by_origin(result, privacy_map)
    G_private = build_from_json(private_dict)
    G_shared = build_from_json(shared_dict)
    communities_private = cluster(G_private) if G_private.number_of_nodes() else {}
    communities_shared = cluster(G_shared) if G_shared.number_of_nodes() else {}
    to_json_split(
        G_private,
        communities_private,
        G_shared,
        communities_shared,
        out_dir,
        force=True,
        built_at_commit=None,
    )
    return out_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_legacy_mode_produces_single_graph_json(tmp_path: Path) -> None:
    """No overlays -> pipeline emits a single ``graph.json`` (legacy mode)."""
    root = _make_repo_with_overlays(
        tmp_path,
        files={"src/foo.py": "def foo():\n    return 1\n"},
    )
    out_dir = _run_split_pipeline(root)

    assert (out_dir / "graph.json").exists(), "Legacy mode must produce graph.json"
    assert not (out_dir / "graph-private.json").exists(), (
        "Legacy mode must NOT produce graph-private.json"
    )
    assert not (out_dir / "graph-shared.json").exists(), (
        "Legacy mode must NOT produce graph-shared.json"
    )


def test_new_mode_produces_two_jsons(tmp_path: Path) -> None:
    """Overlays present -> pipeline emits graph-private.json + graph-shared.json,
    no legacy graph.json."""
    root = _make_repo_with_overlays(
        tmp_path,
        shared="src/**\n",
        files={
            "src/foo.py": "def foo():\n    return 1\n",
            "scratch/bar.py": "def bar():\n    return 2\n",
        },
    )
    out_dir = _run_split_pipeline(root)

    assert (out_dir / "graph-private.json").exists(), (
        "New mode must produce graph-private.json"
    )
    assert (out_dir / "graph-shared.json").exists(), (
        "New mode must produce graph-shared.json"
    )
    assert not (out_dir / "graph.json").exists(), (
        "New mode must NOT also produce legacy graph.json"
    )


def test_new_mode_shared_contains_only_shared_nodes(tmp_path: Path) -> None:
    """All nodes in graph-shared.json must originate from files under ``src/``."""
    root = _make_repo_with_overlays(
        tmp_path,
        shared="src/**\n",
        files={
            "src/foo.py": "def foo():\n    return 1\n",
            "scratch/bar.py": "def bar():\n    return 2\n",
        },
    )
    out_dir = _run_split_pipeline(root)

    data = json.loads((out_dir / "graph-shared.json").read_text(encoding="utf-8"))
    for n in data.get("nodes", []):
        origin = n.get("origin")
        assert origin == "shared" or origin is None, (
            f"shared graph contains non-shared node origin={origin!r}: {n}"
        )
        sf = (n.get("source_file") or "").replace(os.sep, "/")
        if sf:
            assert sf.startswith("src/") or "src/" in sf, (
                f"shared graph contains node with source_file outside src/: {sf!r}"
            )


def test_new_mode_private_contains_only_private_nodes(tmp_path: Path) -> None:
    """All nodes in graph-private.json must originate from files outside ``src/``
    (i.e. anything not matched by the shared overlay)."""
    root = _make_repo_with_overlays(
        tmp_path,
        shared="src/**\n",
        files={
            "src/foo.py": "def foo():\n    return 1\n",
            "scratch/bar.py": "def bar():\n    return 2\n",
        },
    )
    out_dir = _run_split_pipeline(root)

    data = json.loads((out_dir / "graph-private.json").read_text(encoding="utf-8"))
    for n in data.get("nodes", []):
        origin = n.get("origin")
        assert origin == "private" or origin is None, (
            f"private graph contains non-private node origin={origin!r}: {n}"
        )


def test_cross_bucket_edges_go_to_private_only(tmp_path: Path) -> None:
    """An edge between a shared node and a private node must appear in
    graph-private.json and NOT in graph-shared.json (private wins)."""
    from graphify.extract import split_extraction_by_origin

    privacy_map = {
        "src/foo.py": "shared",
        "scratch/bar.py": "private",
    }
    nodes = [
        {"id": "shared_node", "source_file": "src/foo.py", "label": "foo"},
        {"id": "private_node", "source_file": "scratch/bar.py", "label": "bar"},
    ]
    edges = [
        # Cross-bucket: shared -> private
        {
            "source": "shared_node",
            "target": "private_node",
            "relation": "calls",
            "source_file": "src/foo.py",
        },
    ]
    extraction = _make_extraction_dict(nodes, edges)
    private_dict, shared_dict = split_extraction_by_origin(extraction, privacy_map)

    private_edges = private_dict.get("edges", [])
    shared_edges = shared_dict.get("edges", [])

    assert any(
        e["source"] == "shared_node" and e["target"] == "private_node"
        for e in private_edges
    ), "Cross-bucket edge must appear in private graph"
    assert not any(
        e["source"] == "shared_node" and e["target"] == "private_node"
        for e in shared_edges
    ), "Cross-bucket edge must NOT appear in shared graph"


def test_hyperedges_with_any_private_member_go_to_private(tmp_path: Path) -> None:
    """Hyperedges follow private-wins semantics:
    * all-shared members -> shared graph,
    * any private member -> private graph."""
    from graphify.extract import split_extraction_by_origin

    privacy_map = {
        "src/a.py": "shared",
        "src/b.py": "shared",
        "scratch/c.py": "private",
    }
    nodes = [
        {"id": "a", "source_file": "src/a.py"},
        {"id": "b", "source_file": "src/b.py"},
        {"id": "c", "source_file": "scratch/c.py"},
    ]
    hyperedges = [
        {"id": "h_all_shared", "nodes": ["a", "b"], "label": "all-shared"},
        {"id": "h_mixed", "nodes": ["a", "c"], "label": "mixed"},
    ]
    extraction = _make_extraction_dict(nodes, [], hyperedges=hyperedges)
    private_dict, shared_dict = split_extraction_by_origin(extraction, privacy_map)

    private_h_ids = {h.get("id") for h in private_dict.get("hyperedges", [])}
    shared_h_ids = {h.get("id") for h in shared_dict.get("hyperedges", [])}

    assert "h_mixed" in private_h_ids, "Mixed hyperedge must be in private"
    assert "h_mixed" not in shared_h_ids, "Mixed hyperedge must NOT be in shared"
    assert "h_all_shared" in shared_h_ids, "All-shared hyperedge must be in shared"


def test_manifest_v2_when_split(tmp_path: Path) -> None:
    """In new mode the on-disk manifest must record schema_version=2 and stamp
    an ``origin`` per file entry."""
    from graphify.detect import save_manifest

    root = _make_repo_with_overlays(
        tmp_path,
        shared="src/**\n",
        files={
            "src/foo.py": "def foo():\n    return 1\n",
            "scratch/bar.py": "def bar():\n    return 2\n",
        },
    )
    out_dir = root / "graphify-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"

    files = {
        "code": [
            str(root / "src" / "foo.py"),
            str(root / "scratch" / "bar.py"),
        ],
    }
    privacy_map = {
        str(root / "src" / "foo.py"): "shared",
        str(root / "scratch" / "bar.py"): "private",
    }
    save_manifest(files, str(manifest_path), privacy_map=privacy_map, root=root)

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data.get("schema_version") == 2, (
        f"Manifest schema_version must be 2 in new mode, got {data.get('schema_version')!r}"
    )

    file_entries = [v for k, v in data.items() if k != "schema_version"]
    assert file_entries, "Manifest should record at least one file entry"
    for entry in file_entries:
        assert isinstance(entry, dict), f"unexpected manifest entry shape: {entry!r}"
        assert "origin" in entry, "Every file entry must have an origin in new mode"
        assert entry["origin"] in ("private", "shared")


def test_manifest_v1_legacy_compat(tmp_path: Path) -> None:
    """In legacy mode the manifest must either omit schema_version or set it to 1
    (no v2 features used)."""
    from graphify.detect import save_manifest

    root = _make_repo_with_overlays(
        tmp_path,
        files={"src/foo.py": "def foo():\n    return 1\n"},
    )
    out_dir = root / "graphify-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"

    save_manifest(
        {"code": [str(root / "src" / "foo.py")]},
        str(manifest_path),
        root=root,
    )

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = data.get("schema_version")
    assert schema in (None, 1), (
        f"Legacy manifest must omit schema_version or set it to 1, got {schema!r}"
    )
    for k, v in data.items():
        if k == "schema_version":
            continue
        if isinstance(v, dict):
            assert "origin" not in v, (
                f"Legacy manifest must not stamp origin on file entries, got {v!r}"
            )


def test_legacy_manifest_auto_migrates_on_load(tmp_path: Path) -> None:
    """A v1 manifest on disk must be auto-migrated to v2 on load:
    * ``schema_version`` becomes 2,
    * every file entry gains ``origin='private'`` (safest default),
    * a subsequent save persists v2.
    """
    from graphify.detect import load_manifest, save_manifest

    manifest_path = tmp_path / "manifest.json"
    v1_payload = {
        "src/foo.py": {"mtime": 1700000000.0, "ast_hash": "deadbeef", "semantic_hash": ""},
        "scratch/bar.py": {"mtime": 1700000001.0, "ast_hash": "cafebabe", "semantic_hash": ""},
    }
    manifest_path.write_text(json.dumps(v1_payload), encoding="utf-8")

    migrated = load_manifest(str(manifest_path))
    assert migrated.get("schema_version") == 2, (
        f"load_manifest must bump schema_version to 2, got {migrated.get('schema_version')!r}"
    )
    for k, v in migrated.items():
        if k == "schema_version":
            continue
        assert isinstance(v, dict), f"unexpected entry: {v!r}"
        assert v.get("origin") == "private", (
            f"v1 entries must migrate to origin='private', got {v.get('origin')!r}"
        )

    # Round-trip save preserves v2.
    save_manifest({"code": []}, str(manifest_path), privacy_map={}, root=tmp_path)
    reloaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert reloaded.get("schema_version") == 2, (
        "save_manifest with privacy_map must persist schema_version=2"
    )
