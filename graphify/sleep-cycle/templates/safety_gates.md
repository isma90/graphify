# Sleep cycle safety gates

Shared rules across all 5 phases. Templates `sleep_1_replay.md`, `sleep_2_nrem.md`, `sleep_3_prune.md` (Sprint 2+), `sleep_4_rem.md` (Sprint 3+), `sleep_5_wake.md` (Sprint 4+) all reference these.

## Failure markers in `context_from`

`context_from` from a prior phase may carry one of three markers:

- `[ok] <phase>: <details>` — phase succeeded. Proceed.
- `[failed: <reason>] <phase>` — phase failed. Each downstream phase decides whether to skip per the dependency rules below.
- `[skipped — upstream failed] <phase>` — phase skipped because an earlier phase failed. Propagate the marker.

### Dependency rules

- **Phase 3 (SHY)** is INDEPENDENT of Phases 1+2 — run even if upstream failed. Weight decay needs no fresh extraction data.
- **Phase 4 (REM)** requires Phase 2 (NREM) — skip if NREM failed. REM operates on consolidated structure.
- **Phase 5 (Wake)** emits a degraded briefing on ANY upstream failure with an explicit note: "Some phases skipped overnight; see `~/.hermes/cron/output/<job_id>/`." Never silent.

## Git rollback semantics

Each phase commits ONLY `graphify-out/graph.json`. Transient files (`.graphify_touched.json`, `dream_log.md`, `~/brain/raw/dreams/<date>.md`) must be in `.gitignore`.

Recovery from a bad phase uses `git reset HEAD~<n>` where `n` = number of phases already committed this night. NEVER `git reset --hard` (would blast prior successful phases too).

Each phase template tracks its position via `~/.hermes/cron/output/<job_id>/commits_this_night.txt` (single-line integer counter). The counter resets at the start of Phase 1 (Replay) every night.

Per /autoplan Eng decision #3.

## Working tree safety

Phase commits use git plumbing only (no `git checkout`, no `git commit` porcelain). The user's working tree is never touched. This mirrors the Stage 2 `graphify push` mechanism that ships in v0.9.0.

Concretely, the commit sequence in each template is:

```bash
# Single-file stage (NOT git add -A)
git add graphify-out/graph.json

# Plumbing commit (preserves working tree)
git -c user.email='hermes-sleep@local' -c user.name='Hermes Sleep' commit -m "<phase-name> $(date -I)" graphify-out/graph.json
```

If the user is mid-edit at 02:30 AM, their working tree stays untouched.

## Per-subagent safety gate (Phase 4 REM)

REM (Phase 4, ships in 0.10.0-rc1) dispatches 3 subagents in parallel: `fuse`, `dream`, `hypothesize`. Each one independently exits code 2 if it produces > 50 net-new entities (nodes + edges). The threshold is **per subagent**, NOT summed across all three.

When any subagent exits 2:
1. Hermes interprets exit code 2 as "abort this phase, revert this phase's commit"
2. Hermes runs `git reset HEAD~1` (one commit back)
3. Phase 4 emits `[failed: subagent <name> produced > 50 entities, reverted] rem`
4. Phase 5 (Wake) reads the failure marker and emits a degraded briefing

Per /autoplan Eng decision #8.

## Budget gates

Per-job `budget_usd` in `~/.hermes/state/graphify-sleep.manifest.json`.

**Sprint 1-3 (soft tracking)**: each phase template logs actual cost to `~/.hermes/cron/output/<job_id>/cost.jsonl` (estimated tokens × per-token rate from `graphify.llm.BACKEND_RATES`). `graphify sleep status` flags red if last-7-day average actual cost > 120% of budget. NO LLM call is aborted automatically.

**Sprint 4+ (opt-in hard cap)**: `graphify sleep install --budget-mode=hard` enables pre-call cost estimation via `tiktoken` token counts. If estimated cost exceeds the budget, the phase logs `[budget-exceeded] estimated=$X budget=$Y` and exits 0 without calling the LLM. User can override per-night via manual cron invocation.

Default rates are hardcoded constants in `graphify.llm.BACKEND_RATES`, updated quarterly via PR.

Per /autoplan resolution of Open Question #1.

## Split-mode refusal

All 5 cycle subcommands (`decay`, `fuse`, `dream`, `hypothesize`, `briefing`) refuse to run on repos with `.graphifyshared` / `.graphifyprivate` overlays present in the working tree OR a configured remote in `~/.graphify/config.toml`.

Error message:

```
graphify <cmd>: split mode (overlays or configured remote) is not yet supported by the sleep cycle.
Tracking: https://github.com/safishamsi/graphify/issues/<N>
Workaround (Sprint 2+): --force-split-mode-unsafe flag applies the operation to the legacy graph.json only.
```

Sprint 2 (0.10.0-alpha2) adds `--force-split-mode-unsafe` opt-in flag. Decay etc. then operate on legacy `graph-out/graph.json` only (not `graph-private.json` or `graph-shared.json`). Full split-mode-aware sleep is deferred to a hypothetical Stage 4.

Per /autoplan decisions #17 and #23.

## Time limits

| Phase | Hard timeout |
|---|---|
| 1 (Replay) | 15 min |
| 2 (NREM) | 30 min |
| 3 (SHY) | 10 min |
| 4 (REM) | 45 min (3 subagents × ~15 min each) |
| 5 (Wake) | 5 min |

If a phase hits its timeout, Hermes kills the process and emits `[failed: timeout]`. The next phase decides per dependency rules.

## Concurrent runs

The cycle assumes single-machine, single-user. Two machines running concurrent cycles on the same brain root MAY work via the Stage 2 git merge driver, but is NOT tested.

If you run on two machines, set distinct cron schedules (e.g., laptop at 02:00, desktop at 04:00) to avoid commit races on `graph.json`.

## Versioning

The manifest carries `"api_version"` matching the Hermes version it was generated against (per /autoplan Eng decision #7). On `graphify sleep status`, the templates check Hermes' actual version. Mismatch warns:

```
[warning] sleep cycle manifest expected Hermes 0.13.0, runtime is 0.12.0.
Some features may not work. Run: graphify sleep install --force
```

Re-running `graphify sleep install` rewrites the manifest with the current API version.
