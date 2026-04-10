"""TeamWriteAdapter — dual-write adapter for team collaboration mode.

Wraps SurrealDBLedgerAdapter via composition. On every write operation,
emits an event file first, then delegates to the inner adapter.
All read operations pass through directly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .materializer import EventMaterializer
from .writer import EventFileWriter

logger = logging.getLogger(__name__)


class TeamWriteAdapter:
    """Dual-write: event file + local SurrealDB on every mutation."""

    def __init__(
        self,
        inner,
        writer: EventFileWriter,
        materializer: EventMaterializer,
    ) -> None:
        self._inner = inner
        self._writer = writer
        self._materializer = materializer

    async def connect(self) -> None:
        """Connect inner adapter, then replay any new events from peers."""
        await self._inner.connect()
        replayed = await self._materializer.replay_new_events(self._inner)
        if replayed:
            logger.info("[team] materialized %d peer events on startup", replayed)

    # ── Write methods (intercepted: event file first, then DB) ───────────

    async def ingest_payload(self, payload: dict) -> dict:
        """Write ingest event, then delegate to inner adapter."""
        self._writer.write("ingest.completed", payload)
        return await self._inner.ingest_payload(payload)

    async def ingest_commit(
        self, commit_hash: str, repo_path: str, drift_analyzer=None,
    ) -> dict:
        """Write link_commit event, then delegate to inner adapter."""
        self._writer.write(
            "link_commit.completed",
            {"commit_hash": commit_hash, "repo_path": repo_path},
        )
        return await self._inner.ingest_commit(
            commit_hash, repo_path, drift_analyzer=drift_analyzer,
        )

    async def upsert_source_cursor(
        self,
        repo: str,
        source_type: str,
        source_scope: str = "default",
        cursor: str = "",
        last_source_ref: str = "",
        status: str = "ok",
        error: str = "",
    ) -> dict:
        """Source cursor is local bookkeeping — no event emitted."""
        return await self._inner.upsert_source_cursor(
            repo=repo,
            source_type=source_type,
            source_scope=source_scope,
            cursor=cursor,
            last_source_ref=last_source_ref,
            status=status,
            error=error,
        )

    # ── Read methods (pass-through) ──────────────────────────────────────

    async def get_all_decisions(self, filter: str = "all") -> list[dict]:
        return await self._inner.get_all_decisions(filter=filter)

    async def search_by_query(
        self, query: str, max_results: int = 10, min_confidence: float = 0.5,
    ) -> list[dict]:
        return await self._inner.search_by_query(query, max_results, min_confidence)

    async def get_decisions_for_file(self, file_path: str) -> list[dict]:
        return await self._inner.get_decisions_for_file(file_path)

    async def get_undocumented_symbols(self, file_path: str) -> list[str]:
        return await self._inner.get_undocumented_symbols(file_path)

    async def get_source_cursor(
        self, repo: str, source_type: str, source_scope: str = "default",
    ) -> dict | None:
        return await self._inner.get_source_cursor(repo, source_type, source_scope)
