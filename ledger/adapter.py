"""SurrealDBLedgerAdapter — real implementation replacing MockLedgerAdapter.

Implements the same interface as MockLedgerAdapter (see mocks/decision_ledger.py)
plus ingest_payload() for loading CodeLocatorPayload data into the graph.

Uses embedded SurrealDB via Python SDK (surrealdb>=1.0.0):
  - surrealkv://~/.bicameral/ledger.db  (persistent, default)
  - memory://                            (tests, no persistence)
  - ws://host:port                       (standalone server, optional)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .client import LedgerClient
from .queries import (
    get_all_decisions,
    get_decisions_for_file,
    get_regions_for_files,
    get_regions_without_hash,
    get_source_cursor,
    get_sync_state,
    get_undocumented_symbols,
    relate_implements,
    relate_maps_to,
    relate_yields,
    lookup_vocab_cache,
    search_by_bm25,
    update_intent_status,
    update_region_hash,
    upsert_source_cursor,
    upsert_vocab_cache,
    upsert_code_region,
    upsert_intent,
    upsert_source_span,
    upsert_symbol,
    upsert_sync_state,
)
from .schema import init_schema, migrate
from .status import compute_content_hash, derive_status, get_changed_files, resolve_head

logger = logging.getLogger(__name__)


def _default_db_url() -> str:
    """Persistent SurrealDB URL under ~/.bicameral/."""
    db_path = Path.home() / ".bicameral" / "ledger.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"surrealkv://{db_path}"


# Priority is "loudest wins" — any region drifting flags the whole intent as
# drifted so users see the alarm, even if other regions still reflect.
_STATUS_PRIORITY = {"drifted": 3, "reflected": 2, "pending": 1, "ungrounded": 0}


def _aggregate_intent_status(region_statuses: list[str]) -> str:
    """Collapse per-region statuses to a single intent status.

    drifted > reflected > pending > ungrounded. A multi-region intent is
    drifted if any of its regions drifted; reflected if all surviving
    regions reflect; pending if any region is waiting on code that doesn't
    exist yet; else ungrounded.
    """
    if not region_statuses:
        return "ungrounded"
    return max(region_statuses, key=lambda s: _STATUS_PRIORITY.get(s, -1))


class SurrealDBLedgerAdapter:
    """Real SurrealDB-backed ledger adapter.

    Drop-in replacement for MockLedgerAdapter. Wire it in adapters/ledger.py
    and set USE_REAL_LEDGER=1.

    The adapter lazy-connects on first use. Call connect() explicitly
    for setup or when you need schema init.
    """

    def __init__(
        self,
        url: str | None = None,
        ns: str = "bicameral",
        db: str = "ledger",
    ) -> None:
        self._url = url or os.getenv("SURREAL_URL", _default_db_url())
        self._client = LedgerClient(url=self._url, ns=ns, db=db)
        self._connected = False

    async def connect(self) -> None:
        """Connect, initialize schema, and run migrations (idempotent)."""
        if not self._connected:
            await self._client.connect()
            await init_schema(self._client)
            await migrate(self._client)
            self._connected = True
            logger.info("[ledger] SurrealDBLedgerAdapter ready at %s", self._url)

    async def _ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    # ── Core adapter interface (mirrors MockLedgerAdapter) ────────────────

    async def get_all_decisions(self, filter: str = "all") -> list[dict]:
        """Return all tracked decisions, optionally filtered by status."""
        await self._ensure_connected()
        return await get_all_decisions(self._client, filter=filter)

    async def search_by_query(
        self,
        query: str,
        max_results: int = 10,
        min_confidence: float = 0.5,
    ) -> list[dict]:
        """BM25 search on intent descriptions."""
        await self._ensure_connected()
        return await search_by_bm25(self._client, query, max_results, min_confidence)

    async def lookup_vocab_cache(
        self,
        query_text: str,
        repo: str,
    ) -> list[dict]:
        """Check vocab_cache for cached grounding results."""
        await self._ensure_connected()
        return await lookup_vocab_cache(self._client, query_text, repo)

    async def upsert_vocab_cache(
        self,
        query_text: str,
        repo: str,
        symbols: list[dict],
    ) -> None:
        """Cache grounded code_regions for a query in vocab_cache."""
        await self._ensure_connected()
        await upsert_vocab_cache(self._client, query_text, repo, symbols)

    async def get_decisions_for_file(self, file_path: str) -> list[dict]:
        """Reverse traversal: all decisions touching symbols in file_path."""
        await self._ensure_connected()
        return await get_decisions_for_file(self._client, file_path)

    async def get_undocumented_symbols(self, file_path: str) -> list[str]:
        """Symbols in file_path with no mapped intent."""
        await self._ensure_connected()
        return await get_undocumented_symbols(self._client, file_path)

    async def ingest_commit(self, commit_hash: str, repo_path: str, drift_analyzer=None) -> dict:
        """Heartbeat: sync a commit into the ledger, recompute affected statuses.

        Idempotent via ledger_sync cursor.
        Resolves 'HEAD' to the actual SHA before processing.

        Args:
            drift_analyzer: DriftAnalyzerPort implementation. If None, uses
                HashDriftAnalyzer (Layer 1 hash-only). Pass a different
                implementation for L2 (AST) or L3 (semantic) drift detection.
        """
        await self._ensure_connected()

        # Default to HashDriftAnalyzer (Layer 1) if no analyzer provided
        if drift_analyzer is None:
            from .drift import HashDriftAnalyzer
            drift_analyzer = HashDriftAnalyzer()

        # Resolve HEAD to actual SHA
        if commit_hash == "HEAD":
            resolved = resolve_head(repo_path)
            if resolved:
                commit_hash = resolved

        # Fast-path: already synced
        state = await get_sync_state(self._client, repo_path)
        if state and state.get("last_synced_commit") == commit_hash:
            return {
                "synced": True,
                "commit_hash": commit_hash,
                "reason": "already_synced",
                "regions_updated": 0,
                "decisions_reflected": 0,
                "decisions_drifted": 0,
                "undocumented_symbols": [],
            }

        # Get changed files from this commit
        changed_files = get_changed_files(commit_hash, repo_path)
        if not changed_files:
            await upsert_sync_state(self._client, repo_path, commit_hash)
            return {
                "synced": True,
                "commit_hash": commit_hash,
                "reason": "no_changes",
                "regions_updated": 0,
                "decisions_reflected": 0,
                "decisions_drifted": 0,
                "undocumented_symbols": [],
            }

        # Find all code_regions for changed files
        regions = await get_regions_for_files(self._client, changed_files)

        regions_updated = 0
        decisions_reflected = 0
        decisions_drifted = 0
        undocumented_symbols: list[str] = []

        for region in regions:
            region_id = region.get("region_id", "")
            file_path = region.get("file_path", "")
            symbol_name = region.get("symbol_name", "")
            start_line = region.get("start_line", 0)
            end_line = region.get("end_line", 0)
            stored_hash = region.get("content_hash", "")

            # Collect source context from linked intents for L3 drift analysis.
            # L1 (hash) ignores this; L3 (semantic) will use it to evaluate compliance.
            intent_descriptions = [
                i.get("description", "")
                for i in (region.get("intents") or [])
                if i and i.get("description")
            ]
            source_context = " | ".join(intent_descriptions)

            # Delegate drift analysis to the port implementation
            drift_result = await drift_analyzer.analyze_region(
                file_path=file_path,
                symbol_name=symbol_name,
                start_line=start_line,
                end_line=end_line,
                stored_hash=stored_hash,
                repo_path=repo_path,
                ref=commit_hash,
                source_context=source_context,
            )

            new_status = drift_result.status
            new_hash = drift_result.content_hash

            # Update the region's content_hash + pinned_commit
            await update_region_hash(self._client, region_id, new_hash, commit_hash)
            # If the analyzer resolved new line numbers (via symbol resolution),
            # detect and update them by re-resolving here for the ledger update.
            from .status import resolve_symbol_lines
            resolved = resolve_symbol_lines(file_path, symbol_name, repo_path, ref=commit_hash)
            if resolved and (resolved[0] != region.get("start_line") or resolved[1] != region.get("end_line")):
                await self._client.query(
                    "UPDATE $rid SET start_line = $sl, end_line = $el",
                    {"rid": region_id, "sl": resolved[0], "el": resolved[1]},
                )
            regions_updated += 1

            # Update all intents mapped to this region
            for intent in (region.get("intents") or []):
                if intent is None:
                    continue
                intent_id = str(intent.get("id", ""))
                if not intent_id:
                    continue
                old_status = intent.get("status", "ungrounded")
                await update_intent_status(self._client, intent_id, new_status)
                if new_status == "reflected" and old_status != "reflected":
                    decisions_reflected += 1
                elif new_status == "drifted" and old_status != "drifted":
                    decisions_drifted += 1

            # Flag as undocumented if no intents mapped
            intents = [i for i in (region.get("intents") or []) if i is not None]
            if not intents and symbol_name:
                undocumented_symbols.append(symbol_name)

        await upsert_sync_state(self._client, repo_path, commit_hash)

        return {
            "synced": True,
            "commit_hash": commit_hash,
            "reason": "new_commit",
            "regions_updated": regions_updated,
            "decisions_reflected": decisions_reflected,
            "decisions_drifted": decisions_drifted,
            "undocumented_symbols": list(set(undocumented_symbols)),
        }

    async def backfill_empty_hashes(
        self,
        repo_path: str,
        drift_analyzer=None,
    ) -> dict:
        """Self-heal pre-v0.4.5 regions that were persisted with an empty
        content_hash. Walks every code_region for ``repo_path`` that has no
        stored hash, runs the configured drift analyzer (which, for
        HashDriftAnalyzer, adopts the current source as the baseline and
        returns reflected), and updates the region + its linked intents.

        Idempotent and scoped: regions already carrying a hash are ignored,
        and regions belonging to other repos are left alone. Safe to call
        on every link_commit — once every region is stamped, the query
        returns an empty set and the sweep is a no-op.
        """
        await self._ensure_connected()

        if drift_analyzer is None:
            from .drift import HashDriftAnalyzer
            drift_analyzer = HashDriftAnalyzer()

        legacy = await get_regions_without_hash(self._client, repo=repo_path)
        if not legacy:
            return {"healed": 0, "failed": 0}

        healed = 0
        failed = 0
        # Use HEAD as the backfill ref — that's "what the code looks like now,"
        # which is the only meaningful baseline when no prior hash exists.
        ref = resolve_head(repo_path) or "HEAD"

        for region in legacy:
            region_id = region.get("region_id", "")
            file_path = region.get("file_path", "")
            symbol_name = region.get("symbol_name", "")
            start_line = region.get("start_line", 0)
            end_line = region.get("end_line", 0)
            if not region_id or not file_path or not symbol_name:
                failed += 1
                continue

            drift_result = await drift_analyzer.analyze_region(
                file_path=file_path,
                symbol_name=symbol_name,
                start_line=start_line,
                end_line=end_line,
                stored_hash="",
                repo_path=repo_path,
                ref=ref,
                source_context="",
            )

            # Only persist heals that produced a real baseline. If compute
            # failed (file/range missing at ref), we leave the region alone
            # so a future code move can still find it.
            if not drift_result.content_hash:
                failed += 1
                continue

            await update_region_hash(self._client, region_id, drift_result.content_hash, ref)
            new_status = drift_result.status
            for intent in (region.get("intents") or []):
                if intent is None:
                    continue
                intent_id = str(intent.get("id", ""))
                if intent_id:
                    await update_intent_status(self._client, intent_id, new_status)
            healed += 1

        if healed or failed:
            logger.info(
                "[backfill] repo=%s healed=%d failed=%d",
                repo_path, healed, failed,
            )
        return {"healed": healed, "failed": failed}

    # ── Extended: ingestion of CodeLocatorPayload ─────────────────────────

    async def ingest_payload(self, payload: dict) -> dict:
        """Ingest a CodeLocatorPayload dict into the graph.

        Creates intent, symbol, code_region nodes and maps_to / implements edges.
        Used by integration tests and the future /ingest MCP tool.
        """
        await self._ensure_connected()

        repo = payload.get("repo", "")
        commit_hash = payload.get("commit_hash", "")
        # Resolve HEAD once per ingest so every region hashes against the
        # same baseline ref. Without this, bulk ingests that don't carry a
        # commit_hash stamped empty hashes and decisions were born pending.
        effective_ref = commit_hash or resolve_head(repo) or "HEAD"
        intents_created = 0
        symbols_mapped = 0
        regions_linked = 0
        ungrounded = []
        region_ids: list[str] = []

        for mapping in payload.get("mappings", []):
            span = mapping.get("span", {})
            description = mapping.get("intent", span.get("text", ""))
            source_ref = span.get("source_ref", payload.get("query", ""))
            source_type = span.get("source_type", "manual")
            span_text = span.get("text", description)

            code_regions = mapping.get("code_regions", [])
            initial_status = "ungrounded" if not code_regions else "pending"

            # Create source_span node (raw text from meeting/PRD/Slack)
            span_id = await upsert_source_span(
                self._client,
                text=span_text,
                source_type=source_type,
                source_ref=source_ref,
                speakers=span.get("speakers", []),
                meeting_date=span.get("meeting_date", ""),
            )

            # Create intent node
            intent_id = await upsert_intent(
                self._client,
                description=description,
                source_type=source_type,
                source_ref=source_ref,
                status=initial_status,
            )
            intents_created += 1

            if not intent_id:
                logger.warning("[ingest] failed to create intent for: %s", description[:60])
                continue

            # Link source_span → yields → intent
            if span_id and intent_id:
                await relate_yields(self._client, span_id, intent_id)

            if not code_regions:
                ungrounded.append(description)
                continue

            # Track per-region derived status so we can aggregate up to the intent.
            region_statuses: list[str] = []

            for region_data in code_regions:
                symbol_name = region_data.get("symbol", "")
                file_path = region_data.get("file_path", "")

                if not symbol_name or not file_path:
                    continue

                # Compute content hash at the effective ref. Always — no gate.
                # When repo is unset or the file/range isn't in git, this
                # returns None and the region stays pending via derive_status.
                start_line = region_data.get("start_line", 0)
                end_line = region_data.get("end_line", 0)
                content_hash = ""
                if repo:
                    content_hash = compute_content_hash(
                        file_path, start_line, end_line, repo, ref=effective_ref
                    ) or ""

                # Create / update symbol node
                symbol_id = await upsert_symbol(
                    self._client,
                    name=symbol_name,
                    file_path=file_path,
                    sym_type=region_data.get("type", "function"),
                )
                if not symbol_id:
                    continue
                symbols_mapped += 1

                # Create / update code_region node
                region_id = await upsert_code_region(
                    self._client,
                    file_path=file_path,
                    symbol_name=symbol_name,
                    start_line=start_line,
                    end_line=end_line,
                    purpose=region_data.get("purpose", ""),
                    repo=repo,
                    content_hash=content_hash,
                )
                if not region_id:
                    continue
                regions_linked += 1
                region_ids.append(region_id)

                # Baseline == actual at ingest time, so derive_status returns
                # "reflected" when the hash was computable, "ungrounded" when
                # the file/range isn't in git yet.
                region_statuses.append(
                    derive_status(content_hash, content_hash if content_hash else None)
                )

                # intent → symbol → code_region edges
                provenance = {}
                grounding_tier = region_data.get("grounding_tier")
                if grounding_tier is not None:
                    provenance["grounding_tier"] = grounding_tier
                    provenance["method"] = "auto_ground"
                await relate_maps_to(
                    self._client, intent_id, symbol_id, provenance=provenance,
                )
                await relate_implements(self._client, symbol_id, region_id)

            # Aggregate region statuses up to the intent. An intent is
            # drifted if any region drifted; else reflected if any reflect;
            # else pending if any pending; else ungrounded (all regions
            # failed to link or no region could be hashed against HEAD).
            if intent_id:
                aggregated = _aggregate_intent_status(region_statuses)
                await update_intent_status(self._client, intent_id, aggregated)

        return {
            "ingested": True,
            "repo": repo,
            "stats": {
                "intents_created": intents_created,
                "symbols_mapped": symbols_mapped,
                "regions_linked": regions_linked,
                "ungrounded": len(ungrounded),
            },
            "ungrounded_intents": ungrounded,
            "region_ids": region_ids,
        }

    async def get_source_cursor(
        self,
        repo: str,
        source_type: str,
        source_scope: str = "default",
    ) -> dict | None:
        await self._ensure_connected()
        return await get_source_cursor(self._client, repo, source_type, source_scope)

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
        await self._ensure_connected()
        return await upsert_source_cursor(
            self._client,
            repo=repo,
            source_type=source_type,
            source_scope=source_scope,
            cursor=cursor,
            last_source_ref=last_source_ref,
            status=status,
            error=error,
        )
