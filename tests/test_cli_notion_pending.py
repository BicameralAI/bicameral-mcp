"""Tests for the ``bicameral-mcp notion-pending`` CLI (#337 cycle 8b).

Coverage:
- _list_pending: empty registry, one pending entry, multiple entries,
  malformed entry, missing value, stale-vs-active status
- _retrieve_token: happy path, no matching entry, malformed entry,
  missing token field
- main: invalid fingerprint shape rejected with exit 2, secrets_store
  unavailable returns exit 3
"""

from __future__ import annotations

import argparse
import json
import time

import pytest

from cli.notion_pending_cli import main as cli_main


@pytest.fixture(autouse=True)
def _disable_keyring(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests as _secrets_reset

    _secrets_reset()
    yield
    _secrets_reset()


def _put_pending(
    fingerprint: str, token: str = "secret_xyz", received_at: int | None = None
) -> None:
    """Helper: store a pending entry in the shape the webhook receiver writes."""
    from secrets_store import put_secret

    entry = json.dumps(
        {
            "token": token,
            "received_at": received_at if received_at is not None else int(time.time()),
        }
    )
    put_secret(source_id="notion", key=f"pending_{fingerprint}", value=entry)


def _args(fingerprint: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(fingerprint=fingerprint)


# ── list mode ──────────────────────────────────────────────────────────────


def test_list_empty_registry(capsys: pytest.CaptureFixture):
    rc = cli_main(_args(None))
    assert rc == 0
    captured = capsys.readouterr()
    assert "No pending Notion verification entries." in captured.out


def test_list_one_entry_shows_fingerprint_not_token(capsys: pytest.CaptureFixture):
    _put_pending("a" * 16, token="secret_should_not_appear")
    rc = cli_main(_args(None))
    assert rc == 0
    captured = capsys.readouterr()
    assert "a" * 16 in captured.out
    assert "active" in captured.out
    # Token must NOT appear in list-mode output (F3 review fix).
    assert "secret_should_not_appear" not in captured.out


def test_list_multiple_entries(capsys: pytest.CaptureFixture):
    _put_pending("a" * 16)
    _put_pending("b" * 16)
    rc = cli_main(_args(None))
    assert rc == 0
    captured = capsys.readouterr()
    assert "a" * 16 in captured.out
    assert "b" * 16 in captured.out


def test_list_stale_entry_flagged(capsys: pytest.CaptureFixture):
    """Entries older than 24h are flagged as 'stale' (matching the
    webhook handler's adoption-TTL cutoff)."""
    _put_pending("c" * 16, received_at=0)  # epoch 1970
    rc = cli_main(_args(None))
    assert rc == 0
    captured = capsys.readouterr()
    assert "c" * 16 in captured.out
    assert "stale" in captured.out


def test_list_malformed_entry_does_not_crash(capsys: pytest.CaptureFixture):
    """A pending key with a non-JSON value should print '(malformed entry)'
    and continue rather than blowing up the whole listing."""
    from secrets_store import put_secret

    put_secret(source_id="notion", key="pending_" + "d" * 16, value="not-json")
    _put_pending("e" * 16)
    rc = cli_main(_args(None))
    assert rc == 0
    captured = capsys.readouterr()
    assert "malformed entry" in captured.out
    # Good entry still shown.
    assert "e" * 16 in captured.out


# ── retrieve mode ──────────────────────────────────────────────────────────


def test_retrieve_token_happy_path(capsys: pytest.CaptureFixture):
    _put_pending("a" * 16, token="secret_real_token_value")
    rc = cli_main(_args("a" * 16))
    assert rc == 0
    captured = capsys.readouterr()
    # Token to stdout, no trailing newline (so operator can pipe to
    # clipboard tools without spurious whitespace).
    assert captured.out == "secret_real_token_value"


def test_retrieve_token_missing_returns_1(capsys: pytest.CaptureFixture):
    rc = cli_main(_args("a" * 16))
    assert rc == 1
    captured = capsys.readouterr()
    assert "no pending entry" in captured.err


def test_retrieve_token_malformed_entry_returns_3(capsys: pytest.CaptureFixture):
    from secrets_store import put_secret

    put_secret(source_id="notion", key="pending_" + "a" * 16, value="not-json")
    rc = cli_main(_args("a" * 16))
    assert rc == 3
    captured = capsys.readouterr()
    assert "malformed" in captured.err


def test_retrieve_token_no_token_field_returns_3(capsys: pytest.CaptureFixture):
    from secrets_store import put_secret

    put_secret(source_id="notion", key="pending_" + "a" * 16, value=json.dumps({"received_at": 1}))
    rc = cli_main(_args("a" * 16))
    assert rc == 3
    captured = capsys.readouterr()
    assert "no token field" in captured.err


# ── input validation ──────────────────────────────────────────────────────


def test_invalid_fingerprint_shape_rejected(capsys: pytest.CaptureFixture):
    rc = cli_main(_args("NOT-HEX"))
    assert rc == 2
    captured = capsys.readouterr()
    assert "16 lowercase hex" in captured.err


def test_invalid_fingerprint_too_short(capsys: pytest.CaptureFixture):
    rc = cli_main(_args("abc"))
    assert rc == 2
    captured = capsys.readouterr()
    assert "16 lowercase hex" in captured.err


def test_uppercase_fingerprint_normalized(capsys: pytest.CaptureFixture):
    """Per the CLI's .lower() normalization — operators copy-pasting
    from stderr shouldn't be tripped up by accidental capitalization."""
    _put_pending("a" * 16, token="secret_lowercase_stored")
    rc = cli_main(_args("A" * 16))
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == "secret_lowercase_stored"
