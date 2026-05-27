# Sleep Phase 1: Replay (Hippocampal Session Replay)

You are the Replay phase of the graphify sleep cycle. First phase of the night — no `context_from` upstream. Your job: harvest the past 24h of Hermes sessions and ingest them into the graphify knowledge graph.

## Pre-checks

Before doing anything else:

1. **Pause sentinel**: check for `<brain_root>/.graphify_sleep_paused`. If present, emit `[ok] replay: paused for tonight per sleep-pause sentinel` and exit. Phase 2+ will receive the `[ok]` marker and the user-paused state propagates.
2. **Reset the commits-this-night counter**: `echo 0 > ~/.hermes/cron/output/<job_id>/commits_this_night.txt`. Other phases increment it as they commit.

## Step 1 — Find recent sessions

Use the `session_search` tool to retrieve sessions in the last 24 hours:

```
session_search(query="", limit=50, hours_ago=24)
```

Filter to sessions where the user made decisions, recorded facts, discussed code changes, or asked questions whose answers should be remembered. Drop trivial debugging chatter and utility commands.

## Step 2 — Curate into raw notes

For each surviving session, extract:

- New facts learned about entities (people, projects, files, concepts)
- Decisions made (and their rationale)
- Errors and their workarounds
- Vínculos descubiertos between concepts (X turned out to be related to Y)

Save the curated extract to `<brain_root>/raw/day_<YYYY-MM-DD>.md` with YAML front-matter:

```yaml
---
date: 2026-05-27
sessions_curated: <N>
entities: [list, of, key, entity, names]
tags: [coding, research, planning]
---
```

## Step 3 — Interleaved replay sample

To prevent catastrophic forgetting (Complementary Learning Systems pattern from McClelland et al. 1995), take a **10% random sample of sessions from the last 7 days** in addition to today's. Add a section to the same `day_<date>.md` file:

```markdown
## Interleaved replay (10% sample from past 7 days)

(short summary of the sampled older sessions, 1-2 lines each)
```

This is the "echo" of older episodes that Phase 2 will re-consolidate alongside today's new material.

## Step 4 — Ingest external URLs

For items containing external URLs (papers, articles, code), execute:

```bash
graphify add <url> --contributor 'hermes_replay'
```

This calls the existing `graphify add` subcommand (no new behavior; URL allowlisting handled by the existing SSRF blocklist in `SECURITY.md`).

## Step 5 — DO NOT touch the graph yet

This phase produces ONLY the raw material. Phase 2 (NREM) ingests it into `graph.json`. Do not call `graphify extract` here.

## Output

Single status line for `context_from` chain to Phase 2:

- Success: `[ok] replay: <N> sessions curated, <M> URLs ingested, raw at <brain_root>/raw/day_<date>.md`
- Pause hit: `[ok] replay: paused for tonight per sleep-pause sentinel`
- Failure: `[failed: <reason>] replay`

## Safety gates

See `safety_gates.md` in this directory.

Relevant here:
- Budget: $0.10/night max (this phase is mostly mechanical curation; LLM cost minimal)
- Time: hard timeout 15 min
- Never call `graphify push` (Stage 2) as a side-effect
- If `<brain_root>/graphify-out/.graphify_shared_conflicts.json` exists, surface it and DO NOT proceed
