# MCP Regression Tests

The suite is **phase-gated**: each phase layers on the previous one and is toggled
by an environment variable, so you can run only what is wired up locally. All
phases run against **real adapters** — the legacy mock layer is retired (see
`mocks/README.md` for history). In tests the embedded SurrealDB runs in-process
via `SURREAL_URL=memory://` (no server, no persistence).

## Quickstart

```bash
source .venv/bin/activate            # or call .venv/bin/pytest directly

# Packaging / startup smoke — registers and lists every MCP tool
bicameral-mcp --smoke-test

# Full suite, the way CI runs it
SURREAL_URL=memory:// pytest tests/ -v
```

## Phase gates

| Phase | File | Gate (env) | Validates |
|---|---|---|---|
| 1 | `test_phase1_code_locator.py` | `USE_REAL_CODE_LOCATOR=1` + `REPO_PATH=…` | Code-locator correctness: located paths exist on disk, symbols are real repo names, confidence in range |
| 2 | `test_phase2_ledger.py` | `USE_REAL_LEDGER=1` + `SURREAL_URL=memory://` | Ledger correctness: idempotent ingest, BM25 search relevance, file→decision reverse traversal, `link_commit` status updates |
| 3 | `test_phase3_integration.py` | Both of the above | End-to-end: ingest transcript → code locator → graph store → query-back coheres |

```bash
# Phase 1
USE_REAL_CODE_LOCATOR=1 REPO_PATH=/path/to/repo pytest tests/test_phase1_code_locator.py -v

# Phase 2
USE_REAL_LEDGER=1 SURREAL_URL=memory:// pytest tests/test_phase2_ledger.py -v

# Phase 3 (full integration — needs both gates)
USE_REAL_CODE_LOCATOR=1 USE_REAL_LEDGER=1 SURREAL_URL=memory:// REPO_PATH=/path/to/repo \
  pytest tests/test_phase3_integration.py -v
```

## Environment variables

| Var | Default | Effect |
|---|---|---|
| `SURREAL_URL` | `memory://` | Ledger URL for tests (in-process, no persistence). Override when exercising a persistent SurrealKV path. |
| `USE_REAL_CODE_LOCATOR` | unset | Gate phase-1/3 code-locator tests on a real tree-sitter index. |
| `USE_REAL_LEDGER` | unset | Gate phase-2/3 tests on a real embedded SurrealDB adapter. |
| `REPO_PATH` | `.` | Repo the code locator indexes. |

## Packaging smoke

The installable surface is the first startup check:

1. `pip install -e ".[test]"`
2. `bicameral-mcp --smoke-test`
3. It prints the server name/version and **every registered MCP tool name** — 20
   today (18 `bicameral.*` ledger/session tools + the 2 code-locator primitives
   `validate_symbols` and `get_neighbors`). The asserted source of truth is
   `EXPECTED_TOOL_NAMES` in `server.py`; the smoke test fails if the live registry
   drifts from it. The user-facing subset is documented in the root `README.md`
   § MCP Tools Reference.

## Sociable testing

Handler and ledger tests default to **sociable** units (real `memory://` adapter,
`SimpleNamespace` ctx) — not mocks. The full contract and the reference patterns
are in the repo-root `CLAUDE.md` § "Sociable Testing for UX Paths".

## What CI runs

`.github/workflows/test-mcp-regression.yml` runs the phase suites plus the ledger,
schema-recovery, replay-determinism, extractor-parity, shadow-dispatch, and
dashboard tests in a single `pytest` invocation against `SURREAL_URL=memory://`,
then uploads JUnit XML + a self-contained HTML report as artifacts. The end-to-end
user-flow suite is separate and currently shelved to manual dispatch — see
`tests/e2e/README.md`.
