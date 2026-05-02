"""Peer-author event writer — writes a `team_event` row shaped to match
the `events/writer.py` JSONL contract.

Per the research brief: the team-server is a peer in the existing
event-sourcing model. Authoring identity is `team-server@<team_id>.bicameral`
(single-bot per workspace). The sequence number is monotonic per
team-server instance.
"""

from __future__ import annotations

from ledger.client import LedgerClient


def author_email_for_workspace(team_id: str) -> str:
    return f"team-server@{team_id}.bicameral"


async def write_team_event(
    client: LedgerClient,
    workspace_team_id: str,
    event_type: str,
    payload: dict,
) -> None:
    """Append a team_event row. Sequence is computed as max(existing) + 1
    so multi-instance scenarios degrade to last-write-wins per workspace
    (single-instance v0 deployment is the contract; multi-instance HA is
    a v1 concern per plan boundaries.non_goals)."""
    rows = await client.query(
        "SELECT sequence FROM team_event ORDER BY sequence DESC LIMIT 1"
    )
    next_seq = (rows[0]["sequence"] + 1) if rows else 1
    await client.query(
        "CREATE team_event CONTENT { author_email: $ae, event_type: $et, "
        "payload: $pl, sequence: $sq, created_at: time::now() }",
        {
            "ae": author_email_for_workspace(workspace_team_id),
            "et": event_type,
            "pl": payload,
            "sq": next_seq,
        },
    )
