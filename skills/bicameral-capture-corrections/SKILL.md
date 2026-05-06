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
    g11_queue_drained: N,   # pending files fully processed and archived (#156 PR B)
    g11_queue_remaining: N, # pending files left after drain (>0 when cap was hit OR partial processing left files for next preflight)
    g11_queue_cap_hit: <bool>, # true if accumulated ask-corrections reached 4 mid-drain
  })
```

Pass `invocation_mode` as a top-level string kwarg (not inside `diagnostic`):
- `invocation_mode="queue_drain"` — invoked by next-session preflight Step 3.5 / Step 0 to drain `<repo>/.bicameral/pending-transcripts/` (the SessionEnd hook itself is now a queue writer, not a capture-corrections invoker — see #156)
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

### Step 0 — drain the pending-transcripts queue (#156)

Before scanning the current session's transcript, check `<repo>/.bicameral/pending-transcripts/`. Each `*.jsonl` file there is a transcript from a prior session whose corrections never surfaced (the SessionEnd hook deferred them to next-session triage rather than running an empty `claude -p` subprocess that couldn't see the parent transcript).

For each pending file, in mtime-order (oldest first):

1. Read the file (it's a Claude Code transcript JSONL — same shape as the current session's, just from a prior run).
2. Apply Steps A/B/C below to the file's user turns. Treat each correction-marker hit as a candidate for ingest, just like the in-session path.
3. After processing, archive the file by invoking the queue module via the dedicated helper:

   ```
   python3 scripts/hooks/transcript_archive.py <basename>.jsonl
   ```

   `<basename>.jsonl` is the filename only (e.g. `abc-1234.jsonl`), not the full path. The helper resolves it to `<repo>/.bicameral/pending-transcripts/<basename>` itself, calls `events.transcript_queue.archive_processed`, and ensures idempotent overwrite + cross-platform behavior. Exit code `0` on success, `2` on usage error (unsafe basename), `1` on missing file.

   Do NOT use raw `mv` shell — it bypasses the queue module's idempotent overwrite semantic and breaks on Windows.

If `<repo>/.bicameral/pending-transcripts/` doesn't exist or is empty, skip Step 0 entirely.

The processed-transcripts folder is kept for audit; v1 has no automatic cleanup. A future team-server config may override retention.

**Why this Step 0 exists**: prior to #156, the canonical `SessionEnd` hook ran `claude -p '/bicameral-capture-corrections --auto-ingest'` which spawned an empty subprocess that couldn't see the parent transcript — corrections silently failed to surface. The new shape defers transcript handling to the next session, where the agent + user are present with full ledger context to confirm or dismiss each correction (matches the in-session path's UX).

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
bicameral.history(feature_filter=<short keyword from the correction>)
```

(`bicameral.search` was retired — `history` with a substring `feature_filter`
is the live equivalent. There is no `top_k` or `min_confidence`; the filter
is a substring match over feature/decision text.)

If any decision in the response describes the same correction → treat as
already ingested, skip. Presence in the result set (not a score value) is
the dedup signal. All corrections with no matching decision → queue as
`uningested_corrections`.

For **mechanical** corrections: skip the ledger check, auto-ingest directly.

---

## In-session mode

Invoked by `bicameral-preflight` step 3.5 with `--mode in-session`.

Scope: last ~10 user messages in the current conversation (not the full
session — preflight fires on every code verb, so a full-session scan would
re-examine the same turns repeatedly).

### Steps

**0. Drain the pending-transcripts queue (#156 PR B).**
Before scanning recent in-session turns, drain the pending-transcripts queue per the canonical "Step 0 — drain the pending-transcripts queue (#156)" rubric above. In in-session mode the drain is bounded:

- Process pending files in mtime-order (oldest first), applying Steps A/B/C to each file's user turns.
- Track accumulated ask-corrections across all processed files.
- When accumulated ask-corrections reach 4 (the preflight ≤4-question cap), stop processing further pending files and surface a final note: "N more pending transcript(s) — invoke `/bicameral-capture-corrections` directly to drain manually." Remaining files stay in `.bicameral/pending-transcripts/` for the next preflight.
- Archive each fully-processed file via `python3 scripts/hooks/transcript_archive.py <basename>.jsonl`. Do NOT archive partially-processed files (the cap was hit mid-scan); the file stays pending and the next preflight resumes from its first un-surfaced correction.
- If `<repo>/.bicameral/pending-transcripts/` doesn't exist or is empty, skip Step 0 silently — same shape as the canonical rubric's empty path.

The 4-cap is shared with the in-session turn-scan that runs in step 1 below: queue-drained ask-corrections + in-session ask-corrections ≤ 4 total. If the queue alone fills the cap, the in-session turn scan still runs (its mechanical corrections still auto-ingest silently) but its ask-corrections are dropped (not surfaced) to preserve the cap.

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
If no corrections found (across both the queue drain and the in-session scan), return nothing. Preflight continues without any
capture-corrections output.

---

## SessionEnd batch mode

Fires via the `SessionEnd` hook in `.claude/settings.json`. Also invocable
manually as `/bicameral-capture-corrections`.

### Steps

**1. Check for `.bicameral/` directory.**
If not present, exit silently — this repo isn't using bicameral.

**2. Determine invocation mode and transcript scope.**
- If invoked via next-session queue drain (Step 0 above, originating from
  the SessionEnd hook's queue write — see #156): for each pending
  transcript file, scan its full user turns. Surface findings through the
  confirmation flow (steps 6-7) so the user reviews each correction with
  full ledger context — the prior `--auto-ingest` "skip confirmation" path
  is gone (it was unsafe in a separate-session subprocess that lacked the
  parent transcript).
- If invoked manually (no flag): scan the last 20 user turns as a proxy
  for the session and show the confirmation flow.

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
4. **Dedup by presence, not score.** Call `bicameral.history` with a
   short `feature_filter`. If any decision in the response describes the
   same correction, treat it as already ingested. Never gate on a numeric
   score value (the retired `bicameral.search` returned scores; `history`
   does not).
5. **Ingest as proposals.** Captured corrections enter as `proposed`
   and need explicit ratification — same as all other ingests.
6. **Guard on `.bicameral/`.** Never run in repos without a bicameral
   setup. The hook fires globally; the guard keeps it scoped.

---

## SessionEnd hook

The SessionEnd hook is installed automatically by `bicameral setup` into the
user's project `.claude/settings.json`. No manual configuration needed.

Command written by the setup wizard (post #156):
```
[ -d .bicameral ] && [ -z "$BICAMERAL_SESSION_END_RUNNING" ] && BICAMERAL_SESSION_END_RUNNING=1 python3 scripts/hooks/session_end_queue_writer.py || true
```

The hook is now a queue writer, not a capture-corrections invoker. It reads
the SessionEnd JSON envelope from stdin (containing `session_id` and
`transcript_path`), copies the transcript into
`<repo>/.bicameral/pending-transcripts/<session_id>.jsonl`, and exits.
Capture-corrections runs in the *next* session via Step 0 of this skill (or
preflight Step 3.5), where the agent has full ledger context to surface each
correction through the user-facing confirmation flow.

Two guards:
- `.bicameral` directory check — keeps it silent in repos that don't use bicameral.
- `BICAMERAL_SESSION_END_RUNNING` env var — defense-in-depth re-entrancy guard
  preserved from the prior shape; the new hook does not spawn a subprocess so
  recursion is no longer the threat, but the guard remains in case a parent
  shell loops on SessionEnd events.

The prior shape (`claude -p '/bicameral-capture-corrections --auto-ingest'`)
spawned an empty subprocess that couldn't see the parent transcript — corrections
silently failed to surface. #156 replaces it with the queue-write pattern above.

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
