"""Functionality tests for `context._resolve_agent_identity` (#231 Phase 1).

Locks the per-developer-stable / per-install-isolated / privacy-positive
contracts for the agent-identity resolver:

- email + salt → 16-char hex hash (opaque)
- same email + same salt → same hash (per-developer stable)
- different emails + same salt → different hashes (per-developer isolated)
- same email + different salts → different hashes (per-install isolated)
- raw email never appears in the hash output (privacy contract)
- fallback chain to ``_SESSION_ID`` on git failure / salt failure / module
  import failure
"""

from __future__ import annotations

import re

import context

_FIXED_SALT = b"\x00\x01\x02\x03" * 8  # 32 bytes, deterministic for tests


def _patch_email(monkeypatch, value: str) -> None:
    """Override `events.writer._get_git_email` for the test."""
    import events.writer as writer_mod

    monkeypatch.setattr(writer_mod, "_get_git_email", lambda _repo: value)


def _patch_salt(monkeypatch, value: bytes = _FIXED_SALT) -> None:
    """Override `preflight_telemetry._get_or_create_salt` for the test."""
    import preflight_telemetry

    monkeypatch.setattr(preflight_telemetry, "_get_or_create_salt", lambda: value)


def test_resolve_agent_identity_returns_16_char_hex_when_email_present(monkeypatch) -> None:
    _patch_email(monkeypatch, "alice@example.com")
    _patch_salt(monkeypatch)
    identity = context._resolve_agent_identity("/tmp/repo")
    assert re.match(r"^[0-9a-f]{16}$", identity), identity


def test_resolve_agent_identity_is_stable_for_same_email_and_salt(monkeypatch) -> None:
    _patch_email(monkeypatch, "alice@example.com")
    _patch_salt(monkeypatch)
    first = context._resolve_agent_identity("/tmp/repo")
    second = context._resolve_agent_identity("/tmp/repo")
    assert first == second


def test_resolve_agent_identity_differs_across_emails_with_same_salt(monkeypatch) -> None:
    _patch_salt(monkeypatch)
    _patch_email(monkeypatch, "alice@example.com")
    alice = context._resolve_agent_identity("/tmp/repo")
    _patch_email(monkeypatch, "bob@example.com")
    bob = context._resolve_agent_identity("/tmp/repo")
    assert alice != bob


def test_resolve_agent_identity_differs_across_salts_with_same_email(monkeypatch) -> None:
    _patch_email(monkeypatch, "alice@example.com")
    _patch_salt(monkeypatch, b"\x00" * 32)
    install_a = context._resolve_agent_identity("/tmp/repo")
    _patch_salt(monkeypatch, b"\xff" * 32)
    install_b = context._resolve_agent_identity("/tmp/repo")
    assert install_a != install_b


def test_resolve_agent_identity_falls_back_to_session_id_when_email_unknown(monkeypatch) -> None:
    _patch_email(monkeypatch, "unknown")
    _patch_salt(monkeypatch)
    identity = context._resolve_agent_identity("/tmp/repo")
    assert identity == context._SESSION_ID


def test_resolve_agent_identity_falls_back_to_session_id_when_email_empty(monkeypatch) -> None:
    _patch_email(monkeypatch, "")
    _patch_salt(monkeypatch)
    identity = context._resolve_agent_identity("/tmp/repo")
    assert identity == context._SESSION_ID


def test_resolve_agent_identity_falls_back_to_session_id_when_salt_unavailable(monkeypatch) -> None:
    _patch_email(monkeypatch, "alice@example.com")
    import preflight_telemetry

    def raise_oserror() -> bytes:
        raise OSError("salt file unwriteable")

    monkeypatch.setattr(preflight_telemetry, "_get_or_create_salt", raise_oserror)
    identity = context._resolve_agent_identity("/tmp/repo")
    assert identity == context._SESSION_ID


def test_resolve_agent_identity_falls_back_when_get_git_email_raises(monkeypatch) -> None:
    import events.writer as writer_mod

    def raise_called_process_error(_repo):
        import subprocess

        raise subprocess.CalledProcessError(1, ["git"], "fail")

    monkeypatch.setattr(writer_mod, "_get_git_email", raise_called_process_error)
    _patch_salt(monkeypatch)
    identity = context._resolve_agent_identity("/tmp/repo")
    assert identity == context._SESSION_ID


def test_resolve_agent_identity_does_not_leak_raw_email_in_return_value(monkeypatch) -> None:
    """Privacy contract: the hash output must NOT contain any substring of
    the raw email (local-part, domain, or @-symbol). Locks the salted-hash
    semantic against a regression that accidentally leaked the email."""
    _patch_email(monkeypatch, "alice@example.com")
    _patch_salt(monkeypatch)
    identity = context._resolve_agent_identity("/tmp/repo")
    assert "alice" not in identity
    assert "@" not in identity
    assert "example" not in identity
    assert "com" not in identity
