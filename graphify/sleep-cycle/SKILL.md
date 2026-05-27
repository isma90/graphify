---
name: graphify-sleep-cycle
description: "Nightly cognitive consolidation cycle for graphify knowledge graphs. Replay→NREM→SHY→REM→Wake phases run as chained Hermes cron jobs, mirroring mammalian sleep biology. Use when user asks about sleep cycle, nightly graph consolidation, dream hypotheses, or memory consolidation."
trigger: graphify sleep
---

# Graphify Sleep Cycle (Hermes integration)

A 5-phase nightly cognitive consolidation cycle that runs over the local graphify knowledge graph. Mirrors mammalian sleep biology (hippocampus = MEMORY.md, neocortex = graph.json). Each phase commits to git for auditability and rollback.

## Phases

| # | Phase | Time (UTC) | Subcommand | Purpose | Ships in |
|---|---|---|---|---|---|
| 1 | Replay | 02:00 | `session_search` + `graphify add` | Curate hippocampal replay from recent sessions | 0.10.0-alpha1 |
| 2 | NREM | 02:15 | `graphify extract --update --mode deep` | System consolidation + re-clustering | 0.10.0-alpha1 |
| 3 | SHY | 03:00 | `graphify decay` | Synaptic homeostasis (weight decay + prune) | 0.10.0-alpha2 |
| 4 | REM | 03:30 | `graphify fuse` + `dream` + `hypothesize` | Creative recombination | 0.10.0-rc1 |
| 5 | Wake | 04:30 | `graphify briefing` + `memory(add)` | Morning briefing + MEMORY.md update | 0.10.0 |

## When you receive `context_from` from a prior phase

The chained cron job runtime injects the prior phase's output as your `$CONTEXT_FROM_PREVIOUS` env var. The output carries one of three markers:

- `[ok] <phase>: <details>` — prior phase succeeded. Proceed.
- `[failed: <reason>] <phase>` — prior phase failed. Decide per-phase whether to skip.
- `[skipped — upstream failed] <phase>` — earlier phase failed; this phase was already a skip-through.

Per-phase dependency rules:

- **SHY (Phase 3)** is INDEPENDENT of Phases 1+2 — run even if upstream failed (weight decay needs no fresh data).
- **REM (Phase 4)** requires NREM (Phase 2) — skip if NREM failed.
- **Wake (Phase 5)** emits a degraded briefing on upstream failure with explicit note: "Some phases skipped overnight; see `~/.hermes/cron/output/<job_id>/`."

## Output contract

Every phase ends with exactly one status line to stdout. The runtime captures this and prepends it to the next phase's prompt as `$CONTEXT_FROM_PREVIOUS`.

Format: `[ok|failed: <reason>|skipped — upstream failed] <phase_name>: <details>`

## Side files

| Path | Purpose | Commit? |
|---|---|---|
| `graphify-out/graph.json` | The cortex; updated by Phases 2, 3, 4 | **YES** (only this file per phase) |
| `graphify-out/GRAPH_REPORT.md` | Regenerated at end of Phase 2 | NO (rebuilt on demand) |
| `graphify-out/.graphify_touched.json` | Touch log consumed by Phase 3 decay | NO (`.gitignore`) |
| `~/brain/raw/day_<date>.md` | Phase 1 replay output | NO (`.gitignore`) |
| `~/brain/raw/dreams/<date>.md` | Phase 4 hypothesize justification | NO (`.gitignore`) |
| `~/.hermes/MEMORY.md` | Phase 5 wake target (managed by Hermes `memory` tool) | n/a (Hermes-owned) |
| `~/.hermes/skills/sleep-cycle/.graphify_version` | Install version stamp | n/a |
| `~/.hermes/state/graphify-sleep.manifest.json` | The cron job manifest | n/a |
| `~/.hermes/cron/output/<job_id>/cost.jsonl` | Per-phase LLM cost tracking | n/a |

**Unrelated Stage 2 files** (do NOT confuse with sleep cycle):
- `graphify-out/.graphify_shared_base` — Stage 2 sync watermark (team sharing, not sleep)
- `graphify-out/.graphify_shared_conflicts.json` — Stage 2 pull conflicts (team sharing, not sleep)

## Safety gates

See `safety_gates.md` in this same directory.

Highlights:
- Each phase commits **ONLY** `graphify-out/graph.json`. Transient files in `.gitignore`.
- Failure recovery uses `git reset HEAD~<n>` per phase, NOT `git reset --hard`.
- Phase 4 REM has per-subagent safety gates (each of `fuse`, `dream`, `hypothesize` exits code 2 if it produces > 50 net-new entities).
- All 5 cycle subcommands refuse to run on split-mode repos (overlays / configured remote) until Stage 4 designs the interaction. Override: `--force-split-mode-unsafe` flag.

## Honesty rules

- **Never** propose moving nodes between buckets (private / shared from Stage 2) without an explicit user instruction. The way to reclassify a file is to edit `.graphifyshared` and re-run `graphify extract`.
- **Never** invoke `graphify push` (Stage 2) as a side-effect of the sleep cycle. The cycle operates on the local cortex; team sharing is a separate user-initiated workflow.
- When `graphify-out/.graphify_shared_conflicts.json` (Stage 2 pull conflict file) exists, surface it before doing anything else. Do not let the cycle run on top of unresolved sync state.
- **Never** emit an empty briefing on upstream failure. Phase 5 reads upstream markers; on any failure, the briefing includes an explicit note instead of silently overwriting MEMORY.md with empty content.
- The 5-phase metaphor is a mental model, not biology. When the analogy breaks (e.g., REM is pure LLM speculation, not actual eye movement), call it out. Don't oversell the neuroscience.
- **Never** call LLM-cost-bearing operations (Phases 2, 4) past their budget without explicit user override. Soft budgets log to `cost.jsonl`; hard budgets abort the LLM call.
- **Never** touch the user's working tree. Phases use git plumbing only (mirroring Stage 2's `graphify push` mechanism). The user might be mid-edit at 02:30 AM.

## Operational notes

- The cycle assumes single-machine, single-user. Two machines running concurrent cycles on the same brain root MAY work via the Stage 2 merge driver, but is not tested.
- Default backend: Gemini Flash. Hard cap: $0.50/night soft-tracked by default (Sprint 1+). Override per-phase budgets in the manifest.
- Default schedule: 02:00–05:00 UTC. Configure via `graphify sleep install --schedule "<cron expr>"`.
- The cycle is opt-in. Users without `graphify sleep install` never see the cycle. Users with the install can pause for one night via `graphify sleep pause --tonight`.

## Sprint roadmap (current = 0.10.0-alpha1)

Sprint 1 (alpha1) — Phases 1+2 (Replay + NREM). Schema additions. Demo subcommand. Touch logging stub. Manifest with 2 jobs.

Sprint 2 (alpha2) — Phase 3 (SHY decay). Touch logging hook in serve.py. Manifest grows to 3 jobs. Split-mode partial unblock via `--force-split-mode-unsafe`.

Sprint 3 (rc1) — Phase 4 (REM: fuse, dream, hypothesize). 3 inlined subagents. Per-subagent safety gates. Manifest grows to 4 jobs.

Sprint 4 (0.10.0) — Phase 5 (Wake + briefing). MEMORY.md timestamped-add pattern. Manifest at full 5 jobs. Final docs sweep.

Optional plugin (0.10.1) — `~/.hermes/plugins/memory/graphify/` mirroring Hindsight pattern.
