"""OAuth callback + install routes — factored out of app.py per audit
Advisory #2 to keep app.py under the 250-line cap.
"""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, HTTPException, Request

from team_server.auth import slack_oauth
from team_server.auth.encryption import encrypt_token, load_key_from_env

router = APIRouter()

# In-memory CSRF state store. Keys are state-tokens, values are TTL timestamps.
# A team-server restart loses pending OAuth flows in flight; users retry
# the install. Acceptable tradeoff for a self-hosted single-instance
# deployment; multi-instance HA would persist this.
_PENDING_STATES: dict[str, float] = {}


@router.get("/oauth/slack/install")
async def install():
    """Return the Slack OAuth authorize URL with a fresh CSRF state token.
    The admin opens this URL, approves, Slack redirects to /callback."""
    client_id = os.environ.get("SLACK_CLIENT_ID", "")
    redirect_uri = os.environ.get(
        "SLACK_REDIRECT_URI", "http://localhost:8765/oauth/slack/callback"
    )
    state = secrets.token_urlsafe(32)
    _PENDING_STATES[state] = 1.0  # placeholder TTL marker
    url = slack_oauth.build_authorize_url(client_id, redirect_uri, state)
    return {"authorize_url": url, "state": state}


@router.get("/oauth/slack/callback")
async def callback(request: Request, code: str = "", state: str = ""):
    """Exchange the OAuth code for a token, persist the workspace row with
    the token encrypted at rest, and return the team_id for confirmation."""
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state")
    if state not in _PENDING_STATES:
        raise HTTPException(status_code=400, detail="invalid or expired state")
    _PENDING_STATES.pop(state, None)

    client_id = os.environ.get("SLACK_CLIENT_ID", "")
    client_secret = os.environ.get("SLACK_CLIENT_SECRET", "")
    redirect_uri = os.environ.get(
        "SLACK_REDIRECT_URI", "http://localhost:8765/oauth/slack/callback"
    )

    payload = await slack_oauth.exchange_code(
        code=code,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )
    team_id = payload["team"]["id"]
    team_name = payload["team"].get("name", "")
    access_token = payload["access_token"]

    key = load_key_from_env()
    encrypted = encrypt_token(access_token, key).decode("utf-8")

    db = request.app.state.db
    await db.client.query(
        "CREATE workspace CONTENT { name: $name, slack_team_id: $tid, "
        "oauth_token_encrypted: $enc, created_at: time::now() }",
        {"name": team_name, "tid": team_id, "enc": encrypted},
    )
    return {"ok": True, "team_id": team_id}
