# v0 user flow e2e

End-to-end validation of `BicameralAI/bicameral#108`'s six canonical user
flows, driven by **real Claude Code CLI sessions** with `bicameral-mcp`
registered as an MCP server. Test fixture: a pinned commit of
`github.com/desktop/desktop`, with `docs/process/roadmap.md` as ingest
content.

This is the canonical CI test for the spec. The handler-replay simulation
at `scripts/sim_issue_108_flows.py` complements it for fast local iteration
on handler logic without burning Claude API calls.

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

GitHub Actions workflow: `.github/workflows/v0-user-flow-e2e.yml`.

- Triggers on PRs touching `tests/e2e/**`, `handlers/**`, `ledger/**`,
  `contracts.py`, `skills/bicameral-*/**`, or the workflow itself.
- Runs in the `production` GitHub environment for `CLAUDE_CODE_OAUTH_TOKEN`.
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
- **Skill files** — `.claude/skills/bicameral-*/SKILL.md` parsing, trigger
  matching, prompt construction
- **Caller LLM** — natural-language → tool-call sequencing, auto-chains
  (preflight → capture-corrections → context-sentry → ingest → judge_gaps)

This e2e suite covers all three. Together they form the spec's two-level
validation: handler invariants (replay sim) + user-experience contract
(this directory).

<!-- ci-repro: dummy edit to trigger workflows on dev (no-op) -->

