# Sleep Phase 0: Hippocampal Drain (MEMORY.md → graphify)

You are Phase 0 of the graphify sleep cycle. Runs at 01:45 UTC, 15 minutes BEFORE Phase 1 Replay. First in the chain — no upstream `context_from`. Your job: transfer aged Cortex entries from MEMORY.md (hippocampus) into the graphify knowledge graph (neocortex) BEFORE Phase 1 starts curating today's session notes.

## Mental model

`MEMORY.md` is bounded to 2200 chars and frozen per session — fast access, limited capacity, like a working buffer. `graphify-out/graph.json` is unbounded and persistent — long-term semantic memory. Before today's new material lands, yesterday's plus older Cortex entries must move to long-term storage. This mirrors mammalian hippocampus → neocortex consolidation during sleep onset.

## Pre-checks

1. **Pause sentinel**: check for `<brain_root>/.graphify_sleep_paused`. If present, emit `[ok] drain: paused for tonight per sleep-pause sentinel` and exit. Pause propagates to all 6 phases.
2. **Reset commits-this-night counter**: `echo 0 > ~/.hermes/cron/output/sleep_0_drain/commits_this_night.txt`. Phases 2-4 increment this; rollback math depends on it.
3. **Verify plugin installed**: `test -f ~/.hermes/plugins/memory/graphify/drain.py || echo "[failed: plugin missing] drain"`. If absent, exit — the user is on graphify < 0.10.1 and shouldn't have this cron job registered.

## Step 1 — Invoke the drain via execute_code

```bash
BRAIN_ROOT="<brain_root_from_manifest>"
python -m graphify_plugin.drain --mode nightly --brain-root "$BRAIN_ROOT" --age-days 7
```

The drain logic:
1. Reads `~/.hermes/memories/MEMORY.md`
2. Parses entries by `§` delimiter (Hermes convention)
3. For each entry: matches Cortex header `^Cortex YYYY-MM-DD HH:MM`. Entries WITHOUT this header are user-curated — they stay untouched. The user owns their curated memory.
4. For Cortex entries older than 7 days:
   a. Composes archive content (full entry + metadata header)
   b. Invokes `graphify add <tmpfile> --contributor hermes_hippocampal_drain`
   c. On success: removes the entry from MEMORY.md
5. Returns JSON: `{"drained": N, "skipped_user_curated": M, "errors": [...], "remaining_chars": K}`

## Step 2 — Parse drain output

Capture stdout. Expected shape:

```json
{
  "drained": 3,
  "skipped_user_curated": 5,
  "errors": [],
  "remaining_chars": 1240
}
```

If `errors` is non-empty, surface each error to stderr (one line each, prefix `[graphify drain]`). Continue execution — partial drain is acceptable.

## Step 3 — Report status to Phase 1

Single status line — first in the `context_from` chain.

- Success (some entries drained): `[ok] drain: archived <N> Cortex entries to graphify (>7 days old); MEMORY.md now at <K> chars; <M> user-curated entries untouched`
- Success (nothing to drain): `[ok] drain: 0 entries to archive (no Cortex entries older than 7 days); MEMORY.md at <K> chars`
- Paused: `[ok] drain: paused for tonight per sleep-pause sentinel`
- Plugin missing: `[failed: plugin missing — install graphify >= 0.10.1] drain`
- Partial errors: `[ok with warnings] drain: archived <N> entries but encountered <E> errors; see cron output`

Phase 1 (Replay) reads this status line and proceeds normally regardless of drain outcome — drain is a courtesy ahead of the cycle, not a precondition.

## Safety gates

See `safety_gates.md`. Relevant here:

- **NO git commit**: drain modifies MEMORY.md (Hermes-owned) and graph.json (via `graphify add`, which has its own commit). Phase 0 does NOT itself run `git commit graph.json` — `graphify add` handles that.
- **Budget**: $0.05/night. Drain itself doesn't call LLM; `graphify add` does invoke an LLM for new content extraction (~10-30 tokens per archived entry × ~5 entries max).
- **Time**: hard timeout 5 minutes.
- **Working-tree safety**: drain reads MEMORY.md, parses, writes back the trimmed content. Direct file edits — no git operations on the Hermes home (MEMORY.md isn't in a git repo by default).
- **Idempotency**: re-running drain is safe — entries already removed produce zero work. The plugin's drain function is restart-safe.
- **Never drains user-curated**: entries without `Cortex YYYY-MM-DD HH:MM` header are explicitly skipped. The user owns their hand-curated memory.

## Why Phase 0 instead of extending Phase 5 Wake

The previous design had Wake (Phase 5, 04:30) delete old Cortex entries to make room for the new morning entry. That deletion was destructive — knowledge was permanently lost from MEMORY.md and never made it anywhere else.

Phase 0 inverts this: aged entries go to graphify FIRST, then the deletion happens (now safe — the data persists in graphify). Wake at 04:30 still writes today's new Cortex entry, but it no longer has to delete anything because Phase 0 already created room.

This pre-cycle drain is the biological analog of pre-sleep memory consolidation: the brain doesn't wait until REM to start moving experiences from hippocampus to neocortex — it starts immediately on sleep onset.
