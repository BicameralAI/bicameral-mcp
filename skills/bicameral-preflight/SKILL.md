---
name: bicameral-preflight
description: Pre-flight context check BEFORE implementing code. AUTO-FIRES on ANY prompt that involves writing, changing, or touching source code — including: "add", "build", "create", "implement", "modify", "refactor", "update", "fix", "change", "write", "edit", "move", "rename", "remove", "delete", "extract", "convert", "integrate", "deploy", "ship", "configure", "connect", "extend", "migrate", "wire up", "hook up", "set up", "complete", "finish", "continue". Also fires when user asks HOW to implement something (they are about to implement it). Surfaces prior decisions, drifted regions, divergences, and open questions BEFORE Claude writes any code. SKIP ONLY FOR — purely read-only questions with zero code intent, documentation-only typo fixes, dependency version bumps with no semantic change.
---

# Bicameral Preflight

> Tuning parameters for this skill are defined in `skills/CONSTANTS.md`.

The proactive context-surfacing skill. Bicameral notices when you're
about to implement something and pushes the relevant prior decisions,
drift, and open questions at you BEFORE Claude writes any code.

**The wow moment**: developer says *"add a Stripe webhook handler for
payment_intent.succeeded"* — without being asked, bicameral chimes in
with idempotency decisions from a sprint review, the drifted timestamp
handling from PR #287, and the unresolved deduplication question from
last week's Slack thread. The implementation that follows is informed
by all of it.

**The trust contract**: when there's nothing relevant to surface, this
skill produces ZERO output. No "I checked and found nothing" noise.
The empty path is silent.

## When to fire

Auto-fire on ANY prompt that involves writing, changing, or touching
source code. When in doubt, fire — a silent miss is worse than a
redundant check. Examples:

- *"add a Stripe webhook handler for payment_intent.succeeded"*
- *"refactor the rate limiting middleware to use sliding window"*
- *"build a notification system for retention nudges"*
- *"implement OAuth callback for Google Calendar"*
- *"modify the discount calculation to handle cents"*
- *"create a migration to add the audit_log table"*
- *"continue what we started yesterday on the email queue"* (use
  conversation context to extract the topic)
- *"how should I implement the retry logic?"* (asking HOW = about to implement)
- *"wire up the new endpoint to the frontend"*
- *"finish the auth middleware work"*
- *"migrate the payment flow to the new provider"*
- *"rename the function to snake_case"*
- *"remove the deprecated API call"*
- *"set up the webhook integration"*

## When NOT to fire

**Only skip for these narrow cases** — when there is ZERO intent to write code:

- *"how does the rate limiter work?"* (purely read-only — but if they say "how should I build it", FIRE)
- *"fix the typo in the README"* (doc-only, no code change)
- *"bump lodash to 4.17.21"* (dependency version bump only, no semantic change)

**Do NOT use "why is this test failing?" as a skip trigger** — debugging
a test often precedes writing a fix. If the user asks to fix it, fire.

If uncertain whether the user will write code, **fire anyway** — the
handler is gated on actionable signal and will stay silent if nothing
relevant is found. The cost of a false fire is one silent no-op.

### Hook reinforcement

The trigger described above is reinforced by a `UserPromptSubmit` hook
configured in [`.claude/settings.json`](../../.claude/settings.json).
The hook reads the user prompt, runs a deterministic regex over the
canonical verb list at
[`scripts/hooks/preflight_intent.py`](../../scripts/hooks/preflight_intent.py),
and — on match — injects a `<system-reminder>` block elevating
`bicameral.preflight` above the agent's default tool-selection priority.

For v0 the verb list is duplicated by intent: the SKILL.md
`description` field above embeds the list as a string literal so
Claude Code skill discovery can read it, while the Python module is
the canonical source for the hook. Both must be edited together to
evolve the trigger surface; future configurability will deduplicate.

## Telemetry

> **Guard**: Only call `skill_begin` and `skill_end` if telemetry is enabled. Telemetry is enabled by default; disabled by setting `BICAMERAL_TELEMETRY=0` (or `false`/`off`/`no`). If disabled, skip both calls and omit all `diagnostic` tracking.

**At skill start** (before any tool calls):
```
bicameral.skill_begin(skill_name="bicameral-preflight", session_id=<uuid4>,
  rationale="<one-liner: why triggered — e.g. 'user said implement Stripe webhook handler'>")
```

**At skill end**:

> ⚠ **USE THESE EXACT FIELD NAMES.** The dashboard queries on `g9_*` / `g10_*` / `g11_*` prefixes. Substituting natural-language names silently drops the event from every dashboard panel. Copy the names below verbatim.

```
bicameral.skill_end(skill_name="bicameral-preflight", session_id=<stored_id>,
  errored=<bool>, error_class="<if errored>",
  diagnostic={
    g9_history_features_count: N,
    g9_features_in_scope: N,
    g9_decisions_in_scope: N,
    g9_preflight_fired: <bool>,

    g10_findings_drift_total: N,
    g10_findings_drift_cosmetic_autopass: N,
    g10_findings_drift_ask: N,
    g10_questions_surfaced: N,
    g10_user_overrode: N,

    g11_corrections_turns_scanned: N,
    g11_corrections_prefilter_retained: N,
    g11_corrections_classified_ask: N,
    g11_corrections_classified_mechanical: N,
    g11_corrections_classified_not: N,
    g11_corrections_dedup_removed: N,
    g11_user_overrode: 0,  # always 0 in preflight (in-session mode, no batch confirmation)
  })
```

## Steps

### 1. Read the full decision ledger

Call `bicameral.history()` with no filters to get the complete picture:

```
bicameral.history()
```

This returns all decisions grouped by feature area, each with its current status
(`reflected`, `drifted`, `pending`, `ungrounded`) and signoff state. Scan the
returned `features` list and **identify which feature groups are relevant to the
current implementation task** using your own judgment — name overlap, decision
descriptions, or both. A feature group is relevant when at least one of its
decisions describes behavior the current task will touch or depend on.

**This replaces keyword search.** The LLM reads the full ledger and reasons about
relevance — no BM25 approximation.

Skip this step only if the ledger is empty (history returns 0 features) — in that
case proceed directly to step 2.

### 2. Call `bicameral.preflight` for region-anchored and HITL state

```
bicameral.preflight(
  topic="<the 1-line topic>",
  file_paths=["<repo-relative path>", ...],  # include if you've scoped the files
)
```

This call handles two things the history read cannot:
- **Region-anchored lookup**: if you know which files the task will touch, the
  handler returns decisions pinned to those exact files (higher precision than
  feature-name matching).
- **HITL state**: returns any unresolved collision-pending or context-pending
  decisions that need attention regardless of topic.

The response carries `fired: bool`. When `fired=false`, skip to step 3 (you
already have context from history). When `fired=true` with region matches, merge
those into your in-scope set.

The response also carries an optional `sync_metrics` field — skip rendering it.
If `response.product_stage` is non-null, surface it verbatim to the user as a brief note (shown once per device only).

**Omit `file_paths`** if you haven't scoped the files yet (early "how should I
approach X?" queries). The handler still runs sync and HITL checks.

### 2.5 Resolve pending compliance checks if present

Before evaluating `response.fired`, check `response._pending_compliance_checks`.
If non-empty, a new commit was detected — **resolve compliance before proceeding
or status will be stale.** Use the `bicameral-sync` compliance resolution flow:

For each check: read `file_path` (use `code_body` if available; read directly if
truncated), evaluate code against `decision_description`, then batch all verdicts:

```
bicameral.resolve_compliance(
  phase="drift",
  flow_id="<response._pending_flow_id>",
  verdicts=[{
    decision_id:  "<check.decision_id>",
    region_id:    "<check.region_id>",
    content_hash: "<check.content_hash — echo exactly>",
    verdict:      "compliant" | "drifted" | "not_relevant",
    confidence:   "high" | "medium" | "low",
    explanation:  "<one sentence>"
  }, ...]
)
```

Then continue to step 3 as normal. **Silent when empty** — no output to user
about this step unless a drift is found.

### 3. Decide whether to render

Combine what you have from steps 1 and 2:

- **In-scope decisions** = relevant feature groups from history + region-anchored decisions from preflight (deduplicated by `decision_id`)
- **Actionable signal** = any in-scope decision with `status=drifted`, `status=ungrounded`, or with an open question; plus any HITL issues from preflight response

**If no in-scope decisions and no HITL issues** → produce **NO OUTPUT** about the preflight.
Do not say "I checked bicameral and found nothing." Just proceed silently.

**If `fired == false` and no relevant features from history** → same: silent skip.

The `reason` field on the preflight response is for debugging, never user-facing.
Possible reasons: `recently_checked` (same topic checked in last 5 min),
`preflight_disabled` (env override mute).

**Note on ephemeral commits**: when `bicameral.link_commit` is called on a
feature branch commit (one not yet in the authoritative branch), the response
includes `ephemeral: true` and any compliance verdicts are tagged as such.
These verdicts are still authoritative for status — `drifted`/`reflected` reflects
the branch state — but the dashboard renders them with a branch-delta indicator
so you can see what your branch changes relative to main.

- **`fired == true`** → render the surfaced block (next step) BEFORE
  doing any code work.

### 3.5 Scan recent user turns for uningested corrections

Before classifying server-returned findings, invoke
`/bicameral:capture-corrections` in **in-session mode**:

```
Skill("bicameral:capture-corrections", args="--mode in-session")
```

That skill owns the canonical scan-and-classify rubric (Steps A → B → C).
In in-session mode it scans the last ~10 user messages, auto-ingests
mechanical corrections silently, and returns ask-corrections for merging
into the stop-and-ask queue below.

**Merge outcomes into step 4:**
- Mechanical corrections → already ingested by capture-corrections, no
  output needed here.
- Ask corrections → add as `uningested_corrections` category (priority
  slot 3: after drift, before open questions). One question max.

### 4. Classify findings before surfacing

Before rendering anything, classify each finding as **mechanical** or
**ask** (see Stop-and-Ask Contract below). Auto-resolve mechanical
findings silently. For ask-findings, emit at most **one question per
category**, in this priority order: drift → divergence →
uningested_corrections → open questions → ungrounded.
Hard cap: ≤ 4 questions total per preflight call (if all 5 categories
have ask-findings, drop `ungrounded` — least urgent for correctness).

Categories with no ask-findings are silently skipped. If every
finding in every category is mechanical, produce NO output (same as
`fired=false` — silent).

**Cosmetic drift rule**: if a `drifted` entry has `cosmetic_hint=true`,
classify it as **mechanical** regardless of guided mode. The server has
verified via AST comparison that the change is whitespace-only and
semantically inert — the stored intent is still intact. Auto-resolve
silently; do NOT add it to the drift ask-queue and do NOT emit a
blocking hint. Render it with `~` prefix (not `⚠ DRIFTED:`) if you
render it at all — see the template in Step 5.

### 5. Render the surfaced block

When at least one ask-finding exists, surface the response using this
format. Lead with the `(bicameral surfaced)` attribution line.

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

  ~ REFORMATTED: <decision description>      ← cosmetic_hint=true only
    <file_path>:<symbol>:<lines>
    Source: <source_ref>
    (whitespace-only change — intent intact, no action needed)

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

Then, if `response.action_hints` is non-empty, render each hint
verbatim — never paraphrase the `message` field.

### 5.4 Surface HITL clarification prompts (#112)

If `response.hitl_prompts` is non-empty, the server has detected one
or more decisions with an unresolved `signoff_state`. Each prompt
already carries the question, the trigger label, and a closed option
list. Render them via `AskUserQuestion`.

**Trigger conditions** — a prompt is emitted whenever a surfaced
decision's `signoff_state` is one of:

- `proposed` — decision exists but no one has ratified it yet.
- `ai_surfaced` — auto-extracted by an LLM, awaiting human review.
- `needs_context` — decision text is unclear; asks for more.
- `collision_pending` — two decisions appear to conflict.
- `context_pending` — awaiting a span to ground it.

Bypass is **mandatory and last** in every option list — assert
`prompt.options[-1].kind == "bypass"` before rendering. Trust contract:
Bicameral does NOT block work, the bypass option is always reachable.

```python
for prompt in response.hitl_prompts:
    assert prompt.options[-1].kind == "bypass"
    answer = AskUserQuestion({
        "question": prompt.question,
        "multiSelect": False,
        "options": [
            {"label": opt.label, "description": opt.kind}
            for opt in prompt.options
        ],
    })
    if answer.kind == "bypass":
        bicameral.record_bypass(decision_id=prompt.decision_id)
```

**Bypass semantics**:

- Bypass does NOT mutate decision state. The unresolved
  `signoff_state` persists for future preflight surfaces.
- Calling `bicameral.record_bypass(decision_id)` writes a
  `preflight_prompt_bypassed` event to the local JSONL log
  (`~/.bicameral/preflight_events.jsonl`). Idempotent within a 1-hour
  recency window — repeat calls inside the window return
  `deduped=true` without re-writing (V4 spam-bypass guard prevents
  indefinite escalation suppression).
- The governance engine reads recent bypass events and drops one
  escalation tier (e.g. `escalate` → `warn`, `warn` → `context`) when
  the same decision is resurfaced inside the recency window. This
  acknowledges that the user has SEEN the unresolved state without
  permanently silencing the finding.
- Telemetry must be enabled (`BICAMERAL_PREFLIGHT_TELEMETRY=1`) for
  bypass writes to persist; otherwise `record_bypass` returns
  `recorded=false, deduped=false, reason="telemetry_disabled"` and the
  engine sees no recency.

### 5.5 Confirm finding relevance (ground truth for calibration)

> **Guard**: Only run this step if guided mode is enabled (`guided: true` in `.bicameral/config.yaml`). In normal mode, skip and set `g10_user_overrode = 0`.

After rendering the surfaced block, if any ask-findings were surfaced, call `AskUserQuestion` to let the user dismiss findings that are not relevant to the current task. Batch into groups of ≤ 4 findings per call; loop through batches if there are more than 4 total.

```python
AskUserQuestion({
  question: "Any of these finding(s) not relevant to your current task?",
  multiSelect: True,
  options: [
    { label: "<one-line summary>", description: "<category: drift | divergence | open_question | uningested>" },
    ...  # one entry per ask-finding
  ]
})
```

- User selects items → those are dismissed. `g10_user_overrode` += count of dismissed findings.
- User selects nothing → all findings treated as relevant. `g10_user_overrode = 0`.
- `g10_questions_surfaced` = total ask-findings shown across all batches.

After the surfaced block, **continue with the user's original request**.
A one-line forward narration helps:

> "Proceeding with implementation; pulling the Redis SETNX pattern
> from idempotency.ts. I'll flag the event.id deduplication question
> for you to answer before I commit."

### 5.6 Capture refinements when the user's prompt contradicts a surfaced decision

If any decision rendered in the surfaced block describes an approach the user's current request **replaces** (signals: "instead of", "actually we're switching to", "no more X", "I know X said Y but…", "X is out — we're going with Z"), this is a contradiction. Before doing any code work:

1. **Ingest the refinement** with `source=agent_session`, scoped to the same `feature_group` as the contradicted decision:

```
bicameral.ingest(payload={
  "query": "<contradicted decision's topic>",
  "source": "agent_session",
  "title": "<short label, e.g. 'reorder-text-editor-refactor'>",
  "date": "<today ISO date>",
  "decisions": [{ "description": "<user's new direction, stated as a decision>" }]
}, feature_group="<same feature group as the contradicted decision>")
```

2. **Wire it to the seeded decision** via `bicameral.resolve_collision`:

```
bicameral.resolve_collision(
  new_id="<just-ingested refinement id>",
  old_id="<surfaced contradicted decision id>",
  action="supersede" | "keep_both" | "link_parent"
)
```

Pick `action` mechanically:
- `supersede` — user fully replaces the old direction ("drag-drop is out, text-editor instead"). Writes a RELATE edge; old decision's status becomes `superseded`.
- `keep_both` — user adds a constraint or narrows the surface without invalidating the old ("also need X for accessibility" / "only on the conflict UI surface"). No edge; both remain.
- `link_parent` — the user's direction is a child decision under a higher-level (e.g. L1) parent; the contradicted decision is the parent. Writes `parent_decision_id`, no supersede edge, no status change.

Mechanical — the user already stated the refinement. Do not ask. PM ratifies in the inbox.

Narrate one line: "Captured refinement: '<paraphrase>' — wired as <action> of <feature> roadmap entry."

### 6. Honor blocking hints (guided mode vs normal mode)

The agent's `guided_mode` setting controls whether action hints are
blocking or advisory. The flag has two settings chosen at `bicameral setup`
time:

- **Normal mode** (`guided: false`, default) — hints fire with `blocking: false`
  and advisory tone ("heads up — N drifted decision(s) detected"). Mention
  the hint to the user and **continue with the implementation**. Normal
  mode is a heads-up, not a stop sign.
- **Guided mode** (`guided: true`) — hints fire with `blocking: true` and
  imperative tone ("N drifted decision(s) — review BEFORE making changes").
  When any hint has `blocking: true`, **MUST stop after the surfaced block
  and wait for user acknowledgment** before any write operation (file edit,
  commit, PR, `bicameral_ingest`). Surface the hint's `message` verbatim
  and ask the user to either resolve it or explicitly tell you to proceed.

**How to enable/disable:**

*Durable (setup time)*: `bicameral setup` prompts:
```
  Interaction intensity:
    1. Normal  — bicameral flags discrepancies as advisory hints (default)
    2. Guided  — bicameral stops you when it detects discrepancies
  Choice [1/2]:
```
Written to `.bicameral/config.yaml` as `guided: true` or `guided: false`.

*One-off override (env var)*: Set `BICAMERAL_GUIDED_MODE=1` (or `true`, `yes`,
`on`) on the MCP server process to force guided mode for one session without
touching the config file. Set to `0` / `false` to force normal mode.

**When to use guided mode:**
- Onboarding a new user to a repo with an existing bicameral ledger.
- Demos where you want the audience to see bicameral doing adversarial-audit work.
- Critical-path work — touching auth, billing, security, migrations.

**When normal mode is enough:**
- Day-to-day workflow on a codebase you know.
- Read-only exploration flows.
- Batch / headless ingest with no human-in-the-loop.

### 7. On stop-and-ask resolution — ingest the answer

When a blocking hint is resolved and the user answers an open question
or confirms a design decision, immediately capture it into the ledger:

```
bicameral.ingest(payload={
  "query": "<the feature topic preflight was scoped to>",
  "source": "agent_session",
  "title": "<short label for the decision, e.g. 'preflight-resolution-<topic>'>",
  "date": "<today ISO date>",
  "decisions": [{ "description": "<the user's answer as a decision statement>" }]
}, feature_group="<same feature group as the implementation task>")
```

Use `source="agent_session"` — a source type distinct from transcript/slack/document
that marks decisions resolved inline during an agent session. This ensures the
decision is recorded in the ledger and not lost when the session ends.

## Stop-and-Ask Contract

<!-- Copy of bicameral-ask-contract.md v1 — see source for canonical version -->

For every finding this skill surfaces, classify first:

- **mechanical** — one obvious correct answer (e.g., renamed symbol
  with identical signature; a decision whose code moved but semantics
  are intact; a `drifted` entry with `cosmetic_hint=true` — AST
  comparison confirmed whitespace-only change). Auto-apply the
  resolution silently. Do NOT ask the user.
- **ask** — reasonable people could disagree (e.g., drifted behavior
  where the old decision may still be valid; divergent decisions where
  no clear winner exists). Emit ONE question per finding, using the
  format below.

**Question format** — always:
1. **Re-ground:** repo + branch + one-sentence current task
2. **Simplify:** plain English, no raw symbol names
3. **Recommend:** `RECOMMENDATION: Choose X because Y` + Completeness
   X/10 per option
4. **Options:** A / B / C — one sentence each, pickable in < 5s

**Per-skill caps (preflight):**
- Max 1 question per category (drift / divergence /
  uningested_corrections / open questions / ungrounded)
- Hard cap 4 questions per preflight call
- If all 5 categories have ask-findings, drop `ungrounded` (least
  urgent for correctness) questions

**Advisory-mode override:** if `BICAMERAL_GUIDED_MODE=0`, emit
questions as informational notes (non-blocking); do not gate
downstream tool calls.

## Examples

### Hit — guided mode, drift + divergence found

**User**: "Add a Stripe webhook handler for payment_intent.succeeded"

**Topic extracted**: `Stripe webhook payment_intent succeeded`

**Tool call**: `bicameral.preflight(topic="Stripe webhook payment_intent succeeded")`

**Response** (fired=true, guided_mode=true):

```
(bicameral surfaced — checking Stripe webhook payment_intent succeeded
context before implementing)

📌 3 prior decisions in scope:

  ✓ Idempotency via Redis SETNX with 24h TTL
    src/middleware/idempotency.ts:checkIdempotencyKey:42-67
    Source: Sprint 14 architecture review · Ian, 2026-03-12

  ✓ Retry failed webhooks with exponential backoff (max 5 attempts)
    src/queue/webhook-retry.ts:scheduleRetry:18-45
    Source: PR #261 review · Brian, 2026-03-22

  ⚠ DRIFTED: Trust Stripe event.created timestamp, not server time
    src/handlers/webhook.ts:processEvent:80-92
    Source: arch review 2026-03-15
    Drift evidence: switched from event.created to Date.now() in PR #287

⚠ 1 unresolved open question:
  • "Should we deduplicate by event.id or by (account_id, event.id)?"
    Source: Slack #payments 2026-03-20

⚠ BLOCKING (guided mode): 1 matched decision(s) have drifted — review
the drifted regions and confirm the code still matches stored intent
BEFORE making changes.

I need you to resolve before I proceed:
1. Was the switch to Date.now() in PR #287 intentional, or should I
   revert to event.created?
2. Which deduplication key should I use — event.id or
   (account_id, event.id)?
```

(Then waits for user acknowledgment.)

### Miss — silent skip

**User**: "Fix the typo in the README"

**Topic extracted**: `typo README` (or skipped entirely if you decide
this is doc-only)

**Tool call**: skipped, OR `bicameral.preflight(topic="typo README")`

**Response** (fired=false, reason=topic_too_generic OR no_matches):

```
[no output about preflight at all]
```

Then continue with the typo fix. The user should not see any preflight
output for prompts that don't match anything.

### Hit — normal mode, advisory only

**User**: "Refactor the discount calculation to handle cents"

**Response** (fired=true, guided_mode=false):

```
(bicameral surfaced — checking discount calculation cents context
before implementing)

📌 1 prior decision in scope:
  ⚠ DRIFTED: Apply 10% discount on orders >= $100
    src/pricing/discount.py:calculate_discount:42-67
    Source: Sprint 14 planning · Ian, 2026-03-12
    Drift evidence: threshold raised 100 → 500, rate lowered 10% → 5%

Note: the discount logic is currently drifted from the original
intent. Worth confirming with Ian before changing it again. Proceeding
with the refactor — let me know if you want me to align it back to
the original 10% / $100 baseline or keep the current 5% / $500
behavior.
```

(Continues with the refactor — no blocking pause in normal mode.)

## Rules

1. **Honest empty path.** When `fired=false`, produce NO output about
   preflight. Silent skip. Period.
2. **Verbatim attribution.** Every cited decision includes its
   `source_ref` so the user can trace it.
3. **Never paraphrase hint messages.** Surface them as-is. The
   message tone (advisory vs imperative) is calibrated by guided mode
   and the user can read intent from it directly.
4. **Topic from prompt + context.** If the user's prompt is indirect
   ("continue what we started yesterday"), use the prior conversation
   to extract a meaningful topic. Don't pass the raw prompt verbatim.
5. **Forward narration after surfacing.** Tell the user what you're
   about to do with the surfaced context, not just what you found.
   "Proceeding with X; pulling pattern from Y; will flag Z for you to
   answer before commit."
6. **Skip the SKIP-FOR list.** Read-only, doc-only, and dependency-
   only prompts do not need preflight. Don't fire on them.

## How to disable

If preflight is too noisy for the current session, the user can set
`BICAMERAL_PREFLIGHT_MUTE=1` on the MCP server process to silence it
for one session. The handler will return `fired=false` with
`reason="preflight_disabled"` for every call.

For a permanent off-switch, edit `.bicameral/config.yaml` and remove
the preflight skill from the agent's skill set, OR set
`guided: false` (which dials preflight back to "actionable signal
only" — silent on plain matches).
