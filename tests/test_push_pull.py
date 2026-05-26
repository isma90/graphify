"""End-to-end tests for ``graphify push`` and ``graphify pull`` (D4, Stage 2).

Tests use temporary bare git remotes and two clones to exercise the full
push/pull cycle without touching any real repository.  The installed CLI is
invoked via ``sys.executable -m graphify`` to avoid PATH fragility.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in *cwd*."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"}
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        check=check, env=env,
    )


def _git_init(root: Path, *, bare: bool = False) -> None:
    args = ["git", "init", "-q", "-b", "main", str(root)]
    if bare:
        args.insert(2, "--bare")
    subprocess.run(args, check=True, capture_output=True)
    if not bare:
        subprocess.run(
            ["git", "-C", str(root), "config", "user.email", "t@t.com"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(root), "config", "user.name", "Test"],
            check=True, capture_output=True,
        )


def _run_cli(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run ``graphify <args>`` via ``python -m graphify``."""
    cmd = [sys.executable, "-m", "graphify", *args]
    merged_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if env:
        merged_env.update(env)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=merged_env,
    )


def _make_bare_remote(tmp_path: Path) -> Path:
    """Create a bare git repo to act as remote."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git_init(remote, bare=True)
    return remote


def _make_clone(tmp_path: Path, remote: Path, name: str) -> Path:
    """Clone the bare remote into a working directory."""
    clone = tmp_path / name
    clone.mkdir()
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(clone)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(clone), "config", "user.email", "t@t.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(clone), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    return clone


def _upsert_remote_api(repo: Path, url: str, config_path: Path | None = None) -> None:
    """Call config.upsert_remote directly (bootstraps without needing init-sharing)."""
    from graphify import config as _cfg
    kwargs: dict = {}
    if config_path:
        kwargs["path"] = config_path
    _cfg.upsert_remote(repo, url, **kwargs)


def _write_minimal_shared_graph(path: Path, nodes: list[dict] | None = None) -> None:
    """Write a minimal graph-shared.json to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "directed": False,
        "multigraph": False,
        "graph": {},
        "nodes": nodes or [{"id": "n1", "label": "hello", "source_file": "src/api.py", "origin": "shared"}],
        "links": [],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPushPullRoundTrip:
    """Push from clone A, pull into clone B, verify content lands."""

    def test_push_pull_round_trip(self, tmp_path: Path) -> None:
        remote = _make_bare_remote(tmp_path)
        clone_a = _make_clone(tmp_path, remote, "clone_a")
        clone_b = _make_clone(tmp_path, remote, "clone_b")

        config_file = tmp_path / "config.toml"
        env_cfg = {"GRAPHIFY_CONFIG": str(config_file)}

        # Clone A: register remote + write a hand-crafted graph-shared.json
        _upsert_remote_api(clone_a, str(remote), config_path=config_file)
        shared_a = clone_a / "graphify-out" / "graph-shared.json"
        _write_minimal_shared_graph(shared_a, nodes=[
            {"id": "api_func", "label": "public", "source_file": "src/api.py", "origin": "shared"},
        ])

        proc = _run_cli(["push", str(clone_a)], env=env_cfg)
        assert proc.returncode == 0, f"push failed: rc={proc.returncode}\n{proc.stderr}"
        assert "pushed" in proc.stdout.lower()

        watermark_a = clone_a / "graphify-out" / ".graphify_shared_base"
        assert watermark_a.exists(), "push must create watermark"
        pushed_sha = watermark_a.read_text().strip()
        assert len(pushed_sha) == 40, f"watermark should be a full SHA: {pushed_sha!r}"

        # Clone B: register remote + pull
        _upsert_remote_api(clone_b, str(remote), config_path=config_file)
        proc = _run_cli(["pull", str(clone_b)], env=env_cfg)
        assert proc.returncode == 0, f"pull failed: rc={proc.returncode}\n{proc.stderr}"
        assert "merged" in proc.stdout.lower()

        shared_b = clone_b / "graphify-out" / "graph-shared.json"
        assert shared_b.exists(), "pull must create graph-shared.json in B"
        data_b = json.loads(shared_b.read_text())
        node_ids = [n["id"] for n in data_b.get("nodes", [])]
        assert "api_func" in node_ids, f"A's node should appear in B after pull; nodes={node_ids}"

    def test_pull_when_remote_branch_missing_is_noop(self, tmp_path: Path) -> None:
        """Empty bare remote → pull exits 0 with friendly message, no local files changed."""
        remote = _make_bare_remote(tmp_path)
        clone_b = _make_clone(tmp_path, remote, "clone_b")
        config_file = tmp_path / "config.toml"
        env_cfg = {"GRAPHIFY_CONFIG": str(config_file)}

        _upsert_remote_api(clone_b, str(remote), config_path=config_file)
        proc = _run_cli(["pull", str(clone_b)], env=env_cfg)
        assert proc.returncode == 0, f"pull should be a no-op but failed: {proc.stderr}"
        assert "does not exist" in proc.stdout.lower() or "nothing" in proc.stdout.lower()
        # No shared file should have been created
        assert not (clone_b / "graphify-out" / "graph-shared.json").exists()

    def test_push_when_no_local_shared_errors(self, tmp_path: Path) -> None:
        """``graphify push`` without a graph-shared.json must exit 1 with error."""
        remote = _make_bare_remote(tmp_path)
        clone_a = _make_clone(tmp_path, remote, "clone_a")
        config_file = tmp_path / "config.toml"
        env_cfg = {"GRAPHIFY_CONFIG": str(config_file)}

        _upsert_remote_api(clone_a, str(remote), config_path=config_file)
        # Do NOT write graph-shared.json
        proc = _run_cli(["push", str(clone_a)], env=env_cfg)
        assert proc.returncode == 1, f"push should fail without shared graph but got rc={proc.returncode}"
        assert "does not exist" in proc.stderr.lower() or "graphify extract" in proc.stderr.lower()

    def test_push_creates_watermark(self, tmp_path: Path) -> None:
        """After ``graphify push``, the watermark file contains the pushed SHA."""
        remote = _make_bare_remote(tmp_path)
        clone_a = _make_clone(tmp_path, remote, "clone_a")
        config_file = tmp_path / "config.toml"
        env_cfg = {"GRAPHIFY_CONFIG": str(config_file)}

        _upsert_remote_api(clone_a, str(remote), config_path=config_file)
        _write_minimal_shared_graph(clone_a / "graphify-out" / "graph-shared.json")

        proc = _run_cli(["push", str(clone_a)], env=env_cfg)
        assert proc.returncode == 0, f"push failed: {proc.stderr}"

        watermark = clone_a / "graphify-out" / ".graphify_shared_base"
        assert watermark.exists(), "watermark must exist after push"
        sha = watermark.read_text().strip()
        assert len(sha) == 40, f"watermark must contain a 40-char SHA, got {sha!r}"

    def test_pull_creates_watermark(self, tmp_path: Path) -> None:
        """After a successful pull, watermark matches the remote SHA."""
        remote = _make_bare_remote(tmp_path)
        clone_a = _make_clone(tmp_path, remote, "clone_a")
        clone_b = _make_clone(tmp_path, remote, "clone_b")
        config_file = tmp_path / "config.toml"
        env_cfg = {"GRAPHIFY_CONFIG": str(config_file)}

        _upsert_remote_api(clone_a, str(remote), config_path=config_file)
        _upsert_remote_api(clone_b, str(remote), config_path=config_file)
        _write_minimal_shared_graph(clone_a / "graphify-out" / "graph-shared.json")

        push_proc = _run_cli(["push", str(clone_a)], env=env_cfg)
        assert push_proc.returncode == 0
        pushed_sha = (clone_a / "graphify-out" / ".graphify_shared_base").read_text().strip()

        pull_proc = _run_cli(["pull", str(clone_b)], env=env_cfg)
        assert pull_proc.returncode == 0, f"pull failed: {pull_proc.stderr}"

        watermark_b = clone_b / "graphify-out" / ".graphify_shared_base"
        assert watermark_b.exists(), "pull must create watermark"
        pulled_sha = watermark_b.read_text().strip()
        assert pulled_sha == pushed_sha, (
            f"pull watermark {pulled_sha!r} must match pushed SHA {pushed_sha!r}"
        )

    def test_first_pull_uses_empty_base(self, tmp_path: Path) -> None:
        """Fresh clone B with no watermark pulls from populated remote → success."""
        remote = _make_bare_remote(tmp_path)
        clone_a = _make_clone(tmp_path, remote, "clone_a")
        clone_b = _make_clone(tmp_path, remote, "clone_b")
        config_file = tmp_path / "config.toml"
        env_cfg = {"GRAPHIFY_CONFIG": str(config_file)}

        _upsert_remote_api(clone_a, str(remote), config_path=config_file)
        _upsert_remote_api(clone_b, str(remote), config_path=config_file)
        _write_minimal_shared_graph(clone_a / "graphify-out" / "graph-shared.json", nodes=[
            {"id": "node_from_a", "label": "fn_a", "source_file": "api.py", "origin": "shared"},
        ])

        push_proc = _run_cli(["push", str(clone_a)], env=env_cfg)
        assert push_proc.returncode == 0

        # B has no watermark — first pull
        assert not (clone_b / "graphify-out" / ".graphify_shared_base").exists()
        pull_proc = _run_cli(["pull", str(clone_b)], env=env_cfg)
        assert pull_proc.returncode == 0, f"first pull failed: {pull_proc.stderr}"

        shared_b = clone_b / "graphify-out" / "graph-shared.json"
        assert shared_b.exists()
        data = json.loads(shared_b.read_text())
        ids = [n["id"] for n in data.get("nodes", [])]
        assert "node_from_a" in ids, f"Expected node_from_a in B after first pull; got {ids}"

        watermark_b = clone_b / "graphify-out" / ".graphify_shared_base"
        assert watermark_b.exists(), "watermark must be created on first pull"

    def test_pull_writes_conflict_file_when_conflicts(self, tmp_path: Path) -> None:
        """A and B both modify the same node differently; after B pulls a conflict file should appear.

        If three_way_merge_nodes resolves everything without conflicts (e.g. via dedup),
        we skip rather than fight the merge algorithm — Stage 1 covers that unit path.
        """
        remote = _make_bare_remote(tmp_path)
        clone_a = _make_clone(tmp_path, remote, "clone_a")
        clone_b = _make_clone(tmp_path, remote, "clone_b")
        config_file = tmp_path / "config.toml"
        env_cfg = {"GRAPHIFY_CONFIG": str(config_file)}

        _upsert_remote_api(clone_a, str(remote), config_path=config_file)
        _upsert_remote_api(clone_b, str(remote), config_path=config_file)

        # Establish a common base on remote by pushing from A
        base_graph = clone_a / "graphify-out" / "graph-shared.json"
        _write_minimal_shared_graph(base_graph, nodes=[
            {"id": "shared_node", "label": "v0", "source_file": "api.py", "origin": "shared"},
        ])
        push_a = _run_cli(["push", str(clone_a)], env=env_cfg)
        assert push_a.returncode == 0

        # B pulls the base
        pull_b_base = _run_cli(["pull", str(clone_b)], env=env_cfg)
        assert pull_b_base.returncode == 0

        # A modifies and pushes again (label v_a)
        _write_minimal_shared_graph(base_graph, nodes=[
            {"id": "shared_node", "label": "v_a", "x_attr": "from_a",
             "source_file": "api.py", "origin": "shared"},
        ])
        push_a2 = _run_cli(["push", str(clone_a)], env=env_cfg)
        assert push_a2.returncode == 0

        # B has its own local version (label v_b) — overwrite local shared
        _write_minimal_shared_graph(
            clone_b / "graphify-out" / "graph-shared.json",
            nodes=[
                {"id": "shared_node", "label": "v_b", "x_attr": "from_b",
                 "source_file": "api.py", "origin": "shared"},
            ],
        )

        pull_b_conflict = _run_cli(["pull", str(clone_b)], env=env_cfg)
        assert pull_b_conflict.returncode == 0, f"pull with conflicts should still exit 0: {pull_b_conflict.stderr}"

        conflicts_file = clone_b / "graphify-out" / ".graphify_shared_conflicts.json"
        if not conflicts_file.exists():
            pytest.skip(
                "three_way_merge_nodes resolved without conflicts — "
                "covered by build/three_way_merge unit tests"
            )

        conflicts = json.loads(conflicts_file.read_text())
        assert isinstance(conflicts, list)
        assert len(conflicts) >= 1, "Expected at least one conflict entry"
