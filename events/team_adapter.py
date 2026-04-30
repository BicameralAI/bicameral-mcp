"""TeamWriteAdapter — dual-write adapter for team collaboration mode.

Wraps SurrealDBLedgerAdapter via composition. On every write operation,
emits an event file first, then delegates to the inner adapter.
All read operations pass through directly.
"""

from __future__ import annotations

import logging

from ledger.queries import find_decision_by_canonical_id, get_canonical_id

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

    async def bind_decision(
        self,
        decision_id: str,
        file_path: str,
        symbol_name: str,
        start_line: int,
        end_line: int,
        repo: str = "",
        ref: str = "HEAD",
        purpose: str = "",
    ) -> dict:
        """Emit bind event, then delegate to inner adapter."""
        await self._ensure_ready()
        self._writer.write(
            "bind_decision.completed",
            {
                "decision_id": decision_id,
                "file_path": file_path,
                "symbol_name": symbol_name,
                "start_line": start_line,
                "end_line": end_line,
            },
        )
        return await self._inner.bind_decision(
            decision_id=decision_id,
            file_path=file_path,
            symbol_name=symbol_name,
            start_line=start_line,
            end_line=end_line,
            repo=repo,
            ref=ref,
            purpose=purpose,
        )

    async def apply_ratify(self, decision_id: str, signoff: dict) -> str:
        """Emit decision_ratified event, then delegate to inner adapter.

        The event payload carries ``canonical_id`` so cross-author replay
        can resolve to the peer's local decision row.
        """
        await self._ensure_ready()
        canonical_id = await get_canonical_id(self._inner._client, decision_id)
        self._writer.write(
            "decision_ratified.completed",
            {
                "canonical_id": canonical_id,
                "decision_id": decision_id,
                "signoff": signoff,
            },
        )
        return await self._inner.apply_ratify(decision_id, signoff)

    async def apply_supersede(
        self,
        new_id: str,
        old_id: str,
        signer: str = "",
        signoff_note: str = "",
        superseded_at: str = "",
        session_id: str = "",
    ) -> dict:
        """Emit decision_superseded event, then delegate to inner adapter.

        The event payload carries canonical_ids for both decisions so
        cross-author replay can resolve to the peer's local rows.
        """
        await self._ensure_ready()
        new_canonical = await get_canonical_id(self._inner._client, new_id)
        old_canonical = await get_canonical_id(self._inner._client, old_id)
        self._writer.write(
            "decision_superseded.completed",
            {
                "new_canonical_id": new_canonical,
                "old_canonical_id": old_canonical,
                "new_id": new_id,
                "old_id": old_id,
                "signer": signer,
                "signoff_note": signoff_note,
                "superseded_at": superseded_at,
                "session_id": session_id,
            },
        )
        return await self._inner.apply_supersede(
            new_id=new_id,
            old_id=old_id,
            signer=signer,
            signoff_note=signoff_note,
            superseded_at=superseded_at,
            session_id=session_id,
        )

    async def wipe_all_rows(self, repo: str) -> None:
        """Wipe the DB then reset the event watermark.

        The event files themselves (.bicameral/events/{email}.jsonl) are the
        source of truth and are NOT deleted. Resetting the watermark causes
        the next connect() to re-materialize from all peer events, giving a
        clean DB that reflects current peer state.
        """
        await self._ensure_ready()
        await self._inner.wipe_all_rows(repo)
        self._materializer._watermark_path.write_text("{}", encoding="utf-8")
        logger.info("[team_reset] DB wiped and watermark reset for repo=%s", repo)

    def __getattr__(self, name: str):
        """Passthrough to inner adapter for any method not explicitly overridden."""
        return getattr(self._inner, name)
