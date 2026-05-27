# Sleep Phase 5: Wake (Morning Briefing into MEMORY.md)

You are the Wake phase of the graphify sleep cycle. Runs at 04:30 UTC with `context_from=sleep_4_rem`. Your job: synthesize the night's work into a briefing and write it to the user's `MEMORY.md` so the next session opens with consolidated knowledge in the system prompt.

## Step 0 — Check upstream

You receive `context_from` from Phase 4 (REM). Parse the marker:

- `[ok] rem: fuse=<F>, dream=<D>, hypothesize=<H> ...` — proceed with a normal briefing
- `[ok] replay: paused for tonight ...` or `[skipped — upstream paused] rem` — proceed BUT emit a degraded briefing that says "Cycle paused tonight"
- `[failed: ...] rem` OR `[skipped — upstream failed] rem` — proceed BUT emit a degraded briefing noting which upstream phases failed. NEVER write an empty briefing silently — the user must know the cycle didn't complete normally
- Any earlier-phase failure marker (e.g., upstream chain shows `[failed] nrem`) — degraded briefing notes the failure point

Per `safety_gates.md`: Wake NEVER skips silently. The user's morning MEMORY.md update is the only visible signal that the cycle ran at all.

## Step 1 — Generate the briefing

Run the briefing subcommand. It is READ-ONLY against the graph (no mutations, no git commits).

```bash
cd <brain_root>
graphify briefing --max-chars 1800 --output - > /tmp/cortex-briefing-$(date +%s).txt
BRIEFING_TEXT=$(cat /tmp/cortex-briefing-$(date +%s).txt)
```

Or in one shot:

```bash
BRIEFING_TEXT=$(cd <brain_root> && graphify briefing --max-chars 1800 --output -)
```

The briefing has a `Cortex YYYY-MM-DD HH:MM` header (timestamped to the minute — guaranteed unique even if the user runs the cycle manually multiple times in one day) and sections for god nodes, hypotheses worth reviewing, and the surprising connection of the day. Max length 1800 chars leaves 400 chars of margin for MEMORY.md's 2200-char Hermes limit.

If `graphify briefing` exits non-zero (e.g., graph.json missing), emit `[failed: briefing generation] wake` and exit. Do NOT touch MEMORY.md on briefing failure — the user's existing memory should not be corrupted by a degraded cycle.

## Step 2 — Pre-cleanup: remove Cortex entries older than 7 days

```python
# Pseudocode for the Hermes-side template (executed via Hermes memory tool)

import datetime, re

# Read current MEMORY.md
current = memory(action="view")

# Find all Cortex entry headers
# Format: "Cortex YYYY-MM-DD HH:MM"
cortex_pattern = re.compile(r"^Cortex (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})", re.MULTILINE)
cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=7)

for match in cortex_pattern.finditer(current):
    entry_date = datetime.datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M")
    if entry_date < cutoff:
        # Remove this Cortex entry (the whole block until next "Cortex " line or end of file)
        entry_start = match.start()
        next_match = cortex_pattern.search(current, entry_start + 1)
        entry_end = next_match.start() if next_match else len(current)
        old_text = current[entry_start:entry_end].rstrip("\n")
        memory(action="remove", old_text=old_text)
        # Re-read since memory mutates
        current = memory(action="view")
```

This bounds MEMORY.md growth. Per /autoplan resolution of Open Question #6: a fresh Cortex entry is `memory(add)`-ed each morning; entries older than 7 days are removed; if usage exceeds 85% post-add, additional oldest entries are removed in Step 4.

## Step 3 — Add the new briefing

```python
memory(action="add", content=BRIEFING_TEXT)
```

The briefing's `Cortex YYYY-MM-DD HH:MM` header is unique to the minute. No collision risk with substring matching (we use `memory(add)`, not `memory(replace)`). Per /autoplan Decision NEW-5.

## Step 4 — Post-add cleanup if memory usage > 85%

```python
# Check memory usage (Hermes-side)
usage = memory(action="usage")  # returns dict with .chars_used and .chars_limit
usage_pct = usage["chars_used"] / usage["chars_limit"]

while usage_pct > 0.85:
    # Find oldest Cortex entry
    oldest_match = None
    oldest_date = None
    for match in cortex_pattern.finditer(memory(action="view")):
        entry_date = datetime.datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M")
        if oldest_date is None or entry_date < oldest_date:
            oldest_date = entry_date
            oldest_match = match
    if oldest_match is None:
        break  # no Cortex entries to remove; we're at the floor
    # Remove the oldest
    current = memory(action="view")
    entry_start = oldest_match.start()
    next_match = cortex_pattern.search(current, entry_start + 1)
    entry_end = next_match.start() if next_match else len(current)
    memory(action="remove", old_text=current[entry_start:entry_end].rstrip("\n"))
    # Re-check usage
    usage = memory(action="usage")
    usage_pct = usage["chars_used"] / usage["chars_limit"]
```

Soft floor: keep at least 1 Cortex entry (today's). If only today's entry exists and we're still over 85%, surface a warning in the briefing rather than removing today's work.

## Step 5 — Send morning message to user channel

```python
send_message(
    channel="<user-configured-channel>",  # Telegram, email, Slack, etc.
    subject="🧠 Graphify Cortex — morning briefing",
    body=BRIEFING_TEXT + "\n\n— Generated by graphify sleep cycle at " + datetime.datetime.utcnow().isoformat(timespec="seconds"),
)
```

If `send_message` is not configured for this user (no channel registered), skip silently — the briefing is already in MEMORY.md so the user sees it next session.

## Step 6 — Increment commits-this-night counter and report

Wake does NOT commit `graph.json` (read-only phase). But it DOES increment the counter for accurate failure-recovery math in subsequent nights:

```bash
COUNTER_FILE="${HERMES_CRON_OUTPUT_DIR:-$HOME/.hermes/cron/output}/sleep_5_wake/commits_this_night.txt"
mkdir -p "$(dirname "$COUNTER_FILE")"
echo 0 > "$COUNTER_FILE"  # Wake adds 0 commits — but the counter exists for consistency
```

Single status line — last in the chain:

- `[ok] wake: briefing written to MEMORY.md (<N> chars), <C> Cortex entries cleaned, sent to <channel>`
- Degraded (upstream failure): `[ok] wake: degraded briefing (upstream <phase> failed); MEMORY.md updated with failure note`
- Paused: `[ok] wake: pause-night briefing (cycle paused per sentinel); MEMORY.md acknowledged`
- Failure to generate briefing: `[failed: <reason>] wake` (MEMORY.md NOT touched)

## Safety gates

See `safety_gates.md`. Wake-specific:

- **Read-only against graph**: Wake does NOT mutate graph.json. The briefing subcommand is pure read. If MEMORY.md update fails, graph.json stays intact.
- **Hard budget**: $0.20/night. Briefing generation is local (no LLM); only `send_message` may have a tiny cost depending on channel.
- **Time**: hard timeout 5 min.
- **Never overwrite MEMORY.md without a header**: Each Cortex entry has a unique timestamped header. The user can recognize generated entries vs hand-edited ones.
- **Never silent failure**: If any upstream phase failed, the briefing explicitly notes it. The user has visibility into the cycle's health every morning.

## Why memory(add) and NOT memory(replace)

Per /autoplan v2/v3 design doc resolution of Open Question #6:

`memory(replace, old_text=...)` matches by substring. Multiple Cortex entries from prior days would all contain the substring `"Cortex 2026-"` — `replace` would hit the FIRST match (ambiguous, potentially wrong entry).

`memory(add)` is unambiguous — it appends a new entry without trying to find a prior match. Combined with:
- Timestamped headers (unique to the minute)
- Pre-cleanup of entries > 7 days old (Step 2)
- Post-add usage check (Step 4)

...we get bounded growth, audit trail per day, and zero substring-collision risk.
