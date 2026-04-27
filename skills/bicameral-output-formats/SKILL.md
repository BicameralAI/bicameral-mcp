---
name: bicameral-output-formats
description: Reference catalog of all rendered output templates across bicameral skills. Check here when implementing a new skill or rendering a bicameral response.
type: reference
---

# Bicameral Output Formats

Reference catalog of every rendered output template produced by bicameral skills. Each entry documents the trigger condition, symbols used, and the full template block.

---

## Preflight Surfaced Block

**Source skill**: `bicameral-preflight` (step 5)

**Trigger condition**: `bicameral.preflight` returns `fired=true` AND at least one ask-finding survives classification (step 4). Silent when `fired=false` or all findings are mechanical.

**Symbols**: `📌`, `✓` (reflected), `⚠` (drifted/warn)

**Template**:

```
(bicameral surfaced — checking <topic> context before implementing)

📌 N prior decisions in scope:
  ✓ <decision description>
    <file_path>:<symbol>:<lines>
    Source: <source_ref> · <source_type>

  ✓ <next decision...>

  ⚠ DRIFTED: <decision description>
    <file_path>:<symbol>:<lines>
    Source: <source_ref>
    Drift evidence: <drift_evidence verbatim>

⚠ N divergent decision pair(s) — pick a winner before continuing:
  • <symbol> (<file_path>): <summary>

⚠ N uningested correction(s) from this session:
  • "<user's correction, quoted or one-line paraphrase>"
    Proposed capture: <decision description>
    [Ingest now? Y/n]

⚠ N unresolved open question(s):
  • <description>
    Source: <source_ref>
```

**Notes**:
- Lead with the `(bicameral surfaced)` attribution line — never omit it.
- Emit at most one question per category (drift → divergence → uningested_corrections → open questions → ungrounded). Hard cap: ≤ 4 questions per preflight call.
- After the block, continue with the user's original request — add a one-line forward narration.
- In guided mode (`guided: true`): stop after the block if any hint has `blocking: true`; wait for user acknowledgment before any write operation.
- Action hints from `response.action_hints` are rendered verbatim after the surfaced block — never paraphrase.

---

## History Table

**Source skill**: `bicameral-history`

**Trigger condition**: `bicameral.history`. Truncation note shown when `truncated=True`.

**Template**:

```
FEATURE NAME  ✓ N reflected  ⚠ N drifted  ○ N ungrounded

  ✓ <decision description>          ✓ 2026-04-20
    <file_path>:<start_line>  Source: <source_ref>
  ⚠ <drifted decision>              ✓ 2026-03-15
    Drift evidence: <drift_evidence verbatim>
  ○ <ungrounded decision>           ○
  ~ <ungrounded, AI-surfaced>       ~
  — <superseded decision>           —

(showing top 50 of X — use feature_filter to drill in)
─ status: ✓ reflected · ⚠ drifted · ○ ungrounded · ~ ungrounded + AI-surfaced
  signoff: ✓ date ratified · ○ proposed · ~ AI-surfaced · ✕ rejected · ⚑ needs context · — superseded
```

**Notes**:
- Group decisions by `HistoryFeature`. Lead with features that have drifted or ungrounded decisions.
- Header row: `FEATURE NAME  Nreflected  Ndrifted  Nungrounded  Nsuperseded`.
- Truncation note only when `truncated=True`.

---

## Ratify Prompt

**Source skill**: `bicameral-ingest` (step 7), `bicameral-history` (after table, when proposals exist)

**Trigger condition**: After `bicameral.ingest` — always. After `bicameral.history` — only when decisions with `signoff.state == "proposed"` exist. Silent when no proposals.

**Template**:

```
Captured N decisions as proposals — drift tracking is paused until ratified.

  1  ○ <decision description>
  2  ○ <decision description>
  3  ~ <AI-surfaced decision>

Ratify all N? [Y/n or pick: 1 3]  ›
```

**Notes**:
- If ≤ 5 decisions: show the full list, hint is `[Y/n or pick: 1 3]`.
- If > 5 decisions: default hint to `[all / pick: 1 3 5 / none]`.
- In `bicameral-history`, the variant prompt is: `○ Unratified proposals in: <Feature A>, <Feature B> — Drift tracking is paused. Ratify now? [Y/n or pick features: A C]  ›`
- Never silently skip the ratify step — if the user says "don't ask", make the skip explicit.

---

## Ratify Confirmation

**Source skill**: `bicameral-ingest` (step 7), `bicameral-history`

**Trigger condition**: After `bicameral.ratify` is called for one or more confirmed decisions.

**Symbols**: `✓`

**Template**:

```
✓ Ratified 3/3 — drift tracking active on these decisions.
  (2 skipped — still proposals, will surface as stale after inactivity)
```

**Notes**:
- Skipped count shown only when > 0 decisions were not ratified.

---

## Parked Decisions Prompt

**Source skill**: `bicameral-ingest` (step 4)

**Trigger condition**: `bicameral.ingest` produces decisions with `signoff.state == "context_pending"` (business driver unclear). Always follows the ingest summary. Silent when no parked decisions.

**Symbols**: `⚑`

**Template**:

```
⚑ Parked N decision(s) — business driver unclear:

  1  "<decision description>"
     → <context_question>

  2  "<decision description>"
     → <context_question>

Answer now (e.g. "1a, 2c") to promote to proposals, or leave for next session.
```

**Notes**:
- `context_question` is tailored to the decision's domain (privacy/identity, reliability, security, or generic).
- On answer: option naming a driver → re-ingest as `proposed` with driver noted. Option (d) "engineering hygiene only" → mark as `rejected`. No answer → stays `context_pending`.

---

## Segmentation Preview

**Source skill**: `bicameral-ingest` (step 0)

**Trigger condition**: Input is oversize before extraction — raw content exceeds ~2000 tokens, ≥ 3 H1 headings, ≥ 5 distinct speaker turns suggesting separate sessions, or ≥ 3 distinct topical themes identified on first-pass read. Presented verbatim; the user must confirm before ingest proceeds.

**Template**:

```
Topic N:
  title: <short title, 3–6 words>
  summary: <one line, what the segment is about>
  source_range: <line range, page range, or timestamp range>
  est_decisions: <integer estimate>

Topic N+1:
  ...

Confirm, edit (merge / rename / skip), or re-split?
```

**Notes**:
- Never auto-split silently. Always present the full preview.
- Accept natural-language edits: "merge 3 and 4", "skip 5", "rename 1 to X", "re-split with 8 topics".
- After confirmation, fan out to one `bicameral.ingest` call per topic block, then present a single aggregate roll-up.

---

## Supersession Gate

**Source skill**: `bicameral-ingest` (step 2.5)

**Trigger condition**: `bicameral.ingest` returns `supersession_candidates`; ask-candidates exceed 3 after the first 3 are surfaced individually. Rendered after the batched questions.

**Template**:

```
Bicameral flagged N more potential supersessions not asked individually.
  A. Proceed — record all as parallel decisions (non-superseding)
  B. Review them now — list all, you pick for each
  C. Cancel this ingest — let me refine the payload first
RECOMMENDATION: Choose A if different product areas; B if any touch
a commitment the team has already shipped against.
```

**Notes**:
- The first 3 ask-candidates are surfaced as individual questions (one question per candidate).
- This gate covers the remainder beyond 3.
- In advisory mode (`BICAMERAL_GUIDED_MODE=0`): present candidates as informational notes only; do not gate the ingest.

---

## Capture Corrections Batch

**Source skill**: `bicameral-capture-corrections` (batch mode, SessionEnd hook)

**Trigger condition**: SessionEnd hook fires at session close; `capture-corrections` runs in batch mode over the full transcript. Silent when nothing new is found or all findings are mechanical (auto-ingested silently).

**Symbols**: `✓`

**Template**:

```
Bicameral found N uningested decision(s) from this session:

  1  <correction description>
  2  <correction description>
  3  <correction description>

Ingest all? [Y/n or pick: 1 3]  ›

                    ─────── after confirmation ───────

✓ Ingested N/N corrections — proposals pending ratification.
  (M skipped)
```

**Notes**:
- Mechanical corrections are ingested silently without this prompt.
- Only ask-corrections (where reasonable people could disagree) surface here.
- The confirmation line always shows total ingested / total confirmed + skipped count.

---

## Gap Judgment Rubric

**Source skill**: `bicameral-ingest` → `bicameral-judge-gaps` (step 6); rendered by Caller LLM

**Trigger condition**: `bicameral.ingest` auto-chains `bicameral.judge_gaps`; the ingest response contains a non-null `judgment_payload`. The caller LLM applies the 5-category rubric using its own session context.

**Symbols**: `○` (gap/not-addressed), `✓` (no gaps found), `|` (table separator)

**Template**:

```
Gap judgment for `<feature area>` — 5 categories, N findings total.

### Missing acceptance criteria
• Decision "<description>" — source says "..." (<source_ref> · <date>)
  but does not define <what's missing>.

### Happy path specified, sad path deferred
| Happy path (specified)            | Missing sad path                          |
|-----------------------------------|-------------------------------------------|
| "..." (<source_ref> · <date>)     | What if <scenario>? — not addressed       |

### Implied infrastructure commitments not signed off
○ Decision implies <commitment> → cost / consequence not discussed

### Vendor / provider choices not settled
○ Category: <category> → implied but provider never named (options?)

### Data policy gaps (PII, retention, consent, audit)
○ Decision implies <policy area> → not addressed

(empty category renders as:  ✓ no gaps found)
```

**Notes**:
- Output one section per category, in rubric order.
- Every bullet/row/item must cite a `source_ref` + `meeting_date` OR a `file:line` from a codebase crawl.
- Surface `source_excerpt` verbatim — never paraphrase.
- Honest empty path: if a category produces no findings, emit `✓ no gaps found` under its header. Never skip the header.
- Full rendering contract is in `skills/bicameral-judge-gaps/SKILL.md`.
