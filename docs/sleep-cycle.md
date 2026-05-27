# Sleep cycle (cognitive consolidation)

**Status: alpha (0.10.0-alpha1). Phases 1+2 ship; Phases 3-5 follow in subsequent sprints.**

> Audience: anyone who wants to understand the nightly cognitive consolidation cycle for graphify knowledge graphs before deciding whether to install it.

## Why two systems

The same intuition behind `.gitignore` motivates the sleep cycle. Just as `.gitignore` lets you choose which files travel with your repo, the sleep cycle moves consolidated knowledge from a small frozen-per-session working memory (`MEMORY.md`, 2,200 chars in Hermes) into a large persistent knowledge graph (`graph.json`). The architecture mirrors the mammalian brain's split between hippocampus and neocortex (Complementary Learning Systems; McClelland, McNaughton & O'Reilly 1995).

| | Hippocampus | Neocortex |
|---|---|---|
| **In your agent** | `~/.hermes/MEMORY.md` | `graphify-out/graph.json` |
| Size | small (~2200 chars) | unbounded |
| Lifetime | frozen per session | persistent |
| Content | episodic (today's decisions) | semantic (long-term structure) |
| Update | atomic add/replace | streaming + nightly cycle |

## What the cycle does

Each night the cycle runs 5 phases as chained Hermes cron jobs. Each phase commits independently to git, so the cycle is auditable end-to-end and a bad night is recoverable via `git revert`.

### Phase 1 — Replay (02:00 UTC)

Mirror of hippocampal sharp-wave-ripple replay (Wilson & McNaughton 1994; Chen et al. 2021). The Hermes agent runs `session_search` over the past 24 hours, filters out trivial chatter, and writes a curated raw notes file to `<brain_root>/raw/day_<date>.md`. Adds a 10% sample of older sessions from the last 7 days to prevent catastrophic forgetting (the "interleaved replay" pattern from CLS).

### Phase 2 — NREM (02:15 UTC)

System consolidation. `graphify extract --update --mode deep` ingests the raw notes into `graph.json` incrementally; `graphify extract --cluster-only` re-runs Leiden community detection; GRAPH_REPORT.md is regenerated so downstream phases see fresh data. The phase commits `graphify-out/graph.json` to git.

### Phase 3 — SHY (03:00 UTC) — Sprint 2

Synaptic homeostasis (Tononi & Cirelli 2003, 2006, 2020). `graphify decay` multiplicatively decays every non-`EXTRACTED` edge's weight, boosts edges touched today (via the `.graphify_touched.json` sidecar), prunes sub-threshold INFERRED/AMBIGUOUS edges, removes orphan nodes (except god nodes with `max_observed_degree > 20` and `EXTRACTED` nodes). Re-clusters and commits.

### Phase 4 — REM (03:30 UTC) — Sprint 3

Three subagents in parallel:
- `graphify fuse` (KGGen-style iterative clustering; Mo et al. arXiv:2502.09956) — merges synonym nodes
- `graphify dream` (Graphusion novel triplet inference; Yang et al. arXiv:2407.10794) — proposes edges between disconnected communities
- `graphify hypothesize` — generates "H:" nodes from surprising connections in GRAPH_REPORT.md

Each subagent has a per-subagent safety gate: exits code 2 if it produces > 50 net-new entities, triggering `git reset HEAD~1`.

### Phase 5 — Wake (04:30 UTC) — Sprint 4

`graphify briefing` synthesizes the night's work into a ≤1800-char snippet (3-5 god nodes, 1-2 hypothesis nodes worth reviewing, 1 surprising connection). The Wake template calls Hermes' `memory(add, content="...")` with a timestamped `Cortex YYYY-MM-DD HH:MM` header (uniqueness guaranteed to the minute). Pre-cleanup removes Cortex entries older than 7 days; post-cleanup truncates if MEMORY usage exceeds 85%.

## The trust boundary

The cycle never reveals which private files exist. If you use Stage 2 split mode (`graph-private.json` + `graph-shared.json`), the sleep cycle currently REFUSES to run with a clear error message — full split-mode-aware sleep is deferred to Stage 4. Sprint 2 adds an opt-in `--force-split-mode-unsafe` flag that decays on the legacy graph only.

## How sync works (conceptually)

`graphify push` (Stage 2) writes the local `graph-shared.json` to a git side branch. The sleep cycle does NOT push automatically. It operates on the local cortex; team sharing is a separate user-initiated workflow.

If conflicts arise during `git pull`, the Stage 2 merge driver registered at `init-sharing` time delegates to `graphify merge-driver` which calls `three_way_merge_nodes()`. The sleep cycle's REM phase has equivalent safety gates and rolls back to the prior commit on detected anomalies.

## Empirical grounding

- **Sleep-time compute** — Lin, Snell et al. 2025 (arXiv:2504.13171, Letta + UC Berkeley): offline compute between query bursts reduces test-time compute ~5x in stateful agent regimes (Stateful GSM-Symbolic, Stateful AIME) with +13-18% accuracy gains.
- **Complementary Learning Systems** — McClelland, McNaughton & O'Reilly 1995 (Psychological Review): two-system separation prevents catastrophic forgetting.
- **Synaptic Homeostasis Hypothesis** — Tononi & Cirelli 2003-2020: wakefulness potentiates synapses; NREM downscales globally.
- **REM and creative recombination** — Lewis, Knoblich & Stickgold 2018 (Trends Cogn Sci): REM replay generates novel associations on top of NREM-consolidated structure.
- **KGGen** — Mo et al. 2025 (arXiv:2502.09956, NeurIPS 2025): iterative LLM-guided clustering for synonym fusion (66% on MINE vs 47.8% for GraphRAG).
- **Graphusion** — Yang et al. 2024 (arXiv:2407.10794, ACL Findings): novel-triplet inference between disconnected communities (+10% link prediction over supervised baselines).

## What sleep cycle is NOT

- A team feature: it's single-user. Two laptops with the same brain root work in principle via the Stage 2 merge driver but are not tested.
- A required upgrade: the cycle is opt-in via `graphify sleep install`. Users who don't run it never see it.
- A replacement for `graphify extract`: the cycle calls `graphify extract --update` in Phase 2. You can still run extract manually anytime.
- A vector-search system: graphify uses topology-only clustering (Leiden over node-link). The sleep cycle keeps that architectural property — no embeddings are introduced.

## Cost expectations

Default backend Gemini Flash. Soft cap $0.50/night per /autoplan resolution. Per-phase budgets in the manifest:
- Phase 1 (Replay): $0.10
- Phase 2 (NREM): $3.00 (deep mode extraction)
- Phase 3 (SHY): $0.20 (mostly mechanical; only `--exclude-hubs` reads)
- Phase 4 (REM): $5.00 (3 subagents × LLM calls)
- Phase 5 (Wake): $0.20

Sprint 1-3: soft tracking (logged to `~/.hermes/cron/output/<job_id>/cost.jsonl`). Sprint 4+: `--budget-mode=hard` enables pre-call abort.

## Empirical checkpoint (Sprint 1.5)

After Sprint 1 ships, run 7 nights of Phases 1+2 only. Measure delta nodes/edges per night, accumulated cost. **Does NOT block Sprint 3** (you picked the full B pipeline at D6); checkpoint data informs Sprint 2-4 tuning only.

## Quick start

```bash
uv tool install graphifyy
graphify sleep install --brain-root ~/brain
# Paste the printed snippet into your Hermes chat
```

See `graphify/sleep-cycle/README.md` for the full quick start.

## Disabling the cycle

```bash
# Skip tonight only (sentinel file)
graphify sleep pause --tonight

# Remove the cron jobs entirely
graphify sleep uninstall
```

## Next steps

- `graphify/sleep-cycle/SKILL.md` — the Hermes-facing manual that the cron templates load
- `graphify/sleep-cycle/templates/` — per-phase prompt templates
- `graphify/sleep-cycle/templates/safety_gates.md` — failure handling, budget enforcement, git rollback semantics
- ARCHITECTURE.md (in repo root) — section "Sleep cycle data flow" (added in Sprint 4 final docs sweep)
