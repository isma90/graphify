"""graphify sleep install/status/uninstall/demo/pause subcommand tests (Sprint 1)."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _run(tmp_path, *args, env=None, cwd=None):
    full_env = {**os.environ}
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "graphify", "sleep", *args],
        cwd=str(cwd or tmp_path),
        capture_output=True,
        text=True,
        check=False,
        env=full_env,
    )


@pytest.fixture
def tmp_brain(tmp_path, monkeypatch):
    """Create a tmp brain root within the user's home (path validation requires it)."""
    # Monkeypatch HOME to tmp_path so path validation accepts our brain
    monkeypatch.setenv("HOME", str(tmp_path))
    # Also set hermes state dir under the patched home
    brain = tmp_path / "brain"
    brain.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(brain), check=True)
    subprocess.run(["git", "-c", "user.email=t@t.com", "-c", "user.name=t",
                    "commit", "--allow-empty", "-m", "init"],
                   cwd=str(brain), check=True)
    return brain


def test_install_writes_manifest_and_bundle(tmp_brain, tmp_path):
    r = _run(tmp_path, "install", "--brain-root", str(tmp_brain))
    assert r.returncode == 0, r.stderr
    manifest = tmp_path / ".hermes" / "state" / "graphify-sleep.manifest.json"
    assert manifest.is_file()
    data = json.loads(manifest.read_text())
    assert len(data["jobs"]) == 2  # Sprint 1: 2 jobs
    assert data["jobs"][0]["name"] == "sleep_1_replay"
    assert data["jobs"][1]["name"] == "sleep_2_nrem"
    assert data["jobs"][1]["context_from"] == "sleep_1_replay"
    bundle = tmp_path / ".hermes" / "skills" / "sleep-cycle"
    assert bundle.is_dir()
    assert (bundle / "SKILL.md").is_file()
    assert (bundle / "templates" / "sleep_1_replay.md").is_file()
    assert (bundle / ".graphify_version").is_file()


def test_install_prints_paste_snippet(tmp_brain, tmp_path):
    r = _run(tmp_path, "install", "--brain-root", str(tmp_brain))
    assert "BEGIN HERMES SNIPPET" in r.stdout
    assert 'cronjob(action="delete"' in r.stdout
    assert 'cronjob(action="create"' in r.stdout
    assert "sleep_1_replay" in r.stdout


def test_install_path_traversal_rejected(tmp_path, monkeypatch):
    """--brain-root outside HOME is rejected."""
    monkeypatch.setenv("HOME", str(tmp_path))
    outside = Path("/tmp") / "evil-brain-outside-home"
    r = _run(tmp_path, "install", "--brain-root", str(outside))
    assert r.returncode != 0
    assert "home" in r.stderr.lower() or "traversal" in r.stderr.lower()


def test_install_not_a_git_repo_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    non_git = tmp_path / "not-a-git-repo"
    non_git.mkdir()
    r = _run(tmp_path, "install", "--brain-root", str(non_git))
    assert r.returncode != 0
    assert "git" in r.stderr.lower()


def test_install_idempotent_version_aware(tmp_brain, tmp_path):
    """Re-install at same version is idempotent; at new version triggers clean copy."""
    r1 = _run(tmp_path, "install", "--brain-root", str(tmp_brain))
    assert r1.returncode == 0
    bundle = tmp_path / ".hermes" / "skills" / "sleep-cycle"
    # Simulate a stale file in dest (would normally be left over from prior version)
    stale = bundle / "stale-template.md"
    stale.write_text("stale")
    # Tamper with version file to trigger clean-copy
    (bundle / ".graphify_version").write_text("0.9.0")
    r2 = _run(tmp_path, "install", "--brain-root", str(tmp_brain))
    assert r2.returncode == 0
    # Stale file should be gone after clean copy
    assert not stale.exists()


def test_install_show_snippet_flag(tmp_brain, tmp_path):
    _run(tmp_path, "install", "--brain-root", str(tmp_brain))
    r = _run(tmp_path, "install", "--show-snippet")
    assert r.returncode == 0
    assert "BEGIN HERMES SNIPPET" in r.stdout


def test_install_phases_filter(tmp_brain, tmp_path):
    """--phases 1 produces a manifest with only the replay job."""
    r = _run(tmp_path, "install", "--brain-root", str(tmp_brain), "--phases", "1")
    assert r.returncode == 0
    manifest = tmp_path / ".hermes" / "state" / "graphify-sleep.manifest.json"
    data = json.loads(manifest.read_text())
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["name"] == "sleep_1_replay"


def test_status_reports_jobs(tmp_brain, tmp_path):
    _run(tmp_path, "install", "--brain-root", str(tmp_brain))
    r = _run(tmp_path, "status", "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert "jobs" in data
    assert len(data["jobs"]) == 2
    # No actual runs yet — should report "not yet run"
    for job in data["jobs"]:
        assert job["last_run"] is None or job["status"] in ("not yet run", "ok", "failed")


def test_status_no_manifest_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = _run(tmp_path, "status", "--json")
    assert r.returncode != 0


def test_uninstall_removes_bundle(tmp_brain, tmp_path):
    _run(tmp_path, "install", "--brain-root", str(tmp_brain))
    r = _run(tmp_path, "uninstall")
    assert r.returncode == 0
    bundle = tmp_path / ".hermes" / "skills" / "sleep-cycle"
    assert not bundle.exists()


def test_uninstall_archives_manifest(tmp_brain, tmp_path):
    _run(tmp_path, "install", "--brain-root", str(tmp_brain))
    r = _run(tmp_path, "uninstall")
    assert r.returncode == 0
    # Archive should exist
    state_dir = tmp_path / ".hermes" / "state"
    archives = list(state_dir.glob("graphify-sleep.manifest.json.archive-*"))
    assert len(archives) >= 1
    # Active manifest gone
    assert not (state_dir / "graphify-sleep.manifest.json").exists()


def test_uninstall_keep_state(tmp_brain, tmp_path):
    _run(tmp_path, "install", "--brain-root", str(tmp_brain))
    r = _run(tmp_path, "uninstall", "--keep-state")
    assert r.returncode == 0
    # Active manifest still present
    state_dir = tmp_path / ".hermes" / "state"
    assert (state_dir / "graphify-sleep.manifest.json").exists()


def test_pause_writes_sentinel(tmp_brain, tmp_path):
    r = _run(tmp_path, "pause", "--tonight", cwd=tmp_brain)
    assert r.returncode == 0, r.stderr
    sentinel = tmp_brain / ".graphify_sleep_paused"
    assert sentinel.is_file()


def test_resume_removes_sentinel(tmp_brain, tmp_path):
    _run(tmp_path, "pause", "--tonight", cwd=tmp_brain)
    r = _run(tmp_path, "resume", cwd=tmp_brain)
    assert r.returncode == 0
    sentinel = tmp_brain / ".graphify_sleep_paused"
    assert not sentinel.exists()


def test_demo_runs_on_fixture_corpus(tmp_brain, tmp_path):
    """Demo should complete successfully on a tmp brain with fixture corpus."""
    r = _run(tmp_path, "demo", "--brain-root", str(tmp_brain))
    assert r.returncode == 0, r.stderr
    assert "demo" in r.stdout.lower() or "completed" in r.stdout.lower()
