"""Tests for the 4 new ``graphify`` CLI subcommands shipped in Stage 1.2:

* ``graphify init-sharing [root] [--non-interactive] [--default-remote URL]``
* ``graphify share <path>...``
* ``graphify unshare <path>...``
* ``graphify migrate-to-shared [root] [--non-interactive]``

Each test invokes the CLI via ``subprocess.run`` so the full argument-parsing
and dispatch surface is exercised end-to-end.
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


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )


def _run_cli(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run ``graphify <args>`` via ``python -m graphify`` to avoid PATH issues."""
    cmd = [sys.executable, "-m", "graphify", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


# ---------------------------------------------------------------------------
# init-sharing
# ---------------------------------------------------------------------------


def test_cli_init_sharing_non_interactive(tmp_path: Path) -> None:
    """``graphify init-sharing --non-interactive <tmpdir>`` must create both
    overlay files and a ``graphify-out/.gitignore``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    proc = _run_cli(["init-sharing", str(repo), "--non-interactive"])
    assert proc.returncode == 0, (
        f"init-sharing failed (rc={proc.returncode})\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert (repo / ".graphifyshared").exists(), ".graphifyshared must be created"
    assert (repo / ".graphifyprivate").exists(), ".graphifyprivate must be created"
    assert (repo / "graphify-out" / ".gitignore").exists(), (
        "graphify-out/.gitignore must be created"
    )


# ---------------------------------------------------------------------------
# share / unshare
# ---------------------------------------------------------------------------


def test_cli_share_appends_to_shared(tmp_path: Path) -> None:
    """``graphify share src/`` must append ``src/`` to ``.graphifyshared``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    _run_cli(["init-sharing", str(repo), "--non-interactive"])

    proc = _run_cli(["share", "src/"], cwd=repo)
    assert proc.returncode == 0, (
        f"share failed (rc={proc.returncode})\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    content = (repo / ".graphifyshared").read_text(encoding="utf-8")
    assert "src/" in content or "src" in content, (
        f".graphifyshared must contain src/ after share command, got:\n{content}"
    )


def test_cli_unshare_appends_to_private(tmp_path: Path) -> None:
    """``graphify unshare scratch/`` must append ``scratch/`` to ``.graphifyprivate``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    _run_cli(["init-sharing", str(repo), "--non-interactive"])

    proc = _run_cli(["unshare", "scratch/"], cwd=repo)
    assert proc.returncode == 0, (
        f"unshare failed (rc={proc.returncode})\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    content = (repo / ".graphifyprivate").read_text(encoding="utf-8")
    assert "scratch/" in content or "scratch" in content, (
        f".graphifyprivate must contain scratch/ after unshare command, got:\n{content}"
    )


def test_cli_share_warns_when_path_already_private(tmp_path: Path) -> None:
    """``graphify share`` on a path already matched (more specifically) by
    ``.graphifyprivate`` should warn on stderr."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    _run_cli(["init-sharing", str(repo), "--non-interactive"])

    # Force the private overlay to take precedence over a future share request.
    (repo / ".graphifyprivate").write_text("src/secrets/**\n", encoding="utf-8")

    proc = _run_cli(["share", "src/secrets/"], cwd=repo)
    # The command may succeed (warn-only) or exit non-zero; in either case a
    # warning about the conflict must appear on stderr.
    stderr_lower = proc.stderr.lower()
    assert (
        "private" in stderr_lower
        or "warn" in stderr_lower
        or "conflict" in stderr_lower
        or "already" in stderr_lower
    ), (
        f"share on a path that is already private must warn, got stderr:\n{proc.stderr}\n"
        f"stdout:\n{proc.stdout}"
    )


# ---------------------------------------------------------------------------
# migrate-to-shared
# ---------------------------------------------------------------------------


def test_cli_migrate_to_shared_non_interactive(tmp_path: Path) -> None:
    """``graphify migrate-to-shared --non-interactive <tmpdir>`` on a repo
    with a legacy graph.json must produce overlays and a renamed split file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    out_dir = repo / "graphify-out"
    out_dir.mkdir()
    (out_dir / "graph.json").write_text(
        json.dumps({"nodes": [{"id": "n1"}], "links": []}),
        encoding="utf-8",
    )

    proc = _run_cli(["migrate-to-shared", str(repo), "--non-interactive"])
    assert proc.returncode == 0, (
        f"migrate-to-shared failed (rc={proc.returncode})\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert (repo / ".graphifyshared").exists(), ".graphifyshared must exist post-migration"
    assert (repo / ".graphifyprivate").exists(), ".graphifyprivate must exist post-migration"
    assert (out_dir / "graph-private.json").exists(), (
        "graph.json must be renamed to graph-private.json"
    )
    assert not (out_dir / "graph.json").exists(), (
        "Legacy graph.json must be gone after migration"
    )
