# Plan: Pre-push git hook for drift warnings (Issue #48)

**Tracks**: BicameralAI/bicameral-mcp#48 — *Pre-push git hook: surface drift warnings before `git push`*
**Targets**: v0.17.x (Jin's call at release-PR time)
**Branch**: `feat/48-pre-push-drift-hook` (off `BicameralAI/dev`, current tip `77b9ee3` — post-#113 sticky drift report and Dependabot retargets in flight)
**Risk grade**: L2 — adds new CLI subcommand to `bicameral-mcp` console-script surface; modifies `setup_wizard.py` install path; consumes existing `handle_link_commit` handler unchanged. No schema migrations, no MCP tool changes, no contract changes.
**Change class**: minor (additive CLI subcommand + setup-wizard install option + opt-in git hook).

---

## Open Questions

These are decisions worth flagging for audit; the plan proposes provisional answers.

### Q1. Where does the drift-summary CLI live?

The issue body references `bicameral-mcp branch-scan <base>..<head>`. The console-script registry in `pyproject.toml` declares `bicameral-mcp = "server:cli_main"`, with existing subcommands `config`, `reset`, `setup`. Adding `branch-scan` to `cli_main` follows the established pattern.

The CLI's logic should live in a module under `cli/` (sibling of `cli/classify.py` and `cli/drift_report.py` from #107 / #113). New module: `cli/branch_scan.py`.

**Recommend**: server.py's `cli_main` adds a `branch-scan` subparser that delegates to `cli.branch_scan:main`. No business logic in server.py — only the dispatcher entry. Pattern matches the existing `setup` → `setup_wizard.run_setup` delegation.

### Q2. Is the existing post-commit hook a working precedent?

`setup_wizard.py:439` defines `_GIT_POST_COMMIT_HOOK` as `bicameral-mcp link_commit HEAD >/dev/null 2>&1 || true`. But `cli_main` in server.py doesn't register a `link_commit` subcommand — only `config`, `reset`, `setup`. **The post-commit hook may be silently no-op'ing right now** (the `|| true` swallows the argparse error).

This is a separate bug, not in scope for #48. Flag in audit; file a follow-up issue. For #48's purposes: build `branch-scan` correctly via the subcommand pattern; do **not** model the hook command line on a possibly-broken predecessor.

### Q3. What's the hook's invocation semantics?

Git's pre-push hook receives stdin lines of `local_ref local_sha remote_ref remote_sha` per ref being pushed. The hook can extract:
- `head_sha` = `local_sha` (what's about to be pushed)
- `base_sha` = `remote_sha` (what the remote currently has, or `0000…` if the branch is new)

For simplicity v1, the hook ignores stdin specifics and runs `bicameral-mcp branch-scan` against `HEAD` only — surfaces drift in the *current* commit, not the full push range. Multi-commit push ranges are a v2 enhancement.

**Recommend**: v1 = HEAD-only scan. v2 (separate issue) = full push-range walk.

### Q4. How does the hook handle missing ledger / non-TTY?

- **No `~/.bicameral/ledger.db`**: `bicameral-mcp branch-scan` exits 0 with a one-line stderr advisory ("no bicameral ledger configured; pre-push drift check skipped"). Hook proceeds silently.
- **Non-TTY (CI, scripts, `git push --no-verify`)**: `bicameral-mcp branch-scan` prints output to stdout; hook script detects `[ ! -t 0 ]` and exits 0 even on drift detected. The drift signal goes to stderr in case a CI log-scanner wants it; the push proceeds.
- **TTY + drift detected + `BICAMERAL_PUSH_HOOK_BLOCK=0`**: warn, do not prompt, exit 0.
- **TTY + drift detected + default**: prompt `Push anyway? [y/N]`. Default `N` → exit 1, blocks push.

### Q5. setup_wizard's existing pattern

`_install_git_post_commit_hook(repo_path) -> bool` at `setup_wizard.py:446`:
- Idempotent: returns `False` if hook already contains `bicameral`; else writes/appends.
- Sets executable bit `0o755` after write.

The new `_install_git_pre_push_hook(repo_path) -> bool` mirrors this exactly. Same return semantics, same file-permission, same idempotence rule.

The `--with-push-hook` flag wires through `cli_main`'s `setup_parser` → `run_setup(repo_path, history_path, with_push_hook=False)` → conditional install.

---

## Background (grounding — verified against `dev` HEAD `77b9ee3`)

- Top-level packages: `adapters/`, `assets/`, `classify/`, `cli/`, `code_locator/`, `codegenome/`, `dashboard/`, `docs/`, `events/`, `handlers/`, `ledger/`, `scripts/`, `skills/`, `tests/`, `thoughts/`. (Avoids SG-PLAN-GROUNDING-DRIFT instance #4 — `cli/` is real.)
- `setup_wizard.py` exists at repo root.
- `server.py:1277` — `cli_main(argv)` with subparsers `config`/`reset`/`setup`.
- `pyproject.toml` `[project.scripts]`:
  - `bicameral-mcp = "server:cli_main"`
  - `bicameral-mcp-classify = "cli.classify:main"` (PR #107 precedent)
- `setup_wizard.py:439-471` — `_GIT_POST_COMMIT_HOOK` constant + `_install_git_post_commit_hook(repo_path) -> bool` idempotent installer.
- `handlers/link_commit.py:444` — `async def handle_link_commit(ctx, commit_hash, ...)` is the underlying drift primitive. Returns `LinkCommitResponse` carrying `pending_compliance_checks` (drifted/uncertain), `auto_resolved_count` (cosmetic), `continuity_resolutions` (Phase 3).
- No `bicameral-mcp branch-scan` subcommand exists. No `cli/branch_scan.py` module exists. No `_install_git_pre_push_hook` function exists.
- `cli/drift_report.py` (just landed in #113) renders Markdown for PR sticky comments — not the right surface for a terminal hook (different output format, different exit-code semantics).

---

## Phase 0: `branch-scan` CLI subcommand

TDD-light: tests written FIRST, confirm red, then implement, confirm green.

### Affected files

- `tests/test_branch_scan_cli.py` — **new**, ~110 LOC, 7 tests covering CLI shape, exit codes, env-var override, and renderer output.
- `cli/branch_scan.py` — **new**, ~140 LOC. Pure-function terminal-output renderer + `main()` CLI entry that calls `handle_link_commit` against HEAD and prints summary.
- `server.py` — **modify**, +~12 LOC. Add `branch-scan` subparser to `cli_main`; dispatch to `cli.branch_scan:main`.

### Public interface

```python
# cli/branch_scan.py

def render_terminal_summary(
    response: LinkCommitResponse | None,
) -> str:
    """Pure function. Returns terminal-friendly summary text.

    None ⇒ "no bicameral ledger configured" advisory.
    Zero drifted/uncertain ⇒ empty string (caller skips printing).
    Drift detected ⇒ multiline summary with header + bullet list.
    """


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Returns:
       0 — no drift, or skip (no ledger)
       1 — drift detected AND user declined the prompt
       2 — drift detected AND BICAMERAL_PUSH_HOOK_BLOCK=1 (non-interactive block)

    Non-TTY stdin ⇒ never blocks (warns to stderr; returns 0).
    """
```

### Output contract

When drift detected (printed to stderr so the hook can show it before prompting on stdin):

```
⚠ bicameral: 2 decisions drifted in this push
  • Auth token expiry — src/auth/session.ts:checkExpiry:40-55
  • Rate limit window — src/middleware/rate.ts:applyLimit:12-28
```

When no drift, no output.

When no ledger:

```
bicameral: no ledger configured at ~/.bicameral/ledger.db; pre-push drift check skipped
```

### Unit tests (Phase 0)

- `tests/test_branch_scan_cli.py`:
  - `test_renderer_empty_when_no_drift` — `LinkCommitResponse` with empty `pending_compliance_checks` and zero `auto_resolved_count` → empty string.
  - `test_renderer_skip_message_when_response_none` — `None` → contains "no ledger" + "skipped".
  - `test_renderer_drift_summary_groups_by_decision` — 2 drifted entries → output has `⚠ bicameral`, `2 decisions`, both decision IDs as bullets.
  - `test_renderer_uncertain_treated_as_drifted` — pending check with `pre_classification.verdict == "uncertain"` is included in the drift count (the hook surfaces ambiguity, doesn't filter it).
  - `test_main_exit_zero_when_no_drift` — invokes `main([])` with mocked `handle_link_commit` returning empty pending → returncode 0.
  - `test_main_exit_two_when_block_env_set` — `BICAMERAL_PUSH_HOOK_BLOCK=1` + drift detected → returncode 2 (non-interactive block).
  - `test_main_exit_zero_when_non_tty_and_drift` — when `sys.stdin.isatty() is False` and drift detected → returncode 0 (non-blocking; warn-only).

### Function-level razor

- `render_terminal_summary` ≤ 25 LOC.
- `main()` ≤ 35 LOC (orchestrator: load context → call handler → render → decide exit code → return).
- Helpers: `_render_drift_bullets(checks)` ≤ 20 LOC, `_should_block(args, isatty)` ≤ 15 LOC, `_resolve_exit_code(drift_count, isatty)` ≤ 15 LOC.

### server.py wiring

```python
# In cli_main, after the existing 'setup' subparser block:
subparsers.add_parser(
    "branch-scan",
    help="surface bicameral drift for HEAD; used by the pre-push git hook",
)
# ...
if args.command == "branch-scan":
    from cli.branch_scan import main as branch_scan_main
    return branch_scan_main(argv[1:] if argv else [])
```

---

## Phase 1: `setup_wizard.py` pre-push hook install

TDD-light: install-step tests written first, confirm red, then implement, confirm green.

### Affected files

- `tests/test_setup_pre_push_hook.py` — **new**, ~80 LOC, 5 tests covering install/idempotent/permissions/no-git-root path.
- `setup_wizard.py` — **modify**, +~30 LOC. New `_GIT_PRE_PUSH_HOOK` constant + `_install_git_pre_push_hook(repo_path)` function modeled after `_install_git_post_commit_hook`.
- `setup_wizard.py:run_setup(...)` — extend signature with `with_push_hook: bool = False` parameter; conditionally call install function.
- `server.py:cli_main` — add `--with-push-hook` flag to `setup_parser`; thread through to `run_setup(...)` call.

### Hook script template

```bash
#!/bin/sh
# Bicameral MCP — pre-push hook (installed by bicameral-mcp setup --with-push-hook)
# Surfaces drift warnings before git push completes.
# Silent on missing ledger; non-blocking unless BICAMERAL_PUSH_HOOK_BLOCK=1 or TTY-attached interactive decline.

[ -d .bicameral ] || exit 0   # no bicameral configured here, do nothing

# Run the scan; exit codes:
#   0 — no drift, or skipped (no ledger)
#   1 — drift detected AND user declined (TTY-attached) the prompt
#   2 — drift detected AND BICAMERAL_PUSH_HOOK_BLOCK=1 (non-interactive block)
#
# Stderr from branch-scan is the warning text; we let it pass through to
# the user's terminal so they see it before any prompt.
bicameral-mcp branch-scan
status=$?

if [ "$status" = "0" ]; then
    exit 0
fi

# Non-zero from branch-scan — drift was detected. If we're attached to a
# TTY, prompt; otherwise honor whatever exit code branch-scan returned.
if [ -t 0 ]; then
    printf "Push anyway? [y/N] " >&2
    read -r answer </dev/tty
    case "$answer" in
        [yY]|[yY][eE][sS]) exit 0 ;;
        *) exit 1 ;;
    esac
fi

exit "$status"
```

### Changes to setup_wizard.py

```python
_GIT_PRE_PUSH_HOOK = """\
#!/bin/sh
# Bicameral MCP — pre-push hook (installed by bicameral-mcp setup --with-push-hook)
# (full content per template above)
"""


def _install_git_pre_push_hook(repo_path: Path) -> bool:
    """Install a git pre-push hook that calls bicameral-mcp branch-scan.

    Idempotent — if a hook already exists and already contains a bicameral
    call, leaves it untouched. If an existing hook lacks a bicameral call,
    appends one rather than overwriting.

    Returns True if anything was written.
    """
    git_root = _find_git_root(repo_path)
    if git_root is None:
        return False
    hook_path = git_root / ".git" / "hooks" / "pre-push"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    if hook_path.exists():
        existing = hook_path.read_text()
        if "bicameral" in existing:
            return False
        hook_path.write_text(existing.rstrip("\n") + "\n" + _GIT_PRE_PUSH_HOOK)
    else:
        hook_path.write_text(_GIT_PRE_PUSH_HOOK)
    hook_path.chmod(0o755)
    return True
```

### Changes to server.py

```python
# In setup_parser block, after --history-path:
setup_parser.add_argument(
    "--with-push-hook",
    action="store_true",
    help="also install a git pre-push hook that surfaces drift before push",
)
# ...
if args.command == "setup":
    return run_setup(args.repo_path, args.history_path, with_push_hook=args.with_push_hook)
```

### Unit tests (Phase 1)

- `tests/test_setup_pre_push_hook.py`:
  - `test_install_writes_hook_in_fresh_repo` — empty `.git/hooks/`, install → file exists at `.git/hooks/pre-push`, contains `bicameral-mcp branch-scan`, executable.
  - `test_install_is_idempotent_when_already_bicameral` — install once, install twice → second call returns False (no change).
  - `test_install_appends_when_existing_hook_lacks_bicameral` — write a stub `pre-push` that doesn't mention bicameral, install → file now contains both stub content and bicameral call.
  - `test_install_returns_false_when_no_git_root` — invoke with a path that's not in a git repo → returns False, writes nothing.
  - `test_install_sets_executable_bit` — install → mode is `0o755` (POSIX). Use `pytest.skipif(sys.platform == "win32")` for the chmod check.

### Function-level razor

- `_install_git_pre_push_hook` ≤ 25 LOC (matches existing `_install_git_post_commit_hook` line count).
- New `--with-push-hook` flag adds ~5 LOC to `cli_main`; ~3 LOC threading through `run_setup`.

---

## Phase 2: CHANGELOG entry + user guide

TDD-light: this phase has no tests — it's pure documentation.

### Affected files

- `CHANGELOG.md` — **modify**, `[Unreleased]` entry under Added.
- `docs/guides/pre-push-drift-hook.md` — **new**, ~80 LOC. User guide per `DEV_CYCLE.md` §8 docs matrix (user-facing CLI surface change → guide required).

### CHANGELOG entry

```markdown
## [Unreleased]

### Added

- **`bicameral-mcp branch-scan` CLI + opt-in pre-push git hook (#48).** New
  console subcommand prints a terminal summary of drifted decisions for
  HEAD; calls `link_commit` under the hood. Installed as a git pre-push
  hook via `bicameral-mcp setup --with-push-hook`. Surfaces drift warnings
  in the terminal before `git push` completes, with a `Push anyway? [y/N]`
  prompt when attached to a TTY. Non-blocking by default; `BICAMERAL_PUSH_HOOK_BLOCK=1`
  forces hard-block on drift. Idempotent install. Issue #48.
```

### User guide

`docs/guides/pre-push-drift-hook.md`:

- **What it does**: drift warnings before push.
- **When you'd use it**: developers who push directly from terminal without going through Claude Code first.
- **Quickstart**:
  ```
  bicameral-mcp setup --with-push-hook
  # ...edit code, commit, push
  git push
  # ⚠ bicameral: 1 decision drifted in this push
  #   • Auth token expiry — src/auth/session.ts:checkExpiry:40-55
  # Push anyway? [y/N] _
  ```
- **Reference**: env-var overrides, exit codes, removal instructions (`rm .git/hooks/pre-push` or edit out the bicameral lines).
- **See also**: post-commit hook (existing); `DEV_CYCLE.md` §10 hotfix path.

---

## Test invocation (matches CI workflow)

```bash
# Phase 0 + 1 sweep
SURREAL_URL=memory:// python -m pytest -q \
    tests/test_branch_scan_cli.py \
    tests/test_setup_pre_push_hook.py

# Lint + format (CI Phase 1 gate from PR #102)
ruff check cli/branch_scan.py setup_wizard.py server.py tests/test_branch_scan_cli.py tests/test_setup_pre_push_hook.py
ruff format --check cli/branch_scan.py setup_wizard.py server.py tests/test_branch_scan_cli.py tests/test_setup_pre_push_hook.py
mypy cli/branch_scan.py setup_wizard.py server.py
```

---

## Section 4 razor pre-check

| File | Estimate | Razor cap | OK? |
|---|---|---|---|
| `cli/branch_scan.py` | ~140 LOC | ≤250 | yes |
| `setup_wizard.py` | growth ~30 LOC; current size already > 250 | exempt (legacy oversize file, B1 backlog) | n/a |
| `server.py` | growth ~12 LOC; current size already > 250 | exempt (legacy oversize file) | n/a |
| `tests/test_branch_scan_cli.py` | ~110 LOC | ≤250 | yes |
| `tests/test_setup_pre_push_hook.py` | ~80 LOC | ≤250 | yes |

Function-level razor: every new function ≤ 35 LOC entry / ≤ 25 LOC helpers / nesting ≤ 3 / no nested ternaries. All within caps.

`setup_wizard.py` and `server.py` are pre-existing oversize files (tracked in BACKLOG `[B1]` for future split). Adding ~30 + ~12 LOC to them does not worsen the situation enough to require remediation now; remediation belongs to the dedicated split workstream.

---

## Exit criteria

1. **Phase 0 GREEN**: 7/7 branch-scan CLI tests pass; `ruff` + `format --check` + `mypy` clean on the new module.
2. **Phase 1 GREEN**: 5/5 install tests pass; idempotent re-install verified.
3. **End-to-end smoke (manual operator pass at substantiation)**: in a real repo with a populated ledger and a known drifted decision, install via `bicameral-mcp setup --with-push-hook`, `git push`, observe the warning + prompt, decline → push aborts; accept → push proceeds. Non-TTY (e.g. `git push 2>&1 | cat`) does not block.
4. **No regression on `bicameral-mcp setup` without `--with-push-hook`**: existing setup paths unchanged.
5. **Skill-rule compliance** (`CLAUDE.md`): no MCP tool changes — this PR adds a CLI subcommand and a setup-wizard option, not a tool. No `skills/*/SKILL.md` updates required.

---

## What this plan is NOT

- Not a new MCP tool — pure CLI + setup-wizard surface.
- Not a fix for the existing post-commit hook's possibly-broken `bicameral-mcp link_commit HEAD` invocation. That's a separate finding worth a follow-up issue (audit may want to file it).
- Not a multi-commit-range push scanner — v1 scans HEAD only. Multi-commit walk is a v2 enhancement.
- Not a Windows-specific implementation — the hook is POSIX shell; Windows users who want this need WSL or Git Bash. Documented in the user guide.
