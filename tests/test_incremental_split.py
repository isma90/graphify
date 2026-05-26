"""Unit tests for ``graphify.extract.compute_bucket_migration_prune_ids``.

This file covers the pure helper function introduced in Stage 2 / D6a.
The second section (after the dashed separator) covers the D6b wiring:
integration tests that exercise the incremental rebuild path through
``graphify extract`` in ``__main__.py``.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import networkx as nx
import pytest

from graphify.extract import compute_bucket_migration_prune_ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph(nodes: list[dict]) -> nx.Graph:
    """Build a small nx.Graph from a list of node dicts.

    Each dict must have an ``"id"`` key; all other keys become node
    attributes.
    """
    g: nx.Graph = nx.Graph()
    for node in nodes:
        node_id = node["id"]
        attrs = {k: v for k, v in node.items() if k != "id"}
        g.add_node(node_id, **attrs)
    return g


def _make_manifest(files: dict[str, str]) -> dict:
    """Build a minimal schema-v2 manifest.

    Args:
        files: Mapping of relpath -> origin string.
    """
    return {
        "schema_version": 2,
        "files": {path: {"origin": origin} for path, origin in files.items()},
    }


# ---------------------------------------------------------------------------
# compute_bucket_migration_prune_ids unit tests
# ---------------------------------------------------------------------------


class TestComputeBucketMigrationPruneIds:
    """Unit tests for compute_bucket_migration_prune_ids."""

    # ------------------------------------------------------------------
    # Test 1
    # ------------------------------------------------------------------

    def test_empty_graph_returns_empty_list(self) -> None:
        """An empty existing_graph must yield an empty prune list."""
        g = nx.Graph()
        result = compute_bucket_migration_prune_ids({}, {}, g)
        assert result == []

    # ------------------------------------------------------------------
    # Test 2
    # ------------------------------------------------------------------

    def test_no_migrations_no_prunes(self) -> None:
        """All nodes remain in the same bucket → nothing to prune."""
        g = _make_graph([
            {"id": "n1", "source_file": "src/a.py", "origin": "private"},
            {"id": "n2", "source_file": "src/b.py", "origin": "private"},
            {"id": "n3", "source_file": "src/c.py", "origin": "private"},
        ])
        new_map = {"src/a.py": "private", "src/b.py": "private", "src/c.py": "private"}
        result = compute_bucket_migration_prune_ids({}, new_map, g)
        assert result == []

    # ------------------------------------------------------------------
    # Test 3
    # ------------------------------------------------------------------

    def test_deleted_file_prunes_its_nodes(self) -> None:
        """Nodes whose source_file is absent from new_privacy_map are pruned."""
        g = _make_graph([
            {"id": "n1", "source_file": "a.py", "origin": "private"},
            {"id": "n2", "source_file": "a.py", "origin": "private"},
        ])
        # a.py is NOT in new_map — file was deleted.
        result = compute_bucket_migration_prune_ids({}, {}, g)
        assert sorted(result) == ["n1", "n2"]

    # ------------------------------------------------------------------
    # Test 4
    # ------------------------------------------------------------------

    def test_migrated_file_prunes_old_bucket_nodes(self) -> None:
        """Nodes whose file moved private→shared are pruned from the private graph."""
        g = _make_graph([
            {"id": "n1", "source_file": "a.py", "origin": "private"},
            {"id": "n2", "source_file": "a.py", "origin": "private"},
        ])
        new_map = {"a.py": "shared"}  # file moved to shared
        result = compute_bucket_migration_prune_ids({}, new_map, g)
        assert sorted(result) == ["n1", "n2"]

    # ------------------------------------------------------------------
    # Test 5
    # ------------------------------------------------------------------

    def test_mixed_keep_delete_migrate(self) -> None:
        """Keep, delete and migrate cases handled correctly in one graph."""
        g = _make_graph([
            {"id": "keep1",    "source_file": "a.py", "origin": "private"},  # kept
            {"id": "deleted1", "source_file": "b.py", "origin": "private"},  # deleted
            {"id": "moved1",   "source_file": "c.py", "origin": "private"},  # migrated
            {"id": "moved2",   "source_file": "c.py", "origin": "private"},  # migrated
        ])
        new_map = {
            "a.py": "private",  # stays private — keep
            # b.py absent — deleted
            "c.py": "shared",   # moved to shared — prune from private graph
        }
        result = compute_bucket_migration_prune_ids({}, new_map, g)
        assert result == sorted(["deleted1", "moved1", "moved2"])

    # ------------------------------------------------------------------
    # Test 6
    # ------------------------------------------------------------------

    def test_synthetic_nodes_never_pruned(self) -> None:
        """Nodes without source_file (synthetic) are never included in prune list."""
        g = _make_graph([
            {"id": "synth1"},               # no source_file at all
            {"id": "synth2", "source_file": None},  # explicit None treated as falsy
        ])
        # new_map is empty — if source_file were checked, both would be pruned.
        result = compute_bucket_migration_prune_ids({}, {}, g)
        assert result == []

    # ------------------------------------------------------------------
    # Test 7
    # ------------------------------------------------------------------

    def test_legacy_v1_node_uses_manifest_origin(self) -> None:
        """Legacy nodes (no origin attr) fall back to old_manifest for current bucket.

        Sub-case A: manifest says private, new_map says shared → pruned.
        Sub-case B: manifest missing entry → default private, new_map says private → kept.
        """
        # Sub-case A: manifest has a.py as private, new_map promotes it to shared.
        manifest_a = _make_manifest({"a.py": "private"})
        g_a = _make_graph([
            {"id": "legacy1", "source_file": "a.py"},  # no 'origin' attr
        ])
        result_a = compute_bucket_migration_prune_ids(manifest_a, {"a.py": "shared"}, g_a)
        assert result_a == ["legacy1"], (
            "Node with no origin attr, manifest=private, new=shared → should be pruned"
        )

        # Sub-case B: manifest has no entry for b.py → default private.
        # new_map also says private → NOT pruned.
        manifest_b: dict = {"schema_version": 2, "files": {}}
        g_b = _make_graph([
            {"id": "legacy2", "source_file": "b.py"},  # no 'origin' attr
        ])
        result_b = compute_bucket_migration_prune_ids(manifest_b, {"b.py": "private"}, g_b)
        assert result_b == [], (
            "Node with no origin attr, manifest missing (default private), new=private → kept"
        )

    # ------------------------------------------------------------------
    # Test 8
    # ------------------------------------------------------------------

    def test_return_value_sorted_and_unique(self) -> None:
        """Result is lexicographically sorted and contains no duplicate IDs."""
        g = _make_graph([
            {"id": "z_node", "source_file": "z.py", "origin": "private"},
            {"id": "a_node", "source_file": "a.py", "origin": "private"},
            {"id": "m_node", "source_file": "m.py", "origin": "private"},
        ])
        # All files deleted — all three should be pruned.
        result = compute_bucket_migration_prune_ids({}, {}, g)
        assert result == sorted(result), "Result must be sorted"
        assert len(result) == len(set(result)), "Result must contain no duplicates"
        assert set(result) == {"a_node", "m_node", "z_node"}

    # ------------------------------------------------------------------
    # Test 9
    # ------------------------------------------------------------------

    def test_does_not_mutate_inputs(self) -> None:
        """The function must not mutate old_manifest, new_privacy_map, or existing_graph."""
        manifest = _make_manifest({"a.py": "private"})
        new_map = {"a.py": "shared"}
        g = _make_graph([
            {"id": "n1", "source_file": "a.py", "origin": "private"},
        ])

        # Deep-copy snapshots before the call.
        manifest_before = copy.deepcopy(manifest)
        map_before = copy.deepcopy(new_map)
        nodes_before = list(g.nodes(data=True))
        edges_before = list(g.edges())

        compute_bucket_migration_prune_ids(manifest, new_map, g)

        assert manifest == manifest_before, "old_manifest was mutated"
        assert new_map == map_before, "new_privacy_map was mutated"
        assert list(g.nodes(data=True)) == nodes_before, "existing_graph nodes were mutated"
        assert list(g.edges()) == edges_before, "existing_graph edges were mutated"

    # ------------------------------------------------------------------
    # Test 10
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "node_origin, new_bucket, should_prune",
        [
            # private graph: file moves to shared → prune
            ("private", "shared", True),
            # private graph: file stays private → keep
            ("private", "private", False),
            # shared graph: file moves to private → prune
            ("shared", "private", True),
            # shared graph: file stays shared → keep
            ("shared", "shared", False),
        ],
    )
    def test_runs_for_either_bucket_symmetrically(
        self,
        node_origin: str,
        new_bucket: str,
        should_prune: bool,
    ) -> None:
        """The same logic applies regardless of which bucket the graph represents."""
        g = _make_graph([
            {"id": "node1", "source_file": "f.py", "origin": node_origin},
        ])
        new_map = {"f.py": new_bucket}
        result = compute_bucket_migration_prune_ids({}, new_map, g)
        if should_prune:
            assert result == ["node1"], (
                f"Expected pruned (origin={node_origin}, new={new_bucket})"
            )
        else:
            assert result == [], (
                f"Expected kept (origin={node_origin}, new={new_bucket})"
            )


# ---------------------------------------------------------------------------
# __main__.py incremental wiring integration tests
# ---------------------------------------------------------------------------
# These tests call ``graphify extract`` via subprocess and verify that the
# split-mode incremental path (D6b) is taken / skipped as expected.
#
# All tests are skipped when the graphify CLI is not importable as a module
# (``python -m graphify`` must work).
# ---------------------------------------------------------------------------

_CLI = [sys.executable, "-m", "graphify"]
_SKIP_REASON = "graphify CLI not importable as a module"


def _cli_available() -> bool:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "graphify", "--help"],
            capture_output=True, timeout=15,
        )
        return r.returncode in (0, 1, 2)  # any non-crash response is fine
    except Exception:
        return False


_CLI_AVAILABLE = _cli_available()


def _git_init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "t@t.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )


def _run_extract(
    root: Path,
    *,
    extra_env: dict | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run ``graphify extract <root>`` and return the completed process."""
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        # Disable LLM extraction so tests don't need API keys.
        # We rely on AST-only extraction (code files only).
        "GRAPHIFY_NO_LLM": "1",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [*_CLI, "extract", str(root)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _make_py_file(root: Path, rel: str, content: str = "def hello(): pass\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _write_shared_overlay(root: Path, patterns: list[str]) -> None:
    (root / ".graphifyshared").write_text("\n".join(patterns) + "\n", encoding="utf-8")
    (root / ".graphifyprivate").write_text("", encoding="utf-8")


@pytest.mark.skipif(not _CLI_AVAILABLE, reason=_SKIP_REASON)
class TestIncrementalSplitWiring:
    """Integration tests for D6b: incremental merge in split-mode extract."""

    def test_incremental_path_taken_when_graphs_exist(self, tmp_path: Path) -> None:
        """Second extract uses incremental merge and logs the expected line."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        _make_py_file(repo, "src/api.py")
        _write_shared_overlay(repo, ["src/**"])

        # First extract — creates split files
        r1 = _run_extract(repo)
        # First extract may fail if no LLM is available and there are no
        # code-only nodes — that's acceptable; we only need the files created.
        priv = repo / "graphify-out" / "graph-private.json"
        shar = repo / "graphify-out" / "graph-shared.json"
        if not (priv.exists() and shar.exists()):
            pytest.skip("first extract did not produce split files (no backend available)")

        # Modify a source file to trigger re-extraction
        _make_py_file(repo, "src/api.py", "def hello(): pass\ndef world(): pass\n")

        r2 = _run_extract(repo)
        combined_out = r2.stdout + r2.stderr
        assert "incremental merge against existing graph-private.json" in combined_out, (
            f"Expected incremental log line in output:\nstdout={r2.stdout}\nstderr={r2.stderr}"
        )

    def test_full_rebuild_when_GRAPHIFY_FORCE_set(self, tmp_path: Path) -> None:
        """GRAPHIFY_FORCE=1 skips the incremental path."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        _make_py_file(repo, "src/api.py")
        _write_shared_overlay(repo, ["src/**"])

        r1 = _run_extract(repo)
        priv = repo / "graphify-out" / "graph-private.json"
        shar = repo / "graphify-out" / "graph-shared.json"
        if not (priv.exists() and shar.exists()):
            pytest.skip("first extract did not produce split files (no backend available)")

        _make_py_file(repo, "src/api.py", "def hello(): pass\ndef world(): pass\n")

        r2 = _run_extract(repo, extra_env={"GRAPHIFY_FORCE": "1"})
        combined_out = r2.stdout + r2.stderr
        assert "incremental merge against existing graph-private.json" not in combined_out, (
            "GRAPHIFY_FORCE=1 should skip incremental path; got:\n"
            f"stdout={r2.stdout}\nstderr={r2.stderr}"
        )

    def test_file_moves_buckets_prunes_old_origin(self, tmp_path: Path) -> None:
        """A file moved private→shared should disappear from graph-private.json."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        _make_py_file(repo, "src/a.py", "def pub(): pass\n")
        _make_py_file(repo, "scratch/b.py", "def priv(): pass\n")

        # First extract: a.py is private, scratch/b.py is also private (no shared overlay)
        (repo / ".graphifyshared").write_text("", encoding="utf-8")
        (repo / ".graphifyprivate").write_text("", encoding="utf-8")
        r1 = _run_extract(repo)
        priv = repo / "graphify-out" / "graph-private.json"
        shar = repo / "graphify-out" / "graph-shared.json"
        if not (priv.exists() and shar.exists()):
            pytest.skip("first extract did not produce split files (no backend available)")

        priv_data_before = json.loads(priv.read_text())
        priv_nodes_before = {n["id"] for n in priv_data_before.get("nodes", [])}

        # Move a.py to shared bucket
        _write_shared_overlay(repo, ["src/**"])
        r2 = _run_extract(repo)

        priv_data_after = json.loads(priv.read_text())
        shar_data_after = json.loads(shar.read_text())
        priv_sources_after = {
            n.get("source_file", "") for n in priv_data_after.get("nodes", [])
        }
        shar_sources_after = {
            n.get("source_file", "") for n in shar_data_after.get("nodes", [])
        }

        # a.py nodes should not be in private anymore
        a_py_str = str(repo / "src" / "a.py")
        rel_a_py = "src/a.py"
        assert not any(
            rel_a_py in (s or "") or a_py_str in (s or "") for s in priv_sources_after
        ), (
            f"a.py nodes should be pruned from private after moving to shared; "
            f"private sources={priv_sources_after}"
        )

    def test_file_deleted_prunes_nodes(self, tmp_path: Path) -> None:
        """Deleted files have their nodes removed from the appropriate bucket."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init_repo(repo)
        _make_py_file(repo, "src/a.py", "def aa(): pass\n")
        _make_py_file(repo, "src/b.py", "def bb(): pass\n")
        _write_shared_overlay(repo, ["src/**"])

        r1 = _run_extract(repo)
        shar = repo / "graphify-out" / "graph-shared.json"
        if not shar.exists():
            pytest.skip("first extract did not produce split files (no backend available)")

        nodes_before = json.loads(shar.read_text()).get("nodes", [])
        if len(nodes_before) == 0:
            pytest.skip("no nodes extracted — cannot verify deletion pruning")

        # Delete a.py
        (repo / "src" / "a.py").unlink()

        r2 = _run_extract(repo)
        nodes_after = json.loads(shar.read_text()).get("nodes", [])
        a_py_str = str(repo / "src" / "a.py")
        rel_a = "src/a.py"
        remaining_a = [
            n for n in nodes_after
            if rel_a in (n.get("source_file") or "")
            or a_py_str in (n.get("source_file") or "")
        ]
        assert remaining_a == [], (
            f"Nodes from deleted a.py should be pruned; remaining: {remaining_a}"
        )
