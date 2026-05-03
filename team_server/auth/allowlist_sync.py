"""Channel allowlist startup-time sync.

Reads config.slack.workspaces[] and reconciles channel_allowlist
against the workspace table. Per-team_id additive + subtractive sync
so operator YAML edits propagate on next restart. Workspaces in YAML
without a corresponding workspace-table row (no OAuth completed yet)
are logged and skipped — they get picked up on the next sync after
OAuth completes.
"""

from __future__ import annotations

import logging

from ledger.client import LedgerClient
from team_server.config import TeamServerConfig

logger = logging.getLogger(__name__)


async def sync_channel_allowlist(
    client: LedgerClient,
    config: TeamServerConfig,
) -> None:
    for workspace_cfg in config.slack.workspaces:
        await _sync_one_workspace(
            client,
            workspace_cfg.team_id,
            workspace_cfg.channels,
        )


async def _sync_one_workspace(
    client: LedgerClient,
    team_id: str,
    yaml_channels: list[str],
) -> None:
    rows = await client.query(
        "SELECT id FROM workspace WHERE slack_team_id = $tid LIMIT 1",
        {"tid": team_id},
    )
    if not rows:
        logger.info(
            "[allowlist-sync] no workspace row for team_id=%s; skipping (OAuth not yet completed)",
            team_id,
        )
        return
    # workspace_id arrives as 'workspace:<rid>' from SELECT; split for type::thing()
    raw_id = str(rows[0]["id"])
    _tb, _, ws_rid = raw_id.partition(":")
    existing_rows = await client.query(
        "SELECT channel_id FROM channel_allowlist "
        "WHERE workspace_id = type::thing('workspace', $wrid)",
        {"wrid": ws_rid},
    )
    existing = {r["channel_id"] for r in existing_rows or []}
    desired = set(yaml_channels)
    to_add = desired - existing
    to_remove = existing - desired
    for channel_id in to_add:
        await client.query(
            "CREATE channel_allowlist CONTENT { "
            "workspace_id: type::thing('workspace', $wrid), "
            "channel_id: $cid, channel_name: '' }",
            {"wrid": ws_rid, "cid": channel_id},
        )
    for channel_id in to_remove:
        await client.query(
            "DELETE channel_allowlist "
            "WHERE workspace_id = type::thing('workspace', $wrid) "
            "AND channel_id = $cid",
            {"wrid": ws_rid, "cid": channel_id},
        )
    logger.info(
        "[allowlist-sync] team_id=%s: +%d -%d (now %d total)",
        team_id,
        len(to_add),
        len(to_remove),
        len(desired),
    )
