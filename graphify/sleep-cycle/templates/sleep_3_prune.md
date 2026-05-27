# Sleep Phase 3: SHY (Synaptic Homeostasis Downscaling)

You are the SHY phase of the graphify sleep cycle. Runs at 03:00 UTC with `context_from=sleep_2_nrem`. Your job: apply Tononi-Cirelli synaptic homeostasis to the consolidated graph — multiplicative decay on plastic (INFERRED/AMBIGUOUS) edges, boost edges touched today, prune sub-threshold edges, remove orphan nodes except god-nodes and AST-extracted structure.

## Step 0 — Check upstream

You receive `context_from` from Phase 2 (NREM). Parse the marker:

- `[ok] nrem: ...` — proceed normally
- `[ok] replay: paused for tonight ...` or `[skipped — upstream paused] nrem` — emit `[skipped — upstream paused] prune` and exit. Pause propagates.
- `[failed: ...] nrem` — **SHY is INDEPENDENT of consolidation**. Decay still runs (the cortex doesn't stop renormalizing just because we couldn't consolidate today). Log the upstream failure to `~/.hermes/cron/output/sleep_3_prune/<ts>/upstream_failed.md` but proceed to Step 1.
- `[skipped — upstream failed] nrem` — same as `[failed]`: proceed anyway.

## Step 1 — Verify the graph is loadable

```bash
test -f <brain_root>/graphify-out/graph.json || {
    echo "[failed: graph.json missing] prune"
    exit 0  # graceful — Phase 4 (REM) decides whether to skip
}
```

## Step 2 — Run graphify decay

The `graphify decay` subcommand (Sprint 2 deliverable) does the actual work:

```bash
cd <brain_root>
graphify decay --rate 0.95 --threshold 0.20 .
```

Algorithm summary (full detail in `graphify decay --help`):
1. Read and clear `<brain_root>/graphify-out/.graphify_touched.json` under an exclusive flock (today's query-touched node IDs)
2. Multiplicative decay: `weight *= 0.95` on every non-EXTRACTED edge
3. Boost: `weight += 0.10` (capped at 1.0) on edges where both endpoints were touched today; increment `uses`; set `last_used = now`
4. Threshold removal: remove edges where `weight < 0.20 AND confidence != EXTRACTED`
5. Update `max_observed_degree` on every node
6. Orphan removal: prune nodes with degree==0 EXCEPT god-nodes (`max_observed_degree > 20`) and nodes that have any EXTRACTED incident edge in their history
7. Re-cluster via `graphify extract --cluster-only --exclude-hubs 99` to recompute communities
8. Commit ONLY `graphify-out/graph.json` to git

The commit uses plumbing semantics — the user's working tree is never touched. Mid-edit at 03:00 AM is safe.

## Step 3 — Split-mode handling

If the user has Stage 2 split mode active (`.graphifyshared` or `.graphifyprivate` overlays, or a configured remote in `~/.graphify/config.toml`), `graphify decay` refuses by default. Two options:

a) Document and skip: log to `~/.hermes/cron/output/sleep_3_prune/<ts>/split_mode_refusal.md` and emit `[skipped: split-mode unsupported in Sprint 2] prune`.

b) Force partial behavior: invoke `graphify decay --force-split-mode-unsafe .` which decays the LEGACY `graphify-out/graph.json` only (skipping `graph-private.json` / `graph-shared.json`). This is a stopgap until full split-mode-aware sleep ships in a hypothetical Stage 4.

The template default is (a) — refuse and document. Users opt in to (b) by passing the flag manually when running `graphify decay`. The cron job does NOT auto-enable `--force-split-mode-unsafe`.

## Step 4 — Increment commits-this-night counter

```bash
COUNTER_FILE="${HERMES_CRON_OUTPUT_DIR:-$HOME/.hermes/cron/output}/sleep_3_prune/commits_this_night.txt"
mkdir -p "$(dirname "$COUNTER_FILE")"
# graphify decay already committed; bump the counter for failure-recovery math
NREM_COUNTER="${HERMES_CRON_OUTPUT_DIR:-$HOME/.hermes/cron/output}/sleep_2_nrem/commits_this_night.txt"
PRIOR=$(cat "$NREM_COUNTER" 2>/dev/null || echo 0)
echo $((PRIOR + 1)) > "$COUNTER_FILE"
```

Phase 4 (REM, Sprint 3+) reads this counter to know how many `git reset HEAD~<n>` steps to do on failure.

## Step 5 — Report

Single status line for `context_from` chain to Phase 4:

- Success: `[ok] prune: removed=<N> edges (below threshold), pruned=<M> orphan nodes, boosted=<K> touched edges`
- Skipped (split-mode): `[skipped: split-mode unsupported in Sprint 2] prune`
- Failed (graph missing): `[failed: graph.json missing] prune`
- Failed (decay error): `[failed: <stderr first line>] prune`

## Safety gates

See `safety_gates.md`. Relevant here:
- Budget: $0.20/night (mostly mechanical; only the re-cluster step touches LLM-shaped data, and Leiden is offline)
- Time: hard timeout 10 min
- Working tree safety: `graphify decay` uses git plumbing internally
- Failure recovery: if decay corrupts the graph, the user's previous commit (NREM's) is intact — `git reset HEAD~1` undoes only the SHY decay commit
- Phase 3 NEVER aborts the night based on its own results. Subsequent phases (REM, Wake) decide whether to proceed.
