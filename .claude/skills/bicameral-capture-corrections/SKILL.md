---
name: bicameral-capture-corrections
description: Scans recent conversation turns (or a full session transcript at session end) for uningested corrections — load-bearing design, scope, or constraint decisions the user stated mid-session that never reached the decision ledger. AUTO-FIRES at session end via the SessionEnd hook. Can also be invoked manually after any session with implicit decisions.
---

# Bicameral Capture Corrections

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
bicameral.search(query=<one-line paraphrase of correction>, top_k=3)
```

If any result has similarity ≥ 0.75 → treat as already ingested, skip.
All others → queue as `uningested_corrections`.

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
Auto-ingest silently via `bicameral.ingest(source="conversation", decisions=[...])`.
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

**2. Determine transcript scope.**
- If invoked by SessionEnd hook: scan the full session (all user turns from
  this session).
- If invoked manually: scan the last 20 user turns as a proxy for the session.

**3. Run the canonical rubric** (Steps A → B → C above) across all turns.

**4. Filter to new findings.**
Exclude corrections that were already surfaced by preflight's step 3.5
in this session — don't re-ask about the same correction twice.

**5. If no new uningested ask-corrections:**
Exit silently. No output. The empty path is always silent.

**6. If ≤ 5 new ask-corrections:**
Present as a numbered list, ask for batch confirmation:

```
Bicameral found N uningested decision(s) from this session:

  1  <one-liner paraphrase of correction>
  2  <one-liner paraphrase of correction>
  ...

Ingest all? [Y/n or pick: 1 3]  ›
```

**7. If > 5 new ask-corrections:**
Show first 5, note the total:

```
Bicameral found N uningested decision(s). Showing 5:

  1  <one-liner>
  ...
  5  <one-liner>
  (+N more — run /bicameral:capture-corrections to review)

Ingest all 5? [Y/n or pick: 1 3 5]  ›
```

**8. For each confirmed decision, call:**
```
bicameral.ingest(
  source="conversation",
  decisions=[{
    "description": "<correction stated as a decision>",
    "source_ref": "session-correction-<YYYY-MM-DD>",
  }]
)
```
Then run the standard ratify prompt (same as bicameral-ingest step 7).

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
4. **Similarity threshold is 0.75.** Don't second-guess it in v1.
   If the check seems wrong (obvious duplicate slips through), adjust
   the query phrasing, not the threshold.
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
[ -d .bicameral ] && claude -p '/bicameral:capture-corrections' || true
```

The `.bicameral` guard keeps it silent in repos that don't use bicameral.

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
