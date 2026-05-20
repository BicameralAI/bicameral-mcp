"""OAuth handshake flow for the Google Drive / Docs ingest adapter (Phase 5b).

Phase 5a (#427) shipped the read path against an already-stored token JSON;
this module ships the actual acquisition flow. Operator runs
``bicameral-mcp source-auth google_drive``, the local-loopback OAuth flow
opens the browser, Google issues a token with ``documents.readonly`` scope,
the result is persisted via ``secrets_store`` under
``source_id="google_drive"``, key ``"oauth_token"``.

Refresh handling: the adapter calls :func:`load_credentials` on every
fetch. When the stored token is expired but has a refresh token, the new
access token is silently minted and re-persisted. When the refresh token
itself is gone (revoked, replaced) the function raises a clear error so
the operator re-runs the handshake.

Threat model parity with ``events/backends/google_drive.py``:
- Same bundled OAuth client (one consent screen entry for the
  ``bicameral-mcp`` product, not two).
- Documents-only scope: ``documents.readonly`` — we cannot enumerate
  Drive, list folders, or touch any non-Docs MIME type. Operator passes
  individual doc URLs to the adapter.
- Token never leaves the OS keyring (via ``secrets_store``). Falls back
  to the in-process dict + warn-level audit event when no backend is
  available, same as every other secret in the project.
"""

from __future__ import annotations

import json

# Scopes required by the Google Drive / Docs ingest flow.
# - documents.readonly: read Google Docs body via the Docs API
#   (used by sources.google_drive.adapter.fetch_active).
# - drive.metadata.readonly: list files in a configured folder by mtime
#   (used by events.sources.google_drive.GoogleDriveFolderAdapter,
#   Phase 5c). Metadata-only — does NOT grant read access to non-Docs
#   files; doc content still flows through the documents.readonly scope.
#
# Operators who consented under Phase 5b (documents.readonly only) must
# re-run `bicameral-mcp source-auth google_drive` after upgrading; the
# refresh path can't expand scope on its own.
INGEST_SCOPES = [
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]
# Back-compat alias kept until external callers migrate. Renamed in 5c
# (#337) to reflect the multi-scope reality.
INGEST_SCOPE = INGEST_SCOPES[0]

_SOURCE_ID = "google_drive"
_TOKEN_KEY = "oauth_token"


class OAuthFlowAbortedError(RuntimeError):
    """Raised when the local-loopback OAuth flow ends without credentials.

    The most common cause is the operator closing the browser tab before
    completing consent. The CLI surfaces this with a retry hint.
    """


def _bundled_client_config() -> dict:
    """Delegate to the existing team-backend config helper.

    Shared single source of truth for the bundled OAuth client identity
    — both the team-backend Drive flow and this ingest flow show up as
    the same ``bicameral-mcp`` app on the operator's consent screen.
    """
    from events.backends.google_drive import _bundled_client_config as _cfg

    return _cfg()


def run_oauth_handshake() -> None:
    """Run the local-loopback OAuth flow and persist the result.

    Blocks until the operator completes consent in the browser (or aborts
    by closing the tab). On success, the token JSON is stored in
    ``secrets_store``; ``put_secret`` emits ``SOURCE_AUTH_GRANTED`` so
    the operator's audit log records the grant without ever logging
    the token value.

    Re-running the handshake when a token is already stored is fine — the
    new token overwrites the old, and ``put_secret`` emits a fresh
    ``SOURCE_AUTH_GRANTED`` event.

    Raises:
        OAuthFlowAbortedError: flow completed without producing credentials.
        RuntimeError: any other OAuth failure (network, bundled-client
            misconfiguration, etc.). The CLI translates to an exit code.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-not-found]

    from secrets_store import put_secret

    client_config = _bundled_client_config()
    flow = InstalledAppFlow.from_client_config(client_config, INGEST_SCOPES)
    creds = flow.run_local_server(port=0)
    if creds is None:
        raise OAuthFlowAbortedError(
            "OAuth flow did not produce credentials. "
            "Common cause: browser tab closed before consent. Re-run the command."
        )
    put_secret(source_id=_SOURCE_ID, key=_TOKEN_KEY, value=creds.to_json())


def load_credentials():
    """Return live ``google.oauth2.credentials.Credentials`` for the adapter.

    Resolution path:
    1. Read the persisted token JSON from ``secrets_store``.
    2. Build a ``Credentials`` object scoped to ``documents.readonly``.
    3. If the access token is expired, refresh using the refresh token;
       re-persist the rotated JSON so subsequent calls see the new
       access token.
    4. If no token is stored OR refresh fails, raise ``RuntimeError`` with
       an operator-facing hint pointing at the handshake command.

    Phase 5a's adapter currently builds Credentials directly from
    ``from_authorized_user_info``; Phase 5b's adapter is updated to call
    this function instead so the refresh path is exercised on every fetch.
    """
    from google.auth.transport.requests import Request  # type: ignore[import-not-found]
    from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]

    from secrets_store import get_secret, put_secret

    token_json = get_secret(source_id=_SOURCE_ID, key=_TOKEN_KEY)
    if not token_json:
        raise RuntimeError(
            "Google Drive OAuth token not configured. Run:\n"
            "  bicameral-mcp source-auth google_drive"
        )
    try:
        info = json.loads(token_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Stored Google Drive OAuth token is not valid JSON. "
            "Re-run: bicameral-mcp source-auth google_drive"
        ) from exc

    creds = Credentials.from_authorized_user_info(info, scopes=INGEST_SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001 — surface as actionable RuntimeError
            raise RuntimeError(
                "Failed to refresh Google Drive OAuth token "
                f"({type(exc).__name__}: {exc}). The refresh token may have been "
                "revoked. Re-run: bicameral-mcp source-auth google_drive"
            ) from exc
        # Re-persist with the rotated access_token + token_expiry so subsequent
        # calls don't trigger another refresh round-trip. put_secret emits
        # SOURCE_AUTH_GRANTED again, which is correct — the access token IS
        # a new grant even though the underlying consent is unchanged.
        put_secret(source_id=_SOURCE_ID, key=_TOKEN_KEY, value=creds.to_json())
        return creds

    # No refresh path available — either no refresh_token was returned at
    # initial consent (Google sometimes omits it on re-grant for the same
    # client) or the token shape is malformed.
    raise RuntimeError(
        "Google Drive OAuth credentials are not valid and cannot be refreshed. "
        "Re-run: bicameral-mcp source-auth google_drive"
    )
