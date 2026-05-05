"""Slack OAuth v2 helpers for the team-server.

Pure functions — no DB, no app state. The router (`team_server/auth/router.py`)
composes these with persistence + state validation.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"

REQUIRED_SCOPES: tuple[str, ...] = (
    "channels:history",
    "channels:read",
    "groups:history",
    "groups:read",
)


class SlackOAuthError(RuntimeError):
    """Raised when Slack rejects an OAuth code exchange."""


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": ",".join(REQUIRED_SCOPES),
    }
    return f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    """POST to Slack oauth.v2.access; raise on `ok=false`."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            SLACK_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
        )
    payload = resp.json()
    if not payload.get("ok"):
        raise SlackOAuthError(payload.get("error", "unknown"))
    return payload
