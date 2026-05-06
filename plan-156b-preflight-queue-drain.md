# Plan: Preflight queue-drain integration + Flow 4b cross-flow assertion (#156 PR B)

**change_class**: feature

**doc_tier**: standard

**boundaries**:
- limitations: Per-preflight drain caps at 4 surfaced ask-corrections (the existing preflight ≤4-question hard cap; queue-drained corrections share that budget with in-session correction findings). When drained corrections fill the cap, remaining pending files stay in `.bicameral/pending-transcripts/` for the next preflight to pick up. Multi-session backlog clears across multiple preflights, not in a single one.
- non_goals: SessionStart hook (still — non-goal carried forward from plan-156); auto-ingest mode (every drained correction goes through the same user-confirmation `AskUserQuestion` flow as in-session corrections — the prior `--auto-ingest` shape that bypassed confirmation is gone permanently); team-server-driven backlog notification; UI surface for browsing/managing pending corrections.
- exclusions: Plan-156 PR A's queue-write path is unchanged. This plan modifies only the read/drain side. The `events/transcript_queue.py` module is read but not restructured. `transcript_archive.py` CLI helper is unchanged. **Forward-compat note**: operator stated during dialogue that "a UI solution might prove to be a better overall solution" for managing a backlog of corrections; this plan ships the text-prompt approach as the v1 default, with the design explicitly preserving the option for a v1.x UI surface to take over by reading the same queue module without protocol churn.

## Open Questions

None. All design choices resolved in `/qor-plan` dialogue:
- Q1 queue-drain integration → modify capture-corrections in-session mode to also run Step 0 (single skill call from preflight; queue-drain becomes implicit on every preflight).
- Q2 drain-batch cap → drain all pending files but cap surfaced ask-corrections at 4 (use the existing preflight cap as natural backpressure; remaining pending files stay queued).
- Q3 Flow 4 harness rewrite → add new `Flow 4b` testing the cross-flow path explicitly; preserve existing Flow 4 in-session coverage.

## Phase 1: Wire queue-drain into capture-corrections in-session mode (SKILL.md updates)

This phase has no new unit-testable code; it is SKILL.md edits to two skills. Per `doctrine-test-functionality` and the precedent set by plan-156 PR A's Phase 2 (and plan-187's Phase 2): skill markdown is LLM-consumed agent-instruction, not pytest-invocable. The structural primitives the new instructions invoke (`list_pending_fifo`, `archive_processed`, the `transcript_archive.py` CLI helper) are already unit-tested in PR A's `tests/test_session_end_queue_writer.py` (9 tests). The end-to-end correctness of the SKILL.md change is asserted by Phase 2's `Flow 4b`.

### Affected Files

- `skills/bicameral-capture-corrections/SKILL.md` — modify the "## In-session mode" section. Currently the in-session mode scans only the last ~10 user turns (per the section's existing description). Update it so that in-session mode runs Step 0 (queue drain) FIRST, then the in-session turn scan. Add an explicit cap-handling rule: stop draining files once accumulated ask-corrections reach 4 (the existing preflight ≤4-question cap); remaining pending files stay in `.bicameral/pending-transcripts/` for the next preflight. Update the telemetry section's `g11_corrections_*` diagnostic counter list to include three new fields (`g11_queue_drained`, `g11_queue_remaining`, `g11_queue_cap_hit`) so post-deploy telemetry can quantify how often the cap is binding (signal that a UI surface is warranted).
- `skills/bicameral-preflight/SKILL.md` — update Step 3.5's description (currently at line 244-256). The existing copy says capture-corrections in in-session mode "scans the last ~10 user messages, auto-ingests mechanical corrections silently, and returns ask-corrections for merging into the stop-and-ask queue below." Append: "Post #156 PR B, in-session mode also drains the pending-transcripts queue at `<repo>/.bicameral/pending-transcripts/` (transcripts from prior sessions whose corrections never surfaced because that session ended without a follow-up preflight). Drained ask-corrections share the same ≤4 cap as in-session corrections; remaining pending files stay queued for the next preflight to pick up. See `skills/bicameral-capture-corrections/SKILL.md` Step 0 for the canonical drain rubric." This keeps the canonical rubric in capture-corrections (single source of truth) and preflight references it.

### Changes

**`skills/bicameral-capture-corrections/SKILL.md`** — in the "## In-session mode" section, replace the description of what the mode scans to incorporate the queue drain. The in-session mode's "## Steps" sub-section gains a new step before the existing "scan recent turns" step:

```markdown
**Step 0 (in-session mode).** Before scanning recent in-session turns, drain the pending-transcripts queue per the "Step 0 — drain the pending-transcripts queue (#156)" rubric in the canonical scan-and-classify rubric above. In in-session mode the drain is bounded:

- Process pending files in mtime-order (oldest first), applying Steps A/B/C to each file's user turns.
- Track accumulated ask-corrections across all processed files.
- When accumulated ask-corrections reach 4 (the preflight ≤4-question cap), stop processing further pending files and surface a final note: "N more pending transcript(s) — invoke `/bicameral-capture-corrections` directly to drain manually." Remaining files stay in `.bicameral/pending-transcripts/` for the next preflight.
- Archive each fully-processed file via `python3 scripts/hooks/transcript_archive.py <basename>.jsonl`. Do NOT archive partially-processed files (the cap was hit mid-scan); the file stays pending and the next preflight resumes from its first un-surfaced correction.
- If `<repo>/.bicameral/pending-transcripts/` doesn't exist or is empty, skip Step 0 silently — same shape as the canonical rubric's empty path.

The 4-cap is shared with the in-session turn-scan that runs in the next sub-step: queue-drained ask-corrections + in-session ask-corrections ≤ 4 total. If the queue alone fills the cap, the in-session turn scan still runs (its mechanical corrections still auto-ingest silently) but its ask-corrections are dropped (not surfaced) to preserve the cap.
```

The "## Telemetry" section's `g11_corrections_*` diagnostic field list at line ~36-46 gains:

```markdown
    g11_queue_drained: N,        # pending files fully processed and archived
    g11_queue_remaining: N,      # pending files left after drain (>0 when cap was hit OR partial processing left files for next preflight)
    g11_queue_cap_hit: <bool>,   # true if accumulated ask-corrections reached 4 mid-drain
```

**`skills/bicameral-preflight/SKILL.md`** — in the "### 3.5 Scan recent user turns for uningested corrections" section (line 244), append a paragraph after the existing description:

```markdown
**Queue drain (#156 PR B):** in-session mode also drains the pending-transcripts queue at `<repo>/.bicameral/pending-transcripts/` — transcripts from prior sessions whose corrections never surfaced (because that session ended without a follow-up preflight). Drained ask-corrections share the same ≤4-cap as in-session corrections; remaining pending files stay queued for the next preflight to pick up. The canonical drain rubric lives in `skills/bicameral-capture-corrections/SKILL.md` (Step 0 of the scan-and-classify rubric); preflight delegates to it via the in-session mode invocation.
```

### Unit Tests

None for Phase 1. Per `doctrine-test-functionality` and the precedent set by plan-156 PR A Phase 2 + plan-187 Phase 2: skill markdown is LLM-consumed agent-instruction, not pytest-invocable. The structural primitives the new instructions invoke (`list_pending_fifo`, `archive_processed`, `scripts/hooks/transcript_archive.py`) are already unit-tested in PR A's `tests/test_session_end_queue_writer.py`. The end-to-end correctness of the SKILL.md instruction change is asserted by Phase 2's `Flow 4b` cross-flow assertion.

## Phase 2: Flow 4b — cross-flow ledger assertion via queue drain

Adds a new e2e flow that exercises the cross-flow path end-to-end: Flow 4 plants a correction that the in-session preflight does NOT catch (correction stated AFTER the only preflight in Flow 4 fires); SessionEnd hook writes the transcript to `.bicameral/pending-transcripts/`; Flow 4b's preflight Step 3.5 drains the queue, surfaces the correction, and the user-confirmation flow ingests it; Flow 4b's asserter validates the test ledger contains a `source=agent_session` decision describing the correction.

### Affected Files

- `tests/test_run_e2e_flows_filter.py` — new behavioral tests (3 tests) for the `--flow` substring filter helper added below.
- `tests/e2e/prompts/flow-4b-queue-drain.md` — new prompt file. Drives a fresh `claude -p` session that begins with a code-implementation prompt (auto-fires preflight per the UserPromptSubmit hook). Prompt content guides the agent through one trivial code-implementation task; the prompt does NOT mention bicameral, queue, or corrections — the queue drain must happen automatically through the auto-fired preflight Step 3.5 invoking capture-corrections in-session mode.
- `tests/e2e/run_e2e_flows.py` — three additions:
  - `FlowSpec` for Flow 4b. Place it immediately after the existing Flow 4 entry (around line 1180) so it inherits the `session_group="dev_session"` continuation.
  - `assert_flow_4b` asserter function (described below).
  - **`--flow PATTERN` argparse filter on `main()`**. Add a small `_filter_flow_plan(plan: list[FlowSpec], pattern: str | None) -> list[FlowSpec]` helper above `main()`. When `pattern` is None, return `plan` unchanged. When `pattern` is given, return only the FlowSpecs whose `flow_id` contains the pattern as a substring (case-sensitive). Wire `main()` to construct an `argparse.ArgumentParser`, parse `sys.argv[1:]`, and apply `_filter_flow_plan(FLOW_PLAN, args.flow)` before the iteration loop at line ~1199. Print a one-line summary when filtering: "Filter `--flow=<pattern>`: N of M flows selected." When the filter selects zero flows, exit non-zero with a clear error so CI surfaces the typo. Substring semantics chosen so `--flow "Flow 4"` runs both Flow 4 and Flow 4b (useful for the cross-flow path validation in this PR's CI command), while `--flow "Flow 4b"` runs Flow 4b only (useful when developing the asserter in isolation against a pre-staged queue).
  - Update the existing Flow 4 advisory text (modified in plan-156 Phase 3) — replace the placeholder "PR B follow-up" wording with a concrete "see Flow 4b for cross-flow assertion" pointer.
- `tests/e2e/_harness_setup.py` — minor: extend the existing flow-4 prompt-staging logic (if any flow-specific state is required for Flow 4b's run) so the queue is verifiably non-empty when Flow 4b starts. Verification at implement time: if the existing harness already preserves `<desktop_repo_path>/.bicameral/pending-transcripts/` between flows in the same `session_group`, no harness change is needed beyond importing the new flow spec.

### Changes

**`tests/e2e/prompts/flow-4b-queue-drain.md`** (new):

```markdown
# Flow 4b: Queue drain via preflight

You're continuing work in the same project as the prior session. Make a small change to a tracked file in this repo: pick any function in `events/writer.py` and add a one-line docstring to it (no behavior change). Use the standard write-op flow.

(This prompt deliberately does not mention bicameral, queues, or corrections. The queue drain happens automatically through the preflight hook on the user-prompt classification.)
```

**`tests/e2e/run_e2e_flows.py`** — add the FlowSpec entry after Flow 4:

```python
FlowSpec(
    flow_id="Flow 4b",
    prompt_file="flow-4b-queue-drain.md",
    asserter=assert_flow_4b,
    category="agentic_layer",
    session_group="dev_session",
    advisory=(
        "Flow 4b validates the cross-flow path closed by #156 PR B: "
        "the prior flow's SessionEnd hook wrote a transcript into "
        ".bicameral/pending-transcripts/; Flow 4b's auto-fired "
        "preflight (UserPromptSubmit hook) invokes capture-corrections "
        "in-session mode, which now drains the queue per #156 PR B's "
        "Step 0 integration. Asserter checks the test ledger contains "
        "a source=agent_session decision describing the prior flow's "
        "correction — proving the cross-flow capture path closed."
    ),
),
```

**`tests/e2e/run_e2e_flows.py`** — add `assert_flow_4b` asserter function. The asserter queries the test ledger via the same pattern Flow 5's asserter uses (parameterizing on `LEDGER_DIR`) and asserts that `decisions` includes at least one row whose `source = "agent_session"` and whose `description` contains a substring from the Flow 4 prompt's planted correction (the actual substring is determined at implement time by reading `tests/e2e/prompts/flow-4-session-end.md` to identify the correction phrase). The asserter also verifies that `<desktop_repo_path>/.bicameral/pending-transcripts/` is empty after Flow 4b runs (drain completed), and that `<desktop_repo_path>/.bicameral/processed-transcripts/` contains the archived transcript file.

**Flow 4 advisory update**: modify the existing advisory text (last touched in plan-156 Phase 3) to replace the "deferred to PR B" placeholder with a concrete pointer:

```python
advisory=(
    "Flow 4 captures an emerging constraint via correction markers "
    '("wait", "shouldn\'t") — no collision-detection involved. NOT '
    "the same gap as #154 (which is Flow 2a / contradiction-with-"
    "prior-decision specific). #156 (PR A) shipped the queue-write; "
    "#156 (PR B) shipped the preflight Step 3.5 queue-drain "
    "integration — see Flow 4b for the cross-flow ledger assertion "
    "that validates the full session_end → next-preflight → ingest "
    "pipeline end-to-end."
),
```

### Unit Tests

`Flow 4b` IS the e2e test for the cross-flow path. Its asserter (`assert_flow_4b`) is the unit-under-test invocation: the e2e harness runs the flow against a real test ledger + materialized MCP config, the asserter queries the ledger, and the assertions validate cross-flow ingest behavior. This is the same pattern every other Flow N asserter follows in `run_e2e_flows.py`.

Three explicit assertions inside `assert_flow_4b`:
- `tests/e2e/run_e2e_flows.py::assert_flow_4b` — assert the test ledger contains ≥1 `source=agent_session` decision whose `description` matches the Flow 4 correction substring. Confirms cross-flow ingest landed.
- Same asserter — assert `<desktop_repo_path>/.bicameral/pending-transcripts/` is empty after Flow 4b. Confirms drain completed.
- Same asserter — assert `<desktop_repo_path>/.bicameral/processed-transcripts/` contains the archived transcript file with the same content as the original. Confirms archival via `transcript_archive.py` happened.

Three additional unit tests for the `--flow` filter helper (no Claude Code subprocess required — the helper is pure):

- `tests/test_run_e2e_flows_filter.py::test_filter_flow_plan_returns_all_when_pattern_is_none` — call `_filter_flow_plan(FLOW_PLAN, None)`; assert the returned list is identical (same length, same FlowSpec objects, same order) to `FLOW_PLAN`. Confirms the no-arg path leaves the plan unchanged.
- `tests/test_run_e2e_flows_filter.py::test_filter_flow_plan_substring_matches_multiple` — call `_filter_flow_plan(FLOW_PLAN, "Flow 4")`; assert the returned list contains exactly the FlowSpec(s) whose `flow_id` starts with "Flow 4" (post-Phase-2 implementation: both `Flow 4` and `Flow 4b`); assert order is preserved from the source list. Confirms substring matching includes Flow 4b alongside Flow 4 — the case the PR's CI command exercises.
- `tests/test_run_e2e_flows_filter.py::test_filter_flow_plan_exact_match_returns_single` — call `_filter_flow_plan(FLOW_PLAN, "Flow 4b")`; assert the returned list contains exactly one FlowSpec with `flow_id == "Flow 4b"`. Confirms narrow filter selects Flow 4b only — the case the dev-velocity workflow uses. Acceptance question: "if `_filter_flow_plan` were silently broken (e.g. returned the unfiltered list, or returned []), would this test fail?" YES — the assertion compares the filter's actual return against the expected single-element list.

## CI Commands

- `pytest tests/test_session_end_queue_writer.py -v` — re-run PR A's 9 tests to confirm Phase 2's Flow 4b dependencies (the queue primitives) still pass.
- `pytest tests/ -v --no-cov --ignore=tests/e2e` — full non-e2e regression sweep. No tests in this set should change behavior; this is a sanity check.
- `pytest tests/test_run_e2e_flows_filter.py -v` — validates the new `_filter_flow_plan` helper (3 tests; pure Python, no Claude Code subprocess required).
- `python tests/e2e/run_e2e_flows.py --flow "Flow 4"` — runs Flow 4 + Flow 4b together (substring filter). Flow 4 stages the queue (its SessionEnd hook writes the transcript to `.bicameral/pending-transcripts/`); Flow 4b drains and asserts. This is the canonical CI command for validating the cross-flow path.
- `python tests/e2e/run_e2e_flows.py` — run the full e2e suite. Validates that adding Flow 4b doesn't regress Flows 1/2/2a/3/4/5.
- `ruff check tests/e2e/run_e2e_flows.py && ruff format --check tests/e2e/run_e2e_flows.py` — lint + format on the file with the new flow spec.
- `mypy tests/e2e/run_e2e_flows.py` — type-check the new asserter signature.
