# v0 user flow e2e

End-to-end validation of the canonical user flows in
`BicameralAI/bicameral#108`, driven by **real Claude Code CLI sessions** with
`bicameral-mcp` registered as an MCP server. Five flows (1–5) are automated.
Test fixture: a pinned commit of `github.com/desktop/desktop`, with
`docs/process/roadmap.md` as ingest content.

> **Status: shelved to manual dispatch (#556).** This suite is no longer a PR
> gate. The harness accumulated maintenance debt — API-key credit exhaustion,
> agent-budget non-determinism (#272), and twice-reworked auth (#528, #540) —
> that blocked PRs without actionable signal. The test code and prompts are
> preserved; run it manually via **Actions → v0 user flow e2e → Run workflow**.
> A replacement validation strategy is tracked in RFQ #555.

The handler-replay simulation at `scripts/sim_issue_108_flows.py` is the fast
local path for iterating on handler logic without burning Claude API calls.

## What it tests

Each flow corresponds to a section of [bicameral#108 spec](https://github.com/BicameralAI/bicameral/issues/108):

| Flow | Spec section | Asserts |
|---|---|---|
| 1 | Record decisions from a meeting | `bicameral.ingest` called with mappings |
| 2 | Begin to write code (preflight) | `bicameral.preflight` called with `file_paths` |
| 3 | Commit code → reflected | `bicameral.link_commit` + `bicameral.resolve_compliance` (with verdicts) |
| 4 | End coding session | `bicameral.ingest` called with `source="agent_session"` |
| 5 | Review what's been tracked | `bicameral.history` called (with seed ingest + ratify) |

Each flow is a separate `claude -p` invocation with a fresh `memory://`
ledger. Within a session, prompts may chain multiple tool calls — the
asserter walks the entire stream-json transcript.

## How it works

```
prompts/flow-N-*.md  →  claude -p  →  stream-json transcript  →  assert
                          │
                          ├─ --mcp-config bicameral.mcp.json  (registers bicameral-mcp)
                          ├─ --strict-mcp-config              (no other MCP servers loaded)
                          ├─ --allowed-tools mcp__bicameral Read Grep
                          ├─ --add-dir <desktop_clone>        (skill Read access)
                          └─ --output-format stream-json --verbose
```

`run_e2e_flows.py` orchestrates all five flows, captures transcripts to
`test-results/e2e/flow-N.ndjson`, and asserts on the tool-use blocks.

## Running locally

```bash
# 1. Install bicameral-mcp + Claude Code CLI
cd pilot/mcp
pip install -e ".[test]"
npm install -g @anthropic-ai/claude-code

# 2. Authenticate Claude Code CLI (interactive — once)
claude auth

# 3. Clone the test fixture
git clone --depth=1 https://github.com/desktop/desktop /tmp/desktop-clone
cd /tmp/desktop-clone && git checkout -b main && cd -

# 4. Run all five flows
DESKTOP_REPO_PATH=/tmp/desktop-clone python tests/e2e/run_e2e_flows.py
```

Cost per run: ~$0.50–$2.00 across all five flows depending on how much the
LLM exercises in each session. Each run is bounded by `--max-budget-usd 2.0`
per flow.

## CI

GitHub Actions workflow: `.github/workflows/v0-user-flow-e2e.yml` —
**dispatch-only (shelved, #556)**.

- **No PR trigger.** Run manually: Actions → *v0 user flow e2e* → *Run workflow*.
  (It previously triggered on PRs touching `tests/e2e/**`, `handlers/**`,
  `ledger/**`, `contracts.py`, or `skills/bicameral-*/**`.)
- Replacement validation strategy: RFQ #555.
- Runs in the `ci-test` GitHub environment for `ANTHROPIC_API_KEY`
  (switched from `production` + `CLAUDE_CODE_OAUTH_TOKEN` in #528 after the
  org subscription was disabled).
- Pinned `desktop/desktop` commit in the workflow file (update by editing
  the env var).
- Uploads `test-results/e2e/*.ndjson` as job artifacts (30-day retention)
  for failure forensics.

## Updating

When the spec changes, update both:

1. The relevant `prompts/flow-N-*.md` (natural-language user prompt)
2. The matching `assert_flow_N` in `run_e2e_flows.py`

When `desktop/desktop`'s `roadmap.md` or `cherry-pick.ts` shape drifts in
ways that break the prompts or bind targets, bump the pinned commit in
the workflow + adjust prompts.

## Why not handler-replay only?

The handler-replay sim (`scripts/sim_issue_108_flows.py`) directly imports
handler functions and calls them. It's fast and useful for iterating on
handler logic, but it bypasses three layers we need to validate:

- **MCP protocol** — JSON-RPC over stdio, tool schema marshalling
- **Skill files** — `skills/bicameral-*/SKILL.md` parsing, trigger
  matching, prompt construction
- **Caller LLM** — natural-language → tool-call sequencing, auto-chains
  (preflight → capture-corrections → context-sentry → ingest → judge_gaps)

This e2e suite covers all three. Together they form the spec's two-level
validation: handler invariants (replay sim) + user-experience contract
(this directory).

## Debugging "Flow 3 ❌ FAIL: agent did NOT commit"

Symptom: Flow 3 fails with `stream-json precondition: agent did NOT commit in Flow 3` and zero `compliance_check` rows in the test ledger.

Investigation order (most likely first):

1. **Prompt clarity.** Check `tests/e2e/prompts/flow-3-commit-sync.md`. The prompt MUST include explicit imperative shell phrasing — `Run \`git add ...\` and \`git commit -m ...\`` — not verb-y phrasing like "Stage and commit it as ..." which newer models can interpret as non-shell actions. Resolved in #197.
2. **Allowed-tools grant.** Check `tests/e2e/run_e2e_flows.py` allowed-tools list (currently line ~474). `Bash` MUST be present alongside `mcp__bicameral,Read,Grep,Edit`. The grant is re-passed on every `claude -p` invocation including `--resume`.
3. **Permissions gate.** Confirm `--dangerously-skip-permissions` is on the `claude -p` command list (it is, by default at line ~480). If removed, the agent stops to ask before every Bash call and the headless session times out.
4. **Session continuation.** Flow 3 resumes the `dev_session` chain via `--resume`. The resume-session command is built in the same `cmd` list as the first-in-group invocation, so the tool grant is preserved. Verify by inspecting the cmd construction in `run_claude_session`.
5. **Model behavior.** If 1-4 are clean and Flow 3 still flakes, it's a model-version drift — re-run with the next model release and re-evaluate prompt phrasing. The `--flow "Flow 3"` filter (shipped in #156 PR B) makes isolation testing cheap.
