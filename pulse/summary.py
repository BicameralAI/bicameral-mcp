"""``ProjectPulseSummary`` — the shared Project Pulse backend object (#437 Phase 1).

A *Project Pulse* is the operator-facing summary of "what does project memory
look like right now" — health counts, what needs attention, what was recently
learned, and one suggested next move. ``ProjectPulseSummary`` is the structured,
read-only data object behind that summary; ``build_project_pulse`` computes it
once from the ledger.

This module is **data, not presentation**. The object carries no markdown, no
ANSI, no HTML — rendering is Phases 2/3 (CLI ``bicameral-mcp brief`` and the
dashboard view). ``to_dict()`` gives #437's ``--json`` acceptance criterion
directly.

Design notes:

* ``build_project_pulse`` is **read-only** — it issues only ``get_*`` queries
  against the ledger adapter.
* It is **fail-soft per section**: a failure computing one section degrades that
  section (empty list / zeroed counts) and the summary still builds. The
  all-clear state is a first-class result, not an error.
* ``drift_findings`` and ``last_sync`` are **injected arguments**, not computed
  here — drift computation is the caller's best-effort job (mirroring
  ``cli/sync_and_brief_cli.py``), and ``last_sync`` comes from sync metadata
  the renderer phases own. This keeps the object a pure assembler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Decision status enum — the eng-reflected (double-entry) axis. Confirmed at
# ``ledger/adapter.py`` ``_STATUS_PRIORITY`` and the ``decision`` schema ASSERT.
_HEALTH_STATUSES = ("reflected", "drifted", "pending", "ungrounded")

# The friendly all-clear message (#437: "all-clear is a useful result").
_ALL_CLEAR_MESSAGE = "Project memory is current — no drift, no pending signoffs."


@dataclass
class Health:
    """Health counts for the Project Pulse summary.

    ``decisions_reflected`` / ``_drifted`` / ``_pending`` / ``_ungrounded`` are
    counts over the decision ``status`` enum. ``drifted_regions`` is the count
    of drift findings supplied by the caller (drift analysis is best-effort and
    not computed here). ``last_sync`` is an injected ISO timestamp string, or
    ``None`` when no canonical sync watermark is available.
    """

    decisions_reflected: int = 0
    decisions_drifted: int = 0
    decisions_pending: int = 0
    decisions_ungrounded: int = 0
    drifted_regions: int = 0
    last_sync: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict of the health counts."""
        return {
            "decisions_reflected": self.decisions_reflected,
            "decisions_drifted": self.decisions_drifted,
            "decisions_pending": self.decisions_pending,
            "decisions_ungrounded": self.decisions_ungrounded,
            "drifted_regions": self.drifted_regions,
            "last_sync": self.last_sync,
        }


@dataclass
class NeedsAttentionItem:
    """One item in the ``needs_attention`` list.

    ``kind`` is a discriminant literal — Phase 1 only emits
    ``"awaiting_ratification"``, but the field exists so Phase 1b can add
    ``context_pending`` / ``collision_pending`` / ``rejected_in_branch`` items
    without changing the object's shape.
    """

    kind: str
    decision_id: str
    summary: str
    signer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict of the needs-attention item."""
        return {
            "kind": self.kind,
            "decision_id": self.decision_id,
            "summary": self.summary,
            "signer": self.signer,
        }


@dataclass
class LearnedItem:
    """One recently-learned decision in the ``recently_learned`` list.

    Carries only opaque, ledger-exposed fields — ``decision_id``, ``summary``
    (the decision ``description``), ``source_type``, ``source_ref`` and a
    ``date``. ``pulse/render.py`` renders these as plain text. No raw
    transcript text or signer emails are added.
    """

    decision_id: str
    summary: str
    source_type: str | None = None
    source_ref: str | None = None
    date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict of the learned item."""
        return {
            "decision_id": self.decision_id,
            "summary": self.summary,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "date": self.date,
        }


@dataclass
class ProjectPulseSummary:
    """The shared Project Pulse summary object (#437).

    A structured, read-only snapshot of project memory: ``health`` counts,
    ``needs_attention`` items, ``recently_learned`` decisions, one
    ``suggested_next_move`` string, and an ``is_all_clear`` flag. ``to_dict()``
    produces a recursive, JSON-safe representation — the foundation for #437's
    ``--json`` output.
    """

    health: Health
    needs_attention: list[NeedsAttentionItem] = field(default_factory=list)
    recently_learned: list[LearnedItem] = field(default_factory=list)
    suggested_next_move: str = _ALL_CLEAR_MESSAGE
    is_all_clear: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Return a recursive, JSON-safe dict of the whole summary."""
        return {
            "health": self.health.to_dict(),
            "needs_attention": [item.to_dict() for item in self.needs_attention],
            "recently_learned": [item.to_dict() for item in self.recently_learned],
            "suggested_next_move": self.suggested_next_move,
            "is_all_clear": self.is_all_clear,
        }


def _decision_sort_key(decision: dict[str, Any]) -> str:
    """Recency sort key for a decision dict returned by ``get_all_decisions``.

    ``get_all_decisions`` pops ``created_at`` and exposes it as the
    ``ingested_at`` string (``ledger/queries.py``); ``meeting_date`` is the
    fallback when present. A string compare on ISO timestamps is monotonic.
    """
    return str(decision.get("ingested_at") or decision.get("meeting_date") or "")


class SinceParseError(ValueError):
    """Raised when ``--since`` cannot be parsed into a cutoff date.

    Carries a human-readable message; the CLI surfaces it and exits 2.
    """


def _parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """Parse a ``--since`` token into an inclusive UTC cutoff ``datetime``.

    Accepted grammar (minimal by design — fancier natural-language date
    parsing is a deferred nicety):

    * an ISO date — ``2026-05-20``;
    * the relative keyword ``today`` — midnight UTC today;
    * the relative keyword ``yesterday`` — midnight UTC yesterday;
    * ``Nd`` — N days ago (e.g. ``7d``).

    Args:
        value: The raw ``--since`` token.
        now: Injectable "current time" for deterministic tests. Defaults to
            ``datetime.now(UTC)``.

    Returns:
        A timezone-aware UTC ``datetime`` — the inclusive lower bound; a
        decision is kept iff its date is ``>=`` this cutoff.

    Raises:
        SinceParseError: ``value`` does not match the accepted grammar.
    """
    raw = (value or "").strip().lower()
    if not raw:
        raise SinceParseError("--since value is empty")
    reference = now or datetime.now(UTC)
    midnight = reference.replace(hour=0, minute=0, second=0, microsecond=0)

    if raw == "today":
        return midnight
    if raw == "yesterday":
        return midnight - timedelta(days=1)
    if raw.endswith("d") and raw[:-1].isdigit():
        return midnight - timedelta(days=int(raw[:-1]))

    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise SinceParseError(
            f"unrecognized --since value {value!r}; "
            "use an ISO date (2026-05-20), 'today', 'yesterday', or 'Nd' (e.g. 7d)"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _decision_date(decision: dict[str, Any]) -> datetime | None:
    """Best-effort parse of a decision's recency date into a UTC ``datetime``.

    Uses the same ``ingested_at`` / ``meeting_date`` precedence as
    ``_decision_sort_key``. Returns ``None`` when neither field carries a
    parseable ISO timestamp — an undated decision is then conservatively
    excluded by the ``since`` filter.
    """
    raw = _decision_sort_key(decision)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _filter_decisions(
    decisions: list[dict[str, Any]],
    *,
    since: datetime | None,
    feature: str | None,
) -> list[dict[str, Any]]:
    """Apply the ``since`` / ``feature`` filters to raw decision rows.

    Both filters run on the raw ``get_all_decisions`` rows — before the
    ``LearnedItem`` / ``NeedsAttentionItem`` objects are assembled — so the
    filters apply uniformly and no Phase-1 object-shape change is needed.

    * ``since``: keeps decisions whose date is ``>= since`` (an undated
      decision is excluded).
    * ``feature``: keeps decisions whose ``feature_hint`` equals ``feature``.
    """
    if since is None and feature is None:
        return decisions
    out: list[dict[str, Any]] = []
    for decision in decisions:
        if since is not None:
            decision_date = _decision_date(decision)
            if decision_date is None or decision_date < since:
                continue
        if feature is not None and str(decision.get("feature_hint") or "") != feature:
            continue
        out.append(decision)
    return out


def _is_awaiting_ratification(decision: dict[str, Any]) -> bool:
    """True iff the decision is awaiting ratification.

    A decision is awaiting ratification iff its ``signoff`` is a dict with
    ``signoff.state == "proposed"`` — the post-v0.7.0 default a fresh ingest
    writes while awaiting signoff (``ledger/schema.py``). This is the
    ``signoff.state`` axis, orthogonal to the ``status`` enum, so
    ``get_decisions_by_status`` cannot answer it.
    """
    signoff = decision.get("signoff")
    return isinstance(signoff, dict) and signoff.get("state") == "proposed"


async def _build_health(
    ledger: Any,
    *,
    drift_findings: list[dict[str, Any]] | None,
    last_sync: str | None,
) -> Health:
    """Compute the ``Health`` section — fail-soft.

    Counts decisions by status via ``get_decisions_by_status``; the
    ``drifted_regions`` count comes from the injected ``drift_findings``. A
    ledger failure degrades the counts to zero rather than crashing the
    summary.
    """
    health = Health(
        drifted_regions=len(drift_findings or []),
        last_sync=last_sync,
    )
    try:
        rows = await ledger.get_decisions_by_status(list(_HEALTH_STATUSES))
    except Exception as exc:  # noqa: BLE001 — fail-soft per section
        logger.warning("[project-pulse] health counts failed: %s", exc)
        return health

    counts = {status: 0 for status in _HEALTH_STATUSES}
    for row in rows or []:
        status = row.get("status")
        if status in counts:
            counts[status] += 1
    health.decisions_reflected = counts["reflected"]
    health.decisions_drifted = counts["drifted"]
    health.decisions_pending = counts["pending"]
    health.decisions_ungrounded = counts["ungrounded"]
    return health


async def _build_needs_attention(
    ledger: Any,
    *,
    since: datetime | None = None,
    feature: str | None = None,
) -> list[NeedsAttentionItem]:
    """Compute the ``needs_attention`` section — fail-soft.

    From ``get_all_decisions(filter="all")``, keeps decisions awaiting
    ratification (``signoff.state == "proposed"``) and builds one
    ``NeedsAttentionItem`` each. The ``since`` / ``feature`` filters are
    applied to the raw rows before assembly. A ledger failure degrades the
    section to an empty list.
    """
    try:
        decisions = await ledger.get_all_decisions(filter="all")
    except Exception as exc:  # noqa: BLE001 — fail-soft per section
        logger.warning("[project-pulse] needs_attention fetch failed: %s", exc)
        return []

    filtered = _filter_decisions(list(decisions or []), since=since, feature=feature)
    items: list[NeedsAttentionItem] = []
    for decision in filtered:
        if not _is_awaiting_ratification(decision):
            continue
        signoff = decision.get("signoff") or {}
        signer = signoff.get("signer")
        items.append(
            NeedsAttentionItem(
                kind="awaiting_ratification",
                decision_id=str(decision.get("decision_id") or ""),
                summary=str(decision.get("description") or ""),
                signer=str(signer) if signer else None,
            )
        )
    return items


async def _build_recently_learned(
    ledger: Any,
    *,
    recent_limit: int,
    since: datetime | None = None,
    feature: str | None = None,
) -> list[LearnedItem]:
    """Compute the ``recently_learned`` section — fail-soft.

    Returns the most-recent ``recent_limit`` decisions (newest first) as
    ``LearnedItem``s. The ``since`` / ``feature`` filters are applied to the
    raw rows before the recency sort + cap. A ledger failure degrades the
    section to an empty list.
    """
    try:
        decisions = await ledger.get_all_decisions(filter="all")
    except Exception as exc:  # noqa: BLE001 — fail-soft per section
        logger.warning("[project-pulse] recently_learned fetch failed: %s", exc)
        return []

    filtered = _filter_decisions(list(decisions or []), since=since, feature=feature)
    ordered = sorted(filtered, key=_decision_sort_key, reverse=True)
    items: list[LearnedItem] = []
    for decision in ordered[: max(recent_limit, 0)]:
        items.append(
            LearnedItem(
                decision_id=str(decision.get("decision_id") or ""),
                summary=str(decision.get("description") or ""),
                source_type=decision.get("source_type") or None,
                source_ref=decision.get("source_ref") or None,
                date=_decision_sort_key(decision) or None,
            )
        )
    return items


def _suggest_next_move(
    *,
    drifted_regions: int,
    pending_count: int,
) -> str:
    """Deterministic priority ladder for ``suggested_next_move``.

    drift present → review drift; else pending ratifications → review them;
    else the explicit friendly all-clear. No model inference.
    """
    if drifted_regions > 0:
        plural = "s" if drifted_regions != 1 else ""
        return f"Review {drifted_regions} drifted region{plural} before further edits."
    if pending_count > 0:
        plural = "s" if pending_count != 1 else ""
        return f"Review {pending_count} decision{plural} awaiting ratification."
    return _ALL_CLEAR_MESSAGE


async def build_project_pulse(
    ledger: Any,
    *,
    recent_limit: int = 8,
    drift_findings: list[dict[str, Any]] | None = None,
    last_sync: str | None = None,
    since: str | None = None,
    feature: str | None = None,
) -> ProjectPulseSummary:
    """Build the shared ``ProjectPulseSummary`` from the ledger — read-only.

    Computes the four #437 sections (``health``, ``needs_attention``,
    ``recently_learned``, ``suggested_next_move``) once. Each section is
    computed fail-soft: a failure in one section degrades that section and the
    summary still builds.

    Args:
        ledger: A ledger adapter exposing ``get_decisions_by_status`` and
            ``get_all_decisions`` (e.g. ``SurrealDBLedgerAdapter``).
        recent_limit: Cap for ``recently_learned``. Defaults to 8.
        drift_findings: Best-effort drift findings supplied by the caller.
            Drift computation is not done here. Defaults to ``None``.
        last_sync: Injected ISO timestamp of the last sync, or ``None`` when no
            canonical watermark is available. Defaults to ``None``.
        since: Optional ``--since`` token (ISO date / ``today`` / ``yesterday``
            / ``Nd``). When given, ``needs_attention`` and ``recently_learned``
            are filtered to decisions dated ``>=`` the resolved cutoff.
            Defaults to ``None`` (no recency filter — Phase 1 behavior).
        feature: Optional feature filter. When given, ``needs_attention`` and
            ``recently_learned`` are filtered to decisions whose
            ``feature_hint`` equals this value. Defaults to ``None``.

    Returns:
        A fully-assembled ``ProjectPulseSummary``.

    Raises:
        SinceParseError: ``since`` is non-empty and does not match the
            accepted grammar. Raised before any ledger query.
    """
    since_dt = _parse_since(since) if since else None

    health = await _build_health(
        ledger,
        drift_findings=drift_findings,
        last_sync=last_sync,
    )
    needs_attention = await _build_needs_attention(ledger, since=since_dt, feature=feature)
    recently_learned = await _build_recently_learned(
        ledger, recent_limit=recent_limit, since=since_dt, feature=feature
    )

    suggested_next_move = _suggest_next_move(
        drifted_regions=health.drifted_regions,
        pending_count=len(needs_attention),
    )
    is_all_clear = (
        health.drifted_regions == 0 and health.decisions_drifted == 0 and len(needs_attention) == 0
    )
    return ProjectPulseSummary(
        health=health,
        needs_attention=needs_attention,
        recently_learned=recently_learned,
        suggested_next_move=suggested_next_move,
        is_all_clear=is_all_clear,
    )
