"""GET /events endpoint — read-only access to the team_event log for
per-dev EventMaterializer pull.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

router = APIRouter()


@router.get("/events")
async def get_events(
    request: Request,
    since: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict]:
    db = request.app.state.db
    rows = await db.client.query(
        "SELECT sequence, author_email, event_type, payload, created_at "
        "FROM team_event WHERE sequence > $since "
        "ORDER BY sequence ASC LIMIT $limit",
        {"since": since, "limit": limit},
    )
    return rows
