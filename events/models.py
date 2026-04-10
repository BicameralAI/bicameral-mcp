"""Event envelope model for the event-sourced spec log."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    """Immutable event written to .bicameral/events/{author}/."""

    schema_version: int = 1
    event_id: str = Field(..., description="Timestamp-UUID composite, e.g. 20260410T180000Z-a1b2c3d4")
    event_type: str = Field(..., description="e.g. ingest.completed, link_commit.completed")
    author: str = Field(..., description="Git user email")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)
