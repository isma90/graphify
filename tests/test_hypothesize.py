"""graphify hypothesize subcommand tests (Stage 3 Sprint 3 — S3.C)."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_dream.py pattern)
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


def _run_hypothesize(repo, *args, extra_env=None):
    """Run graphify hypothesize as a subprocess."""
    env = os.environ.copy()
    # Remove any real API keys so the test never touches external services
    for _k in ("ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "GEMINI_API_KEY",
               "GOOGLE_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        env.pop(_k, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "graphify", "hypothesize", *args, str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _two_nodes_two_files():
    """Minimal graph: two nodes from different files, one cross-file edge."""
    nodes = [
        {"id": "n:alpha", "label": "Alpha", "source_file": "a.py", "community": 0},
        {"id": "n:beta",  "label": "Beta",  "source_file": "b.py", "community": 1},
    ]
    edges = [
        {
            "source": "n:alpha", "target": "n:beta",
            "relation": "calls", "confidence": "AMBIGUOUS",
            "weight": 0.8, "source_file": "a.py",
        }
    ]
    return nodes, edges


def _make_report_two_connections(out_dir: Path) -> Path:
    """Write a GRAPH_REPORT.md with a Surprising Connections section (2 items)."""
    content = (
        "# Graph Report\n\n"
        "## Summary\n\nSome content.\n\n"
        "## Surprising Connections\n\n"
        "- Alpha ↔ Beta (via ambiguous dependency)\n"
        "- Alpha ↔ Gamma (via indirect reference)\n\n"
        "## Other Section\n\nMore content.\n"
    )
    report = out_dir / "GRAPH_REPORT.md"
    report.write_text(content, encoding="utf-8")
    return report


def _make_report_ten_connections(out_dir: Path) -> Path:
    """Write a GRAPH_REPORT.md with 10 surprising connections."""
    lines = ["# Graph Report\n\n## Surprising Connections\n\n"]
    for i in range(10):
        lines.append(f"- NodeA{i} ↔ NodeB{i} (via reason{i})\n")
    lines.append("\n## Other\n\nDone.\n")
    report = out_dir / "GRAPH_REPORT.md"
    report.write_text("".join(lines), encoding="utf-8")
    return report


def _graph_with_three_nodes():
    """Three nodes so the graph has nodes for Gamma as well."""
    nodes = [
        {"id": "n:alpha", "label": "Alpha", "source_file": "a.py", "community": 0},
        {"id": "n:beta",  "label": "Beta",  "source_file": "b.py", "community": 1},
        {"id": "n:gamma", "label": "Gamma", "source_file": "c.py", "community": 2},
    ]
    edges = [
        {
            "source": "n:alpha", "target": "n:beta",
            "relation": "calls", "confidence": "AMBIGUOUS",
            "weight": 0.8, "source_file": "a.py",
        },
        {
            "source": "n:alpha", "target": "n:gamma",
            "relation": "uses", "confidence": "INFERRED",
            "weight": 0.6, "source_file": "a.py",
        },
    ]
    return nodes, edges


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHypothesizeCreatesNodesFromReport:
    """test_hypothesize_creates_nodes_from_report"""

    def test_creates_hypothesis_nodes_and_grounded_in_edges(self, tmp_path):
        nodes, edges = _graph_with_three_nodes()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)
        _make_report_two_connections(repo / "graphify-out")

        result = _run_hypothesize(repo)
        assert result.returncode == 0, result.stderr

        graph_data = json.loads((repo / "graphify-out" / "graph.json").read_text())
        all_nodes = {n["id"]: n for n in graph_data["nodes"]}
        all_links = graph_data["links"]

        hyp_nodes = [n for n in graph_data["nodes"] if n.get("origin") == "hypothesis"]
        assert len(hyp_nodes) == 2, f"Expected 2 hypothesis nodes, got {len(hyp_nodes)}: {[n['id'] for n in hyp_nodes]}"

        grounded_edges = [e for e in all_links if e.get("relation") == "grounded_in"]
        assert len(grounded_edges) == 4, f"Expected 4 grounded_in edges, got {len(grounded_edges)}"


class TestHypothesizeGroundedInEdgesDirectional:
    """test_hypothesize_grounded_in_edges_directional"""

    def test_hypothesis_is_source_of_grounded_in_edges(self, tmp_path):
        nodes, edges = _two_nodes_two_files()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)
        # Single connection so we get exactly one hypothesis node
        content = (
            "# Report\n\n"
            "## Surprising Connections\n\n"
            "- Alpha ↔ Beta (via test)\n\n"
        )
        (repo / "graphify-out" / "GRAPH_REPORT.md").write_text(content)

        result = _run_hypothesize(repo)
        assert result.returncode == 0, result.stderr

        graph_data = json.loads((repo / "graphify-out" / "graph.json").read_text())
        hyp_nodes = [n for n in graph_data["nodes"] if n.get("origin") == "hypothesis"]
        assert len(hyp_nodes) == 1
        hyp_id = hyp_nodes[0]["id"]

        grounded_edges = [e for e in graph_data["links"] if e.get("relation") == "grounded_in"]
        assert len(grounded_edges) == 2
        # Hypothesis node must be the source of both grounded_in edges
        for edge in grounded_edges:
            assert edge["source"] == hyp_id, (
                f"Expected hypothesis node {hyp_id!r} as source, got {edge['source']!r}"
            )
        targets = {e["target"] for e in grounded_edges}
        assert "n:alpha" in targets
        assert "n:beta" in targets


class TestHypothesizeMaxHypothesesCaps:
    """test_hypothesize_max_hypotheses_caps"""

    def test_max_hypotheses_limits_created_nodes(self, tmp_path):
        nodes = [
            {"id": f"n:node{i}", "label": f"NodeA{i}", "source_file": f"f{i}.py", "community": i}
            for i in range(20)
        ]
        repo = _make_repo_with_graph(tmp_path, nodes, [])
        _make_report_ten_connections(repo / "graphify-out")

        result = _run_hypothesize(repo, "--max-hypotheses", "3")
        assert result.returncode == 0, result.stderr

        graph_data = json.loads((repo / "graphify-out" / "graph.json").read_text())
        hyp_nodes = [n for n in graph_data["nodes"] if n.get("origin") == "hypothesis"]
        assert len(hyp_nodes) == 3, f"Expected 3 hypothesis nodes, got {len(hyp_nodes)}"


class TestHypothesizeIdempotent:
    """test_hypothesize_idempotent"""

    def test_running_twice_does_not_duplicate_nodes(self, tmp_path):
        nodes, edges = _two_nodes_two_files()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)
        content = (
            "# Report\n\n"
            "## Surprising Connections\n\n"
            "- Alpha ↔ Beta (via test)\n\n"
        )
        (repo / "graphify-out" / "GRAPH_REPORT.md").write_text(content)

        r1 = _run_hypothesize(repo)
        assert r1.returncode == 0, r1.stderr

        r2 = _run_hypothesize(repo)
        assert r2.returncode == 0, r2.stderr

        graph_data = json.loads((repo / "graphify-out" / "graph.json").read_text())
        hyp_nodes = [n for n in graph_data["nodes"] if n.get("origin") == "hypothesis"]
        assert len(hyp_nodes) == 1, f"Expected 1 (idempotent), got {len(hyp_nodes)}"

        grounded_edges = [e for e in graph_data["links"] if e.get("relation") == "grounded_in"]
        assert len(grounded_edges) == 2, f"Expected 2 grounded_in edges, got {len(grounded_edges)}"


class TestHypothesizeWritesDreamLog:
    """test_hypothesize_writes_dream_log"""

    def test_dream_log_created_with_markdown_content(self, tmp_path, monkeypatch):
        # Redirect ~/.brain to a tmp dir so we don't pollute the real home
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))

        nodes, edges = _two_nodes_two_files()
        repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)
        content = (
            "# Report\n\n"
            "## Surprising Connections\n\n"
            "- Alpha ↔ Beta (via test)\n\n"
        )
        (repo / "graphify-out" / "GRAPH_REPORT.md").write_text(content)

        result = _run_hypothesize(repo, extra_env={"HOME": str(fake_home)})
        assert result.returncode == 0, result.stderr

        import time
        date_str = time.strftime("%Y-%m-%d")
        dream_log = fake_home / ".brain" / "raw" / "dreams" / f"{date_str}.md"
        assert dream_log.exists(), f"Dream log not found at {dream_log}"
        content_written = dream_log.read_text(encoding="utf-8")
        assert "# Hypothesis log" in content_written
        assert "H: Alpha ↔ Beta" in content_written
        assert "grounded_in" not in content_written  # log is human-readable, not edge notation
        assert "Source:" in content_written
        assert "Target:" in content_written


class TestHypothesizeMissingReportExitsCleanly:
    """test_hypothesize_missing_report_exits_cleanly"""

    def test_no_report_exits_zero_with_friendly_message(self, tmp_path):
        nodes, edges = _two_nodes_two_files()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)
        # No GRAPH_REPORT.md written

        result = _run_hypothesize(repo)
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}; stderr={result.stderr}"
        assert "GRAPH_REPORT.md not found" in result.stdout
        assert "graphify extract" in result.stdout


class TestHypothesizeFallbackToSurprisingConnectionsFunction:
    """test_hypothesize_falls_back_to_surprising_connections_function"""

    def test_unrecognizable_report_falls_back_to_analyze(self, tmp_path):
        nodes, edges = _two_nodes_two_files()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)
        # Write a GRAPH_REPORT.md with a Surprising section but no parseable bullets
        content = (
            "# Graph Report\n\n"
            "## Surprising Connections\n\n"
            "No structured data here, just plain prose about surprising things.\n\n"
        )
        (repo / "graphify-out" / "GRAPH_REPORT.md").write_text(content)

        result = _run_hypothesize(repo)
        # Should exit 0 regardless of whether fallback found connections or not
        assert result.returncode == 0, result.stderr
        # The graph should still be valid JSON
        graph_data = json.loads((repo / "graphify-out" / "graph.json").read_text())
        assert "nodes" in graph_data


class TestHypothesizeDryRunDoesNotMutate:
    """test_hypothesize_dry_run_does_not_mutate"""

    def test_dry_run_leaves_graph_and_log_unchanged(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        nodes, edges = _two_nodes_two_files()
        repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)
        content = (
            "# Report\n\n"
            "## Surprising Connections\n\n"
            "- Alpha ↔ Beta (via test)\n\n"
        )
        (repo / "graphify-out" / "GRAPH_REPORT.md").write_text(content)

        original_graph = (repo / "graphify-out" / "graph.json").read_text()

        result = _run_hypothesize(repo, "--dry-run", extra_env={"HOME": str(fake_home)})
        assert result.returncode == 0, result.stderr

        # Graph must be unchanged
        assert (repo / "graphify-out" / "graph.json").read_text() == original_graph

        # Stderr must mention the proposed hypothesis
        assert "dry-run" in result.stderr
        assert "Alpha" in result.stderr

        # Dream log must NOT exist
        import time
        date_str = time.strftime("%Y-%m-%d")
        dream_log = fake_home / ".brain" / "raw" / "dreams" / f"{date_str}.md"
        assert not dream_log.exists(), "Dream log should not exist after --dry-run"


class TestHypothesizeSplitModeRefuses:
    """test_hypothesize_split_mode_refuses"""

    def test_overlay_sentinel_triggers_split_mode_refusal(self, tmp_path):
        nodes, edges = _two_nodes_two_files()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)
        # Create a split-mode sentinel
        (repo / ".graphifyshared").write_text("")
        content = (
            "# Report\n\n"
            "## Surprising Connections\n\n"
            "- Alpha ↔ Beta (via test)\n\n"
        )
        (repo / "graphify-out" / "GRAPH_REPORT.md").write_text(content)

        result = _run_hypothesize(repo)
        assert result.returncode == 1, f"Expected exit 1 in split mode, got {result.returncode}"
        assert "split mode" in result.stderr.lower() or "split" in result.stderr


class TestHypothesizeForceSplitModeUnsafeProceeds:
    """test_hypothesize_force_split_mode_unsafe_proceeds"""

    def test_force_flag_allows_run_in_split_mode(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        nodes, edges = _two_nodes_two_files()
        repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)
        (repo / ".graphifyshared").write_text("")
        content = (
            "# Report\n\n"
            "## Surprising Connections\n\n"
            "- Alpha ↔ Beta (via test)\n\n"
        )
        (repo / "graphify-out" / "GRAPH_REPORT.md").write_text(content)

        result = _run_hypothesize(
            repo, "--force-split-mode-unsafe",
            extra_env={"HOME": str(fake_home)},
        )
        assert result.returncode == 0, f"Expected exit 0 with --force-split-mode-unsafe; stderr={result.stderr}"
        assert "--force-split-mode-unsafe" in result.stderr


class TestHypothesizeCommitsOnlyGraphJson:
    """test_hypothesize_commits_only_graph_json"""

    def test_unrelated_unstaged_change_survives(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        nodes, edges = _two_nodes_two_files()
        repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)
        content = (
            "# Report\n\n"
            "## Surprising Connections\n\n"
            "- Alpha ↔ Beta (via test)\n\n"
        )
        (repo / "graphify-out" / "GRAPH_REPORT.md").write_text(content)

        # Create an unrelated unstaged file
        unrelated = repo / "unrelated.txt"
        unrelated.write_text("should not be committed")

        result = _run_hypothesize(repo, extra_env={"HOME": str(fake_home)})
        assert result.returncode == 0, result.stderr

        # unrelated.txt must still be untracked/unstaged
        git_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo), capture_output=True, text=True,
        )
        assert "unrelated.txt" in git_status.stdout, (
            "unrelated.txt should remain untracked after hypothesize"
        )

        # The commit should exist
        git_log = subprocess.run(
            ["git", "log", "--oneline", "-3"],
            cwd=str(repo), capture_output=True, text=True,
        )
        assert "Hypothesis nodes" in git_log.stdout


class TestHypothesizeHypothesisNodeAttrs:
    """test_hypothesize_hypothesis_node_attrs"""

    def test_created_node_has_correct_attributes(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        nodes, edges = _two_nodes_two_files()
        repo = _make_repo_with_graph(tmp_path / "repo", nodes, edges)
        content = (
            "# Report\n\n"
            "## Surprising Connections\n\n"
            "- Alpha ↔ Beta (via test reason)\n\n"
        )
        (repo / "graphify-out" / "GRAPH_REPORT.md").write_text(content)

        result = _run_hypothesize(repo, extra_env={"HOME": str(fake_home)})
        assert result.returncode == 0, result.stderr

        graph_data = json.loads((repo / "graphify-out" / "graph.json").read_text())
        hyp_nodes = [n for n in graph_data["nodes"] if n.get("origin") == "hypothesis"]
        assert len(hyp_nodes) == 1
        node = hyp_nodes[0]

        assert node.get("origin") == "hypothesis"
        assert node.get("weight") == 0.4
        assert node.get("source_file") == "dream_log.md"
        assert node.get("created_at") is not None
        assert isinstance(node.get("created_at"), (int, float))
        assert node.get("label", "").startswith("H: ")
        assert "Alpha" in node["label"]
        assert "Beta" in node["label"]
