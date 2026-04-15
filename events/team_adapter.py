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
        self._ready = False

    async def connect(self) -> None:
        """Connect inner adapter, then replay any new events from peers."""
        await self._inner.connect()
        replayed = await self._materializer.replay_new_events(self._inner)
        if replayed:
            logger.info("[team] materialized %d peer events on startup", replayed)
        self._ready = True

    async def _ensure_ready(self) -> None:
        """Lazy connect + materialize on first use."""
        if not self._ready:
            await self.connect()

    # ── Write methods (intercepted: event file first, then DB) ───────────

    async def ingest_payload(self, payload: dict, ctx=None) -> dict:
        """Write ingest event, then delegate to inner adapter.

        v0.4.12.1: forward the ``ctx`` kwarg added in v0.4.6's pollution
        fix. Without this, handle_ingest's ``ledger.ingest_payload(payload, ctx=ctx)``
        call fails in team mode with TypeError, which means EVERY team-mode
        ingest has been broken since v0.4.6.
        """
        await self._ensure_ready()
        self._writer.write("ingest.completed", payload)
        return await self._inner.ingest_payload(payload, ctx=ctx)

    async def ingest_commit(
        self,
        commit_hash: str,
        repo_path: str,
        drift_analyzer=None,
        authoritative_ref: str = "",
    ) -> dict:
        """Write link_commit event, then delegate to inner adapter.

        v0.4.12.1: forward the ``authoritative_ref`` kwarg added in
        v0.4.6's pollution guard. Without this, every team-mode
        ``handle_link_commit`` call silently failed with a TypeError —
        bicameral's own bicameral repo (which runs in team mode) had
        all 23 decisions stuck ungrounded because the link_commit sweep
        never ran. Surfaced during v0.4.12 preflight dogfooding.
        """
        await self._ensure_ready()
        self._writer.write(
            "link_commit.completed",
            {"commit_hash": commit_hash, "repo_path": repo_path},
        )
        return await self._inner.ingest_commit(
            commit_hash,
            repo_path,
            drift_analyzer=drift_analyzer,
            authoritative_ref=authoritative_ref,
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
        await self._ensure_ready()
        return await self._inner.upsert_source_cursor(
            repo=repo,
            source_type=source_type,
            source_scope=source_scope,
            cursor=cursor,
            last_source_ref=last_source_ref,
            status=status,
            error=error,
        )

    async def lookup_vocab_cache(
        self, query_text: str, repo: str,
    ) -> tuple[list[dict], str]:
        """Vocab cache is local bookkeeping — no event emitted.

        Returns ``(symbols, matched_query_text)``. The second element is
        the ``query_text`` that the top cache hit was originally stored
        against — the caller uses it for FC-3 similarity gating.
        """
        await self._ensure_ready()
        return await self._inner.lookup_vocab_cache(query_text, repo)

    async def upsert_vocab_cache(
        self, query_text: str, repo: str, symbols: list[dict],
    ) -> None:
        """Vocab cache is local bookkeeping — no event emitted."""
        await self._ensure_ready()
        await self._inner.upsert_vocab_cache(query_text, repo, symbols)

    # ── Read methods (pass-through) ──────────────────────────────────────

    async def get_all_decisions(self, filter: str = "all") -> list[dict]:
        await self._ensure_ready()
        return await self._inner.get_all_decisions(filter=filter)

    async def search_by_query(
        self, query: str, max_results: int = 10, min_confidence: float = 0.5,
    ) -> list[dict]:
        await self._ensure_ready()
        return await self._inner.search_by_query(query, max_results, min_confidence)

    async def get_decisions_for_file(self, file_path: str) -> list[dict]:
        await self._ensure_ready()
        return await self._inner.get_decisions_for_file(file_path)

    async def get_undocumented_symbols(self, file_path: str) -> list[str]:
        await self._ensure_ready()
        return await self._inner.get_undocumented_symbols(file_path)

    async def get_source_cursor(
        self, repo: str, source_type: str, source_scope: str = "default",
    ) -> dict | None:
        await self._ensure_ready()
        return await self._inner.get_source_cursor(repo, source_type, source_scope)

    # v0.4.12.1: pass-throughs for adapter methods added since v0.4.5 that
    # the team wrapper never gained. handle_link_commit / handle_reset call
    # these and silently degraded (or crashed) in team mode pre-v0.4.12.1.

    async def backfill_empty_hashes(
        self, repo_path: str, drift_analyzer=None,
    ) -> dict:
        """Self-heal regions with empty content_hash from pre-v0.4.5
        ingests. Pure local read+update — no event emitted."""
        await self._ensure_ready()
        return await self._inner.backfill_empty_hashes(
            repo_path, drift_analyzer=drift_analyzer,
        )

    async def get_all_source_cursors(self, repo: str) -> list[dict]:
        """List every source_cursor row for a repo. Used by handle_reset's
        dry-run summary. Pure local read."""
        await self._ensure_ready()
        return await self._inner.get_all_source_cursors(repo)

    async def wipe_all_rows(self, repo: str) -> None:
        """Wipe every bicameral row scoped to ``repo``. Used by
        handle_reset(confirm=True). Destructive, no event emitted —
        the reset itself is the event from the team's perspective.
        """
        await self._ensure_ready()
        await self._inner.wipe_all_rows(repo)
