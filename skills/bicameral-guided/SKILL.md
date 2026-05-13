---
name: bicameral-guided
description: Strict-intensity mode for onboarding, demos, and careful workflows. When `guided: true` is set in .bicameral/config.yaml (chosen at setup time) or `BICAMERAL_GUIDED_MODE=1` is exported as an env override, bicameral.search and bicameral.brief responses emit BLOCKING action hints the agent must address before any write operation. In normal (un-guided) mode the same hints still fire, but as advisory notes rather than blockers. This skill explains the contract, when to enable it, and how to debug it.
---

# Bicameral Guided Mode

Guided mode is an opt-in strict-enforcement layer on top of
`bicameral.search` and `bicameral.brief`. It makes bicameral **stop the
agent** when it detects discrepancies — drifted decisions, divergent
decision pairs, unresolved open questions, ungrounded decisions linked
to the query scope.

**The flag has two settings**, chosen at `bicameral setup` time:

- **Normal** (`guided: false`, default) — `action_hints` still appear
  on `bicameral.search` and `bicameral.brief` responses whenever
  findings exist, but with `blocking: false` and an advisory tone
  ("heads up — 3 decision(s) look drifted from their recorded
  intent"). The agent is free to proceed; the hint is informational.
- **Guided** (`guided: true`) — same hints with `blocking: true` and
  an imperative tone ("3 matched decision(s) have drifted — review
  the drifted regions BEFORE making changes"). The agent MUST address
  each blocking hint before any write operation.

**Pick once at setup, override per-session.** The config file
(`.bicameral/config.yaml`) is the durable setting; the
`BICAMERAL_GUIDED_MODE` env var (`1 / true / yes / on`) is a one-off
override that wins over the file. Flip the env var back to `0 / false`
to return to the config file's default.

## When to enable guided mode

- **Onboarding a new user** to a repo with an existing bicameral
  ledger — guided mode makes the first few queries force-surface
  drift, divergences, and unresolved questions before the agent does
  anything the user would have to undo later.
- **Demos** where you want the audience to see bicameral doing
  adversarial-audit work, not just retrieval.
- **Skill evaluation** (eval harness) — guided mode produces
  structured `ActionHint` objects with `blocking=True` which are
  easier to score than free-text agent outputs.
- **Critical-path work** — touching auth, billing, security,
  migrations. Turn guided on temporarily via the env var and let
  bicameral be loud.

## When normal mode is enough

- **Day-to-day workflow** on a codebase you know. You still want the
  heads-ups, but you don't need the agent to pause on every match.
- **Read-only exploration flows** — no write operations means
  blocking hints are redundant.
- **Batch / headless ingest** with no human-in-the-loop — blocking
  hints have nowhere to go; the advisory mode surfaces the same
  signal without forcing a pause that would never resolve.

## What fires

Hint kinds are the same in normal and guided mode — only `blocking`
and the message tone differ.

### On `bicameral.search` responses:

- **`review_drift`** — at least one matched decision has status
  `drifted`. The recorded intent no longer matches the current code.
  Refs: drifted intent_ids + the file paths their regions touch.
- **`ground_decision`** — at least one matched decision has no code
  grounding yet. Refs: the ungrounded intent_ids.

### On `bicameral.brief` responses:

- **`resolve_divergence`** — two non-superseded decisions contradict
  on the same symbol. Highest-stakes signal. Refs:
  `symbol (file_path)` strings.
- **`review_drift`** — at least one decision in scope is drifted.
  Refs: drifted intent_ids.
- **`answer_open_questions`** — gap extraction found
  open-question-shaped decisions in scope. Refs: truncated gap
  descriptions.

Each hint has:
- `kind` — one of the four above
- `message` — human-readable directive (tone varies by mode)
- `blocking: bool` — `true` in guided mode, `false` in normal
- `refs: list[str]` — intent_ids, file paths, or descriptions

**Never paraphrase the `message` field** — surface it verbatim so the
user can observe exactly what the server signaled.

## The blocking contract (guided mode)

When a response includes `action_hints` with `blocking: true`, the
agent MUST:

1. Surface each hint's `message` to the user verbatim, along with the
   relevant refs (drifted files, divergent symbols, open questions).
2. **Pause before any write operation** (file edit, commit, PR,
   `bicameral_ingest`) until the user acknowledges the hint and
   either resolves it or explicitly tells the agent to proceed anyway.
3. If the user chooses to proceed despite a blocking hint, record the
   decision — "user acknowledged X and chose to proceed" — so the
   override is visible in transcripts.

**MCP cannot actually force this** — the wire protocol is advisory.
Enforcement lives in this skill contract. A skill-following agent
stops at blocking hints; a non-compliant agent can ignore them. If
adoption telemetry shows high ignore rates, v0.5.x may add
server-side refusal (e.g. `ingest_payload` refuses inputs when
blocking hints are outstanding for related decisions).

## The advisory contract (normal mode)

When a response includes `action_hints` with `blocking: false`, the
agent SHOULD:

1. Mention the hint to the user in its output — one line is enough,
   e.g. "Note: 2 matched decisions look drifted; worth a quick look
   before we edit `pricing.py`."
2. Continue with whatever the user asked for. Normal mode is a
   heads-up, not a stop sign.

## How to enable / disable

### Durable (setup time)

The setup wizard (`bicameral setup`) prompts:

```
  Interaction intensity:
    1. Normal  — bicameral flags discrepancies as advisory hints (default)
    2. Guided  — bicameral stops you when it detects discrepancies
  Choice [1/2]:
```

The choice is written to `.bicameral/config.yaml` as `guided: true`
or `guided: false`. To change it later, edit the file directly.

### One-off override (env var)

Set `BICAMERAL_GUIDED_MODE=1` (or `true`, `yes`, `on`) on the MCP
server process to force guided mode for one session without touching
the config file. Set to `0` / `false` to force normal mode. Unset to
fall back to the config file.

The env var is read on every `BicameralContext.from_env()` call (once
per MCP tool invocation), so changes take effect on the next tool
call — no server restart needed.

## Debugging

If hints aren't firing when you expect them:

1. **Check the match statuses.** The `review_drift` search hint fires
   only when at least one match has `status == "drifted"`. In v0.4.8
   and earlier, `handle_search_decisions` had a bug where every match
   was reported as `pending` regardless of real state. v0.4.9 fixes
   this — run v0.4.9+ to see the real statuses.
2. **Check the ledger actually has drifted state.** A decision only
   becomes drifted after `handle_link_commit` detects a content_hash
   mismatch against a grounded region's stored baseline. If no commit
   has introduced drift, there's nothing for the hint to fire on.
3. **Check the pollution guard.** Drift status is only persisted when
   `HEAD == authoritative_ref` (the v0.4.6 pollution guard). On a
   feature branch, drift is detected but not persisted — the next
   search will still see `reflected`. Override via
   `BICAMERAL_AUTHORITATIVE_REF=<your-branch>` for local experiments.
4. **Check `ctx.guided_mode`.** If hints are firing as advisory
   (`blocking: false`) but you expected blocking, confirm that either
   the config file has `guided: true` OR the env var
   `BICAMERAL_GUIDED_MODE=1` is set.
