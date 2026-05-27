"""graphify hermes-plugin: memory pressure reactive drain (Stage 3.1)."""
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "graphify" / "hermes-plugin"
sys.path.insert(0, str(PLUGIN_DIR))


@pytest.fixture(autouse=True)
def isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Re-import provider fresh each test so Path.home() picks up the patched HOME
    sys.modules.pop("provider", None)
    yield


def _populate_memory(tmp_home, char_count):
    """Write a MEMORY.md with approximately `char_count` characters."""
    mem_path = tmp_home / ".hermes" / "memories" / "MEMORY.md"
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    mem_path.write_text("x" * char_count)
    return mem_path


def _write_manifest(tmp_home, brain_root="/tmp/brain"):
    state_path = tmp_home / ".hermes" / "state" / "graphify-sleep.manifest.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"version": "0.10.1", "brain_root": "' + brain_root + '", "jobs": []}'
    )
    return state_path


def test_pressure_triggers_above_85_percent(tmp_path, monkeypatch):
    """memory_usage > 85% spawns the pressure-drain subprocess."""
    sys.modules.pop("provider", None)
    import provider as p

    # 1900 chars > 0.85 * 2200 = 1870
    _populate_memory(tmp_path, 1900)
    _write_manifest(tmp_path)

    # Mock the GraphifyMemoryProvider's plumbing
    inst = p.GraphifyMemoryProvider()
    inst.manifest = {"brain_root": str(tmp_path / "fake-brain")}
    # Pre-create the plugin state dir (normally done by initialize(); we bypass it here)
    inst._plugin_state_dir.mkdir(parents=True, exist_ok=True)

    # Make is_available return True for the test
    with patch.object(inst, "is_available", return_value=True):
        with patch.object(p.subprocess, "Popen") as mock_popen:
            inst.on_memory_write(action="add", target="memory", content="anything", metadata=None)

    assert mock_popen.call_count == 1
    # Check the subprocess args contain --mode pressure
    call_args = mock_popen.call_args[0][0]
    assert any("--mode" in str(a) for a in call_args)
    assert any("pressure" in str(a) for a in call_args)


def test_pressure_does_not_trigger_below_threshold(tmp_path, monkeypatch):
    """memory_usage < 85% → no subprocess spawned."""
    sys.modules.pop("provider", None)
    import provider as p

    # 1500 chars = 68%
    _populate_memory(tmp_path, 1500)
    _write_manifest(tmp_path)

    inst = p.GraphifyMemoryProvider()
    inst.manifest = {"brain_root": str(tmp_path / "fake-brain")}
    inst._plugin_state_dir.mkdir(parents=True, exist_ok=True)

    with patch.object(inst, "is_available", return_value=True):
        with patch.object(p.subprocess, "Popen") as mock_popen:
            inst.on_memory_write(action="add", target="memory", content="hi", metadata=None)

    assert mock_popen.call_count == 0


def test_pressure_rate_limited_within_60_seconds(tmp_path, monkeypatch):
    """Second pressure-drain within 60s is skipped (rate limit)."""
    sys.modules.pop("provider", None)
    import provider as p

    _populate_memory(tmp_path, 1900)  # over threshold
    _write_manifest(tmp_path)

    inst = p.GraphifyMemoryProvider()
    inst.manifest = {"brain_root": str(tmp_path / "fake-brain")}
    # Pre-create the rate-limit sentinel with current timestamp
    state_dir = tmp_path / ".hermes" / "plugins" / "memory" / "graphify" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "last_pressure.txt").write_text(str(time.time()))

    with patch.object(inst, "is_available", return_value=True):
        with patch.object(p.subprocess, "Popen") as mock_popen:
            inst.on_memory_write(action="add", target="memory", content="hi", metadata=None)

    # Within 60s of last_pressure → no second spawn
    assert mock_popen.call_count == 0
