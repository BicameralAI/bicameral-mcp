"""Issue #124 Phase 1 — link_commit CLI subcommand contract tests.

Tests the CLI surface of ``cli.link_commit_cli.main`` in isolation:
mocks the shared runner so no SurrealDB / no real git activity is
required. Six tests cover argparse defaults, output shape, --quiet
flag, and the two graceful-skip paths (no ledger, handler exception).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from contracts import LinkCommitResponse


def _fake_response(commit_hash: str = "abc123") -> LinkCommitResponse:
    """Minimal valid LinkCommitResponse for output-shape tests."""
    return LinkCommitResponse(
        commit_hash=commit_hash,
        synced=True,
        reason="new_commit",
    )


def test_default_commit_hash_is_HEAD() -> None:
    """``main()`` with no positional arg passes ``HEAD`` to the runner."""
    from cli import link_commit_cli

    with patch.object(link_commit_cli, "invoke_link_commit") as mock:
        mock.return_value = None
        link_commit_cli.main()
    mock.assert_called_once_with("HEAD")


def test_explicit_commit_hash_passed_through() -> None:
    """``main("abc1234")`` passes the explicit hash to the runner."""
    from cli import link_commit_cli

    with patch.object(link_commit_cli, "invoke_link_commit") as mock:
        mock.return_value = None
        link_commit_cli.main("abc1234")
    mock.assert_called_once_with("abc1234")


def test_json_output_on_success(capsys) -> None:
    """A successful sync prints valid JSON with the response shape."""
    from cli import link_commit_cli

    with patch.object(link_commit_cli, "invoke_link_commit") as mock:
        mock.return_value = _fake_response("deadbeef")
        rc = link_commit_cli.main("deadbeef")
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["commit_hash"] == "deadbeef"
    assert payload["synced"] is True
    assert payload["reason"] == "new_commit"


def test_quiet_flag_suppresses_output(capsys) -> None:
    """``--quiet`` (quiet=True) emits no stdout but still exits 0."""
    from cli import link_commit_cli

    with patch.object(link_commit_cli, "invoke_link_commit") as mock:
        mock.return_value = _fake_response()
        rc = link_commit_cli.main("HEAD", quiet=True)
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""


def test_no_ledger_returns_zero_silently(capsys) -> None:
    """Runner returns None (no ledger) → main exits 0, no stdout."""
    from cli import link_commit_cli

    with patch.object(link_commit_cli, "invoke_link_commit") as mock:
        mock.return_value = None
        rc = link_commit_cli.main()
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""


def test_handler_exception_returns_zero_silently(capsys) -> None:
    """Runner swallows exceptions and returns None — main treats it
    identically to no-ledger (exit 0, silent). The hook's
    failure-loud semantics live in shell, not Python."""
    from cli import link_commit_cli

    with patch.object(link_commit_cli, "invoke_link_commit") as mock:
        mock.return_value = None  # runner already converted exception → None
        rc = link_commit_cli.main()
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""
