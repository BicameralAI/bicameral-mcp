# Pre-push drift hook — User Guide

Issue [#48](https://github.com/BicameralAI/bicameral-mcp/issues/48). Surfaces
bicameral drift warnings in the terminal **before** `git push` completes —
when you can still amend the commit or annotate the decision.

## What it does

When you `git push`, the hook:

1. Runs `bicameral-mcp branch-scan` against `HEAD`.
2. If any bound decisions show drift, prints a compact warning block:
   ```
   [!] bicameral: 2 decisions drifted in this push
     • dec_auth_expiry — src/auth/session.py:checkExpiry@40-55
     • dec_rate_window — src/middleware/rate.py:applyLimit@12-28
   ```
3. Prompts `Push anyway? [y/N]` when running in an interactive terminal.
4. Default `N` aborts the push; `y` (or `yes`) lets it proceed.

When there's no drift, the hook is silent.

## When you'd use it

Install this if you push directly from the terminal without going through
Claude Code (or another MCP-aware agent) first. The post-commit hook already
syncs the ledger after each commit; the pre-push hook gives you one more
chance to see drift *before* it ships to your remote.

If you only push via your agent (which already runs `bicameral-mcp preflight`
or similar), this hook is optional.

## Quickstart

```bash
# In your repo:
bicameral-mcp setup --with-push-hook
```

That's it. The wizard will walk you through normal setup, and additionally
write `.git/hooks/pre-push` (or append to an existing one).

To verify:

```bash
ls -l .git/hooks/pre-push
# -rwxr-xr-x ... .git/hooks/pre-push

cat .git/hooks/pre-push | head -3
# #!/bin/sh
# # Bicameral MCP — pre-push hook (installed by bicameral-mcp setup --with-push-hook, #48)
# # Surfaces drift warnings before git push completes.
```

## Reference

### Exit codes

`bicameral-mcp branch-scan` exits with:

| Code | Meaning |
|---|---|
| `0` | No drift detected, OR skipped (no ledger configured) |
| `1` | Drift detected AND user (TTY) declined the prompt — set by the hook script, not by `branch-scan` itself |
| `2` | Drift detected AND `BICAMERAL_PUSH_HOOK_BLOCK=1` — hard-block, no prompt shown |

The hook script translates these into git's pre-push protocol: `0` allows
the push, anything else blocks it.

### Environment variables

- **`BICAMERAL_PUSH_HOOK_BLOCK`** — set to `1` to force the hook to block
  on any drift, without prompting. Useful when you want hard-fail behavior
  in personal scripts. Default unset (= prompt-or-warn behavior).

### Non-TTY behavior

When `git push` runs in a non-interactive context (CI, scripts, `git push
2>&1 | cat`), the hook detects no TTY and **never blocks** — it warns to
stderr only and exits `0`. This avoids breaking automation pipelines.

If you want CI to treat drift as an error, set `BICAMERAL_PUSH_HOOK_BLOCK=1`
in the CI environment.

### Removing the hook

```bash
# Easy: just delete it (will be reinstalled if you re-run setup --with-push-hook)
rm .git/hooks/pre-push

# Surgical: edit the file and remove the bicameral block
```

If the file contained other content before bicameral was appended, the
installer doesn't overwrite it — only the bicameral lines are added. To
remove just bicameral's contribution, delete from `# Bicameral MCP — pre-push
hook` through the next blank line.

### Idempotency

Re-running `bicameral-mcp setup --with-push-hook` is safe. If the hook
already contains the bicameral block, the installer logs `pre-push hook
already present — skipped` and changes nothing.

## Common pitfalls

1. **Windows users**: the hook is POSIX shell. It works under Git Bash and
   WSL; native CMD/PowerShell git installations may not execute it.
2. **`bicameral-mcp` not on `PATH`**: if the hook fires but the binary
   can't be found, it logs an error to stderr and exits non-zero — git
   reports the hook failed. Solution: `pip install -e .` (from the
   bicameral-mcp source) or `pipx install bicameral-mcp` to put the
   binary on `PATH`.
3. **No `.bicameral/` directory in the repo**: the hook short-circuits on
   the first line (`[ -d .bicameral ] || exit 0`). If you want drift checks
   in this repo, run `bicameral-mcp setup` first to create the ledger.
4. **Skipping the hook for a one-off push**: use `git push --no-verify`.
   Use sparingly — that's exactly what the hook is trying to surface.

## See also

- [`docs/DEV_CYCLE.md`](../DEV_CYCLE.md) — the project's dev workflow.
- Post-commit hook (existing, installed by Guided mode): syncs the ledger
  after every commit. Pairs naturally with the pre-push hook — commits get
  classified at commit time; drift is visible at push time.
- [`cli/branch_scan.py`](../../cli/branch_scan.py) — the source for what
  the hook calls.
