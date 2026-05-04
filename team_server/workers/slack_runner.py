"""Slack worker runner - workspace iteration + per-workspace fan-out.

Single iteration: read all workspaces, decrypt each token, construct a
Slack client per workspace, read the channel allowlist, delegate one
polling pass to slack_worker.poll_once. Per-workspace exceptions are
caught so a single bad token does not break iteration over the rest.

Encryption contract (mirrors team_server/auth/router.py): the Fernet
key is loaded once per iteration via load_key_from_env; the
oauth_token_encrypted field stores the urlsafe-base64 string output of
Fernet(key).encrypt(...).decode("utf-8"), so decrypting requires
encoding the string back to bytes before passing to decrypt_token.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from ledger.client import LedgerClient
from team_server.auth.encryption import decrypt_token, load_key_from_env
from team_server.workers.slack_worker import poll_once

logger = logging.getLogger(__name__)

Extractor = Callable[[str], Awaitable[dict]]


async def run_slack_iteration(
    db_client: LedgerClient, extractor: Extractor
) -> None:
    # slack_sdk imported lazily so the team_server package is importable
    # without slack_sdk installed (tests for unrelated code paths don't
    # need it). The runner is the only production caller; if slack_sdk
    # is missing at runtime, the per-workspace try/except surfaces it.
    from slack_sdk.web.async_client import AsyncWebClient

    key = load_key_from_env()
    workspaces = await db_client.query(
        "SELECT id, slack_team_id, oauth_token_encrypted FROM workspace"
    )
    for ws in workspaces or []:
        try:
            ciphertext = ws["oauth_token_encrypted"].encode("utf-8")
            token = decrypt_token(ciphertext, key)
            channels = await _channel_ids(db_client, ws["id"])
            slack_client = AsyncWebClient(token=token)
            await poll_once(
                db_client=db_client,
                slack_client=slack_client,
                workspace_team_id=ws["slack_team_id"],
                channels=channels,
                extractor=extractor,
            )
        except Exception:  # noqa: BLE001 - per-workspace isolation
            logger.exception(
                "[team-server] slack workspace=%s iteration failed",
                ws.get("slack_team_id", "<unknown>"),
            )


async def _channel_ids(client: LedgerClient, workspace_id) -> list[str]:
    rows = await client.query(
        "SELECT channel_id FROM channel_allowlist WHERE workspace_id = $wid",
        {"wid": workspace_id},
    )
    return [r["channel_id"] for r in rows or []]
