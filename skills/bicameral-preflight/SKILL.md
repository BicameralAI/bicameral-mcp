---
name: bicameral-preflight
description: Pre-flight context check BEFORE implementing code. Auto-fires on implementation requests using verbs like "add", "build", "create", "implement", "modify", "refactor", "update", "fix", or any prompt asking Claude to write or change source code. Surfaces prior decisions, drifted regions, divergences, and open questions linked to the feature area BEFORE Claude starts writing. Silent if no relevant context exists. SKIP FOR — read-only questions, debugging without code changes, documentation-only edits, simple typo fixes, dependency updates.
---

# Bicameral Preflight

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

Auto-fire on prompts that ask Claude to write, modify, or refactor
code:

- *"add a Stripe webhook handler for payment_intent.succeeded"*
- *"refactor the rate limiting middleware to use sliding window"*
- *"build a notification system for retention nudges"*
- *"implement OAuth callback for Google Calendar"*
- *"modify the discount calculation to handle cents"*
- *"create a migration to add the audit_log table"*
- *"continue what we started yesterday on the email queue"* (use
  conversation context to extract the topic)

## When NOT to fire

The "SKIP FOR" list in the description is load-bearing. Do NOT fire on:

- *"how does the rate limiter work?"* (read-only question)
- *"why is this test failing?"* (debugging, no code change yet)
- *"fix the typo in the README"* (doc-only edit)
- *"bump lodash to 4.17.21"* (dependency update, no semantic change)
- *"what does this function do?"* (explanation, not implementation)

If the user is asking you to SHOW or EXPLAIN, not BUILD or CHANGE,
preflight does not fire.

## Steps

### 1. Extract a 1-line topic

Before calling the tool, extract a topic string from the user's
prompt. The topic should capture the feature area in 4-12 words. Use
conversation context if the prompt is indirect.

Examples:

| User prompt | Extracted topic |
|---|---|
| "Add Stripe webhook handler for payment_intent.succeeded" | `Stripe webhook payment_intent succeeded` |
| "Refactor the rate limiting middleware to use sliding window" | `rate limiting middleware sliding window` |
| "Continue what we started yesterday on the email queue" | `email queue retention nudge` *(infer from prior turn)* |
| "Build the audit log feature Brian asked for" | `audit log feature` (with `participants=["Brian"]`) |

The handler validates the topic deterministically (≥4 chars, ≥2
non-stopword content tokens, not a generic catch-all). If your topic
fails validation, the handler returns `fired=false` with
`reason="topic_too_generic"` — that's the silent skip path. Don't
worry about getting validation perfect; the handler is forgiving on
the happy path.

### 2. Call `bicameral.preflight`

```
bicameral.preflight(
  topic="<the 1-line topic>",
  participants=[<names if user mentioned specific people>],  # optional
)
```

The handler runs `bicameral.search` internally, gates on the user's
`guided_mode` setting, conditionally chains to `bicameral.brief`, and
returns a `PreflightResponse` with a `fired: bool` field.

### 3. Decide whether to render

Look at `response.fired`:

- **`fired == false`** → produce **NO OUTPUT** about the preflight.
  Do not say "I checked bicameral and found nothing." Do not say "no
  relevant context." Just proceed silently with the user's original
  request. The reason field tells you why (no_matches,
  no_actionable_signal, topic_too_generic, recently_checked,
  preflight_disabled) — useful for debugging, never user-facing.

- **`fired == true`** → render the surfaced block (next step) BEFORE
  doing any code work.

### 4. Render the surfaced block

When `fired=true`, surface the response using this exact format. Lead
with the `(bicameral surfaced)` attribution line.

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

⚠ N unresolved open question(s):
  • <description>
    Source: <source_ref>
```

Then, if `response.action_hints` is non-empty, render each hint
verbatim — never paraphrase the `message` field.

After the surfaced block, **continue with the user's original request**.
A one-line forward narration helps:

> "Proceeding with implementation; pulling the Redis SETNX pattern
> from idempotency.ts. I'll flag the event.id deduplication question
> for you to answer before I commit."

### 5. Honor blocking hints (guided mode only)

If any hint has `blocking: true`, you MUST stop after the surfaced
block and wait for user acknowledgment before doing any write
operation (file edit, commit, PR, `bicameral_ingest`). Surface the
hint's `message` verbatim and ask the user to either resolve it or
explicitly tell you to proceed.

In normal mode (non-guided), hints have `blocking: false` and you can
proceed after surfacing them. The user opted into the looser
interaction at setup time.

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
