"""Issue #48 — branch-scan CLI: terminal-output drift summary for the
pre-push git hook.

Wraps ``handlers.link_commit`` in a CLI surface that prints a
human-readable warning block and exits with a code the pre-push hook
can act on:

  0 — no drift detected, or skipped (no ledger configured)
  1 — drift detected AND user (TTY) declined the prompt
  2 — drift detected AND ``BICAMERAL_PUSH_HOOK_BLOCK=1`` (hard-block)

Stderr carries the warning text so the user sees it before any
prompt; stdout is reserved for status messages the hook may want
to capture or filter.

Design rule: this module imports only from ``contracts`` and (via
the ``_compute_drift`` indirection) ``handlers.link_commit``. No
imports of GitHub API clients, no Markdown rendering. Pure terminal
output.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts import LinkCommitResponse, PendingComplianceCheck

_HEADER_PREFIX = "[!] bicameral:"
_BLOCK_ENV = "BICAMERAL_PUSH_HOOK_BLOCK"

# Exit codes used by the pre-push hook
_EXIT_OK = 0
_EXIT_USER_DECLINED = 1  # set by the hook, not by main(); main returns _EXIT_BLOCK
_EXIT_BLOCK = 2


# ── Public entry (≤ 25 lines) ────────────────────────────────────────


def render_terminal_summary(
    response: LinkCommitResponse | None,
) -> str:
    """Render a terminal-friendly summary of drift state.

    ``None`` ⇒ skip advisory (no ledger configured).
    Empty pending + zero auto_resolved ⇒ empty string (caller skips).
    Otherwise ⇒ multiline header + bulleted list of drifted decisions.
    """
    if response is None:
        return _render_skip_message()
    pending = response.pending_compliance_checks
    if not pending:
        return ""
    return _render_drift_block(pending)


# ── Helper renderers (each ≤ 20 lines) ───────────────────────────────


def _render_skip_message() -> str:
    """Body when no ledger is configured. ASCII only — no emojis —
    so Windows terminals (cp1252) don't blow up on print()."""
    return (
        "bicameral: no ledger configured at ~/.bicameral/ledger.db; pre-push drift check skipped\n"
    )


def _render_drift_block(
    pending: list[PendingComplianceCheck],
) -> str:
    """Body for the has-drift case. Header line + one bullet per
    decision with file:symbol locator."""
    n = len(pending)
    noun = "decision" if n == 1 else "decisions"
    lines = [f"{_HEADER_PREFIX} {n} {noun} drifted in this push"]
    for check in pending:
        lines.append(_render_bullet(check))
    return "\n".join(lines) + "\n"


def _render_bullet(check: PendingComplianceCheck) -> str:
    """Single bullet line: '  • <decision_id> — <file>:<symbol>'.
    Decision description is omitted (often verbose); the locator is
    what the user needs to navigate to the code."""
    return f"  • {check.decision_id} — {check.file_path}:{check.symbol}"


# ── CLI entry point (≤ 35 lines) ─────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Invoked by the pre-push hook as
    ``bicameral-mcp branch-scan`` (which dispatches via
    ``server:cli_main``) or directly via ``python -m cli.branch_scan``.

    Returns the exit code described in the module docstring.
    """
    response = _compute_drift()
    summary = render_terminal_summary(response)
    if summary:
        print(summary, file=sys.stderr, end="")
    if response is None or not response.pending_compliance_checks:
        return _EXIT_OK
    return _resolve_exit_code()


# ── Orchestration helpers (each ≤ 20 lines) ──────────────────────────


def _compute_drift() -> LinkCommitResponse | None:
    """Run ``handle_link_commit`` against HEAD and return its response.

    Delegates to ``cli._link_commit_runner.invoke_link_commit`` (the
    shared sync-wrapper) which already handles the no-ledger graceful
    skip and the handler-exception graceful skip. Both behaviors match
    the hook's non-blocking design.
    """
    from cli._link_commit_runner import invoke_link_commit

    return invoke_link_commit("HEAD")


def _resolve_exit_code() -> int:
    """Decide exit code when drift IS present. Three branches:

      - BICAMERAL_PUSH_HOOK_BLOCK=1 → 2 (hard-block, no prompt)
      - non-TTY → 0 (advisory only; never block automation)
      - TTY → 0 (let the hook script handle the prompt itself)

    The hook script's prompt logic owns ``_EXIT_USER_DECLINED=1``;
    main() never returns 1 directly. main()'s job is just: 0 = clean/safe
    to push, 2 = blocked.
    """
    if os.environ.get(_BLOCK_ENV, "") == "1":
        return _EXIT_BLOCK
    if not _stdin_is_tty():
        return _EXIT_OK
    return _EXIT_OK


def _stdin_is_tty() -> bool:
    """Indirection for testability — patchable from unit tests so
    they don't have to mock ``sys.stdin.isatty`` directly."""
    return sys.stdin.isatty()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
