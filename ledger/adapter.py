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
from .status import (
    compute_content_hash,
    derive_status,
    get_changed_files,
    get_changed_files_in_range,
    resolve_head,
)


# v0.4.11: cap for range sweep. If the diff between last_synced and HEAD
# spans more files than this, we sweep the first MAX_SWEEP_FILES and report
# `sweep_scope="range_truncated"` so the caller knows the sweep was partial.
# The next link_commit will pick up the remainder.
_MAX_SWEEP_FILES = 200

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
    ) -> tuple[list[dict], str]:
        """Check vocab_cache for cached grounding results.

        Returns ``(symbols, matched_query_text)``. The matched query text
        is needed by callers to run the FC-3 similarity gate before
        deciding whether to reuse the cached symbols.
        """
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

    async def ingest_commit(
        self,
        commit_hash: str,
        repo_path: str,
        drift_analyzer=None,
        authoritative_ref: str = "",
    ) -> dict:
        """Heartbeat: sync a commit into the ledger, recompute affected statuses.

        Idempotent via ledger_sync cursor.
        Resolves 'HEAD' to the actual SHA before processing.

        Args:
            drift_analyzer: DriftAnalyzerPort implementation. If None, uses
                HashDriftAnalyzer (Layer 1 hash-only). Pass a different
                implementation for L2 (AST) or L3 (semantic) drift detection.
            authoritative_ref: Name of the authoritative branch (usually
                "main"). When provided AND the repo's current branch does
                not match, the sync runs in READ-ONLY mode — drift is
                computed for reporting but baseline hashes are NOT
                persisted. This closes the Bug 1 silent pollution path
                where link_commit HEAD on a feature branch would adopt
                branch state as the baseline.

                Branch-name comparison (not SHA comparison) is used so
                normal commits that advance the authoritative branch
                still write as expected.
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

        # Pollution guard: refuse baseline writes unless the repo's current
        # branch matches the authoritative ref. Branch-name comparison is
        # stable across normal commits (main advances, still "main") but
        # catches feature-branch work.
        is_authoritative = True
        if authoritative_ref:
            import subprocess
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                current_branch = result.stdout.strip() if result.returncode == 0 else ""
            except (subprocess.TimeoutExpired, FileNotFoundError):
                current_branch = ""
            if current_branch and current_branch != "HEAD" and current_branch != authoritative_ref:
                is_authoritative = False
                logger.info(
                    "[link_commit] current branch %s != authoritative %s — "
                    "running in read-only mode (no baseline writes)",
                    current_branch, authoritative_ref,
                )

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
                "sweep_scope": "head_only",
                "range_size": 0,
            }

        # v0.4.11: determine sweep scope. The pre-v0.4.11 behavior was always
        # head-only (`git show HEAD --name-only`), which missed every file
        # drifted between last_synced and HEAD. Now the default is range_diff
        # — sweep every file touched since the cursor — falling back to
        # head_only when there's no cursor or the range is unreachable.
        last_synced = (state or {}).get("last_synced_commit", "") or ""
        sweep_scope: str = "head_only"
        changed_files: list[str] = []

        if last_synced and last_synced != commit_hash:
            range_files = get_changed_files_in_range(
                last_synced, commit_hash, repo_path,
            )
            if range_files is None:
                # Range unreachable (force-push, shallow clone, rebase
                # discarded the base). Fall back to head-only — partial
                # but better than crashing. The next sync after a real
                # commit will recover.
                logger.warning(
                    "[link_commit] range %s..%s unreachable, falling "
                    "back to head-only sweep",
                    last_synced[:8], commit_hash[:8],
                )
                changed_files = get_changed_files(commit_hash, repo_path)
                sweep_scope = "head_only"
            else:
                changed_files = range_files
                sweep_scope = "range_diff"
                if len(changed_files) > _MAX_SWEEP_FILES:
                    logger.warning(
                        "[link_commit] range sweep capped at %d files "
                        "(would have swept %d). Remainder will catch up "
                        "on next sync.",
                        _MAX_SWEEP_FILES, len(changed_files),
                    )
                    changed_files = changed_files[:_MAX_SWEEP_FILES]
                    sweep_scope = "range_truncated"
        else:
            # First-ever sync (no cursor) OR same SHA as cursor.
            # Head-only is the right scope here.
            changed_files = get_changed_files(commit_hash, repo_path)
            sweep_scope = "head_only"

        range_size = len(changed_files)

        if not changed_files:
            # Only advance the sync cursor on authoritative refs — pollution guard
            if is_authoritative:
                await upsert_sync_state(self._client, repo_path, commit_hash)
            return {
                "synced": True,
                "commit_hash": commit_hash,
                "reason": "no_changes",
                "regions_updated": 0,
                "decisions_reflected": 0,
                "decisions_drifted": 0,
                "undocumented_symbols": [],
                "sweep_scope": sweep_scope,
                "range_size": 0,
            }

        # Find all code_regions for changed files
        regions = await get_regions_for_files(self._client, changed_files)

        regions_updated = 0
        # v0.4.11: track distinct intent_ids that flipped, not (region, intent)
        # pairs. A decision with N regions all flipping in the same sweep
        # used to inflate the counter N times — now it's counted once. Matches
        # what users expect from "how many decisions just changed status."
        flipped_to_reflected: set[str] = set()
        flipped_to_drifted: set[str] = set()
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

            # Pollution guard: only persist baseline writes when the
            # caller's ref matches the authoritative ref. Non-authoritative
            # refs produce drift reports (accumulated in the counters below)
            # but do NOT touch stored hashes or intent statuses.
            if is_authoritative:
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

            # Update all intents mapped to this region (also pollution-guarded)
            for intent in (region.get("intents") or []):
                if intent is None:
                    continue
                intent_id = str(intent.get("id", ""))
                if not intent_id:
                    continue
                old_status = intent.get("status", "ungrounded")
                if is_authoritative:
                    await update_intent_status(self._client, intent_id, new_status)
                # v0.4.11: dedupe by intent_id. A decision with multiple
                # regions all flipping in the same sweep is one flipped
                # decision, not N. Sets collapse the duplicates.
                if new_status == "reflected" and old_status != "reflected":
                    flipped_to_reflected.add(intent_id)
                elif new_status == "drifted" and old_status != "drifted":
                    flipped_to_drifted.add(intent_id)

            # Flag as undocumented if no intents mapped
            intents = [i for i in (region.get("intents") or []) if i is not None]
            if not intents and symbol_name:
                undocumented_symbols.append(symbol_name)

        # Only persist sync state on authoritative refs — otherwise the
        # cursor would advance to a branch SHA and the next authoritative
        # sync would be incorrectly skipped by the fast-path.
        if is_authoritative:
            await upsert_sync_state(self._client, repo_path, commit_hash)

        return {
            "synced": True,
            "commit_hash": commit_hash,
            "reason": "new_commit",
            "regions_updated": regions_updated,
            "decisions_reflected": len(flipped_to_reflected),
            "decisions_drifted": len(flipped_to_drifted),
            "undocumented_symbols": list(set(undocumented_symbols)),
            "sweep_scope": sweep_scope,
            "range_size": range_size,
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

    async def ingest_payload(self, payload: dict, ctx=None) -> dict:
        """Ingest a CodeLocatorPayload dict into the graph.

        Creates intent, symbol, code_region nodes and maps_to / implements edges.
        Used by integration tests and the future /ingest MCP tool.

        Args:
            payload: The CodeLocatorPayload dict.
            ctx: Optional BicameralContext. When provided, the ingest
                stamps baseline hashes against ``ctx.authoritative_sha``
                rather than the current HEAD. Closes Bug 3 — ingesting
                from a feature branch no longer pollutes the ledger with
                branch-local baselines. See v0.4.6 release plan.
        """
        await self._ensure_connected()

        repo = payload.get("repo", "")
        commit_hash = payload.get("commit_hash", "")
        # Pollution guard (v0.4.6, Bug 3 fix):
        # Prefer the authoritative ref from ctx over HEAD. This keeps the
        # ledger branch-independent — fresh ingests from a feature branch
        # stamp baseline hashes against main, so switching back to main
        # doesn't make every new decision look drifted.
        authoritative_sha = getattr(ctx, "authoritative_sha", "") if ctx is not None else ""
        effective_ref = commit_hash or authoritative_sha or resolve_head(repo) or "HEAD"
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
                meeting_date=span.get("meeting_date", ""),
                speakers=span.get("speakers", []),
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

    async def get_all_source_cursors(self, repo: str) -> list[dict]:
        """Return every source_cursor row scoped to ``repo``.

        Used by ``bicameral_reset`` to build a replay plan before wiping
        the ledger. Multi-repo SurrealDB instances stay isolated because
        the filter is on the ``repo`` column.
        """
        await self._ensure_connected()
        rows = await self._client.query(
            "SELECT * FROM source_cursor WHERE repo = $repo",
            {"repo": repo},
        )
        if not rows:
            return []
        # Normalize the synced_at datetime the same way get_source_cursor does
        out: list[dict] = []
        for row in rows:
            row["synced_at"] = str(row.get("synced_at", ""))
            out.append(row)
        return out

    async def wipe_all_rows(self, repo: str) -> None:
        """Delete every row belonging to ``repo`` across every bicameral
        table, while leaving other repos in the same SurrealDB instance
        untouched.

        Scoping strategy:
          - Tables with a ``repo`` field (code_region, source_cursor,
            vocab_cache, ledger_sync) — filter by the column directly.
          - Tables without a ``repo`` field (intent, source_span) — find
            their IDs via graph traversal from code_region, then delete
            by id. An intent "belongs to" a repo if any of its maps_to
            symbols implement a code_region in that repo. A source_span
            belongs to a repo if it yields an intent in that repo.
          - Edge tables (maps_to, implements, yields) — left alone. Once
            their endpoints are gone, the edges are orphaned and harmless.
          - ``symbol`` — left alone. Symbols are shared across repos.

        Used by the ``bicameral_reset`` fail-safe valve.
        """
        await self._ensure_connected()

        # 1. Gather intent IDs belonging to this repo. Two independent
        # strategies, merged — each catches intents the other misses:
        #   a) Graph traversal via code_region.repo → symbol → intent
        #      (catches grounded intents with at least one code region)
        #   b) source_ref matching via source_cursor audit log
        #      (catches ungrounded intents that never got a code_region)
        intent_ids: set[str] = set()

        # (a) Graph traversal from code_regions belonging to this repo.
        try:
            rows = await self._client.query(
                """
                SELECT <-implements<-symbol<-maps_to<-intent AS intents
                FROM code_region
                WHERE repo = $repo
                """,
                {"repo": repo},
            )
            for row in rows or []:
                intents_field = row.get("intents") or []
                if isinstance(intents_field, list):
                    for nested in intents_field:
                        if isinstance(nested, list):
                            for item in nested:
                                if item:
                                    intent_ids.add(str(item))
                        elif nested:
                            intent_ids.add(str(nested))
        except Exception as exc:
            logger.warning("[wipe_all_rows] code_region → intent traversal failed: %s", exc)

        # (b) source_cursor audit-log matching for ungrounded intents.
        try:
            cursor_rows = await self._client.query(
                "SELECT source_type, source_scope, last_source_ref FROM source_cursor WHERE repo = $repo",
                {"repo": repo},
            )
            for c in cursor_rows or []:
                src_ref = c.get("last_source_ref", "")
                src_type = c.get("source_type", "")
                if not src_ref or not src_type:
                    continue
                matching = await self._client.query(
                    "SELECT type::string(id) AS id FROM intent WHERE source_ref = $r AND source_type = $t",
                    {"r": src_ref, "t": src_type},
                )
                for m in matching or []:
                    if m.get("id"):
                        intent_ids.add(str(m["id"]))
        except Exception as exc:
            logger.warning("[wipe_all_rows] source_cursor → intent matching failed: %s", exc)

        # 2. Gather source_span IDs yielding those intents.
        source_span_ids: set[str] = set()
        if intent_ids:
            try:
                # For each intent, find source_spans pointing at it via yields
                rows = await self._client.query(
                    "SELECT type::string(in) AS in FROM yields",
                )
                # Collect all yields edges, filter to those whose target is
                # in our intent set. Couldn't get a CONTAINS filter working
                # cleanly against a string-of-record-id field, so filter in
                # Python — the yields table is small per repo.
                for row in rows or []:
                    _in = row.get("in")
                    if _in:
                        source_span_ids.add(str(_in))
            except Exception as exc:
                logger.debug("[wipe_all_rows] source_span traversal failed: %s", exc)

        # 3. Delete scoped-by-column tables.
        for table in ("code_region", "source_cursor", "vocab_cache"):
            try:
                await self._client.execute(
                    f"DELETE FROM {table} WHERE repo = $repo",
                    {"repo": repo},
                )
            except Exception as exc:
                logger.warning("[wipe_all_rows] %s scoped delete failed: %s", table, exc)

        # 4. Delete the enumerated intents by id.
        for intent_id in intent_ids:
            try:
                await self._client.execute(f"DELETE {intent_id}")
            except Exception as exc:
                logger.debug("[wipe_all_rows] intent %s delete failed: %s", intent_id, exc)

        # 5. Delete the enumerated source_spans by id. (Best-effort — the
        # yields traversal is approximate.)
        for span_id in source_span_ids:
            try:
                await self._client.execute(f"DELETE {span_id}")
            except Exception as exc:
                logger.debug("[wipe_all_rows] source_span %s delete failed: %s", span_id, exc)

        # 6. ledger_sync is per-repo — wipe this repo's rows.
        try:
            await self._client.execute(
                "DELETE FROM ledger_sync WHERE repo = $repo",
                {"repo": repo},
            )
        except Exception as exc:
            logger.warning("[wipe_all_rows] ledger_sync delete failed: %s", exc)
