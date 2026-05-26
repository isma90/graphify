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


# ---------------------------------------------------------------------------
# D4: remote add / list / remove
# ---------------------------------------------------------------------------


def test_remote_add_then_list_then_remove(tmp_path: Path) -> None:
    """Full round-trip: remote add, list, then remove — isolated via GRAPHIFY_CONFIG."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    config_file = tmp_path / "graphify_config.toml"
    env = {**os.environ, "GRAPHIFY_CONFIG": str(config_file), "PYTHONUNBUFFERED": "1"}

    # add
    proc = subprocess.run(
        [sys.executable, "-m", "graphify", "remote", "add",
         "git@github.com:org/test.git", str(repo)],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"remote add failed: {proc.stderr}"
    assert "added" in proc.stdout.lower() or "git@github.com" in proc.stdout

    # list
    proc = subprocess.run(
        [sys.executable, "-m", "graphify", "remote", "list"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"remote list failed: {proc.stderr}"
    assert str(repo) in proc.stdout
    assert "git@github.com:org/test.git" in proc.stdout

    # remove
    proc = subprocess.run(
        [sys.executable, "-m", "graphify", "remote", "remove", str(repo)],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"remote remove failed: {proc.stderr}"

    # list again — should be empty
    proc = subprocess.run(
        [sys.executable, "-m", "graphify", "remote", "list"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0
    assert str(repo) not in proc.stdout


def test_remote_add_with_branch_and_shared_path_overrides(tmp_path: Path) -> None:
    """--branch and --shared-path overrides are written and reflected in list + find_remote."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    config_file = tmp_path / "graphify_config.toml"
    env = {**os.environ, "GRAPHIFY_CONFIG": str(config_file), "PYTHONUNBUFFERED": "1"}

    proc = subprocess.run(
        [sys.executable, "-m", "graphify", "remote", "add",
         "git@github.com:org/overrides.git", str(repo),
         "--branch", "myteam/shared",
         "--shared-path", "custom-out/shared.json"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"remote add failed: {proc.stderr}"

    # Verify via list
    proc = subprocess.run(
        [sys.executable, "-m", "graphify", "remote", "list"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0
    assert "myteam/shared" in proc.stdout

    # Verify via Python API using the same config path
    import importlib
    from graphify import config as _cfg_mod
    cfg = _cfg_mod.find_remote(repo, path=config_file)
    assert cfg is not None, "find_remote should return entry"
    assert cfg.branch == "myteam/shared"
    assert cfg.shared_path == "custom-out/shared.json"


# ---------------------------------------------------------------------------
# D4: install-merge-driver / uninstall-merge-driver
# ---------------------------------------------------------------------------


def test_install_merge_driver_idempotent_cli(tmp_path: Path) -> None:
    """install-merge-driver succeeds; second call reports 'already installed'."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    proc = _run_cli(["install-merge-driver", str(repo)])
    assert proc.returncode == 0, f"first install failed: {proc.stderr}"
    assert "installed" in proc.stdout.lower()
    assert "already" not in proc.stdout.lower()

    # Second call: idempotent — should report already installed
    proc2 = _run_cli(["install-merge-driver", str(repo)])
    assert proc2.returncode == 0, f"second install failed: {proc2.stderr}"
    assert "already" in proc2.stdout.lower()


def test_uninstall_merge_driver_cli(tmp_path: Path) -> None:
    """install then uninstall via CLI — uninstall reports success."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    _run_cli(["install-merge-driver", str(repo)])

    proc = _run_cli(["uninstall-merge-driver", str(repo)])
    assert proc.returncode == 0, f"uninstall failed: {proc.stderr}"
    assert "uninstalled" in proc.stdout.lower()

    # Second uninstall should report nothing to remove
    proc2 = _run_cli(["uninstall-merge-driver", str(repo)])
    assert proc2.returncode == 0
    assert "no merge driver" in proc2.stdout.lower()
