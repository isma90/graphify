"""graphify touch CLI stub tests (Stage 3 Sprint 1; full hook lands in Sprint 2)."""
import json
import os
import subprocess
import sys
from pathlib import Path


def _run_touch(tmp_path, *args, env=None):
    full_env = {**os.environ}
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "graphify", "touch", *args],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
        env=full_env,
    )


def test_touch_without_env_var_emits_stub_note(tmp_path):
    r = _run_touch(tmp_path, "node-a", env={"GRAPHIFY_TOUCH_LOG": ""})
    assert r.returncode == 0
    # Stub note on stderr
    assert "sprint 1 stub" in r.stderr.lower() or "no GRAPHIFY_TOUCH_LOG" in r.stderr


def test_touch_with_env_var_writes_log(tmp_path):
    log_file = tmp_path / ".graphify_touched.json"
    r = _run_touch(
        tmp_path, "node-a", "node-b",
        env={"GRAPHIFY_TOUCH_LOG": str(log_file)},
    )
    assert r.returncode == 0
    assert log_file.is_file()
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2
    entries = [json.loads(line) for line in lines]
    assert entries[0]["id"] == "node-a"
    assert entries[1]["id"] == "node-b"
    assert "ts" in entries[0]
    assert isinstance(entries[0]["ts"], (int, float))


def test_touch_appends_does_not_overwrite(tmp_path):
    log_file = tmp_path / ".graphify_touched.json"
    log_file.write_text('{"id": "preexisting", "ts": 1234}\n')
    r = _run_touch(
        tmp_path, "node-a",
        env={"GRAPHIFY_TOUCH_LOG": str(log_file)},
    )
    assert r.returncode == 0
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2  # preexisting + new
    entries = [json.loads(line) for line in lines]
    assert entries[0]["id"] == "preexisting"
    assert entries[1]["id"] == "node-a"
