"""Handler for bicameral.history MCP tool.

Internal module — not registered as an MCP tool directly (registered in server.py).
Aggregates the full decision ledger into a renderable, feature-grouped shape.

v0.5.1: adds feature_group-based grouping with fallback to query/source_ref.
"""

from __future__ import annotations

import logging
import re

from contracts import (
    HistoryDecision,
    HistoryFeature,
    HistoryFulfillment,
    HistoryResponse,
    HistorySource,
)
from ledger.status import resolve_head

logger = logging.getLogger(__name__)

# Max features returned in one response before truncation.
_MAX_FEATURES = 50

# Normalize source_type to one of the HistorySource Literal values.
_SOURCE_TYPE_MAP: dict[str, str] = {
    "transcript": "transcript",
    "slack": "slack",
    "document": "document",
    "agent_session": "agent_session",
    "manual": "manual",
    # Legacy / aliases
    "notion": "document",
    "implementation_choice": "manual",
}


def _normalize_source_type(raw: str) -> str:
    return _SOURCE_TYPE_MAP.get(raw, "manual")


def _slugify(name: str) -> str:
    """Convert a feature name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or "uncategorized"


def _decision_status_for_history(
    decision_status: str,
    has_code_regions: bool,
    signoff_state: str | None = None,
) -> str:
    """Map DB decision status to HistoryDecision status (code-compliance axis only).

    Superseded decisions surface their last known code-compliance status so the
    balance sheet stays meaningful; the superseded fact is carried by signoff_state.
    Everything without code bindings is ungrounded regardless of description or sources.
    """
    if not has_code_regions:
        return "ungrounded"
    if decision_status in ("drifted", "reflected", "pending"):
        return decision_status
    return "ungrounded"


def _row_to_history_decision(
    row: dict,
    feature_id: str,
) -> HistoryDecision:
    """Convert a raw decision row (from get_all_decisions) into HistoryDecision."""

    decision_id = str(row.get("decision_id") or row.get("id") or "")
    description = str(row.get("description") or "")
    status = str(row.get("status") or "ungrounded")

    # Code regions → fulfillments (all bound regions)
    code_regions = row.get("code_regions") or []
    fulfillments: list[HistoryFulfillment] = []
    for r in code_regions:
        if not r:
            continue
        symbol = r.get("symbol") or r.get("symbol_name") or None
        fulfillments.append(
            HistoryFulfillment(
                file_path=str(r.get("file_path") or ""),
                symbol=symbol,
                start_line=int(r.get("start_line") or 0),
                end_line=int(r.get("end_line") or 0),
                baseline_hash=r.get("content_hash") or None,
                current_hash=r.get("content_hash") or None,
            )
        )

    # Source spans → HistorySource list
    # get_all_decisions returns source_excerpt + meeting_date extracted from first span.
    # For history we want richer representation; reconstruct from the raw span data
    # if available, otherwise fall back to denormalized columns.
    source_spans_raw = row.get("_source_spans") or []
    sources: list[HistorySource] = []
    if source_spans_raw:
        for span in source_spans_raw:
            if not span:
                continue
            text = str(span.get("text") or "")
            if not text:
                continue
            raw_type = str(span.get("source_type") or row.get("source_type") or "manual")
            speakers = span.get("speakers") or []
            speaker = speakers[0] if speakers else None
            sources.append(
                HistorySource(
                    source_ref=str(span.get("source_ref") or row.get("source_ref") or ""),
                    source_type=_normalize_source_type(raw_type),  # type: ignore[arg-type]
                    date=str(span.get("meeting_date") or row.get("meeting_date") or ""),
                    speaker=speaker,
                    quote=text,
                )
            )
    else:
        # Fallback: build a single source from denormalized columns
        source_excerpt = str(row.get("source_excerpt") or "")
        source_ref = str(row.get("source_ref") or "")
        source_type = str(row.get("source_type") or "manual")
        meeting_date = str(row.get("meeting_date") or "")
        if source_excerpt or source_ref:
            sources.append(
                HistorySource(
                    source_ref=source_ref,
                    source_type=_normalize_source_type(source_type),  # type: ignore[arg-type]
                    date=meeting_date,
                    speaker=None,
                    quote=source_excerpt or description,
                )
            )

    drift_evidence: str | None = row.get("drift_evidence") or None
    signoff: dict | None = row.get("signoff") or None
    signoff_state = signoff.get("state") if isinstance(signoff, dict) else None

    history_status = _decision_status_for_history(
        decision_status=status,
        has_code_regions=bool(fulfillments),
        signoff_state=signoff_state,
    )

    return HistoryDecision(
        id=decision_id,
        summary=description,
        featureId=feature_id,
        status=history_status,  # type: ignore[arg-type]
        signoff_state=signoff_state,
        sources=sources,
        fulfillments=fulfillments,
        drift_evidence=drift_evidence,
        signoff=signoff,
        decision_level=row.get("decision_level") or None,
        parent_decision_id=row.get("parent_decision_id") or None,
        # #109 — surface governance metadata when present on the row.
        # Pre-v15 rows carry None and fall back to derived defaults at
        # the engine evaluation layer, not here.
        governance=row.get("governance") or None,
    )


async def _fetch_all_decisions_enriched(ledger) -> list[dict]:
    """Fetch all decisions with enriched source_spans.

    We need richer source span data than get_all_decisions gives us,
    so we query directly from the adapter's client when available.
    """
    inner = getattr(ledger, "_inner", ledger)
    if not hasattr(inner, "_client"):
        # Fallback: use the standard adapter method
        rows = await ledger.get_all_decisions(filter="all")
        return rows

    await inner._ensure_connected()
    client = inner._client

    try:
        rows = await client.query(
            """
            SELECT
                type::string(id)  AS decision_id,
                description,
                rationale,
                feature_hint,
                feature_group,
                decision_level,
                parent_decision_id,
                governance,
                source_type,
                source_ref,
                meeting_date,
                speakers,
                status,
                signoff,
                created_at,
                ->binds_to->code_region.{
                    file_path,
                    symbol_name,
                    start_line,
                    end_line,
                    purpose,
                    content_hash
                } AS code_regions,
                <-yields<-input_span.{text, source_ref, source_type, meeting_date, speakers} AS _source_spans
            FROM decision
            ORDER BY created_at ASC
            """,
        )
    except Exception as exc:
        logger.warning("[history] enriched query failed, falling back: %s", exc)
        rows = await ledger.get_all_decisions(filter="all")
        return rows

    for row in rows:
        ca = row.pop("created_at", None)
        row.setdefault("ingested_at", str(ca)[:24] if ca else "")
        for region in row.get("code_regions") or []:
            if region and "symbol_name" in region:
                region["symbol"] = region.pop("symbol_name")

    return rows


async def _fetch_ephemeral_decision_ids(ledger, decision_ids: list[str]) -> set[str]:
    """Return the subset of decision_ids that have at least one non-pruned ephemeral
    compliance check — meaning their current status was determined by a feature-branch
    commit not yet in the authoritative ref.

    Single bulk query; returns empty set on any error so callers degrade gracefully.
    """
    if not decision_ids:
        return set()
    inner = getattr(ledger, "_inner", ledger)
    if not hasattr(inner, "_client"):
        return set()
    try:
        await inner._ensure_connected()
        rows = await inner._client.query(
            "SELECT decision_id FROM compliance_check "
            "WHERE decision_id IN $dids AND pruned = false AND ephemeral = true",
            {"dids": decision_ids},
        )
        return {str(r["decision_id"]) for r in rows if r.get("decision_id")}
    except Exception as exc:
        logger.debug("[history] ephemeral lookup failed: %s", exc)
        return set()


def _feature_key_for_row(row: dict) -> str:
    """Determine the feature group key for a decision row.

    Priority:
    1. feature_group field (v0.5.1+)
    2. query field (pre-v0.5.1 rows without feature_group — use source_ref as proxy)
    3. source_ref
    4. "Uncategorized"
    """
    feature_group = (row.get("feature_group") or "").strip()
    if feature_group:
        return feature_group
    source_ref = (row.get("source_ref") or "").strip()
    if source_ref:
        return source_ref
    return "Uncategorized"


def _priority_for_feature(decisions: list[HistoryDecision]) -> int:
    """Features with drifted or ungrounded decisions sort first (lower = higher priority)."""
    statuses = {d.status for d in decisions}
    if "drifted" in statuses:
        return 0
    if "ungrounded" in statuses or "pending" in statuses:
        return 1
    return 2


async def handle_history(
    ctx,
    feature_filter: str | None = None,
    include_superseded: bool = True,
    as_of: str | None = None,
) -> HistoryResponse:
    """Read-only dump of the full decision ledger grouped by feature area.

    1. Fetch all decisions with enriched source span data.
    2. Group by feature_group → source_ref → "Uncategorized".
    3. Convert each group to a HistoryFeature.
    4. Sort: drifted/ungrounded first, then reflected.
    5. Apply feature_filter (substring match, case-insensitive).
    6. Truncate at 50 features and set truncated flag.
    """
    # V1 A3: time the catch-up locally so history can report it.
    import time as _time

    from contracts import SyncMetrics
    from handlers.sync_middleware import ensure_ledger_synced

    _t0 = _time.perf_counter()
    await ensure_ledger_synced(ctx)
    sync_metrics = SyncMetrics(sync_catchup_ms=round((_time.perf_counter() - _t0) * 1000, 3))

    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    as_of_ref = as_of or resolve_head(ctx.repo_path) or "HEAD"

    rows = await _fetch_all_decisions_enriched(ledger)

    feature_groups: dict[str, list[dict]] = {}
    for row in rows:
        key = _feature_key_for_row(row)
        feature_groups.setdefault(key, []).append(row)

    # Build HistoryFeature objects
    features: list[HistoryFeature] = []
    for feature_name, group_rows in feature_groups.items():
        feature_id = _slugify(feature_name)

        decisions: list[HistoryDecision] = []
        for row in group_rows:
            hist_dec = _row_to_history_decision(row, feature_id=feature_id)
            # Filter superseded if requested
            if not include_superseded and hist_dec.status == "superseded":
                continue
            decisions.append(hist_dec)

        if not decisions:
            continue

        features.append(
            HistoryFeature(
                id=feature_id,
                name=feature_name,
                decisions=decisions,
            )
        )

    # Apply feature_filter
    if feature_filter:
        filter_lower = feature_filter.lower()
        features = [f for f in features if filter_lower in f.name.lower()]

    total_features = len(features)

    # Sort: drifted/ungrounded first, then reflected, then others
    features.sort(key=lambda f: _priority_for_feature(f.decisions))

    # Truncate
    truncated = False
    if len(features) > _MAX_FEATURES:
        features = features[:_MAX_FEATURES]
        truncated = True

    # Mark decisions whose current compliance verdict came from a feature-branch commit.
    # Only meaningful for decisions that have a status verdict (reflected/drifted).
    verifiable_ids = [
        d.id for f in features for d in f.decisions if d.status in ("reflected", "drifted")
    ]
    ephemeral_ids = await _fetch_ephemeral_decision_ids(ledger, verifiable_ids)
    if ephemeral_ids:
        for feature in features:
            for decision in feature.decisions:
                if decision.id in ephemeral_ids:
                    decision.ephemeral = True

    return HistoryResponse(
        features=features,
        truncated=truncated,
        total_features=total_features,
        as_of=as_of_ref,
        sync_metrics=sync_metrics,
    )
