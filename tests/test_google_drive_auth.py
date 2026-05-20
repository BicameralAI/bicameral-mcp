"""Tests for #337 Phase 5b — Google Drive OAuth handshake + refresh.

The OAuth flow itself (``InstalledAppFlow.run_local_server``) opens a real
browser and cannot run unattended — every test patches it at the narrowest
seam (the ``InstalledAppFlow.from_client_config`` factory's return value).
The secrets_store integration runs unmocked under
``BICAMERAL_KEYRING_DISABLE=1`` so the dict-backed fallback is the actual
substrate.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import audit_log
from audit_log import AuditEventType
from secrets_store import get_secret, put_secret
from secrets_store.store import _reset_for_tests as _reset_secrets


@pytest.fixture(autouse=True)
def _disable_keyring_and_reset(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    _reset_secrets()
    yield
    _reset_secrets()


def _fake_creds(*, valid=True, expired=False, has_refresh=True, json_payload=None):
    """Build a stand-in for google.oauth2.credentials.Credentials."""
    creds = MagicMock()
    creds.valid = valid
    creds.expired = expired
    creds.refresh_token = "refresh_xyz" if has_refresh else None
    creds.to_json = MagicMock(
        return_value=json.dumps(
            json_payload
            or {
                "token": "access_abc",
                "refresh_token": "refresh_xyz",
                "client_id": "fake",
                "client_secret": "fake",
                "scopes": ["https://www.googleapis.com/auth/documents.readonly"],
            }
        )
    )
    return creds


# ── run_oauth_handshake ─────────────────────────────────────────────────────


def test_handshake_persists_token_and_emits_granted_event():
    fake_creds = _fake_creds(valid=True)
    fake_flow = MagicMock()
    fake_flow.run_local_server = MagicMock(return_value=fake_creds)

    with (
        patch(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_config",
            return_value=fake_flow,
        ),
        patch(
            "sources.google_drive.auth._bundled_client_config",
            return_value={"installed": {"client_id": "x", "client_secret": "y"}},
        ),
        patch.object(audit_log, "emit", wraps=audit_log.emit) as emit_spy,
    ):
        from sources.google_drive.auth import run_oauth_handshake

        run_oauth_handshake()

    # Token landed in secrets_store.
    stored = get_secret(source_id="google_drive", key="oauth_token")
    assert stored is not None
    assert "access_abc" in stored

    # SOURCE_AUTH_GRANTED emitted, no token value in audit fields.
    granted = [
        c
        for c in emit_spy.call_args_list
        if c.args and c.args[0] == AuditEventType.SOURCE_AUTH_GRANTED
    ]
    assert len(granted) == 1
    assert "access_abc" not in str(granted[0].kwargs)


def test_handshake_raises_when_flow_returns_none():
    fake_flow = MagicMock()
    fake_flow.run_local_server = MagicMock(return_value=None)

    with (
        patch(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_config",
            return_value=fake_flow,
        ),
        patch(
            "sources.google_drive.auth._bundled_client_config",
            return_value={"installed": {"client_id": "x", "client_secret": "y"}},
        ),
    ):
        from sources.google_drive.auth import OAuthFlowAbortedError, run_oauth_handshake

        with pytest.raises(OAuthFlowAbortedError):
            run_oauth_handshake()

    # No token persisted on abort.
    assert get_secret(source_id="google_drive", key="oauth_token") is None


def test_handshake_overwrites_existing_token():
    """Re-running the handshake should replace the stored token cleanly."""
    put_secret(source_id="google_drive", key="oauth_token", value='{"token":"old"}')

    fake_creds = _fake_creds(
        valid=True,
        json_payload={
            "token": "fresh",
            "refresh_token": "r",
            "client_id": "x",
            "client_secret": "y",
            "scopes": ["https://www.googleapis.com/auth/documents.readonly"],
        },
    )
    fake_flow = MagicMock()
    fake_flow.run_local_server = MagicMock(return_value=fake_creds)

    with (
        patch(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_config",
            return_value=fake_flow,
        ),
        patch(
            "sources.google_drive.auth._bundled_client_config",
            return_value={"installed": {"client_id": "x", "client_secret": "y"}},
        ),
    ):
        from sources.google_drive.auth import run_oauth_handshake

        run_oauth_handshake()

    stored = get_secret(source_id="google_drive", key="oauth_token")
    assert "fresh" in stored
    assert "old" not in stored


# ── load_credentials ────────────────────────────────────────────────────────


def test_load_raises_when_no_token_stored():
    from sources.google_drive.auth import load_credentials

    with pytest.raises(RuntimeError, match="not configured"):
        load_credentials()


def test_load_raises_when_stored_token_is_not_json():
    put_secret(source_id="google_drive", key="oauth_token", value="not-json{")
    from sources.google_drive.auth import load_credentials

    with pytest.raises(RuntimeError, match="not valid JSON"):
        load_credentials()


def test_load_returns_credentials_when_valid():
    """Valid (non-expired) token → returns the Credentials object directly,
    no refresh attempted."""
    put_secret(
        source_id="google_drive",
        key="oauth_token",
        value=json.dumps(
            {
                "token": "access_abc",
                "refresh_token": "r",
                "client_id": "x",
                "client_secret": "y",
                "scopes": ["https://www.googleapis.com/auth/documents.readonly"],
            }
        ),
    )
    fake_creds = _fake_creds(valid=True, expired=False)
    with patch(
        "google.oauth2.credentials.Credentials.from_authorized_user_info",
        return_value=fake_creds,
    ):
        from sources.google_drive.auth import load_credentials

        result = load_credentials()
    assert result is fake_creds
    fake_creds.refresh.assert_not_called()


def test_load_refreshes_and_repersists_expired_token():
    """Expired token + refresh_token present → refresh path runs, new JSON
    is persisted, returned creds are the refreshed object."""
    original_json = json.dumps(
        {
            "token": "old_access",
            "refresh_token": "r",
            "client_id": "x",
            "client_secret": "y",
            "scopes": ["https://www.googleapis.com/auth/documents.readonly"],
        }
    )
    put_secret(source_id="google_drive", key="oauth_token", value=original_json)

    # Refreshed creds expose a different to_json payload so we can verify
    # re-persistence happened with the new value.
    fake_creds = _fake_creds(
        valid=False,
        expired=True,
        has_refresh=True,
        json_payload={
            "token": "new_access",
            "refresh_token": "r",
            "client_id": "x",
            "client_secret": "y",
            "scopes": ["https://www.googleapis.com/auth/documents.readonly"],
        },
    )

    with patch(
        "google.oauth2.credentials.Credentials.from_authorized_user_info",
        return_value=fake_creds,
    ):
        from sources.google_drive.auth import load_credentials

        result = load_credentials()

    fake_creds.refresh.assert_called_once()
    assert result is fake_creds
    # New token persisted, old one overwritten.
    stored = get_secret(source_id="google_drive", key="oauth_token")
    assert "new_access" in stored


def test_load_raises_when_refresh_fails():
    put_secret(
        source_id="google_drive",
        key="oauth_token",
        value=json.dumps(
            {
                "token": "x",
                "refresh_token": "r",
                "client_id": "x",
                "client_secret": "y",
                "scopes": ["https://www.googleapis.com/auth/documents.readonly"],
            }
        ),
    )
    fake_creds = _fake_creds(valid=False, expired=True, has_refresh=True)
    fake_creds.refresh.side_effect = Exception("Token revoked by user")

    with patch(
        "google.oauth2.credentials.Credentials.from_authorized_user_info",
        return_value=fake_creds,
    ):
        from sources.google_drive.auth import load_credentials

        with pytest.raises(RuntimeError, match="Failed to refresh"):
            load_credentials()


def test_load_raises_when_no_refresh_token_and_token_invalid():
    """Stored token is invalid and has no refresh_token — operator must
    re-run the handshake."""
    put_secret(
        source_id="google_drive",
        key="oauth_token",
        value=json.dumps(
            {
                "token": "x",
                "client_id": "x",
                "client_secret": "y",
                "scopes": ["https://www.googleapis.com/auth/documents.readonly"],
            }
        ),
    )
    fake_creds = _fake_creds(valid=False, expired=True, has_refresh=False)

    with patch(
        "google.oauth2.credentials.Credentials.from_authorized_user_info",
        return_value=fake_creds,
    ):
        from sources.google_drive.auth import load_credentials

        with pytest.raises(RuntimeError, match="not valid and cannot be refreshed"):
            load_credentials()


# ── CLI integration ─────────────────────────────────────────────────────────


def test_cli_main_success(capsys):
    fake_creds = _fake_creds(valid=True)
    fake_flow = MagicMock()
    fake_flow.run_local_server = MagicMock(return_value=fake_creds)

    with (
        patch(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_config",
            return_value=fake_flow,
        ),
        patch(
            "sources.google_drive.auth._bundled_client_config",
            return_value={"installed": {"client_id": "x", "client_secret": "y"}},
        ),
    ):
        from cli.source_auth_cli import main

        exit_code = main(SimpleNamespace(source="google_drive"))

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "success" in out.lower()
    assert get_secret(source_id="google_drive", key="oauth_token") is not None


def test_cli_main_returns_1_on_abort(capsys):
    fake_flow = MagicMock()
    fake_flow.run_local_server = MagicMock(return_value=None)

    with (
        patch(
            "google_auth_oauthlib.flow.InstalledAppFlow.from_client_config",
            return_value=fake_flow,
        ),
        patch(
            "sources.google_drive.auth._bundled_client_config",
            return_value={"installed": {"client_id": "x", "client_secret": "y"}},
        ),
    ):
        from cli.source_auth_cli import main

        exit_code = main(SimpleNamespace(source="google_drive"))

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "aborted" in err.lower()


def test_cli_main_returns_2_on_client_not_provisioned(capsys, monkeypatch):
    """Bundled OAuth client placeholders raise OAuthClientNotProvisionedError
    (which is a RuntimeError subclass) → CLI should exit 2."""
    from events.backends.google_drive import OAuthClientNotProvisionedError

    def _raise(*a, **kw):
        raise OAuthClientNotProvisionedError("not provisioned")

    monkeypatch.setattr("sources.google_drive.auth._bundled_client_config", _raise)

    from cli.source_auth_cli import main

    exit_code = main(SimpleNamespace(source="google_drive"))
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "not provisioned" in err.lower()
