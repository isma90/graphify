"""graphify hermes-plugin: drain logic (Stage 3.1)."""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "graphify" / "hermes-plugin"
sys.path.insert(0, str(PLUGIN_DIR))


@pytest.fixture(autouse=True)
def isolate_home(tmp_path, monkeypatch):
    """Patch HOME so MEMORY_FILE points to a tmp location."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Force drain module to re-resolve MEMORY_FILE if already imported
    sys.modules.pop("drain", None)
    yield


def _populate_memory_md(tmp_home, entries):
    """Write entries (joined by '\n§\n') to ~/.hermes/memories/MEMORY.md."""
    mem_path = tmp_home / ".hermes" / "memories" / "MEMORY.md"
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    mem_path.write_text("\n§\n".join(entries))
    return mem_path


def _old_cortex_entry(days_ago=10):
    ts = (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M")
    return f"Cortex {ts}\n\nGod nodes: foo, bar\nHypotheses: H: X ↔ Y"


def _fresh_cortex_entry():
    ts = (datetime.utcnow() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    return f"Cortex {ts}\n\nGod nodes: alpha, beta\nHypotheses: (none yet)"


def _user_curated_entry():
    return "User-curated note: remember to follow up with Sarah at Acme by Friday"


def test_drain_archives_old_cortex_skips_user_curated(tmp_path, monkeypatch):
    """Old Cortex archived; user-curated kept; fresh Cortex kept."""
    import drain as d

    # Patch MEMORY_FILE to use tmp_path
    mem_path = _populate_memory_md(
        tmp_path,
        [_old_cortex_entry(days_ago=10), _user_curated_entry(), _fresh_cortex_entry()],
    )
    monkeypatch.setattr(d, "MEMORY_FILE", mem_path)

    # Mock subprocess.run (graphify add) to always succeed
    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch.object(d.subprocess, "run", return_value=mock_result) as mock_run:
        result = d._drain_old_cortex_entries(Path("/tmp/fake-brain"), age_threshold_days=7)

    assert result["drained"] == 1
    assert result["skipped_user_curated"] == 1
    assert result["errors"] == []
    # graphify add was called once (for the old Cortex)
    assert mock_run.call_count == 1
    # Verify the call was `graphify add ...`
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "graphify"
    assert call_args[1] == "add"

    # MEMORY.md should still contain user-curated + fresh entry, not the old one
    final = mem_path.read_text()
    assert _user_curated_entry() in final
    assert "alpha, beta" in final  # fresh entry kept
    assert "foo, bar" not in final  # old entry removed


def test_drain_subprocess_called_per_archive(tmp_path, monkeypatch):
    """subprocess.run(['graphify', 'add', ...]) called once per archived entry."""
    import drain as d

    mem_path = _populate_memory_md(
        tmp_path,
        [_old_cortex_entry(days_ago=10), _old_cortex_entry(days_ago=15), _old_cortex_entry(days_ago=20)],
    )
    monkeypatch.setattr(d, "MEMORY_FILE", mem_path)

    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch.object(d.subprocess, "run", return_value=mock_result) as mock_run:
        result = d._drain_old_cortex_entries(Path("/tmp/fake-brain"), age_threshold_days=7)

    assert result["drained"] == 3
    assert mock_run.call_count == 3


def test_drain_removes_after_archive(tmp_path, monkeypatch):
    """After successful archive, entry removed from MEMORY.md."""
    import drain as d

    old_entry = _old_cortex_entry(days_ago=10)
    mem_path = _populate_memory_md(tmp_path, [old_entry, _user_curated_entry()])
    monkeypatch.setattr(d, "MEMORY_FILE", mem_path)
    original_size = len(mem_path.read_text())

    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch.object(d.subprocess, "run", return_value=mock_result):
        result = d._drain_old_cortex_entries(Path("/tmp/fake-brain"), age_threshold_days=7)

    final_size = len(mem_path.read_text())
    assert final_size < original_size
    assert _user_curated_entry() in mem_path.read_text()
    assert result["drained"] == 1


def test_drain_skips_entries_without_cortex_header(tmp_path, monkeypatch):
    """Entries without 'Cortex YYYY-MM-DD HH:MM' header are NEVER archived."""
    import drain as d

    mem_path = _populate_memory_md(
        tmp_path,
        ["Just a random note", "Reminder: book flight", _user_curated_entry()],
    )
    monkeypatch.setattr(d, "MEMORY_FILE", mem_path)

    mock_result = MagicMock(returncode=0)
    with patch.object(d.subprocess, "run", return_value=mock_result) as mock_run:
        result = d._drain_old_cortex_entries(Path("/tmp/fake-brain"), age_threshold_days=7)

    assert result["drained"] == 0
    assert result["skipped_user_curated"] == 3
    # graphify add NEVER called
    assert mock_run.call_count == 0
    # MEMORY.md unchanged
    assert "Just a random note" in mem_path.read_text()


def test_drain_empty_memory_md_graceful(tmp_path, monkeypatch):
    """Empty MEMORY.md → no-op, returns zero counts."""
    import drain as d

    mem_path = _populate_memory_md(tmp_path, [])  # empty
    monkeypatch.setattr(d, "MEMORY_FILE", mem_path)

    result = d._drain_old_cortex_entries(Path("/tmp/fake-brain"), age_threshold_days=7)

    assert result["drained"] == 0
    assert result["skipped_user_curated"] == 0
    assert result["errors"] == []


def test_drain_all_cortex_fresh_zero_drained(tmp_path, monkeypatch):
    """All Cortex entries < 7 days → zero drained, all kept."""
    import drain as d

    mem_path = _populate_memory_md(
        tmp_path,
        [_fresh_cortex_entry(), _fresh_cortex_entry(), _user_curated_entry()],
    )
    monkeypatch.setattr(d, "MEMORY_FILE", mem_path)

    mock_result = MagicMock(returncode=0)
    with patch.object(d.subprocess, "run", return_value=mock_result) as mock_run:
        result = d._drain_old_cortex_entries(Path("/tmp/fake-brain"), age_threshold_days=7)

    assert result["drained"] == 0
    assert mock_run.call_count == 0
