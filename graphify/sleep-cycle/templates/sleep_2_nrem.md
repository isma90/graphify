# Sleep Phase 2: NREM (System Consolidation)

You are the NREM phase of the graphify sleep cycle. Runs at 02:15 UTC with `context_from=sleep_1_replay`. Your job: consolidate the day's raw replay into the persistent neocortex (`graph.json`).

## Step 0 — Check upstream

You receive `context_from` from Phase 1. Parse the marker:

- `[ok] replay: ...` — proceed to Step 1
- `[ok] replay: paused for tonight ...` — emit `[skipped — upstream paused] nrem` and exit
- `[failed: ...] replay` — log to `~/.hermes/cron/output/sleep_2_nrem/<ts>/upstream_failed.md` and emit `[skipped — upstream failed] nrem` and exit (Phase 3+ proceed independently)

## Pre-flight

Verify the brain root is in a git repository AND has a clean working tree (or at most untracked files outside `graphify-out/`). If the working tree has staged or unstaged changes to `graphify-out/graph.json`, surface a warning to stderr but proceed — the user may be experimenting, and `graphify extract` is safe.

## Step 1 — Run graphify extract incremental

```bash
cd <brain_root>
graphify extract . --update --mode deep
```

`--update` is incremental (SHA256 content-only cache). `--mode deep` produces more INFERRED edges. Both flags are existing graphify capabilities (since Stage 0).

This is the most expensive step. Budget tracking via the manifest's `budget_usd` for `sleep_2_nrem`. Default $3.00/night assuming Gemini Flash + a 50k-node corpus.

## Step 2 — Re-cluster

```bash
graphify extract . --cluster-only
```

Re-runs Leiden community detection. Seed is fixed (community IDs stable across runs from v0.7.0).

## Step 3 — Regenerate `GRAPH_REPORT.md`

```bash
graphify extract . --cluster-only --no-viz
```

Regenerates the report from the now-updated graph. Phase 4 (REM) reads this report to find "surprising connections" for hypothesis generation. Stale reports lead to stale hypotheses, so we regenerate here at the end of NREM.

Per /autoplan Eng decision #6.

## Step 4 — Commit

```bash
cd <brain_root>
# Stage ONLY graph.json (transient files are in .gitignore)
git add graphify-out/graph.json

# Plumbing-safe commit (preserves user's working tree on other files)
git -c user.email='hermes-sleep@local' -c user.name='Hermes Sleep' commit -m "NREM consolidation $(date -I)" graphify-out/graph.json

# Increment commits-this-night counter (used by rollback if a later phase fails)
COUNTER_FILE="${HERMES_CRON_OUTPUT_DIR:-$HOME/.hermes/cron/output}/sleep_2_nrem/commits_this_night.txt"
mkdir -p "$(dirname "$COUNTER_FILE")"
echo $(($(cat "$COUNTER_FILE" 2>/dev/null || echo 0) + 1)) > "$COUNTER_FILE"
```

The git commit is the transactional unit. If Phase 3 (SHY) or Phase 4 (REM) fails catastrophically, the recovery is `git reset HEAD~<n>` where `n` reads from the counter.

## Step 5 — Compute and report deltas

```bash
delta_nodes=$(graphify stats --json | jq '.nodes')   # post-NREM count
# (compare to pre-NREM count captured in Step 1 or read from git diff HEAD~1)
```

Surface the delta in the status line.

## Output

Single status line for `context_from` chain to Phase 3:

- Success: `[ok] nrem: delta_nodes=<N>, delta_edges=<M>, top_inferred=<short list of 3 new INFERRED edges>`
- Skipped: `[skipped — upstream <state>] nrem`
- Failure: `[failed: <reason>] nrem`

## Safety gates

See `safety_gates.md`.

Relevant here:
- Budget: $3.00/night (LLM calls for `--mode deep` extraction). Soft-track to `cost.jsonl`.
- Time: hard timeout 30 min.
- If `delta_nodes > 1000`, surface a warning (likely runaway extraction or bug; user should investigate).
- Never overwrite `graph.json` without a `git commit` to mark the transactional boundary.
- Refuse to operate on split-mode repos without `--force-split-mode-unsafe` (Sprint 2+). Sprint 1 NREM operates on legacy `graph.json` only; split-mode users see the refusal error.
