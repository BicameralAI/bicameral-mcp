# Plan: Flow 3 e2e prompt-clarity hardening (#197)

**change_class**: hotfix

**doc_tier**: minimal

## Open Questions

None. All design choices resolved in `/qor-plan` dialogue:
- Q1 diagnosis depth → ship plausible-fix directly (option B); measure stability via the next 5 PR CI runs as natural sampling rather than running an expensive 10× isolated baseline.
- Q2 scope → Flow 3 only (option A); do not pre-emptively touch Flow 2's prompts.
- Q3 doc tier → `minimal` (option A); bug fix on test prompt, not a feature.

## Phase 1: Rewrite Flow 3 prompt with imperative shell phrasing

### Affected Files

- `tests/e2e/prompts/flow-3-commit-sync.md` — replace the existing 1-line verb-y prompt (`"Stage and commit it as ..."`) with explicit imperative shell phrasing (`"Run `git add ...` and `git commit -m ...`"`). The agent currently has Bash granted (`run_e2e_flows.py:474` allowed-tools list), `--dangerously-skip-permissions` set, and `--resume` re-passes the tool grant — so the prompt is the only remaining diagnostic surface per #197's investigation paths.
- `tests/e2e/README.md` — append a short "Flow 3 'agent did NOT commit' debugging note" subsection capturing what we ruled out (tool grant, permissions, session continuation) and what shifted the cause to prompt-clarity, so the next maintainer who sees a Flow 3 flake has a starting checklist.
- `CHANGELOG.md` — under `[Unreleased]` / `### Fixed`: one-line entry referencing #197.

### Changes

**`tests/e2e/prompts/flow-3-commit-sync.md`** — replace the existing single-paragraph prompt with two explicit imperative shell steps:

```markdown
Edit `cherry-pick.ts` to add a one-line comment above the `CherryPickResult` enum: `// Cherry-pick: roadmap v2.7.1 — context menu + interactive`.

Then run `git add cherry-pick.ts && git commit -m "docs: annotate cherry-pick origin"` to commit the change.
```

Two changes from the prior shape:
1. Replaces *"Stage and commit it as..."* with explicit *"Run `git add ... && git commit -m ...`"*. Model can no longer interpret the verbs as non-shell actions.
2. Splits edit + commit into two named sequential steps with the file path and commit message inline.

**`tests/e2e/README.md`** — append the following subsection at the end of the file (or in the appropriate "Debugging" section if one exists; verified at implement time):

```markdown
### Debugging "Flow 3 ❌ FAIL: agent did NOT commit"

Symptom: Flow 3 fails with `stream-json precondition: agent did NOT commit in Flow 3` and zero compliance_check rows in the test ledger.

Investigation order:
1. **Prompt clarity** (most likely first cause). Check `tests/e2e/prompts/flow-3-commit-sync.md`. The prompt MUST include explicit imperative shell phrasing — `Run \`git add ...\` and \`git commit -m ...\`` — not verb-y phrasing like "Stage and commit it as ..." which newer models can interpret as non-shell actions. Resolved in #197.
2. **Allowed-tools grant**. Check `tests/e2e/run_e2e_flows.py` allowed-tools list. `Bash` MUST be present alongside `mcp__bicameral,Read,Grep,Edit`. The grant is re-passed on every invocation including `--resume`.
3. **Permissions gate**. Confirm `--dangerously-skip-permissions` is on the `claude -p` command list (it is, by default). If removed, the agent stops to ask before every Bash call.
4. **Session continuation**. Flow 3 resumes the `dev_session` chain via `--resume`. The resume-session command is built in the same `cmd` list as the first-in-group invocation, so the tool grant is preserved.
5. **Model behavior**. If 1-4 are clean and Flow 3 still flakes, it's a model-version drift — re-run with the next model release and re-evaluate prompt phrasing.
```

**`CHANGELOG.md`** — under `[Unreleased]` / `### Fixed`:

```markdown
- Flow 3 e2e prompt clarified to use imperative shell phrasing for `git add` + `git commit` (#197). Resolves the "agent did NOT commit" flake observed on PR #194 + #195 e2e runs where the agent interpreted "stage and commit" as a non-shell verb.
```

### Unit Tests

None for this phase. Per `doctrine-test-functionality` and the precedent set by plan-156 PR A's Phase 2 (skill markdown), plan-156b's Phase 1 (skill markdown), and plan-187's Phase 2: prompt files are LLM-consumed agent-instruction, not pytest-invocable. The functional validation IS the e2e harness running Flow 3 against the new prompt; that's what the CI Commands section invokes.

A static lint asserting the prompt contains substring `git add` would be presence-only by construction (it doesn't validate the agent's behavior on the new prompt); per the doctrine, presence-only assertions don't satisfy the test functionality bar. The right shape is the e2e harness exercising the actual flow, which the CI commands cover.

The README and CHANGELOG changes are pure documentation; no code units to test.

## CI Commands

- `python tests/e2e/run_e2e_flows.py --flow "Flow 3"` — runs Flow 3 in isolation against the new prompt. Validates that the agent executes the commit step, the SessionEnd path produces compliance_check rows in the test ledger, and the asserter's `_validate_flow3_via_ledger` succeeds. With the `--flow` substring filter (shipped in #156 PR B), Flow 3 is the only matching flow — `dev_session` group has one selected member, so the harness runs it as a fresh `--session-id` invocation rather than `--resume`. This is fine for isolation testing because Flow 3 only depends on `cherry-pick.ts` existing in the desktop repo (which the harness reset already provides), not on Flow 2's session state.
- `python tests/e2e/run_e2e_flows.py` — full e2e suite. Validates that the prompt change doesn't regress chained-flow behavior (Flow 3's commit must still produce the link_commit + compliance_check sequence the dev_session chain depends on for Flow 4's transcript context).
- `grep -n "Stage and commit\|stage and commit" tests/e2e/prompts/flow-3-commit-sync.md` — sanity check that no verb-y phrasing remains. Expected: zero matches after Phase 1.
- `python scripts/lint_plan_grounding.py plan-197-flow3-prompt-clarity.md` — runs the plan-grounding lint shipped in PR #121 against this plan to catch any backtick-wrapped path tokens that don't resolve on the working tree.
