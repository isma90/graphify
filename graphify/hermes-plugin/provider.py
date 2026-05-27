"""GraphifyMemoryProvider — Hermes plugin entry point.

Implements:
- is_available()       — returns True iff graphify CLI + sleep-cycle bundle present
- initialize(config)   — loads the sleep manifest if present
- on_session_start()   — idempotent auto-cron-registration on first session post-install
- on_session_end()     — no-op (drain runs separately via cron + memory_pressure)
- on_memory_write()    — fires on each memory(add); spawns pressure-drain if usage > 85%

The plugin assumes graphify >= 0.10.0 is installed and accessible via `graphify` on PATH.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Defensive import — Hermes is installed separately. If MemoryProvider isn't importable,
# the plugin still loads but is inert (is_available returns False).
try:
    from agent.memory_provider import MemoryProvider as _MemoryProviderBase
except ImportError:
    # Fallback ABC so the module is importable for testing.
    class _MemoryProviderBase:
        def is_available(self) -> bool: return False
        def initialize(self, config: dict) -> None: pass

logger = logging.getLogger("graphify_plugin")

GRAPHIFY_MIN_VERSION = (0, 10, 0)
MEMORY_PRESSURE_THRESHOLD = 0.85
PRESSURE_RATE_LIMIT_SECONDS = 60


class GraphifyMemoryProvider(_MemoryProviderBase):
    """Plugin lifecycle for graphify ↔ hermes integration."""

    def __init__(self) -> None:
        self.manifest: dict | None = None
        self._initialized = False
        self._hermes_home = Path.home() / ".hermes"
        self._plugin_state_dir = self._hermes_home / "plugins" / "memory" / "graphify" / "state"

    # ────────────────────────────── lifecycle ──────────────────────────────

    def is_available(self) -> bool:
        """Plugin is available when graphify CLI + sleep-cycle bundle are both present."""
        if not (self._hermes_home / "skills" / "sleep-cycle" / "SKILL.md").exists():
            return False
        if shutil.which("graphify") is None:
            return False
        if not self._graphify_version_ok():
            return False
        return True

    def _graphify_version_ok(self) -> bool:
        try:
            out = subprocess.run(
                ["graphify", "--version"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            ver_str = (out.stdout + out.stderr).strip().split()[-1]
            # parse "0.10.1" or "0.10.0a2" → tuple of ints (best-effort)
            import re
            m = re.match(r"(\d+)\.(\d+)\.(\d+)", ver_str)
            if not m:
                return False
            ver = tuple(int(x) for x in m.groups())
            return ver >= GRAPHIFY_MIN_VERSION
        except Exception:
            return False

    def initialize(self, config: dict) -> None:
        """Load manifest if present. Idempotent."""
        if self._initialized:
            return
        manifest_path = self._hermes_home / "state" / "graphify-sleep.manifest.json"
        if manifest_path.is_file():
            import json
            try:
                self.manifest = json.loads(manifest_path.read_text())
                logger.info(f"[graphify-plugin] loaded manifest from {manifest_path}")
            except Exception as e:
                logger.warning(f"[graphify-plugin] failed to read manifest: {e}")
                self.manifest = None
        self._plugin_state_dir.mkdir(parents=True, exist_ok=True)
        self._initialized = True

    def on_session_start(self, session_id: str, model: str = "", platform: str = "") -> None:
        """Auto-register cron jobs idempotently."""
        if not self.is_available():
            return
        if self.manifest is None:
            self.initialize({})
        if self.manifest is None:
            return  # No manifest = user hasn't run `graphify sleep install` yet

        try:
            from .auto_register import _auto_register_crons
            result = _auto_register_crons(self.manifest)
            log_path = self._plugin_state_dir / "auto_register.log"
            with log_path.open("a") as f:
                import json
                from datetime import datetime
                f.write(json.dumps({
                    "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    "session_id": session_id,
                    "result": result,
                }) + "\n")
            if result.get("created"):
                logger.info(f"[graphify-plugin] registered {len(result['created'])} cron jobs")
        except Exception as e:
            logger.exception(f"[graphify-plugin] auto-register failed: {e}")

    def on_session_end(self, messages: list | None = None) -> None:
        """No-op in 0.10.1. Drain runs separately."""
        pass

    def on_memory_write(self, action: str, target: str, content: str, metadata: dict | None = None) -> None:
        """Fires after memory(add). Trigger pressure-drain if usage exceeds threshold."""
        if action != "add" or target != "memory":
            return
        if not self.is_available():
            return
        if self.manifest is None:
            return

        try:
            mem_path = self._hermes_home / "memories" / "MEMORY.md"
            if not mem_path.is_file():
                return
            current = mem_path.read_text()
            usage_pct = len(current) / 2200.0
            if usage_pct < MEMORY_PRESSURE_THRESHOLD:
                return

            # Rate limit
            last_path = self._plugin_state_dir / "last_pressure.txt"
            import time
            now = time.time()
            if last_path.is_file():
                try:
                    last_ts = float(last_path.read_text().strip())
                    if now - last_ts < PRESSURE_RATE_LIMIT_SECONDS:
                        return
                except Exception:
                    pass

            brain_root = self.manifest.get("brain_root")
            if not brain_root:
                return

            last_path.write_text(str(now))
            # Spawn detached subprocess
            subprocess.Popen(
                [sys.executable, "-m", "graphify_plugin.drain",
                 "--mode", "pressure", "--brain-root", brain_root],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            logger.info(f"[graphify-plugin] memory pressure ({usage_pct:.0%}) → pressure-drain dispatched")
        except Exception as e:
            logger.exception(f"[graphify-plugin] on_memory_write error: {e}")
