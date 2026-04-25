"""SurrealDBLedgerAdapter — v4 (v0.5.0 decision-tier refactor).

Graph shape:
  Decision tier:  input_span -yields-> decision -binds_to-> code_region
  Retrieval tier: symbol -locates-> code_region

Uses embedded SurrealDB via Python SDK (surrealdb>=1.0.0).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .client import LedgerClient
from .queries import (
    decision_exists,
    delete_binds_to_edge,
    get_all_decisions,
    get_compliance_verdict,
    get_decisions_for_file,
    get_decisions_for_files,
    get_regions_for_files,
    get_regions_without_hash,
    get_source_cursor,
    get_sync_state,
    get_undocumented_symbols,
    lookup_vocab_cache,
    project_decision_status,
    region_exists,
    relate_binds_to,
    relate_locates,
    relate_yields,
    search_by_bm25,
    update_decision_status,
    update_region_hash,
    upsert_code_region,
    upsert_decision,
    upsert_input_span,
    upsert_source_cursor,
    upsert_symbol,
    upsert_sync_state,
    upsert_vocab_cache,
)
from .schema import DestructiveMigrationRequired, init_schema, migrate
from .status import (
    compute_content_hash,
    derive_status,
    get_changed_files,
    get_changed_files_in_range,
    get_git_content,
    resolve_head,
)


_CODE_BODY_LINE_CAP = 200


def _extract_code_body(
    file_path: str,
    start_line: int,
    end_line: int,
    repo_path: str,
    ref: str,
) -> str:
    content = get_git_content(file_path, start_line, end_line, repo_path, ref=ref)
    if content is None:
        return ""
    lines = content.splitlines()
    s = max(0, start_line - 1)
    e = min(len(lines), end_line)
    body = lines[s:e]
    if len(body) > _CODE_BODY_LINE_CAP:
        truncated = len(body) - _CODE_BODY_LINE_CAP
        body = body[:_CODE_BODY_LINE_CAP] + [f"... ({truncated} more lines truncated)"]
    return "\n".join(body)


_MAX_SWEEP_FILES = 200

logger = logging.getLogger(__name__)


def _default_db_url() -> str:
    db_path = Path.home() / ".bicameral" / "ledger.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"surrealkv://{db_path}"


_STATUS_PRIORITY = {"drifted": 3, "reflected": 2, "pending": 1, "ungrounded": 0}


def _aggregate_decision_status(region_statuses: list[str]) -> str:
    """Collapse per-region statuses to a single decision status.

    drifted > reflected > pending > ungrounded.
    """
    if not region_statuses:
        return "ungrounded"
    return max(region_statuses, key=lambda s: _STATUS_PRIORITY.get(s, -1))


class SurrealDBLedgerAdapter:
    """Real SurrealDB-backed ledger adapter — v4 graph shape."""

    def __init__(
        self,
        url: str | None = None,
        ns: str = "bicameral",
        db: str = "ledger",
    ) -> None:
        self._url = url or os.getenv("SURREAL_URL", _default_db_url())
        self._client = LedgerClient(url=self._url, ns=ns, db=db)
        self._connected = False
        self._pending_destructive: DestructiveMigrationRequired | None = None

    async def connect(self) -> None:
        """Connect, initialize schema, and run migrations (idempotent).

        If a destructive migration is pending, stores it on _pending_destructive
        and marks _connected=True (partial connect). All tool calls will then
        surface the error until the user runs bicameral_reset(confirm=True).
        SchemaVersionTooNew propagates immediately — caller must upgrade binary.
        """
        if not self._connected:
            await self._client.connect()
            await init_schema(self._client)
            # memory:// has no persisted data — always allow destructive migrations.
            _allow_destructive = self._url.startswith("memory://")
            try:
                await migrate(self._client, allow_destructive=_allow_destructive)
            except DestructiveMigrationRequired as exc:
                self._pending_destructive = exc
                self._connected = True
                logger.warning("[ledger] destructive migration pending: %s", exc)
                return
            self._connected = True
            logger.info("[ledger] SurrealDBLedgerAdapter ready at %s", self._url)

    async def _ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()
        if self._pending_destructive is not None:
            raise self._pending_destructive

    async def force_migrate(self) -> None:
        """Apply all pending migrations including destructive ones.

        Called by handle_reset after confirm=True to unblock the partial connect.
        """
        await migrate(self._client, allow_destructive=True)
        self._pending_destructive = None
        logger.info("[ledger] force_migrate: all pending migrations applied")

    # ── Core adapter interface ────────────────────────────────────────────

    async def get_all_decisions(self, filter: str = "all") -> list[dict]:
        await self._ensure_connected()
        return await get_all_decisions(self._client, filter=filter)

    async def search_by_query(
        self,
        query: str,
        max_results: int = 10,
        min_confidence: float = 0.5,
    ) -> list[dict]:
        await self._ensure_connected()
        return await search_by_bm25(self._client, query, max_results, min_confidence)

    async def decision_exists(self, decision_id: str) -> bool:
        await self._ensure_connected()
        from .queries import decision_exists
        return await decision_exists(self._client, decision_id)

    async def get_decision_description(self, decision_id: str) -> str:
        await self._ensure_connected()
        rows = await self._client.query(f"SELECT description FROM {decision_id} LIMIT 1")
        return str((rows or [{}])[0].get("description", "")) if rows else ""

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
        """Upsert code_region + binds_to edge for a caller-LLM-supplied binding.

        Returns {"region_id": str, "content_hash": str}.
        Transitions decision status ungrounded → pending.
        Uses authoritative ref (not raw HEAD) to avoid branch-pollution.
        """
        await self._ensure_connected()
        content_hash = compute_content_hash(file_path, start_line, end_line, repo, ref=ref) or ""

        region_id = await upsert_code_region(
            self._client,
            file_path=file_path,
            symbol_name=symbol_name,
            start_line=start_line,
            end_line=end_line,
            purpose=purpose,
            repo=repo,
            content_hash=content_hash,
        )
        if not region_id:
            raise ValueError(f"upsert_code_region returned empty id for {file_path}:{symbol_name}")

        await relate_binds_to(
            self._client, decision_id, region_id,
            confidence=0.95,
            provenance={"method": "caller_llm"},
        )
        await update_decision_status(self._client, decision_id, "pending")

        return {"region_id": region_id, "content_hash": content_hash}

    async def lookup_vocab_cache(
        self,
        query_text: str,
        repo: str,
    ) -> tuple[list[dict], str]:
        await self._ensure_connected()
        return await lookup_vocab_cache(self._client, query_text, repo)

    async def upsert_vocab_cache(
        self,
        query_text: str,
        repo: str,
        symbols: list[dict],
    ) -> None:
        await self._ensure_connected()
        await upsert_vocab_cache(self._client, query_text, repo, symbols)

    async def get_decisions_for_file(self, file_path: str) -> list[dict]:
        await self._ensure_connected()
        return await get_decisions_for_file(self._client, file_path)

    async def get_decisions_for_files(self, file_paths: list[str]) -> list[dict]:
        await self._ensure_connected()
        return await get_decisions_for_files(self._client, file_paths)

    async def get_undocumented_symbols(self, file_path: str) -> list[str]:
        await self._ensure_connected()
        return await get_undocumented_symbols(self._client, file_path)

    async def get_decisions_by_status(self, statuses: list[str]) -> list[dict]:
        """Return all decisions whose current status is in ``statuses``.

        Used by the session-start banner to surface drifted items at the
        beginning of each MCP server session. Statuses are internal enum
        values ("reflected", "drifted", "pending", "ungrounded") — safe
        to interpolate directly into the query.
        """
        if not statuses:
            return []
        await self._ensure_connected()
        conditions = " OR ".join(f"status = '{s}'" for s in statuses)
        query = (
            f"SELECT decision_id, description, status, source_ref, meeting_date "
            f"FROM decision WHERE {conditions} LIMIT 50"
        )
        result = await self._client.query(query)
        return result if result else []

    async def ingest_commit(
        self,
        commit_hash: str,
        repo_path: str,
        drift_analyzer=None,
        authoritative_ref: str = "",
    ) -> dict:
        """Heartbeat: sync a commit into the ledger, recompute affected statuses.

        Idempotent via ledger_sync cursor. Resolves 'HEAD' to actual SHA.
        Uses project_decision_status for holistic aggregation (v0.5.0).
        """
        await self._ensure_connected()

        if drift_analyzer is None:
            from .drift import HashDriftAnalyzer
            drift_analyzer = HashDriftAnalyzer()

        if commit_hash == "HEAD":
            resolved = resolve_head(repo_path)
            if resolved:
                commit_hash = resolved

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

        last_synced = (state or {}).get("last_synced_commit", "") or ""
        sweep_scope: str = "head_only"
        changed_files: list[str] = []

        if last_synced and last_synced != commit_hash:
            range_files = get_changed_files_in_range(last_synced, commit_hash, repo_path)
            if range_files is None:
                logger.warning(
                    "[link_commit] range %s..%s unreachable, falling back to head-only sweep",
                    last_synced[:8], commit_hash[:8],
                )
                changed_files = get_changed_files(commit_hash, repo_path)
                sweep_scope = "head_only"
            else:
                changed_files = range_files
                sweep_scope = "range_diff"
                if len(changed_files) > _MAX_SWEEP_FILES:
                    logger.warning(
                        "[link_commit] range sweep capped at %d files (would have swept %d).",
                        _MAX_SWEEP_FILES, len(changed_files),
                    )
                    changed_files = changed_files[:_MAX_SWEEP_FILES]
                    sweep_scope = "range_truncated"
        else:
            changed_files = get_changed_files(commit_hash, repo_path)
            sweep_scope = "head_only"

        range_size = len(changed_files)

        if not changed_files:
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

        regions = await get_regions_for_files(self._client, changed_files)

        regions_updated = 0
        flipped_to_reflected: set[str] = set()
        flipped_to_drifted: set[str] = set()
        undocumented_symbols: list[str] = []
        pending_checks: list[dict] = []
        pending_grounding_checks: list[dict] = []

        for region in regions:
            region_id = region.get("region_id", "")
            file_path = region.get("file_path", "")
            symbol_name = region.get("symbol_name", "")
            start_line = region.get("start_line", 0)
            end_line = region.get("end_line", 0)
            stored_hash = region.get("content_hash", "")

            decision_descriptions = [
                i.get("description", "")
                for i in (region.get("decisions") or [])
                if i and i.get("description")
            ]
            source_context = " | ".join(decision_descriptions)

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

            actual_hash = drift_result.content_hash

            # Check if symbol has disappeared from the new commit.
            symbol_disappeared = False
            if is_authoritative:
                await update_region_hash(self._client, region_id, actual_hash, commit_hash)
                from .status import resolve_symbol_lines
                resolved = resolve_symbol_lines(file_path, symbol_name, repo_path, ref=commit_hash)
                if resolved is None:
                    symbol_disappeared = True
                elif resolved[0] != region.get("start_line") or resolved[1] != region.get("end_line"):
                    await self._client.query(
                        f"UPDATE {region_id} SET start_line = $sl, end_line = $el",
                        {"sl": resolved[0], "el": resolved[1]},
                    )
            regions_updated += 1

            region_code_body: str | None = None
            phase = "ingest" if not stored_hash else "drift"

            # v0.5.0: decisions are accessed via binds_to (renamed from intents via maps_to)
            for decision in (region.get("decisions") or []):
                if decision is None:
                    continue
                decision_id = str(decision.get("id", ""))
                if not decision_id:
                    continue
                old_status = decision.get("status", "ungrounded")

                # If symbol disappeared, emit a grounding check instead of compliance check.
                if symbol_disappeared:
                    pending_grounding_checks.append({
                        "decision_id": decision_id,
                        "description": str(decision.get("description", "")),
                        "reason": "symbol_disappeared",
                        "file_path": file_path,
                        "symbol": symbol_name,
                    })
                    continue

                verdict: dict | None = None
                if actual_hash:
                    verdict = await get_compliance_verdict(
                        self._client, decision_id, region_id, actual_hash,
                    )

                new_status = derive_status(stored_hash, actual_hash, cached_verdict=verdict)

                if is_authoritative:
                    # v0.5.0: use project_decision_status for holistic aggregation
                    projected = await project_decision_status(self._client, decision_id)
                    await update_decision_status(self._client, decision_id, projected)
                    new_status = projected

                if new_status == "reflected" and old_status != "reflected":
                    flipped_to_reflected.add(decision_id)
                elif new_status == "drifted" and old_status != "drifted":
                    flipped_to_drifted.add(decision_id)

                if actual_hash and verdict is None:
                    if region_code_body is None:
                        region_code_body = _extract_code_body(
                            file_path, start_line, end_line, repo_path, ref=commit_hash,
                        )
                    pending_checks.append({
                        "phase": phase,
                        "decision_id": decision_id,
                        "region_id": region_id,
                        "decision_description": str(decision.get("description", "")),
                        "file_path": file_path,
                        "symbol": symbol_name,
                        "content_hash": actual_hash,
                        "code_body": region_code_body,
                    })

            decisions = [i for i in (region.get("decisions") or []) if i is not None]
            if not decisions and symbol_name:
                undocumented_symbols.append(symbol_name)

        # Surface any ungrounded decisions so the caller can bind them.
        try:
            ungrounded_decisions = await get_all_decisions(self._client, filter="ungrounded")
            for d in ungrounded_decisions:
                pending_grounding_checks.append({
                    "decision_id": str(d.get("id", "")),
                    "description": str(d.get("description", "")),
                    "reason": "ungrounded",
                })
        except Exception as exc:
            logger.warning("[link_commit] could not query ungrounded decisions: %s", exc)

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
            "pending_compliance_checks": pending_checks,
            "pending_grounding_checks": pending_grounding_checks,
        }

    async def backfill_empty_hashes(
        self,
        repo_path: str,
        drift_analyzer=None,
    ) -> dict:
        """Self-heal regions with no content_hash."""
        await self._ensure_connected()

        if drift_analyzer is None:
            from .drift import HashDriftAnalyzer
            drift_analyzer = HashDriftAnalyzer()

        legacy = await get_regions_without_hash(self._client, repo=repo_path)
        if not legacy:
            return {"healed": 0, "failed": 0}

        healed = 0
        failed = 0
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

            if not drift_result.content_hash:
                failed += 1
                continue

            await update_region_hash(self._client, region_id, drift_result.content_hash, ref)
            new_status = drift_result.status
            for decision in (region.get("decisions") or []):
                if decision is None:
                    continue
                decision_id = str(decision.get("id", ""))
                if decision_id:
                    await update_decision_status(self._client, decision_id, new_status)
            healed += 1

        if healed or failed:
            logger.info("[backfill] repo=%s healed=%d failed=%d", repo_path, healed, failed)
        return {"healed": healed, "failed": failed}

    # ── Extended: ingestion of CodeLocatorPayload ─────────────────────────

    async def ingest_payload(self, payload: dict, ctx=None) -> dict:
        """Ingest a CodeLocatorPayload dict into the v4 graph.

        Creates input_span, decision, code_region, symbol nodes and
        yields / binds_to / locates edges. The v0.4.x maps_to + implements
        chain is replaced by direct decision → binds_to → code_region.
        """
        await self._ensure_connected()

        repo = payload.get("repo", "")
        commit_hash = payload.get("commit_hash", "")
        authoritative_sha = getattr(ctx, "authoritative_sha", "") if ctx is not None else ""
        effective_ref = commit_hash or authoritative_sha or resolve_head(repo) or "HEAD"
        decisions_created = 0
        symbols_mapped = 0
        regions_linked = 0
        ungrounded = []
        region_ids: list[str] = []

        for mapping in payload.get("mappings", []):
            span = mapping.get("span", {})
            description = mapping.get("intent", span.get("text", ""))
            source_ref = span.get("source_ref", payload.get("query", ""))
            source_type = span.get("source_type", "manual")
            span_text = span.get("text", "")
            product_signoff = mapping.get("product_signoff", None)

            code_regions = mapping.get("code_regions", [])
            initial_status = "ungrounded" if not code_regions else "pending"
            feature_group = mapping.get("feature_group") or None

            # Create input_span node only when verbatim text is available.
            # Per v0.5.0 contract: span.text must be non-empty; the schema
            # ASSERT constraint enforces this at the DB level too.
            span_id = ""
            if span_text:
                span_id = await upsert_input_span(
                    self._client,
                    text=span_text,
                    source_type=source_type,
                    source_ref=source_ref,
                    speakers=span.get("speakers", []),
                    meeting_date=span.get("meeting_date", ""),
                )

            # Create decision node
            decision_id = await upsert_decision(
                self._client,
                description=description,
                source_type=source_type,
                source_ref=source_ref,
                status=initial_status,
                meeting_date=span.get("meeting_date", ""),
                speakers=span.get("speakers", []),
                product_signoff=product_signoff,
                feature_group=feature_group,
            )
            decisions_created += 1

            if not decision_id:
                logger.warning("[ingest] failed to create decision for: %s", description[:60])
                continue

            # Link input_span → yields → decision
            if span_id and decision_id:
                await relate_yields(self._client, span_id, decision_id)

            if not code_regions:
                ungrounded.append({"decision_id": decision_id, "description": description})
                continue

            region_statuses: list[str] = []

            for region_data in code_regions:
                symbol_name = region_data.get("symbol", "")
                file_path = region_data.get("file_path", "")

                if not symbol_name or not file_path:
                    continue

                start_line = region_data.get("start_line", 0)
                end_line = region_data.get("end_line", 0)
                content_hash = ""
                if repo:
                    content_hash = compute_content_hash(
                        file_path, start_line, end_line, repo, ref=effective_ref
                    ) or ""

                # Create / update symbol node (retrieval tier)
                symbol_id = await upsert_symbol(
                    self._client,
                    name=symbol_name,
                    file_path=file_path,
                    sym_type=region_data.get("type", "function"),
                )

                # Create / update code_region node (shared between tiers)
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

                region_statuses.append(
                    derive_status(content_hash, content_hash if content_hash else None)
                )

                # Decision tier: decision → binds_to → code_region (direct)
                provenance: dict = {}
                grounding_tier = region_data.get("grounding_tier")
                if grounding_tier is not None:
                    provenance["grounding_tier"] = grounding_tier
                    provenance["method"] = "auto_ground"
                await relate_binds_to(
                    self._client, decision_id, region_id,
                    confidence=region_data.get("confidence", 0.8),
                    provenance=provenance,
                )

                # Retrieval tier: symbol → locates → code_region
                if symbol_id:
                    symbols_mapped += 1
                    await relate_locates(self._client, symbol_id, region_id)

            if decision_id:
                aggregated = _aggregate_decision_status(region_statuses)
                await update_decision_status(self._client, decision_id, aggregated)

        return {
            "ingested": True,
            "repo": repo,
            "stats": {
                "intents_created": decisions_created,  # keep key for compat with callers
                "symbols_mapped": symbols_mapped,
                "regions_linked": regions_linked,
                "ungrounded": len(ungrounded),
            },
            "ungrounded_decisions": ungrounded,
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
        await self._ensure_connected()
        rows = await self._client.query(
            "SELECT * FROM source_cursor WHERE repo = $repo",
            {"repo": repo},
        )
        if not rows:
            return []
        out: list[dict] = []
        for row in rows:
            row["synced_at"] = str(row.get("synced_at", ""))
            out.append(row)
        return out

    async def wipe_all_rows(self, repo: str) -> None:
        """Delete every row belonging to repo across every bicameral table.

        v0.5.0 update: traversals use binds_to (decision tier) instead of
        maps_to + implements. Scoping strategy unchanged from v0.4.x.
        """
        await self._ensure_connected()

        decision_ids: set[str] = set()

        # (a) Graph traversal from code_regions belonging to this repo.
        try:
            rows = await self._client.query(
                """
                SELECT <-binds_to<-decision AS decisions
                FROM code_region
                WHERE repo = $repo
                """,
                {"repo": repo},
            )
            for row in rows or []:
                decisions_field = row.get("decisions") or []
                if isinstance(decisions_field, list):
                    for nested in decisions_field:
                        if isinstance(nested, list):
                            for item in nested:
                                if item:
                                    decision_ids.add(str(item))
                        elif nested:
                            decision_ids.add(str(nested))
        except Exception as exc:
            logger.warning("[wipe_all_rows] code_region → decision traversal failed: %s", exc)

        # (b) source_cursor audit-log matching for ungrounded decisions.
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
                    "SELECT type::string(id) AS id FROM decision WHERE source_ref = $r AND source_type = $t",
                    {"r": src_ref, "t": src_type},
                )
                for m in matching or []:
                    if m.get("id"):
                        decision_ids.add(str(m["id"]))
        except Exception as exc:
            logger.warning("[wipe_all_rows] source_cursor → decision matching failed: %s", exc)

        # Gather input_span IDs yielding those decisions.
        input_span_ids: set[str] = set()
        if decision_ids:
            try:
                rows = await self._client.query("SELECT type::string(in) AS in FROM yields")
                for row in rows or []:
                    _in = row.get("in")
                    if _in:
                        input_span_ids.add(str(_in))
            except Exception as exc:
                logger.debug("[wipe_all_rows] input_span traversal failed: %s", exc)

        # Delete scoped-by-column tables.
        for table in ("code_region", "source_cursor", "vocab_cache"):
            try:
                await self._client.execute(
                    f"DELETE FROM {table} WHERE repo = $repo",
                    {"repo": repo},
                )
            except Exception as exc:
                logger.warning("[wipe_all_rows] %s scoped delete failed: %s", table, exc)

        # Delete enumerated decisions by id.
        for decision_id in decision_ids:
            try:
                await self._client.execute(f"DELETE {decision_id}")
            except Exception as exc:
                logger.debug("[wipe_all_rows] decision %s delete failed: %s", decision_id, exc)

        # Delete enumerated input_spans by id.
        for span_id in input_span_ids:
            try:
                await self._client.execute(f"DELETE {span_id}")
            except Exception as exc:
                logger.debug("[wipe_all_rows] input_span %s delete failed: %s", span_id, exc)

        # ledger_sync is per-repo.
        try:
            await self._client.execute(
                "DELETE FROM ledger_sync WHERE repo = $repo",
                {"repo": repo},
            )
        except Exception as exc:
            logger.warning("[wipe_all_rows] ledger_sync delete failed: %s", exc)
