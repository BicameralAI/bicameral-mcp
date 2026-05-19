"""Tests for #419 Phase 0b — secrets_store wrapper.

Sociable where it matters: when ``BICAMERAL_KEYRING_DISABLE=1`` is set the
dict fallback IS the real backend, and the audit-log emit goes through
the real ``audit_log.emit`` channel. We don't mock either of those.

For backend-present platforms, the OS keyring is exercised in a separate
manual-run job (CI sandboxes don't reliably ship a Secret Service /
Keychain daemon). The dict-fallback path covers the API surface contract.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import audit_log
from audit_log import AuditEventType
from secrets_store import store as secrets_store_module
from secrets_store.store import (
    _reset_for_tests,
    delete_secret,
    get_secret,
    list_keys,
    put_secret,
)


@pytest.fixture(autouse=True)
def _disable_keyring_and_reset(monkeypatch):
    """Force dict-fallback path + reset module state for each test."""
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    _reset_for_tests()
    yield
    _reset_for_tests()


# ── round-trip ──────────────────────────────────────────────────────────────


def test_put_get_delete_round_trip():
    put_secret(source_id="linear", key="api_key", value="lin_abc123")
    assert get_secret(source_id="linear", key="api_key") == "lin_abc123"
    delete_secret(source_id="linear", key="api_key")
    assert get_secret(source_id="linear", key="api_key") is None


def test_get_returns_none_for_unset():
    assert get_secret(source_id="notion", key="missing") is None


def test_delete_is_idempotent():
    # Should not raise on a key that was never set.
    delete_secret(source_id="github", key="never_existed")


def test_list_keys_isolates_by_source():
    put_secret(source_id="linear", key="api_key", value="x")
    put_secret(source_id="linear", key="refresh_token", value="y")
    put_secret(source_id="notion", key="api_key", value="z")
    linear_keys = set(list_keys(source_id="linear"))
    notion_keys = set(list_keys(source_id="notion"))
    assert linear_keys == {"api_key", "refresh_token"}
    assert notion_keys == {"api_key"}


# ── validation: service-name + key whitelist ────────────────────────────────


@pytest.mark.parametrize(
    "bad_source",
    [
        "../../etc/foo",
        "linear/../notion",
        "with space",
        "with\\backslash",
        "with/slash",
        "with\x00null",
        "",
    ],
)
def test_put_rejects_traversal_source_id(bad_source):
    with pytest.raises(ValueError, match="source_id"):
        put_secret(source_id=bad_source, key="api_key", value="x")


@pytest.mark.parametrize(
    "bad_key",
    [
        "../password",
        "key with space",
        "",
        "key/with/slash",
    ],
)
def test_put_rejects_bad_key(bad_key):
    with pytest.raises(ValueError, match="key"):
        put_secret(source_id="linear", key=bad_key, value="x")


def test_get_validates_same_whitelist():
    with pytest.raises(ValueError):
        get_secret(source_id="../etc", key="api_key")


def test_delete_validates_same_whitelist():
    with pytest.raises(ValueError):
        delete_secret(source_id="../etc", key="api_key")


# ── value size cap ──────────────────────────────────────────────────────────


def test_put_rejects_oversized_value():
    big = "x" * (8 * 1024 + 1)
    with pytest.raises(ValueError, match="too large"):
        put_secret(source_id="linear", key="api_key", value=big)


def test_put_accepts_value_at_cap():
    # Exactly 8 KiB is allowed; the gate is `> max`.
    at_cap = "x" * (8 * 1024)
    put_secret(source_id="linear", key="api_key", value=at_cap)
    assert get_secret(source_id="linear", key="api_key") == at_cap


# ── audit lifecycle ─────────────────────────────────────────────────────────


def test_put_emits_source_auth_granted():
    with patch.object(audit_log, "emit", wraps=audit_log.emit) as emit_spy:
        put_secret(source_id="linear", key="api_key", value="x")

    granted_calls = [
        c
        for c in emit_spy.call_args_list
        if c.args and c.args[0] == AuditEventType.SOURCE_AUTH_GRANTED
    ]
    assert len(granted_calls) == 1
    # Audit event must carry source_id + secret_key, NEVER the value.
    kwargs = granted_calls[0].kwargs
    assert kwargs["source_id"] == "linear"
    assert kwargs["secret_key"] == "api_key"
    assert "value" not in kwargs
    assert "x" not in str(kwargs)  # belt-and-suspenders


def test_delete_emits_source_auth_revoked():
    put_secret(source_id="linear", key="api_key", value="x")

    with patch.object(audit_log, "emit", wraps=audit_log.emit) as emit_spy:
        delete_secret(source_id="linear", key="api_key")

    revoked_calls = [
        c
        for c in emit_spy.call_args_list
        if c.args and c.args[0] == AuditEventType.SOURCE_AUTH_REVOKED
    ]
    assert len(revoked_calls) == 1


def test_delete_emits_revoked_even_when_key_missing():
    """Revocation signal must be operator-visible regardless of prior state."""
    with patch.object(audit_log, "emit", wraps=audit_log.emit) as emit_spy:
        delete_secret(source_id="linear", key="never_existed")

    revoked_calls = [
        c
        for c in emit_spy.call_args_list
        if c.args and c.args[0] == AuditEventType.SOURCE_AUTH_REVOKED
    ]
    assert len(revoked_calls) == 1


def test_get_does_not_emit():
    """Reads must not flood the audit log — granted/revoked carry state."""
    put_secret(source_id="linear", key="api_key", value="x")

    with patch.object(audit_log, "emit", wraps=audit_log.emit) as emit_spy:
        get_secret(source_id="linear", key="api_key")

    # No SOURCE_* event from a read.
    source_events = [
        c
        for c in emit_spy.call_args_list
        if c.args and str(c.args[0]).startswith("AuditEventType.SOURCE_")
    ]
    assert source_events == []
