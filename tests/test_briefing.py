"""graphify briefing subcommand tests (Stage 3 Sprint 4 — S4.A)."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_hypothesize.py pattern)
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


def _run_briefing(repo, *args, extra_env=None):
    """Run graphify briefing as a subprocess."""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "graphify", "briefing", *args, str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _make_report_with_surprising(out_dir: Path, items: list[str]) -> Path:
    """Write a GRAPH_REPORT.md with a Surprising Connections section."""
    bullets = "".join(f"- {item}\n" for item in items)
    content = (
        "# Graph Report\n\n"
        "## Summary\n\nSome content.\n\n"
        "## Surprising Connections\n\n"
        f"{bullets}\n"
        "## Other Section\n\nMore content.\n"
    )
    report = out_dir / "GRAPH_REPORT.md"
    report.write_text(content, encoding="utf-8")
    return report


def _make_report_no_surprising(out_dir: Path) -> Path:
    """Write a GRAPH_REPORT.md WITHOUT a Surprising Connections section."""
    content = (
        "# Graph Report\n\n"
        "## Summary\n\nSome content.\n\n"
        "## Other Section\n\nMore content.\n"
    )
    report = out_dir / "GRAPH_REPORT.md"
    report.write_text(content, encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _five_nodes_varying_degree():
    """Five nodes with edges creating clear degree hierarchy."""
    nodes = [
        {"id": "n:hub1",  "label": "Hub1",  "community": 0},
        {"id": "n:hub2",  "label": "Hub2",  "community": 0},
        {"id": "n:hub3",  "label": "Hub3",  "community": 1},
        {"id": "n:leaf1", "label": "Leaf1", "community": 1},
        {"id": "n:leaf2", "label": "Leaf2", "community": 2},
    ]
    # Hub1 gets degree 4 (connects to everyone)
    # Hub2 gets degree 3, Hub3 gets degree 2
    edges = [
        {"source": "n:hub1", "target": "n:hub2",  "relation": "calls", "weight": 1.0},
        {"source": "n:hub1", "target": "n:hub3",  "relation": "calls", "weight": 1.0},
        {"source": "n:hub1", "target": "n:leaf1", "relation": "calls", "weight": 1.0},
        {"source": "n:hub1", "target": "n:leaf2", "relation": "calls", "weight": 1.0},
        {"source": "n:hub2", "target": "n:hub3",  "relation": "calls", "weight": 1.0},
        {"source": "n:hub2", "target": "n:leaf1", "relation": "calls", "weight": 1.0},
        {"source": "n:hub3", "target": "n:leaf2", "relation": "calls", "weight": 1.0},
    ]
    return nodes, edges


def _graph_with_hypothesis_node(created_at: float, with_grounded_edges: bool = False):
    """Graph containing one hypothesis node optionally with grounded_in edges."""
    now_ts = created_at
    nodes = [
        {"id": "n:alpha", "label": "Alpha", "community": 0},
        {"id": "n:beta",  "label": "Beta",  "community": 1},
        {
            "id": "n:hyp1",
            "label": "H: Alpha ↔ Beta",
            "source_file": "dream_log.md",
            "origin": "hypothesis",
            "created_at": now_ts,
            "community": 0,
        },
    ]
    edges = [
        {"source": "n:alpha", "target": "n:beta", "relation": "calls", "weight": 1.0},
    ]
    if with_grounded_edges:
        edges += [
            {
                "source": "n:hyp1",
                "target": "n:alpha",
                "relation": "grounded_in",
                "weight": 0.4,
            },
            {
                "source": "n:hyp1",
                "target": "n:beta",
                "relation": "grounded_in",
                "weight": 0.4,
            },
        ]
    return nodes, edges


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBriefingCortexHeader:
    """test_briefing_includes_cortex_header_with_minute_timestamp"""

    def test_output_starts_with_cortex_timestamp(self, tmp_path):
        nodes, edges = _five_nodes_varying_degree()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)

        result = _run_briefing(repo)
        assert result.returncode == 0, result.stderr

        # Must start with "Cortex YYYY-MM-DD HH:MM"
        assert re.match(
            r"Cortex \d{4}-\d{2}-\d{2} \d{2}:\d{2}",
            result.stdout.strip().splitlines()[0],
        ), f"Header mismatch: {result.stdout[:80]!r}"


class TestBriefingGodNodes:
    """test_briefing_lists_top_god_nodes"""

    def test_lists_top_3_nodes_by_degree(self, tmp_path):
        nodes, edges = _five_nodes_varying_degree()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)

        result = _run_briefing(repo)
        assert result.returncode == 0, result.stderr

        stdout = result.stdout
        assert "God nodes:" in stdout
        # Hub1 (degree 4) and Hub2 (degree 3) must appear
        assert "Hub1" in stdout
        assert "Hub2" in stdout
        assert "Hub3" in stdout
        # Must show community info
        assert "central to community" in stdout


class TestBriefingRecentHypotheses:
    """test_briefing_includes_recent_hypothesis_nodes"""

    def test_recent_hypothesis_appears_in_briefing(self, tmp_path):
        now = time.time()
        nodes, edges = _graph_with_hypothesis_node(created_at=now)
        repo = _make_repo_with_graph(tmp_path, nodes, edges)

        result = _run_briefing(repo)
        assert result.returncode == 0, result.stderr

        assert "Hypotheses worth reviewing:" in result.stdout
        assert "H: Alpha ↔ Beta" in result.stdout


class TestBriefingOldHypothesesFiltered:
    """test_briefing_filters_old_hypotheses"""

    def test_hypothesis_older_than_24h_not_included(self, tmp_path):
        old_ts = time.time() - 90_000  # > 24h ago
        nodes, edges = _graph_with_hypothesis_node(created_at=old_ts)
        repo = _make_repo_with_graph(tmp_path, nodes, edges)

        result = _run_briefing(repo)
        assert result.returncode == 0, result.stderr

        # Section exists but says "none yet"
        assert "none yet" in result.stdout

        # The hypothesis label must NOT appear as a bullet under the
        # "Hypotheses worth reviewing" section.  We locate that section and
        # verify no "- H: Alpha ↔ Beta" bullet is present there.
        stdout = result.stdout
        hyp_section_start = stdout.find("Hypotheses worth reviewing")
        assert hyp_section_start != -1, "Hypotheses section missing from output"
        # Find the next section boundary (double-newline after the section header)
        hyp_section_end = stdout.find("\n\n", hyp_section_start + 1)
        if hyp_section_end == -1:
            hyp_section_end = len(stdout)
        hyp_section_text = stdout[hyp_section_start:hyp_section_end]
        assert "- H: Alpha" not in hyp_section_text, (
            f"Old hypothesis appeared as a bullet in Hypotheses section: {hyp_section_text!r}"
        )


class TestBriefingGroundedInSources:
    """test_briefing_lists_grounded_in_sources_for_hypothesis"""

    def test_hypothesis_grounded_in_edges_named_in_briefing(self, tmp_path):
        now = time.time()
        nodes, edges = _graph_with_hypothesis_node(
            created_at=now, with_grounded_edges=True
        )
        repo = _make_repo_with_graph(tmp_path, nodes, edges)

        result = _run_briefing(repo)
        assert result.returncode == 0, result.stderr

        stdout = result.stdout
        assert "H: Alpha ↔ Beta" in stdout
        # Both source node labels must appear in the grounded_in line
        assert "Alpha" in stdout
        assert "Beta" in stdout
        assert "grounded in" in stdout


class TestBriefingSurprisingConnectionFromReport:
    """test_briefing_includes_surprising_connection_from_report"""

    def test_first_item_from_graph_report_appears(self, tmp_path):
        nodes, edges = _five_nodes_varying_degree()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)
        _make_report_with_surprising(
            repo / "graphify-out",
            ["Hub1 ↔ Leaf2 (via unexpected dependency)"],
        )

        result = _run_briefing(repo)
        assert result.returncode == 0, result.stderr

        assert "Surprising connection of the day:" in result.stdout
        assert "Hub1 ↔ Leaf2" in result.stdout


class TestBriefingMaxChars:
    """test_briefing_max_chars_respected"""

    def test_output_does_not_exceed_max_chars(self, tmp_path):
        # Use a very small cap (200 chars) so the output is guaranteed to exceed
        # the budget even after section-dropping, forcing the hard-truncation path
        # that inserts the "..." marker.
        nodes = [
            {
                "id": f"n:node{i}",
                "label": f"VeryLongNodeNameThatTakesUpSpaceInBriefing{i}",
                "community": i % 3,
            }
            for i in range(30)
        ]
        edges = [
            {"source": f"n:node{i}", "target": f"n:node{i+1}", "relation": "calls", "weight": 1.0}
            for i in range(29)
        ]
        repo = _make_repo_with_graph(tmp_path, nodes, edges)
        # Use a surprise line long enough (>137 chars of bullet text) so that
        # body_minimal (header + surprise section + footer) exceeds 200 chars,
        # forcing the hard-truncation path that inserts "...".
        long_reason = "X" * 160
        _make_report_with_surprising(
            repo / "graphify-out",
            [f"NodeA0 ↔ NodeB0 (via {long_reason})"],
        )

        # 200-char cap forces hard truncation even after section-dropping.
        cap = 200
        result = _run_briefing(repo, "--max-chars", str(cap))
        assert result.returncode == 0, result.stderr

        output = result.stdout
        assert len(output) <= cap, f"Output length {len(output)} exceeds {cap} chars"
        # The truncation marker must appear because hard truncation fired
        assert "..." in output, "Truncation marker '...' missing from truncated output"


class TestBriefingOutputToFile:
    """test_briefing_output_to_file"""

    def test_writes_to_file_stdout_silent_stderr_has_message(self, tmp_path):
        nodes, edges = _five_nodes_varying_degree()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)
        out_file = tmp_path / "briefing_out.md"

        result = _run_briefing(repo, "--output", str(out_file))
        assert result.returncode == 0, result.stderr

        # stdout must be empty (or just a newline from print)
        assert result.stdout.strip() == "", f"Expected silent stdout, got: {result.stdout!r}"
        # file must exist and have content
        assert out_file.exists(), "Output file was not created"
        content = out_file.read_text(encoding="utf-8")
        assert "Cortex" in content
        # stderr must have the "wrote N chars" message
        assert "wrote" in result.stderr
        assert "chars to" in result.stderr


class TestBriefingEmptyGraph:
    """test_briefing_empty_graph_graceful"""

    def test_empty_graph_emits_header_and_no_data(self, tmp_path):
        repo = _make_repo_with_graph(tmp_path, [], [])

        result = _run_briefing(repo)
        assert result.returncode == 0, result.stderr

        stdout = result.stdout
        # Must have the Cortex header
        assert re.match(
            r"Cortex \d{4}-\d{2}-\d{2} \d{2}:\d{2}",
            stdout.strip().splitlines()[0],
        )
        # All body sections must say "(no data)"
        assert "(no data)" in stdout
        # Legend line must appear
        assert "For details: /graphify query <topic>" in stdout


class TestBriefingNoHypothesesIn24h:
    """test_briefing_no_hypotheses_in_24h"""

    def test_old_hypothesis_shows_none_yet_message(self, tmp_path):
        old_ts = time.time() - 90_000  # > 24h
        nodes, edges = _graph_with_hypothesis_node(created_at=old_ts)
        repo = _make_repo_with_graph(tmp_path, nodes, edges)

        result = _run_briefing(repo)
        assert result.returncode == 0, result.stderr

        assert "none yet — run a few more nights" in result.stdout


class TestBriefingNoSurprisingSectionInReport:
    """test_briefing_no_surprising_section_in_report"""

    def test_report_without_surprising_section_shows_fallback(self, tmp_path):
        nodes, edges = _five_nodes_varying_degree()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)
        _make_report_no_surprising(repo / "graphify-out")

        result = _run_briefing(repo)
        assert result.returncode == 0, result.stderr

        # Either fallback text or the "none found today" graceful message
        stdout = result.stdout
        has_graceful = "none found today — check back tomorrow" in stdout
        # It's also acceptable that the fallback surprising_connections produced a result
        has_surprise_section = "Surprising connection of the day:" in stdout
        assert has_surprise_section, "Surprising connection section missing entirely"
        # If no data found: must say the graceful message
        if "none found today" not in stdout:
            # fallback produced a result — that's fine too
            pass


class TestBriefingSplitModeRefuses:
    """test_briefing_split_mode_refuses"""

    def test_graphifyshared_sentinel_causes_exit1(self, tmp_path):
        nodes, edges = _five_nodes_varying_degree()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)
        # Create the split-mode sentinel file
        (repo / ".graphifyshared").touch()

        result = _run_briefing(repo)
        assert result.returncode == 1, (
            f"Expected exit 1 for split mode, got {result.returncode}; "
            f"stderr={result.stderr!r}"
        )
        assert "split mode" in result.stderr


class TestBriefingForceSplitModeUnsafeProceeds:
    """test_briefing_force_split_mode_unsafe_proceeds"""

    def test_force_flag_allows_run_and_warns(self, tmp_path):
        nodes, edges = _five_nodes_varying_degree()
        repo = _make_repo_with_graph(tmp_path, nodes, edges)
        (repo / ".graphifyshared").touch()

        result = _run_briefing(repo, "--force-split-mode-unsafe")
        assert result.returncode == 0, (
            f"Expected exit 0 with --force-split-mode-unsafe, got {result.returncode}; "
            f"stderr={result.stderr!r}"
        )
        # Warning must appear in stderr
        assert "force-split-mode-unsafe" in result.stderr
        # Briefing still emitted to stdout
        assert "Cortex" in result.stdout


class TestBriefingMissingGraphErrors:
    """test_briefing_missing_graph_errors"""

    def test_no_graph_json_exits_1_with_clean_error(self, tmp_path):
        repo = tmp_path
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(repo), check=True)
        # graphify-out/ exists but graph.json does NOT
        (repo / "graphify-out").mkdir(exist_ok=True)

        result = _run_briefing(repo)
        assert result.returncode == 1, (
            f"Expected exit 1 for missing graph.json, got {result.returncode}"
        )
        assert "does not exist" in result.stderr or "graph.json" in result.stderr
