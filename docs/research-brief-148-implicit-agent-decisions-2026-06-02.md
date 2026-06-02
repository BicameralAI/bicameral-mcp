# Research Brief — #148: Capturing implicit decisions the agent makes while implementing

**Date**: 2026-06-02
**Issue**: #148 (P1, enhancement / observability) — co-owned jinhongkuan + Knapp-Kevin
**Mode**: research-and-design (implementation explicitly out of scope per the issue)
**Sibling**: #147 / `bicameral-capture-corrections` captures the *developer-stated* implicit-decision population; this brief covers the *agent-authored* population.

## 1. Problem restatement

Agentic implementation makes a large population of unspoken design decisions —
library picks, retry/no-retry, exception handling, concurrency model, inline-vs-
extract — whose rationale never becomes text a human reads. They shape the
codebase and are a dominant future-drift source, yet the ledger captures **zero**
of them today. The open question: *which signal(s), in which substrate, surface
these reliably and portably (interactive Claude Code, headless `claude -p`,
hosted runner) without flooding the PM inbox with noise?*

## 2. Substrate reality in THIS codebase (what's actually observable)

Grounded against the current hook + transcript machinery, not generic theory:

| Substrate | Exists today? | Notes |
|---|---|---|
| **SessionEnd transcript drain** | ✅ `scripts/hooks/session_end_queue_writer.py` → `events/transcript_queue.py` (`write_pending`/`list_pending_fifo`/`archive_processed`) | The Claude Code stream-json/ndjson transcript (assistant messages, `tool_use` blocks, and — if extended thinking is on — `thinking` blocks) is queued at SessionEnd. **`bicameral-capture-corrections` already scans this exact artifact** for the #147 population. The #148 agent-decision signals that live in the transcript are therefore **near-zero marginal substrate cost** — a second lens over an artifact we already drain. |
| **post-commit hook** | ✅ `scripts/hooks/post_commit_sync_reminder.py` + git `post-commit` (`_GIT_POST_COMMIT_HOOK`) | Already fires on every commit. Agent commit messages routinely carry rationale ("chose X over Y because…"). **High-signal, low-noise, fully substrate-portable (git is universal), already persisted.** Not in the issue's candidate list — added here (the issue invited additions). |
| **PostToolUse on Edit/Write** | ❌ not wired | Current PostToolUse hooks are on `Bash` and `bicameral_preflight` only (`release/hooks_source.py`). "Diff-shape inference" needs a **new** hook. Claude-Code-hook-specific → not portable to headless/hosted as-is. |
| **`bicameral.note_choice` tool** | ❌ does not exist | "Structured introspection probe" is net-new. Portable (just a tool call) but voluntary — agents skip it absent a backstop. |
| **Counterfactual replay** | ❌ no harness | Replay-and-diff only feasible in headless/hosted; expensive; research-only. |
| **Reasoning/thinking traces** | ⚠️ conditional | Present in the transcript **only when extended thinking is enabled**; not guaranteed across substrates or model configs. Treat as an opportunistic enrichment, not a load-bearing signal. |

## 3. Candidate signals — evaluated

Scored on cost (capture+compute), noise (false-positive rate → PM-inbox pollution,
the #393 trust risk), and portability across {interactive, headless `claude -p`,
hosted}.

| # | Signal | Substrate | Cost | Noise | Portability | Verdict |
|---|---|---|---|---|---|---|
| 1 | **Marker-based prose scan** (LLM-as-judge over assistant text for "opting for X", "I'll go with Y because") | SessionEnd transcript (reuse #147) | Low (one batched judge pass already happening for #147) | Med | interactive ✅ / headless ✅ / hosted ✅ | **LEAD** |
| 2 | **Tool-call deltas** (chose `Edit` over `Write`, etc.) | SessionEnd transcript | Low | **High** (most tool choices are not decisions) | ✅/✅/✅ | Weak alone; use only as a corroborating feature, not a trigger |
| 3 | **Reasoning/thinking-trace scan** ("decided not to…") | transcript (if thinking on) | Low | Med | ⚠️ thinking-dependent | Opportunistic enrichment to #1 when present |
| 4 | **Commit-message rationale scan** (NEW) | post-commit hook + git | Low | **Low** | ✅/✅/✅ | **LEAD** (best signal-to-noise; rationale is already authored deliberately) |
| 5 | **`bicameral.note_choice` voluntary probe** | new tool + skill nudge | Low | **Very low** (explicit) | ✅/✅/✅ | **Adopt as opt-in high-signal**, with #1/#4 as the passive backstop |
| 6 | **Diff-shape inference** (PostToolUse on Edit/Write → classify "non-trivial choice?") | new hook | Med (per-edit classifier) | Med-High | ❌ hook-only (not headless/hosted) | Defer — portability gap + cost; revisit if #1/#4 under-recall |
| 7 | **Counterfactual probing** (replay, diff the diffs) | new harness | **High** | Low | headless/hosted only | Defer — research-only; not a product path now |

## 4. Recommendation — phased, portability-first

**Phase 1 (passive, reuse existing substrate, no new hooks/tools):** a unified
LLM-as-judge **agent-decision lens** that runs in the **same SessionEnd transcript
pass `bicameral-capture-corrections` already performs** (signal #1, enriched by #3
when thinking is present) **plus a commit-message rationale scan in the existing
post-commit path** (signal #4). Both are fully portable and add no new substrate.
Output: candidate agent-decisions routed through the *same* `proposed` →
ratify queue as #147, so the PM stays in control and noise is gated by the
existing ratification step (not auto-committed to the ledger).

**Phase 2 (opt-in high-signal):** add a `bicameral.note_choice(choice, rationale,
alternatives)` tool (signal #5) the implement-skill nudges on non-trivial choices;
the Phase-1 passive scan is its backstop for when the agent skips it.

**Defer:** diff-shape hook (#6 — portability gap) and counterfactual replay (#7 —
cost) until Phase 1 recall data shows they're needed.

**Noise is the existential risk** (per #393: "every false positive a PM has to
skip erodes the value prop"). Every candidate from these signals MUST enter as a
`proposed` decision behind ratification — never auto-ratified — and SHOULD reuse
the #393 dev-process hard-exclude so the agent's CI/lint/test/tooling choices are
dropped before they reach the PM.

## 5. Open questions / risks (for Jin)

- **Recall vs. noise threshold for the judge** — what hit-rate is acceptable
  before PM trust erodes? Suggest measuring on real session transcripts before
  wiring auto-surfacing.
- **Grounding**: agent-decisions are inherently code-bound (they live in the
  diff) — can the SessionEnd pass attach `code_regions` from the diff so they
  enter as L2/L3 grounded rather than ungrounded? (Ties to the #404 hierarchy.)
- **Thinking-trace availability** across model configs / API tiers — confirm
  before relying on signal #3.
- **Substrate parity test** — a capture mechanism must be validated on all three
  substrates; recommend a parity fixture in `v0-user-flow-e2e` before GA.

## 6. Required next action

Jin selects one or more Phase-1 signals (recommendation: #4 commit-message scan +
#1 transcript prose-scan, both passive and portable). That selection becomes a
separate **implementation** issue (the issue keeps implementation out of scope).
This brief is the decision input; no code ships from #148 itself.
