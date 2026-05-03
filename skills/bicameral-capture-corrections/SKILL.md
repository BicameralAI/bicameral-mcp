---
name: bicameral-capture-corrections
description: Scans recent conversation turns (or a full session transcript at session end) for uningested corrections — load-bearing design, scope, or constraint decisions the user stated mid-session that never reached the decision ledger. AUTO-FIRES at session end via the SessionEnd hook. Can also be invoked manually after any session with implicit decisions.
---

# Bicameral Capture Corrections

> Tuning parameters for this skill are defined in `skills/CONSTANTS.md`.

Closes the gap where user corrections shape code but never reach the ledger.
Bicameral only captures what gets explicitly ingested. This skill catches the
rest — the "actually, don't do X", "wait, that should use Y", "let's not go
that route" moments that are real decisions but rarely get written down.

Two modes:
- **In-session (via preflight step 3.5)** — scans last ~10 user turns on each
  code verb, silently ingests mechanical fixes, surfaces ask-corrections with a
  single question.
- **SessionEnd batch (auto-fired by hook)** — scans the full session transcript
  at exit, prompts for any uningested ask-corrections the user hasn't seen yet.

---

## Telemetry

> **Guard**: Only call `skill_begin` and `skill_end` if telemetry is enabled. Telemetry is enabled by default; disabled by setting `BICAMERAL_TELEMETRY=0` (or `false`/`off`/`no`). If disabled, skip both calls and omit all `diagnostic` tracking.

**At skill start** (before any tool calls):
```
bicameral.skill_begin(skill_name="bicameral-capture-corrections", session_id=<uuid4>,
  rationale="<one-liner: why triggered — e.g. 'SessionEnd hook — scanning full transcript' or 'in-session scan via preflight'>")
```

**At skill end** (after all work is complete):
```
bicameral.skill_end(skill_name="bicameral-capture-corrections", session_id=<stored_id>,
  errored=<bool>, error_class="<if errored>",
  diagnostic={
    g11_corrections_turns_scanned: N,
    g11_corrections_prefilter_retained: N,
    g11_corrections_classified_ask: N,
    g11_corrections_classified_mechanical: N,
    g11_corrections_classified_not: N,
    g11_corrections_dedup_removed: N,
    g11_user_overrode: N,   # ask corrections user declined — labeled precision signal
  })
```

Pass `invocation_mode` as a top-level string kwarg (not inside `diagnostic`):
- `invocation_mode="auto_ingest"` — fired by SessionEnd hook with `--auto-ingest`
- `invocation_mode="manual"` — invoked directly by the user

`error_class` values (pass only when `errored=true`): `ledger_empty`, `user_abort`, `other`.

**In-session mode** (invoked by preflight step 3.5): emit the same `skill_end` call
but populate only the fields available in the shorter scan scope:
`g11_corrections_turns_scanned`, `g11_corrections_prefilter_retained`,
`g11_corrections_classified_ask`, `g11_corrections_classified_mechanical`,
`g11_corrections_classified_not`, `g11_corrections_dedup_removed`.
Set `g11_user_overrode` to `0` (no batch confirmation in in-session mode).

---

## Canonical scan-and-classify rubric

<!-- This section is the authoritative source. bicameral-preflight/SKILL.md
     step 3.5 is derived from it. Keep both in sync. -->

### Step A — cheap pre-filter

Retain only messages with at least one correction marker (case-insensitive):

`actually` · `shouldn't` · `should not` · `don't use` · `do not use` ·
`wait,` · `no wait` · `nope` · `not X` (negation + referent) ·
`instead of` · `rather than` · `let's not` · `that shouldn't` ·
`we shouldn't` · `that's wrong` · `wrong approach`

Zero matches → skip entirely.

### Step B — classify candidates

For each candidate user message, classify as one of:

- **correction (ask)** — load-bearing design, scope, or product decision
  that contradicts, redirects, or constrains in-flight work. It must be:
  - Stated by the *user* (not Claude — Claude's responses are downstream)
  - Substantive: affects code behavior, product semantics, or architecture
  - Example: *"abandoned checkout shouldn't use account_status — that
    conflates signed-up-never-paid with churned"*

- **correction (mechanical)** — pure symbol/name clarification with no
  design impact. No new constraint. Would not affect architecture if
  someone else re-derived the same code.
  - Example: *"s/account_status/stripe_status/"*

- **not-a-correction** — clarifying question, acknowledgment, reaction
  ("nice!", "got it"), off-topic, minor copy-edit. Skip.

Only `user` turns qualify. Claude's own responses are never corrections.

### Step C — ledger dedup check

For each **ask** correction:

```
bicameral.search(query=<one-line paraphrase of correction>, top_k=3, min_confidence=0.4)
```

If any result is returned → treat as already ingested, skip.
`bicameral.search` uses full-text scoring; `min_confidence=0.4` sets the
floor. Presence in the result set (not a score value) is the dedup signal.
All corrections with no results → queue as `uningested_corrections`.

For **mechanical** corrections: skip the ledger check, auto-ingest directly.

---

## In-session mode

Invoked by `bicameral-preflight` step 3.5 with `--mode in-session`.

Scope: last ~10 user messages in the current conversation (not the full
session — preflight fires on every code verb, so a full-session scan would
re-examine the same turns repeatedly).

### Steps

**1. Run the canonical rubric** (Steps A → B → C above) on the last ~10
user messages.

**2. Mechanical corrections:**
Auto-ingest silently via `bicameral.ingest(source="agent_session", decisions=[...])`.
No user question asked.

**3. Ask corrections:**
Return to preflight's step 3.5 caller as `uningested_corrections` findings.
Preflight merges them into its stop-and-ask queue (one question max,
priority slot 3: after drift, before open questions).

**4. Silent empty path.**
If no corrections found, return nothing. Preflight continues without any
capture-corrections output.

---

## SessionEnd batch mode

Fires via the `SessionEnd` hook in `.claude/settings.json`. Also invocable
manually as `/bicameral:capture-corrections`.

### Steps

**1. Check for `.bicameral/` directory.**
If not present, exit silently — this repo isn't using bicameral.

**2. Determine invocation mode and transcript scope.**
- If invoked with `--auto-ingest` (by the SessionEnd hook): scan the full
  session and skip the user confirmation in steps 6-7 — auto-ingest all
  found corrections immediately without prompting.
- If invoked manually (no flag): scan the last 20 user turns as a proxy
  for the session and show the confirmation flow.

**SessionEnd-hook transcript propagation**: when invoked via the
SessionEnd hook (`--auto-ingest` mode), the parent session's transcript
path is provided via the `BICAMERAL_PARENT_TRANSCRIPT_PATH` env var.
Read the JSONL at that path to scan the user's last ~10 messages for
uningested corrections. Without this env var (e.g., manual invocation),
the skill scans only the live conversation context.

**3. Run the canonical rubric** (Steps A → B → C above) across all turns.

**4. Filter to new findings.**
Exclude corrections that were already surfaced by preflight's step 3.5
in this session — don't re-ask about the same correction twice.

**5. If no new uningested ask-corrections:**
Exit silently. No output. The empty path is always silent.

**6. Surface corrections via `AskUserQuestion`.**
Regardless of count, batch into groups of ≤ 4. For each batch call:

```python
AskUserQuestion({
  question: "Bicameral found N uningested decision(s) from this session — ingest any? (batch M of K)",
  multiSelect: True,
  options: [
    { label: "<one-liner paraphrase of correction>" },
    ...  # one entry per correction in this batch
  ]
})
```

No pre-selections — user opts in to each correction. Loop through all batches before proceeding to step 8. Collect all selected corrections across batches.

`g11_user_overrode` = total corrections offered but NOT selected across all batches (offered − accepted).

**8. For each confirmed decision, call:**
```
bicameral.ingest(
  source="agent_session",
  decisions=[{
    "description": "<correction stated as a decision>",
    "source_ref": "session-correction-<YYYY-MM-DD>",
  }]
)
```
Do **not** run the ratify prompt here. Ratification is surfaced by
`bicameral-history` when the user next reviews the ledger — grouping
all unratified proposals together is a better experience than a ratify
gate at the end of every session.

**9. Confirm:**
```
✓ Ingested N/N corrections — proposals pending ratification.
  (M skipped)
```

---

## Rules

1. **Silent empty path.** If nothing to surface, produce zero output.
   Never say "I checked and found nothing." Never say "all good."
2. **Only user turns.** Claude's own text is never a correction source.
3. **No double-ask.** If preflight already surfaced a correction this
   session, do not surface it again in the SessionEnd batch.
4. **Dedup by presence, not score.** Call `bicameral.search` with
   `min_confidence=0.4`. If any result is returned, treat the correction
   as already ingested. Search scores are corpus-dependent and unbounded —
   never gate on a numeric score value.
5. **Ingest as proposals.** Captured corrections enter as `proposed`
   and need explicit ratification — same as all other ingests.
6. **Guard on `.bicameral/`.** Never run in repos without a bicameral
   setup. The hook fires globally; the guard keeps it scoped.

---

## SessionEnd hook

The SessionEnd hook is installed automatically by `bicameral setup` into the
user's project `.claude/settings.json`. No manual configuration needed.

Command written by the setup wizard:
```
[ -d .bicameral ] && [ -z "$BICAMERAL_SESSION_END_RUNNING" ] && BICAMERAL_SESSION_END_RUNNING=1 claude -p '/bicameral:capture-corrections --auto-ingest' || true
```

Two guards:
- `.bicameral` directory check — keeps it silent in repos that don't use bicameral.
- `BICAMERAL_SESSION_END_RUNNING` env var — the child `claude -p` process inherits
  the env var, so when it terminates and fires its own SessionEnd hook, the guard
  sees the var is set and exits immediately. Prevents infinite recursion.

`--auto-ingest` skips the interactive Y/n confirmation (non-interactive invocation).

---

## Example

**Session summary:**
- User said: *"wait, pagination should default to 25 not 10 — 10 is too aggressive"*
- Preflight caught it mid-session, user skipped ("too minor")
- Session ends

**SessionEnd batch output:**
```
Bicameral found 1 uningested decision from this session:

  1  Pagination defaults to 25 items per page (not 10)

Ingest? [Y/n]  ›
```

User types `y`. Ingested as proposal. Ratify prompt follows.
