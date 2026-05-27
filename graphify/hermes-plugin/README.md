# Graphify Hermes Plugin

**Hippocampus → Neocortex consolidation for AI agents.**

This plugin makes the graphify ↔ Hermes integration fully automatic. Once installed:

- **MEMORY.md becomes your hippocampus** — fast access, bounded at 2200 chars, frozen per session
- **Graphify becomes your neocortex** — unbounded, persistent, structured knowledge graph
- **Aged Cortex entries auto-transfer** from MEMORY.md to graphify after 7 days (instead of being deleted)
- **Cron jobs auto-register** in Hermes — no more "paste this snippet in chat"
- **Memory pressure relief** — when MEMORY.md hits 85%, the oldest Cortex entry auto-drains in the background

## Install

```bash
uv tool install tecnoandina-graphify  # >=0.10.1
graphify sleep install --brain-root ~/brain
# Restart Hermes. That's it. The plugin handles the rest.
```

## How it works

| Phase | Time (UTC) | What happens |
|-------|------------|--------------|
| **0 — Drain** (new in 0.10.1) | 01:45 | Cortex entries >7 days old archived to graphify, then removed from MEMORY.md |
| 1 — Replay | 02:00 | Hermes session_search → curated raw notes |
| 2 — NREM | 02:15 | `graphify extract --update --mode deep` consolidates the cortex |
| 3 — SHY | 03:00 | `graphify decay` prunes weak edges (Tononi homeostasis) |
| 4 — REM | 03:30 | `graphify fuse` + `dream` + `hypothesize` introduce novelty |
| 5 — Wake | 04:30 | `graphify briefing` writes a Cortex entry to MEMORY.md |

The plugin auto-registers all 6 jobs on the first Hermes session after `graphify sleep install`. Re-running install updates the bundle; cron registrations stay valid.

## Memory consolidation policy

- **Drained**: only entries with `Cortex YYYY-MM-DD HH:MM` headers, older than 7 days
- **Never drained**: hand-curated entries from `memory(action=add)` invocations (no Cortex header)
- **Archive format**: each drained entry becomes a markdown document in graphify with `contributor=hermes_hippocampal_drain` and the original timestamp preserved

You own your curated memory. The plugin only manages the auto-generated Cortex entries.

## Verification

```bash
# Plugin installed?
ls ~/.hermes/plugins/memory/graphify/

# Manifest read?
cat ~/.hermes/state/graphify-sleep.manifest.json | jq '.jobs[].name'
# Should show: sleep_0_drain, sleep_1_replay, sleep_2_nrem, sleep_3_prune, sleep_4_rem, sleep_5_wake

# Crons registered after first Hermes session?
cat ~/.hermes/plugins/memory/graphify/state/registered_at.txt

# Drain log after first run?
ls ~/.hermes/plugins/memory/graphify/state/
```

## Disable

```bash
rm -rf ~/.hermes/plugins/memory/graphify/
# Then in your Hermes chat:
cronjob(action="delete", name="sleep_0_drain")
# ... (5 more deletes for sleep_1_* through sleep_5_*)
```

## Mental model: why two systems?

The mammalian brain separates short-term episodic memory (hippocampus) from long-term semantic memory (neocortex). During sleep, the hippocampus replays its day's experiences to the neocortex, which gradually integrates them into stable knowledge structures.

Hermes' MEMORY.md is hippocampal: small (2200 chars), fast (always in the system prompt), session-frozen. Graphify is neocortical: unbounded, structured, queryable on demand. Without this plugin, MEMORY.md fills up and old Cortex entries are deleted to make room. With the plugin, those entries are archived to graphify first — your knowledge accumulates instead of being lost.

This is the closest computational analog to biological memory consolidation that's been shipped in any open-source agent stack. See `docs/sleep-cycle.md` for the full theoretical grounding (Lin/Snell sleep-time compute, Tononi SHY, McClelland CLS, Lewis REM creativity).

## Status

- Version: 0.10.1
- License: MIT
- Source: https://github.com/safishamsi/graphify (upstream) + Tecnoandina fork
