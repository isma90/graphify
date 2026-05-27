# Graphify Sleep Cycle

Nightly cognitive consolidation for your personal knowledge graph. Replay→NREM→SHY→REM→Wake phases run as chained Hermes cron jobs while you sleep. Wake up to a consolidated graph and a Cortex briefing in your `MEMORY.md`.

## Mental model

`.gitignore` lets you choose which files travel with your repo. The sleep cycle does the same thing for the knowledge graph derived from those files. Each night the cycle:

1. **Replays** what you did today (session_search → curated raw notes)
2. **Consolidates** the graph (`graphify extract --update --mode deep`)
3. **Prunes** weak edges (Tononi SHY — synaptic homeostasis)
4. **Dreams** new connections (KGGen iterative clustering + Graphusion novel-triplet inference)
5. **Wakes** with a briefing in your agent's `MEMORY.md`

Hippocampus = `MEMORY.md` (small, frozen-per-session, episodic). Neocortex = `graph.json` (large, persistent, semantic). The cycle moves knowledge from one to the other while you sleep.

## When to use it

- Single-user setup with multi-day knowledge work (research, writing, coding)
- Hermes agent (the cron + delegate + memory orchestrator)
- You commit your graph (`graphify-out/graph.json`) to git
- You want an auditable trail (each phase commits separately; `git revert` undoes a bad night)

If you don't use Hermes, the cycle won't run — but you can still call individual subcommands (`graphify decay`, `graphify fuse`, etc.) manually.

## Quick start

```bash
# 1. Install graphify (>= 0.10.0-alpha1)
uv tool install graphifyy

# 2. Install the sleep-cycle skill bundle (copies templates to ~/.hermes/skills/sleep-cycle/)
graphify sleep install --brain-root ~/brain

# 3. Paste the printed cron-registration snippet into your Hermes chat
# (the snippet includes cronjob delete+create lines so re-paste is safe)
```

## What to expect

- **Timing**: jobs run between 02:00 and 05:00 UTC (configurable via `--schedule`)
- **Cost**: ~$0.50/night soft-tracked by default (Gemini Flash backend; opt in to higher budgets per phase via manifest)
- **Result**: by 04:30 UTC your `MEMORY.md` contains a fresh `Cortex YYYY-MM-DD HH:MM` entry with 3-5 god nodes, 1-2 dream hypotheses, and the most surprising connection of the day
- **Audit**: `git log graphify-out/graph.json` shows one commit per phase; `git diff HEAD~5..HEAD` shows the entire night's work

## Pausing the cycle

```bash
# Skip tonight's cycle (writes a sentinel file checked by Phase 1)
graphify sleep pause --tonight

# Resume
graphify sleep resume   # or: rm ~/brain/.graphify_sleep_paused
```

## Sprint roadmap

| Release | Phases shipped | Status |
|---|---|---|
| 0.10.0-alpha1 | 1, 2 (Replay + NREM) | current |
| 0.10.0-alpha2 | + 3 (SHY decay) | next |
| 0.10.0-rc1 | + 4 (REM: fuse + dream + hypothesize) | sprint 3 |
| 0.10.0 stable | + 5 (Wake + briefing) | sprint 4 |
| 0.10.1 | optional Hermes memory provider plugin | follow-up |

## Demo

Want to see Phases 1+2 run synchronously without waiting 24h?

```bash
graphify sleep demo --brain-root ~/brain
```

Runs on a fixture-sized graph (~500 nodes) and emits a sample briefing to stdout in ~30 seconds. Useful for verifying the install and previewing what the morning briefing will look like.

## Learn more

- `docs/sleep-cycle.md` — full conceptual flow, neuroscience grounding, sprint-by-sprint detail
- `SKILL.md` (this directory) — LLM-facing manual that the cron templates load
- `templates/safety_gates.md` — failure handling, budget enforcement, git rollback semantics
- Research grounding: Lin/Snell arXiv:2504.13171 (sleep-time compute), Tononi & Cirelli (SHY), McClelland et al. 1995 (CLS), KGGen arXiv:2502.09956, Graphusion arXiv:2407.10794
