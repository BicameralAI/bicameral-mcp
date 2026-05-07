"""Issue #124 Phase 2 — hook command registration smoke tests.

Walks every ``bicameral-mcp <subcommand>`` invocation in installed
hook scripts and asserts each subcommand is registered as a subparser
in ``server.cli_main``. Catches the original #124 bug at PR time:
the post-commit hook called ``link_commit`` for months without
``link_commit`` ever being a registered subcommand.

These tests assume Phase 0a's ``_register_subparsers`` is the source
of truth for registered commands — it builds the parser without
running the dispatch.
"""

from __future__ import annotations

import re
from argparse import ArgumentParser

from server import _register_subparsers
from setup_wizard import _GIT_POST_COMMIT_HOOK, _GIT_PRE_PUSH_HOOK

# Match `bicameral-mcp <subcommand>` where the subcommand is a
# lower-snake-or-dash identifier. Anchors on the literal command
# token to avoid matching e.g. comments that mention bicameral-mcp.
_CMD_RE = re.compile(r"\bbicameral-mcp\s+([a-z][a-z0-9_-]+)\b")


def _extract_bicameral_mcp_commands(hook_script: str) -> set[str]:
    """Return the set of unique subcommand tokens invoked in the script."""
    return set(_CMD_RE.findall(hook_script))


def _registered_subcommands() -> set[str]:
    """Build a fresh parser via _register_subparsers and return the
    set of registered subparser names."""
    parser = ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    _register_subparsers(parser, subparsers)
    return set(subparsers.choices.keys())


def test_post_commit_hook_command_is_registered() -> None:
    """The post-commit hook calls ``link_commit``; that subcommand
    must be a registered subparser. THIS TEST WAS RED ON DEV
    BEFORE #124 — the regression that the original bug report named."""
    invoked = _extract_bicameral_mcp_commands(_GIT_POST_COMMIT_HOOK)
    registered = _registered_subcommands()
    missing = invoked - registered
    assert not missing, (
        f"Post-commit hook invokes {invoked} but only {registered} are "
        f"registered. Missing: {missing}"
    )


def test_pre_push_hook_command_is_registered() -> None:
    """The pre-push hook calls ``branch-scan``; that subcommand must
    be registered. Locks the invariant established by #48."""
    invoked = _extract_bicameral_mcp_commands(_GIT_PRE_PUSH_HOOK)
    registered = _registered_subcommands()
    missing = invoked - registered
    assert not missing, (
        f"Pre-push hook invokes {invoked} but only {registered} are registered. Missing: {missing}"
    )


def test_all_hook_commands_have_dispatch_branches() -> None:
    """Every command referenced in any installed hook script must
    appear in server._dispatch as an ``args.command == "..."``
    branch — registered-but-not-dispatched would still pass the
    register tests above but would silently no-op at runtime."""
    import inspect

    from server import _dispatch

    dispatch_src = inspect.getsource(_dispatch)
    invoked = _extract_bicameral_mcp_commands(_GIT_POST_COMMIT_HOOK + "\n" + _GIT_PRE_PUSH_HOOK)
    missing = {cmd for cmd in invoked if f'args.command == "{cmd}"' not in dispatch_src}
    assert not missing, (
        f"Hook scripts invoke {invoked} but _dispatch has branches for "
        f"only {invoked - missing}. Missing: {missing}"
    )
