# Plan: register `link_commit` CLI subcommand + harden post-commit hook (Issue #124)

**Tracks**: BicameralAI/bicameral-mcp#124 — *post-commit hook silently no-ops because `bicameral-mcp link_commit HEAD` is not a registered CLI subcommand*
**Targets**: v0.18.x (Jin's call at release-PR time)
**Branch**: `feat/124-link-commit-cli` (off `BicameralAI/dev`, current tip `8f0253d` — post-#119 governance v0.17.2)
**Risk grade**: L2 — touches user-facing CLI surface and an installed shell hook script. Affects every Guided-mode user since the hook was added.
**Change class**: bug-fix (hotfix-shaped — restores advertised behavior).

---

## Open Questions

These decisions are flagged for audit; the plan proposes provisional answers.

### Q1. CLI output shape on success — JSON, plain text, or silent?

The post-commit hook itself pipes to `/dev/null 2>&1`, so it doesn't care. A human running `bicameral-mcp link_commit HEAD` directly probably wants something parseable.

**Recommend JSON to stdout by default**, plus a `--quiet` flag that suppresses output (still exits 0 on success). Mirrors `kubectl`/`gh` defaults — output to humans by default, `-q` for scripts.

The hook will not pass `--quiet` (the redirect already handles it); humans get JSON they can pipe through `jq`. Either path exits 0/1 the same way.

### Q2. Do existing Guided-mode installs need migration?

No. The hook script content (`bicameral-mcp link_commit HEAD`) is *correct in intent*; the bug is the missing argparse subcommand on the server side. Once the new subcommand ships, every existing hook starts working with no user action.

CHANGELOG note suffices: "Existing post-commit hooks installed by `bicameral-mcp setup` (Guided mode) will start syncing the ledger correctly after this release. No reinstall required."

### Q3. Fix silent-suppression in same PR, or split?

**Same PR.** Three reasons:

1. The smoke-test (Phase 2) needs both fixes to assert correctness — testing "the hook command exists" against a still-suppressed hook leaves the runtime regression class unverified.
2. Shipping the registration fix without the suppression fix means: every user's hook starts working *quietly*. If a future bug breaks `link_commit` again, we're back to silent failure — the suppression was load-bearing for the original bug going undetected for so long.
3. Both changes are tiny (~1 line each in the hook script). Splitting them would create two PRs with overlapping smoke-test logic.

The replacement script writes the failure to stderr but still `exit 0` so the commit doesn't block. Loud-by-default; silent only when explicitly silenced via `--quiet`.

### Q4. Should `branch-scan` be reused for the post-commit hook (since it already calls `_invoke_link_commit`)?

No. `branch-scan` semantically means "drift surfacing for pre-push" (#48) — it composes `link_commit` then renders drift to the terminal. The post-commit hook wants only the sync side-effect, not the rendering. Conflating them overloads the CLI surface and makes future divergence (e.g., adding `--with-summary` to one but not the other) harder.

**Recommend a separate `link_commit` subcommand** that's just the sync. `branch-scan`'s existing `_invoke_link_commit` helper can be **promoted to a shared helper** at `cli/_link_commit_runner.py` (or kept module-private and duplicated — see Phase 1 design choice).

### Q5. Where does the shared async-runner helper live?

Two options:

- **A. Shared module** `cli/_link_commit_runner.py` (~30 LOC) — both `cli/branch_scan.py` and the new `link_commit` subcommand import from it. DRY, single source of truth.
- **B. Duplicate the runner** in each call site (~20 LOC each). Avoids cross-module coupling at the cost of two near-identical functions.

**Recommend A.** Two callers today, more later if/when other subcommands need to drive `link_commit` from sync context (e.g., a future `bicameral-mcp sync` subcommand). Promotion-now is cheaper than refactor-later.

---

## Background (grounding — verified against `dev` HEAD `8f0253d`)

- `setup_wizard.py` exists; line 437–443 defines `_GIT_POST_COMMIT_HOOK` calling `bicameral-mcp link_commit HEAD` with `>/dev/null 2>&1 || true` suppression.
- `setup_wizard.py` line 446+ defines `_install_git_post_commit_hook` (the installer function); pattern mirrors `_install_git_pre_push_hook` from #48.
- `server.py` line 1357 defines `cli_main`. Existing subcommands: `config`, `reset`, `setup`, `branch-scan`. Dispatch branches at lines 1414, 1419, 1424, 1433. **No `link_commit` subcommand or dispatch branch.**
- `handlers/link_commit.py` line 444: `async def handle_link_commit(ctx, commit_hash="HEAD", *, preflight_id=None) -> LinkCommitResponse`. Real, importable, well-typed.
- `cli/branch_scan.py` lines 133–149: `_invoke_link_commit()` already wraps the async handler, lazy-imports both `BicameralContext` and the handler, returns `None` when `~/.bicameral/ledger.db` is absent. The exact pattern needed.
- `contracts.py` line 292: `LinkCommitResponse` is a Pydantic `BaseModel` with `model_dump()` (standard Pydantic v2 method) producing JSON-serializable dict.
- `pyproject.toml` line 56: `bicameral-mcp = "server:cli_main"` — entry point definition. Modifying `cli_main` automatically changes the installed CLI.
- `tests/test_branch_scan_cli.py` (#48, 144 LOC, 7 tests) is the pattern reference for CLI subcommand tests.
- `tests/test_setup_pre_push_hook.py` (#48, 92 LOC, 5 tests) is the pattern reference for hook-script installer tests.

**Anti-finding**: `qor/scripts/`, `qor/reliability/`, `pilot/mcp/skills/` all do not exist on dev (verified via `ls -d`). No plan reference will assume their presence.

---

## Phase 0: Promote `_invoke_link_commit` to shared helper

TDD-light: tests exist already (`test_branch_scan_cli.py` patches `cli.branch_scan._compute_drift`), so Phase 0 is a refactor-with-existing-coverage move.

### Affected files

- `cli/_link_commit_runner.py` — **new**, ~30 LOC. Houses the lazy-import, sync-wrapper-around-async-handler.
- `cli/branch_scan.py` — **modify**, –10 / +3 LOC. Replace local `_invoke_link_commit` with import from runner module.

### Public interface

```python
# cli/_link_commit_runner.py

def invoke_link_commit(commit_hash: str = "HEAD") -> LinkCommitResponse | None:
    """Synchronous wrapper that drives the async handle_link_commit.

    Returns None when:
      - ``~/.bicameral/ledger.db`` does not exist (no configured ledger), OR
      - the underlying handler raises (graceful skip — caller decides on
        loud vs. silent failure).

    Lazy-imports BicameralContext and handle_link_commit so the function
    can be patched in tests without paying the SurrealDB import cost.
    """
```

### Changes (concrete)

`cli/_link_commit_runner.py` (new):

```python
"""Sync wrapper around handle_link_commit. Shared by branch-scan and
link_commit CLI subcommands. Lazy-imports SurrealDB-touching modules."""

from __future__ import annotations

import asyncio
from pathlib import Path

from contracts import LinkCommitResponse


def invoke_link_commit(commit_hash: str = "HEAD") -> LinkCommitResponse | None:
    if not (Path.home() / ".bicameral" / "ledger.db").exists():
        return None
    from context import BicameralContext
    from handlers.link_commit import handle_link_commit

    async def _run() -> LinkCommitResponse:
        ctx = BicameralContext.from_env()
        return await handle_link_commit(ctx, commit_hash=commit_hash)

    try:
        return asyncio.run(_run())
    except Exception:  # noqa: BLE001 — caller decides loud vs. silent
        return None
```

`cli/branch_scan.py` — replace lines 133–149 with:

```python
from cli._link_commit_runner import invoke_link_commit


def _compute_drift() -> LinkCommitResponse | None:
    return invoke_link_commit("HEAD")
```

### Razor

`invoke_link_commit` ≤ 25 LOC. New file ≤ 35 LOC (well under 250 cap).

### Why phased separately

Promoting before adding the second caller (Phase 1) keeps Phase 0 a pure refactor with no behavior change — existing `test_branch_scan_cli.py` proves correctness via its existing patches. Phase 1 then has a tested helper to lean on.

---

## Phase 1: Register `link_commit` CLI subcommand

TDD-light: tests written FIRST (RED), then implementation (GREEN).

### Affected files

- `tests/test_link_commit_cli.py` — **new**, ~80 LOC, 6 tests covering argparse, default arg, JSON output shape, `--quiet` flag, no-ledger graceful exit, exception graceful skip.
- `server.py` — **modify**, +28 LOC. Add subparser registration + dispatch branch in `cli_main`.

### Public interface

CLI surface:

```
bicameral-mcp link_commit [COMMIT_HASH] [--quiet]

  Sync the given commit (default: HEAD) into the bicameral ledger.

  Positional:
    COMMIT_HASH       commit hash to link (default: HEAD)

  Flags:
    --quiet           suppress JSON output to stdout (still exits 0 on success)

  Exit codes:
    0  — sync succeeded, OR ledger not configured (graceful skip)
    1  — handler raised (loud failure)
```

Internal dispatch in `cli_main` (mirrors `branch-scan` plumbing):

```python
# Subparser registration (after branch-scan block):
link_parser = subparsers.add_parser(
    "link_commit",
    help="hash-level sync — link the given commit (or HEAD) into the ledger",
)
link_parser.add_argument(
    "commit_hash",
    nargs="?",
    default="HEAD",
    help="commit hash to link (default: HEAD)",
)
link_parser.add_argument(
    "--quiet",
    action="store_true",
    help="suppress JSON output to stdout (still exits 0 on success)",
)

# Dispatch branch (after branch-scan dispatch):
if args.command == "link_commit":
    from cli.link_commit_cli import main as link_commit_main
    return link_commit_main(args.commit_hash, quiet=args.quiet)
```

`cli/link_commit_cli.py` — **new**, ~35 LOC:

```python
"""link_commit CLI subcommand entry point."""

from __future__ import annotations

import json
import sys

from cli._link_commit_runner import invoke_link_commit


def main(commit_hash: str = "HEAD", *, quiet: bool = False) -> int:
    response = invoke_link_commit(commit_hash)
    if response is None:
        # Graceful skip — no ledger configured. Hook expects exit 0
        # so the post-commit handshake doesn't appear to fail.
        return 0
    if not quiet:
        print(json.dumps(response.model_dump(), default=str, indent=2))
    return 0
```

### Test list (RED first)

- `tests/test_link_commit_cli.py`:
  - `test_default_commit_hash_is_HEAD` — argparse default; verify `main()` called with `"HEAD"` when no positional arg.
  - `test_explicit_commit_hash_passed_through` — `main("abc1234")` calls `invoke_link_commit("abc1234")` (mock).
  - `test_json_output_on_success` — mock `invoke_link_commit` to return a `LinkCommitResponse`; capture stdout; assert valid JSON with `commit_hash`, `synced`, `reason` keys.
  - `test_quiet_flag_suppresses_output` — same setup, but `quiet=True`; stdout is empty; exit code 0.
  - `test_no_ledger_returns_zero_silently` — mock `invoke_link_commit` to return `None`; stdout empty; exit code 0.
  - `test_handler_exception_returns_zero_silently` — mock `invoke_link_commit` to return `None` (graceful skip per runner contract); exit code 0.

### Razor

- `cli_main` `link_commit` subparser block: ~10 LOC.
- `cli_main` dispatch branch: 3 LOC.
- `cli/link_commit_cli.py:main()`: ~8 LOC.
- All ≤ 25 LOC; nesting ≤ 2; no nested ternaries.

---

## Phase 2: Harden post-commit hook + add command-registration smoke test

TDD-light: smoke test written FIRST.

### Affected files

- `tests/test_hook_command_registration.py` — **new**, ~50 LOC, 3 tests asserting every CLI command referenced in installed hook scripts is registered as a subparser in `cli_main`.
- `setup_wizard.py` — **modify**, ~3 LOC delta. Replace `>/dev/null 2>&1 || true` with stderr-loud variant.

### Smoke-test design

The test parses the hook script bodies (`_GIT_POST_COMMIT_HOOK`, `_GIT_PRE_PUSH_HOOK`) for `bicameral-mcp <subcommand>` invocations and asserts each subcommand appears in `cli_main`'s subparser registry. Caught at unit-test time, not in the field.

### Test list (RED first)

- `tests/test_hook_command_registration.py`:
  - `test_post_commit_hook_command_is_registered` — extract `bicameral-mcp link_commit HEAD` from `_GIT_POST_COMMIT_HOOK`; assert `link_commit` is a registered subcommand. **This test fails on `dev` today** — proves we're closing the original bug.
  - `test_pre_push_hook_command_is_registered` — extract `bicameral-mcp branch-scan` from `_GIT_PRE_PUSH_HOOK`; assert `branch-scan` is a registered subcommand. (Already true; locks the invariant.)
  - `test_all_hook_commands_have_dispatch_branches` — for each extracted command, assert there's a matching `if args.command == "<cmd>":` branch in the source of `cli_main`. Catches "registered but not dispatched" half-completes.

Helper: `_extract_bicameral_mcp_commands(hook_script: str) -> set[str]` — regex `r"bicameral-mcp\s+([a-z][a-z0-9_-]+)"`, returns set of unique subcommands.

### `setup_wizard.py` change

```python
# Line 442 BEFORE:
[ -d .bicameral ] && bicameral-mcp link_commit HEAD >/dev/null 2>&1 || true

# Line 442 AFTER:
[ -d .bicameral ] && bicameral-mcp link_commit HEAD >/dev/null 2>/tmp/bicameral-hook.err
[ -s /tmp/bicameral-hook.err ] && echo "bicameral-mcp post-commit hook failed; see /tmp/bicameral-hook.err" >&2
exit 0  # never block the commit
```

Stderr is captured to a temp file so the user sees a one-line summary on the next commit, but the commit itself never blocks. The temp file is overwritten each commit (no log accumulation).

**Alternative considered, rejected**: piping stderr directly to `>&2` from inside the `&&` chain. Rejected because shell semantics around redirecting to multiple destinations across `&&` boundaries vary subtly between dash, bash, zsh — capturing to a file then re-reading is portable.

### Razor

- Hook script: 4 lines (was 3). Still trivially auditable.
- `_extract_bicameral_mcp_commands` helper: ~8 LOC.
- Each test: ~12 LOC.

---

## Phase 3: Documentation

TDD-light: pure documentation; no tests.

### Affected files

- `CHANGELOG.md` — **modify**, ~10 LOC under `[Unreleased]` Fixed.

### `CHANGELOG.md` entry

```markdown
## [Unreleased]

### Fixed

- **Post-commit hook now actually syncs the ledger (#124).** The
  `bicameral-mcp setup` (Guided mode) post-commit hook called
  `bicameral-mcp link_commit HEAD`, which was never a registered CLI
  subcommand — every commit since the hook was introduced silently
  failed via `|| true`. This release adds the missing `link_commit`
  subcommand, replaces the silent-failure suppression with stderr-loud
  reporting (still exits 0 so the commit never blocks), and adds a
  smoke test that walks every command referenced in installed hook
  scripts to verify CLI registration. **Existing Guided-mode installs
  start working automatically; no reinstall required.**
```

---

## Test invocation

```bash
# Phase 0 + 1 + 2
python -m pytest -q tests/test_link_commit_cli.py tests/test_hook_command_registration.py tests/test_branch_scan_cli.py

# Manual smoke
bicameral-mcp link_commit              # JSON to stdout
bicameral-mcp link_commit --quiet       # silent, exit 0
bicameral-mcp link_commit nonexistent   # error to stderr, exit 1
bicameral-mcp --help                    # link_commit appears in subcommand list

# CI gates
ruff check cli/_link_commit_runner.py cli/link_commit_cli.py tests/test_link_commit_cli.py tests/test_hook_command_registration.py server.py setup_wizard.py
ruff format --check cli/_link_commit_runner.py cli/link_commit_cli.py tests/test_link_commit_cli.py tests/test_hook_command_registration.py
mypy cli/_link_commit_runner.py cli/link_commit_cli.py
```

---

## Section 4 razor pre-check

| File | Estimate | Razor cap | OK? |
|---|---|---|---|
| `cli/_link_commit_runner.py` | ~30 LOC | ≤ 250 | yes |
| `cli/link_commit_cli.py` | ~35 LOC | ≤ 250 | yes |
| `server.py` (delta only) | +28 LOC | ≤ 250 (file already much larger; razor on `cli_main` function specifically) | yes — `cli_main` was ~90 LOC before; +28 = ~118 LOC. **Splits into helpers if it crosses 40-LOC entry-function cap on the `cli_main` body itself.** Mid-implement check required. |
| `setup_wizard.py` (delta only) | +3 LOC | n/a (constant string) | yes |
| `tests/test_link_commit_cli.py` | ~80 LOC | ≤ 250 | yes |
| `tests/test_hook_command_registration.py` | ~50 LOC | ≤ 250 | yes |
| `cli/branch_scan.py` (delta only) | –10 / +3 LOC | already-small | yes (file shrinks) |

**Function-level**: every new function ≤ 25 LOC entry / ≤ 20 LOC helpers / nesting ≤ 2 / no nested ternaries.

**Mid-implement watchpoint**: `cli_main` is now an orchestrator function that's getting close to the 40-LOC entry-function cap. If adding the `link_commit` subparser pushes it over, **split it**: factor each subparser into a `_register_<cmd>_parser(subparsers)` helper + a `_dispatch(args)` function. Refactor pre-emptively if the integrated count exceeds 35 LOC.

---

## Exit criteria

1. **Phase 0 GREEN**: `tests/test_branch_scan_cli.py` passes against the new shared helper without modification (refactor preserved behavior).
2. **Phase 1 GREEN**: 6/6 link_commit_cli tests pass; `bicameral-mcp link_commit --help` shows the new subcommand; manual `bicameral-mcp link_commit HEAD` against a configured ledger returns valid JSON.
3. **Phase 2 GREEN**: 3/3 hook-command-registration tests pass; `test_post_commit_hook_command_is_registered` was RED before Phase 1 and is now GREEN.
4. **Phase 3 documented**: `[Unreleased]` Fixed entry committed.
5. **Self-test**: install the post-commit hook locally via `bicameral-mcp setup` (Guided mode), make a no-op commit, observe `link_commit` running (no stderr noise on success path; loud on failure path).

---

## What this plan is NOT

- Not a refactor of the post-commit hook installer pattern (`_install_git_post_commit_hook` is unchanged).
- Not an MCP-tool layer change (the `link_commit` MCP tool already exists and works; this is purely a CLI surface addition).
- Not a migration system — existing installs need no user action.
- Not a hook-uninstall mechanism (out of scope; tracked separately if needed).
- Not adding `link_commit` to `bicameral-mcp setup`'s default install path — that flow already installs the hook script that calls it; no new install branch needed.
