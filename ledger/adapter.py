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
    find_subject_identities_for_decision,
    get_all_decisions,
    get_decision_level,
    get_compliance_verdict,
    get_decisions_for_file,
    get_decisions_for_files,
    get_pending_decisions_with_regions,
    get_regions_for_files,
    get_regions_with_ephemeral_verdicts,
    get_regions_without_hash,
    get_source_cursor,
    get_sync_state,
    get_undocumented_symbols,
    has_prior_compliant_verdict,
    link_decision_to_subject,
    lookup_vocab_cache,
    project_decision_status,
    promote_ephemeral_verdict,
    region_exists,
    relate_binds_to,
    relate_has_identity,
    relate_locates,
    relate_yields,
    search_by_bm25,
    update_decision_status,
    update_region_hash,
    upsert_code_region,
    upsert_code_subject,
    upsert_decision,
    upsert_input_span,
    upsert_source_cursor,
    upsert_subject_identity,
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
    resolve_ref,
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


def _get_branch_delta_files(authoritative_ref: str, commit_hash: str, repo_path: str) -> list[str]:
    """Return files changed between the merge base of authoritative_ref and commit_hash.

    Uses `git diff <auth>...HEAD --name-only` (three-dot diff = merge-base to HEAD).
    Covers all files modified on the feature branch, not just the HEAD commit.
    Returns [] if the command fails or authoritative_ref is unreachable.
    """
    import subprocess as _sp
    try:
        result = _sp.run(
            ["git", "diff", f"{authoritative_ref}...{commit_hash}", "--name-only"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except Exception:
        return []


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

    async def get_decision_level(self, decision_id: str) -> str | None:
        """Return the decision's ``decision_level`` (``"L1"``/``"L2"``/``"L3"``)
        or ``None`` if unset. Used by the bind handler's L1 exemption."""
        await self._ensure_connected()
        return await get_decision_level(self._client, decision_id)

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

    # ── CodeGenome (v11, Phase 1+2 / #59) ─────────────────────────────
    # Thin wrappers around ledger.queries helpers. Used only when
    # codegenome.write_identity_records is enabled at the handler boundary.
    # The ledger module duck-types ``SubjectIdentity`` from codegenome to
    # keep the import direction one-way (codegenome → ledger).

    async def upsert_code_subject(
        self,
        kind: str,
        canonical_name: str,
        current_confidence: float,
        repo_ref: str | None = None,
    ) -> str:
        await self._ensure_connected()
        return await upsert_code_subject(
            self._client,
            kind=kind,
            canonical_name=canonical_name,
            current_confidence=current_confidence,
            repo_ref=repo_ref,
        )

    async def upsert_subject_identity(self, identity) -> str:
        """Persist a ``codegenome.adapter.SubjectIdentity`` and return its id."""
        await self._ensure_connected()
        return await upsert_subject_identity(
            self._client,
            address=identity.address,
            identity_type=identity.identity_type,
            structural_signature=identity.structural_signature,
            behavioral_signature=identity.behavioral_signature,
            signature_hash=identity.signature_hash,
            content_hash=identity.content_hash,
            confidence=identity.confidence,
            model_version=identity.model_version,
        )

    async def relate_has_identity(
        self,
        code_subject_id: str,
        subject_identity_id: str,
        confidence: float = 0.9,
    ) -> None:
        await self._ensure_connected()
        await relate_has_identity(
            self._client, code_subject_id, subject_identity_id, confidence=confidence,
        )

    async def link_decision_to_subject(
        self,
        decision_id: str,
        code_subject_id: str,
        confidence: float = 0.8,
    ) -> None:
        await self._ensure_connected()
        await link_decision_to_subject(
            self._client, decision_id, code_subject_id, confidence=confidence,
        )

    async def find_subject_identities_for_decision(
        self,
        decision_id: str,
    ) -> list[dict]:
        await self._ensure_connected()
        return await find_subject_identities_for_decision(self._client, decision_id)

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
            f"SELECT decision_id, description, status, source_ref, meeting_date, signoff "
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
            # Commit hasn't moved, but decisions ingested after the last sync
            # are still at status='pending' with no compliance checks generated.
            # Surface them so the caller LLM can resolve them now.
            pending_checks: list[dict] = []
            try:
                stale_pending = await get_pending_decisions_with_regions(self._client)
                for row in stale_pending:
                    region_id = str(row.get("region_id", ""))
                    if not region_id:
                        continue
                    fp = row.get("file_path", "")
                    sl = row.get("start_line", 0)
                    el = row.get("end_line", 0)
                    current_hash = compute_content_hash(fp, sl, el, repo_path, ref=commit_hash)
                    if not current_hash:
                        continue
                    code_body = _extract_code_body(fp, sl, el, repo_path, ref=commit_hash)
                    pending_checks.append({
                        "phase": "ingest",
                        "decision_id": str(row.get("decision_id", "")),
                        "region_id": region_id,
                        "decision_description": str(row.get("description", "")),
                        "file_path": fp,
                        "symbol": row.get("symbol_name", ""),
                        "content_hash": current_hash,
                        "code_body": code_body,
                    })
            except Exception as exc:
                logger.warning("[link_commit] could not surface pending decisions on already_synced: %s", exc)

            # Repair stale ephemeral hashes on authoritative branches.
            # A feature-branch bind sets code_region.content_hash = H_branch.
            # When we return to main without merging, already_synced fires early,
            # leaving the stale hash and causing decisions to show "reflected" even
            # though the implementation hasn't landed on main.
            #
            # Two-pass approach: pass 1 updates all stale region hashes, pass 2
            # re-projects status for each unique affected decision (deduped).
            # get_regions_with_ephemeral_verdicts uses idx_cc_ephemeral to return
            # only O(M_ephemeral) regions instead of scanning all code_regions.
            regions_repaired = 0
            if is_authoritative:
                try:
                    stale_regions = await get_regions_with_ephemeral_verdicts(self._client)
                    # Pass 1: update hashes, collect affected decisions
                    affected_decisions: dict[str, None] = {}
                    for region in stale_regions:
                        region_id = region.get("region_id", "")
                        fp = region.get("file_path", "")
                        sl = region.get("start_line", 0)
                        el = region.get("end_line", 0)
                        stored_hash = region.get("content_hash", "")
                        actual_hash = compute_content_hash(fp, sl, el, repo_path, ref=commit_hash)
                        if not actual_hash or actual_hash == stored_hash:
                            continue
                        await update_region_hash(self._client, region_id, actual_hash, commit_hash)
                        regions_repaired += 1
                        for decision in (region.get("decisions") or []):
                            if decision is None:
                                continue
                            decision_id = str(decision.get("id", ""))
                            if not decision_id:
                                continue
                            _signoff = decision.get("signoff") or {}
                            if isinstance(_signoff, dict) and _signoff.get("state") == "superseded":
                                continue
                            affected_decisions[decision_id] = None
                    # Pass 2: project status once per unique decision after all hashes are updated
                    for decision_id in affected_decisions:
                        projected = await project_decision_status(self._client, decision_id)
                        await update_decision_status(self._client, decision_id, projected)
                except Exception as exc:
                    logger.warning("[link_commit] stale ephemeral hash repair failed: %s", exc)

            return {
                "synced": True,
                "commit_hash": commit_hash,
                "reason": "already_synced",
                "regions_updated": regions_repaired,
                "decisions_reflected": 0,
                "decisions_drifted": 0,
                "undocumented_symbols": [],
                "sweep_scope": "head_only",
                "range_size": 0,
                "pending_compliance_checks": pending_checks,
                "pending_grounding_checks": [],
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

        # V2: branch-delta sweep for non-authoritative branches.
        # When no prior cursor exists (cold start on feature branch), head-only
        # sweep only covers the HEAD commit. git diff <auth>...HEAD covers ALL
        # files changed on the branch since the merge base — catches drift from
        # earlier feature commits whose files aren't touched in HEAD.
        if not is_authoritative and authoritative_ref:
            delta_files = _get_branch_delta_files(authoritative_ref, commit_hash, repo_path)
            if delta_files:
                existing = set(changed_files)
                new_files = [f for f in delta_files if f not in existing]
                if new_files:
                    changed_files = changed_files + new_files
                    sweep_scope = "branch_delta"

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
            # Pollution guard: only update code_region.content_hash from authoritative
            # commits. Branch content must not overwrite the stable main baseline.
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

                # Superseded decisions are retired from code tracking.
                # signoff.state='superseded' is written by resolve_collision and
                # means a human explicitly replaced this decision with another.
                _signoff = decision.get("signoff") or {}
                if isinstance(_signoff, dict) and _signoff.get("state") == "superseded":
                    continue

                old_status = decision.get("status", "ungrounded")

                # If symbol disappeared, emit a grounding check instead of compliance check.
                # V1 D1: the payload is informational only — no server-side
                # candidate suggestions (search_code was removed in v0.6.4).
                # The caller LLM finds the new location via Grep/Read +
                # validate_symbols / extract_symbols, then calls bicameral.bind
                # (per the verification_instruction). ``original_lines`` is
                # included so the caller can inspect the prior code via
                # ``git show <prev_ref>:<file_path>`` if useful.
                if symbol_disappeared:
                    # L1 decisions are intentionally ungrounded — skip grounding alarm.
                    if decision.get("decision_level") != "L1":
                        pending_grounding_checks.append({
                            "decision_id": decision_id,
                            "description": str(decision.get("description", "")),
                            "reason": "symbol_disappeared",
                            "file_path": file_path,
                            "symbol": symbol_name,
                            "original_lines": [start_line, end_line],
                        })
                    continue

                verdict: dict | None = None
                if actual_hash:
                    verdict = await get_compliance_verdict(
                        self._client, decision_id, region_id, actual_hash,
                    )

                new_status = derive_status(stored_hash, actual_hash, cached_verdict=verdict)

                if is_authoritative:
                    # V2: promote ephemeral verdict when same hash lands on authoritative branch
                    if actual_hash:
                        await promote_ephemeral_verdict(self._client, decision_id, region_id, actual_hash)
                    # v0.5.0: holistic status projection from DB
                    projected = await project_decision_status(self._client, decision_id)
                    await update_decision_status(self._client, decision_id, projected)
                    new_status = projected
                else:
                    # V2: feature branch — derive status locally from actual_hash vs stored_hash.
                    # project_decision_status reads code_region.content_hash from DB, but we
                    # don't update it on feature branches (pollution guard). Instead, compute
                    # status directly: if hash changed and prior compliant verdict exists → drifted.
                    if not actual_hash or not stored_hash:
                        fb_status = "pending"
                    elif actual_hash == stored_hash:
                        if verdict is not None and not verdict.get("pruned"):
                            fb_status = "reflected" if verdict.get("verdict") == "compliant" else "drifted"
                        elif await has_prior_compliant_verdict(self._client, decision_id, region_id):
                            fb_status = "drifted"
                        else:
                            fb_status = "pending"
                    else:
                        if await has_prior_compliant_verdict(self._client, decision_id, region_id):
                            fb_status = "drifted"
                        else:
                            fb_status = "pending"
                    await update_decision_status(self._client, decision_id, fb_status)
                    new_status = fb_status

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
        # L1 decisions are intentionally ungrounded (behavioral claims, no code binding) —
        # suppress them here so they don't show as grounding gaps.
        try:
            ungrounded_decisions = await get_all_decisions(self._client, filter="ungrounded")
            for d in ungrounded_decisions:
                if d.get("decision_level") == "L1":
                    continue
                # get_all_decisions returns rows with `decision_id` (aliased
                # from id via `type::string(id) AS decision_id`); reading
                # `d["id"]` returns "" and produces unusable grounding
                # checks the caller cannot bind against. Surfaced by V1 F1
                # regression coverage.
                pending_grounding_checks.append({
                    "decision_id": str(d.get("decision_id") or d.get("id", "")),
                    "description": str(d.get("description", "")),
                    "reason": "ungrounded",
                })
        except Exception as exc:
            logger.warning("[link_commit] could not query ungrounded decisions: %s", exc)

        # Surface stale pending decisions left over from an aborted sync.
        # These had content_hash written but were never compliance-evaluated.
        # Include them in pending_checks so the current sync resolves them.
        try:
            already_covered = {c["region_id"] for c in pending_checks}
            stale_pending = await get_pending_decisions_with_regions(self._client)
            for row in stale_pending:
                region_id = str(row.get("region_id", ""))
                if not region_id or region_id in already_covered:
                    continue
                fp = row.get("file_path", "")
                sl = row.get("start_line", 0)
                el = row.get("end_line", 0)
                current_hash = compute_content_hash(fp, sl, el, repo_path, ref=commit_hash)
                if not current_hash:
                    continue
                code_body = _extract_code_body(fp, sl, el, repo_path, ref=commit_hash)
                pending_checks.append({
                    "phase": "drift",
                    "decision_id": str(row.get("decision_id", "")),
                    "region_id": region_id,
                    "decision_description": str(row.get("description", "")),
                    "file_path": fp,
                    "symbol": row.get("symbol_name", ""),
                    "content_hash": current_hash,
                    "code_body": code_body,
                })
        except Exception as exc:
            logger.warning("[link_commit] could not surface stale pending decisions: %s", exc)

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
        created_decisions: list[dict] = []
        region_ids: list[str] = []

        for mapping in payload.get("mappings", []):
            span = mapping.get("span", {})
            description = mapping.get("intent", span.get("text", ""))
            source_ref = span.get("source_ref", payload.get("query", ""))
            source_type = span.get("source_type", "manual")
            span_text = span.get("text", "")
            signoff = mapping.get("signoff", None)

            code_regions = mapping.get("code_regions", [])
            initial_status = "ungrounded" if not code_regions else "pending"
            feature_group = mapping.get("feature_group") or None
            decision_level = mapping.get("decision_level") or None
            parent_decision_id = mapping.get("parent_decision_id") or None

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

            # Stamp discovered on new decisions when signoff not explicitly provided.
            # discovered=True: AI surfaced — agent_session source, or no verbatim human quote.
            # discovered=False: human explicitly stated in a transcript/document/slack.
            if signoff is None:
                is_discovered = source_type == "agent_session" or not span_text
                signoff = {"state": "proposed", "discovered": is_discovered}

            # Create decision node
            decision_id = await upsert_decision(
                self._client,
                description=description,
                source_type=source_type,
                source_ref=source_ref,
                status=initial_status,
                meeting_date=span.get("meeting_date", ""),
                speakers=span.get("speakers", []),
                signoff=signoff,
                feature_group=feature_group,
                decision_level=decision_level,
                parent_decision_id=parent_decision_id,
            )
            decisions_created += 1

            if not decision_id:
                logger.warning("[ingest] failed to create decision for: %s", description[:60])
                continue

            # Track every created decision for the caller-LLM collision check.
            created_entry: dict = {"decision_id": decision_id, "description": description}
            if decision_level:
                created_entry["decision_level"] = decision_level
            created_decisions.append(created_entry)

            # Link input_span → yields → decision
            if span_id and decision_id:
                await relate_yields(self._client, span_id, decision_id)

            if not code_regions:
                ungrounded.append(created_entry)
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
                    # The hallucinated-file guard only fires when we can actually
                    # validate file existence — i.e. ``repo`` is a directory on
                    # disk AND ``effective_ref`` resolves to a real commit.
                    # ``compute_content_hash`` returns None whenever ``git show``
                    # fails, which happens in three distinct cases:
                    #   1. repo path doesn't exist (synthetic / test fixture)
                    #   2. repo exists but ref doesn't resolve (synthetic ref)
                    #   3. repo + ref both real, file genuinely missing at ref
                    # Only case 3 is a hallucinated-file signal and warrants
                    # rejecting the region. Cases 1 and 2 are unverifiable
                    # contexts — fall through with empty hash so the decision
                    # is created as ungrounded (matches pre-v0.10.7 behavior).
                    repo_on_disk = Path(repo).resolve().is_dir()
                    ref_resolves = (
                        repo_on_disk
                        and (effective_ref == "working_tree"
                             or resolve_ref(effective_ref, repo) is not None)
                    )
                    if repo_on_disk and ref_resolves:
                        _computed = compute_content_hash(
                            file_path, start_line, end_line, repo, ref=effective_ref
                        )
                        if _computed is None:
                            logger.warning(
                                "[ingest] skipping region: file '%s' not found at %s in %s"
                                " — only bind to existing code, never hypothetical files",
                                file_path, effective_ref, repo,
                            )
                            continue
                        content_hash = _computed
                    # else: unverifiable context — fall through with empty hash.

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
            "created_decisions": created_decisions,
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
        """Wipe the ledger by closing and deleting the DB, then reconnecting.

        For surrealkv://, the directory on disk is removed entirely.
        For memory://, closing and reconnecting gives a fresh empty DB.
        init_schema() runs automatically inside connect(), so the adapter is
        immediately ready for use after this call returns.
        """
        import shutil
        await self._ensure_connected()
        await self._client.close()
        self._connected = False
        url = self._url
        if url.startswith("surrealkv://"):
            db_path = url[len("surrealkv://"):]
            if db_path:
                shutil.rmtree(db_path, ignore_errors=True)
        await self._ensure_connected()
