---
name: bicameral-tester
description: Strict-enforcement mode for onboarding, demos, and skill evaluation. When BICAMERAL_TESTER_MODE=1 is set on the MCP server, bicameral.search and bicameral.brief responses include blocking action_hints the agent MUST address before any write operation. This skill explains when to enable it, what the contract is, and how to debug it.
---

# Bicameral Tester Mode

Tester mode is an opt-in strict-enforcement layer on top of
`bicameral.search` and `bicameral.brief`. It makes bicameral **push**
signal at the agent instead of waiting for the agent to ask — on every
query, the server appends `action_hints` that describe what MUST be
addressed before any code change.

**The flag is off by default.** When off, responses are byte-identical
to v0.4.8 except for the new empty `action_hints: []` field. Enable it
for onboarding flows, demo runs, skill evaluation, and any session
where you want the tools to be loud instead of quiet.

## When to enable

- **Onboarding a new user** to a repo with an existing bicameral ledger
  — tester mode makes the first few queries surface drift, divergences,
  and unresolved questions the user needs to know about before editing
  any related code.
- **Demos** where you want the audience to see bicameral doing
  adversarial-audit work, not just retrieval.
- **Skill evaluation** (M1 / Silong's eval harness) — tester mode
  produces structured hint objects that are easier to score than
  free-text agent outputs.
- **Your first few sessions in a new codebase** — a safety net against
  editing near drifted regions without realizing.

## How to enable

Set the env var on the MCP server process before it starts:

```bash
BICAMERAL_TESTER_MODE=1
# or: true, yes, on (case-insensitive)
```

Any other value (including empty, `0`, `false`) means off. The flag is
read at `BicameralContext.from_env()` time, which runs once per MCP
tool call — so changes take effect on the next tool invocation, no
server restart needed if you set the env var in the parent shell.

## What fires

### On `bicameral.search` responses:

- **`review_drift`** — at least one matched decision has status
  `drifted`. The recorded intent no longer matches the current code.
  Refs: the drifted intent_ids + the file paths their regions touch.
- **`ground_decision`** — at least one matched decision has no code
  grounding (status `ungrounded`). Refs: the ungrounded intent_ids.

### On `bicameral.brief` responses:

- **`resolve_divergence`** — two non-superseded decisions contradict
  on the same symbol. Refs: `symbol (file_path)` strings.
- **`review_drift`** — at least one decision in scope is drifted.
  Refs: drifted intent_ids.
- **`answer_open_questions`** — gap extraction found
  open-question-shaped decisions in scope. Refs: truncated gap
  descriptions.

Each hint has `blocking: true` and a human-readable `message` field.
**Never paraphrase the message** — surface it verbatim so testers can
observe exactly what the server signaled.

## The blocking contract

When a tester-mode response includes `action_hints` with
`blocking: true`, the agent MUST:

1. Surface each hint's `message` to the user verbatim, along with the
   relevant refs (drifted files, divergent symbols, open questions).
2. **Pause before any write operation** (file edit, commit, PR,
   `bicameral_ingest`) until the user acknowledges the hint and either
   resolves it or explicitly tells the agent to proceed anyway.
3. If the user chooses to proceed despite a blocking hint, record the
   decision — "user acknowledged X and chose to proceed" — so the
   override is visible in transcripts.

**MCP cannot actually force this** — the wire protocol is advisory.
Enforcement lives in this skill contract. A skill-following agent is
expected to stop at blocking hints; a non-compliant agent can ignore
them. If adoption telemetry shows hint-ignore rates are high, v0.5.x
may add server-side refusal (e.g. `ingest_payload` refuses inputs when
blocking hints are outstanding for related decisions).

## When NOT to enable

- **Production adoption of a mature ledger** — tester mode is loud and
  optimized for discovery. Once the user knows the ledger and trusts
  the grounding, tester mode becomes noise. Turn it off for daily
  workflow once the first session or two are past.
- **Automated ingest flows** — the auto-fired `bicameral.brief` from
  `bicameral.ingest` (v0.4.8+) already surfaces divergences and gaps
  through the fused `IngestResponse.brief` field. Tester mode adds the
  blocking wrapper; if your ingest is headless (no user to pause for)
  the hints have nowhere to go.

## Debugging

If hints aren't firing when you expect them to:

1. **Check `ctx.tester_mode`** — print it in a handler to confirm the
   env var parsed correctly. Only `1 / true / yes / on` (case-insensitive)
   enable it; anything else is off.
2. **Check the `status` field on matches.** The
   `review_drift` search hint fires only when at least one match has
   `status == "drifted"`. In v0.4.8 and earlier, `handle_search_decisions`
   had a bug where every match was reported as `pending` regardless of
   real state (read `status` from the wrong table). v0.4.9 fixes this —
   make sure you're running v0.4.9+.
3. **Check the ledger actually has drifted state.** A decision only
   becomes drifted after `handle_link_commit` detects a content_hash
   mismatch against a grounded region's stored baseline. If no commit
   has introduced drift, there's nothing for the hint to fire on.
4. **Check the pollution guard.** Drift status is only persisted when
   `HEAD == authoritative_ref` (the v0.4.6 pollution guard). On a
   feature branch, drift is detected but not persisted — the next
   search will still see `reflected`. Override via
   `BICAMERAL_AUTHORITATIVE_REF=<your-branch>` for local experiments.
