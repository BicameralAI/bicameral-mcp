"""Sociable tests for ``pulse.build_project_pulse`` (#437 Phase 1).

Per CLAUDE.md's mandatory sociable-testing rule for anything that reads the
ledger: every test instantiates a **real** ``SurrealDBLedgerAdapter`` over
``memory://`` and seeds decision rows with the production schema (the
``_fresh_adapter`` pattern from ``test_codegenome_continuity_service.py``).
No ``MagicMock`` ledger — observable output is asserted, not call shapes.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta

import pytest

from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate
from pulse import ProjectPulseSummary, SinceParseError, build_project_pulse
from pulse.summary import _ALL_CLEAR_MESSAGE, _parse_since


async def _fresh_adapter(suffix: str) -> tuple[SurrealDBLedgerAdapter, LedgerClient]:
    """Return a real ``SurrealDBLedgerAdapter`` over an isolated ``memory://`` ledger."""
    client = LedgerClient(url="memory://", ns=f"pulse_{suffix}", db="ledger_test")
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)
    adapter = SurrealDBLedgerAdapter(url="memory://")
    adapter._client = client
    adapter._connected = True
    return adapter, client


# ``idx_decision_canonical`` is UNIQUE over ``canonical_id`` and the field
# defaults to ''; seeding multiple rows therefore needs a distinct value each.
_canonical_counter = itertools.count()


async def _seed_decision(
    client: LedgerClient,
    *,
    description: str,
    status: str = "ungrounded",
    source_type: str = "manual",
    source_ref: str = "",
    signoff: dict | None = None,
    feature_hint: str = "",
    created_at: str | None = None,
) -> str:
    """Create one decision row with the production schema; return its id string."""
    set_clause = (
        "description = $d, status = $s, source_type = $st, source_ref = $sr, "
        "feature_hint = $fh, signoff = $sig, canonical_id = $cid"
    )
    vars_: dict = {
        "d": description,
        "s": status,
        "st": source_type,
        "sr": source_ref,
        "fh": feature_hint,
        "sig": signoff,
        "cid": f"pulse-test-{next(_canonical_counter)}",
    }
    if created_at is not None:
        set_clause += ", created_at = <datetime>$ca"
        vars_["ca"] = created_at
    rows = await client.query(f"CREATE decision SET {set_clause}", vars_)
    return str(rows[0]["id"])


# ── 1. all-clear state ────────────────────────────────────────────────────


async def test_all_clear_state() -> None:
    """Only reflected decisions, no drift, no pending → first-class all-clear."""
    adapter, client = await _fresh_adapter("all_clear")
    await _seed_decision(
        client,
        description="Use BM25 for code search",
        status="reflected",
        signoff={"state": "ratified", "signer": "jin"},
    )

    summary = await build_project_pulse(adapter)

    assert isinstance(summary, ProjectPulseSummary)
    assert summary.is_all_clear is True
    assert summary.needs_attention == []
    assert summary.suggested_next_move == _ALL_CLEAR_MESSAGE


# ── 2. needs_attention lists pending ratifications ────────────────────────


async def test_needs_attention_lists_pending_ratifications() -> None:
    """Two decisions awaiting ratification → two needs_attention items."""
    adapter, client = await _fresh_adapter("needs_attention")
    id_a = await _seed_decision(
        client,
        description="Adopt feature flags",
        signoff={"state": "proposed", "signer": "silong"},
    )
    id_b = await _seed_decision(
        client,
        description="Cache vocab lookups",
        signoff={"state": "proposed"},
    )
    # A ratified decision must NOT appear in needs_attention.
    await _seed_decision(
        client,
        description="Already ratified",
        status="reflected",
        signoff={"state": "ratified", "signer": "jin"},
    )

    summary = await build_project_pulse(adapter)

    assert len(summary.needs_attention) == 2
    ids = {item.decision_id for item in summary.needs_attention}
    assert ids == {id_a, id_b}
    for item in summary.needs_attention:
        assert item.kind == "awaiting_ratification"
        assert item.summary  # the decision description carries through
    signers = {item.signer for item in summary.needs_attention}
    assert "silong" in signers
    assert None in signers  # the decision with no signer field


# ── 3. health counts by status ────────────────────────────────────────────


async def test_health_counts_by_status() -> None:
    """A mix of statuses → exact per-status health counts."""
    adapter, client = await _fresh_adapter("health_counts")
    await _seed_decision(client, description="r1", status="reflected")
    await _seed_decision(client, description="r2", status="reflected")
    await _seed_decision(client, description="d1", status="drifted")
    await _seed_decision(client, description="p1", status="pending")
    await _seed_decision(client, description="u1", status="ungrounded")
    await _seed_decision(client, description="u2", status="ungrounded")
    await _seed_decision(client, description="u3", status="ungrounded")

    summary = await build_project_pulse(adapter)

    assert summary.health.decisions_reflected == 2
    assert summary.health.decisions_drifted == 1
    assert summary.health.decisions_pending == 1
    assert summary.health.decisions_ungrounded == 3


# ── 4. recently_learned respects limit + recency ──────────────────────────


async def test_recently_learned_respects_limit_and_recency() -> None:
    """Twelve decisions → recently_learned is capped to recent_limit, newest first."""
    adapter, client = await _fresh_adapter("recently_learned")
    for i in range(12):
        await _seed_decision(
            client,
            description=f"decision-{i:02d}",
            status="reflected",
            source_type="meeting",
            source_ref="Sprint Planning",
        )

    summary = await build_project_pulse(adapter, recent_limit=8)

    assert len(summary.recently_learned) == 8
    # Newest-first: created_at descending → decision-11 ahead of decision-04.
    dates = [item.date for item in summary.recently_learned]
    assert dates == sorted(dates, reverse=True)
    first = summary.recently_learned[0]
    assert first.source_type == "meeting"
    assert first.source_ref == "Sprint Planning"


# ── 5. suggested_next_move priority ladder ────────────────────────────────


async def test_suggested_next_move_priority_ladder() -> None:
    """Drift wins over pending; pending wins over all-clear; else friendly all-clear."""
    # (a) drift present → drift suggestion takes priority even with pending.
    adapter_a, client_a = await _fresh_adapter("ladder_drift")
    await _seed_decision(
        client_a,
        description="pending one",
        signoff={"state": "proposed"},
    )
    summary_a = await build_project_pulse(
        adapter_a,
        drift_findings=[{"region": "auth.py"}, {"region": "checkout.py"}],
    )
    assert "drifted region" in summary_a.suggested_next_move
    assert summary_a.suggested_next_move.startswith("Review 2 drifted regions")

    # (b) no drift + pending → ratification suggestion.
    adapter_b, client_b = await _fresh_adapter("ladder_pending")
    await _seed_decision(
        client_b,
        description="pending one",
        signoff={"state": "proposed"},
    )
    summary_b = await build_project_pulse(adapter_b)
    assert summary_b.suggested_next_move == "Review 1 decision awaiting ratification."

    # (c) neither → friendly all-clear.
    adapter_c, client_c = await _fresh_adapter("ladder_clear")
    await _seed_decision(
        client_c,
        description="ratified one",
        status="reflected",
        signoff={"state": "ratified", "signer": "jin"},
    )
    summary_c = await build_project_pulse(adapter_c)
    assert summary_c.suggested_next_move == _ALL_CLEAR_MESSAGE


# ── 6. to_dict is JSON-serializable ───────────────────────────────────────


async def test_to_dict_is_json_serializable() -> None:
    """summary.to_dict() round-trips through json.dumps (#437 --json foundation)."""
    adapter, client = await _fresh_adapter("to_dict")
    await _seed_decision(
        client,
        description="proposed decision",
        signoff={"state": "proposed", "signer": "silong"},
    )
    await _seed_decision(
        client,
        description="reflected decision",
        status="reflected",
        source_type="slack",
        source_ref="#payments",
        signoff={"state": "ratified", "signer": "jin"},
    )

    summary = await build_project_pulse(adapter, drift_findings=[{"region": "x.py"}])
    payload = summary.to_dict()

    encoded = json.dumps(payload)
    decoded = json.loads(encoded)
    assert decoded["is_all_clear"] is False
    assert decoded["health"]["drifted_regions"] == 1
    assert isinstance(decoded["needs_attention"], list)
    assert isinstance(decoded["recently_learned"], list)
    assert decoded["needs_attention"][0]["kind"] == "awaiting_ratification"


# ── 7. drift_findings feeds health + suggestion ───────────────────────────


async def test_drift_findings_argument_feeds_health_and_suggestion() -> None:
    """Injected drift_findings drive drifted_regions count + the suggestion + not-all-clear."""
    adapter, client = await _fresh_adapter("drift_arg")
    await _seed_decision(
        client,
        description="reflected decision",
        status="reflected",
        signoff={"state": "ratified", "signer": "jin"},
    )

    summary = await build_project_pulse(
        adapter,
        drift_findings=[{"region": "a.py"}, {"region": "b.py"}, {"region": "c.py"}],
    )

    assert summary.health.drifted_regions == 3
    assert summary.is_all_clear is False
    assert summary.suggested_next_move.startswith("Review 3 drifted regions")


# ── 8. fail-soft when a section errors ────────────────────────────────────


async def test_build_does_not_crash_when_a_section_errors() -> None:
    """A ledger query raising for one section degrades that section; summary still builds."""
    adapter, client = await _fresh_adapter("fail_soft")
    await _seed_decision(
        client,
        description="healthy decision",
        status="reflected",
        signoff={"state": "ratified", "signer": "jin"},
    )

    # Seam off only the failure mode: get_all_decisions raises, so the
    # needs_attention + recently_learned sections must degrade — but health
    # (which uses get_decisions_by_status) and the overall build still succeed.
    async def _boom(*_args: object, **_kwargs: object) -> list[dict]:
        raise RuntimeError("simulated ledger failure")

    adapter.get_all_decisions = _boom  # type: ignore[method-assign]

    summary = await build_project_pulse(adapter)

    assert isinstance(summary, ProjectPulseSummary)
    assert summary.needs_attention == []
    assert summary.recently_learned == []
    # Health still computed via the un-broken get_decisions_by_status path.
    assert summary.health.decisions_reflected == 1
    # to_dict still works on the degraded-but-built summary.
    assert json.dumps(summary.to_dict())


# ── 9. _parse_since grammar (#437 Phase 2) ────────────────────────────────


def test_parse_since_accepts_iso_date() -> None:
    """An ISO date parses to a UTC-aware midnight cutoff."""
    cutoff = _parse_since("2026-05-20")
    assert cutoff.year == 2026
    assert cutoff.month == 5
    assert cutoff.day == 20
    assert cutoff.tzinfo is not None


def test_parse_since_accepts_today_and_yesterday() -> None:
    """``today`` / ``yesterday`` resolve relative to the injected ``now``."""
    now = datetime(2026, 5, 21, 15, 30, tzinfo=UTC)
    today = _parse_since("today", now=now)
    yesterday = _parse_since("yesterday", now=now)
    assert today == datetime(2026, 5, 21, tzinfo=UTC)
    assert yesterday == datetime(2026, 5, 20, tzinfo=UTC)


def test_parse_since_accepts_n_days() -> None:
    """``Nd`` resolves to N days before midnight of the injected ``now``."""
    now = datetime(2026, 5, 21, 9, 0, tzinfo=UTC)
    assert _parse_since("7d", now=now) == datetime(2026, 5, 14, tzinfo=UTC)


def test_parse_since_rejects_garbage() -> None:
    """An unparseable token raises ``SinceParseError``."""
    with pytest.raises(SinceParseError):
        _parse_since("next-tuesday-ish")
    with pytest.raises(SinceParseError):
        _parse_since("")


# ── 10. since filter on build_project_pulse (#437 Phase 2) ────────────────


async def test_since_filters_recently_learned_by_recency() -> None:
    """``since`` drops decisions dated before the cutoff from recently-learned."""
    adapter, client = await _fresh_adapter("since_filter")
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    recent = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    await _seed_decision(client, description="ancient", status="reflected", created_at=old)
    await _seed_decision(client, description="fresh", status="reflected", created_at=recent)

    summary = await build_project_pulse(adapter, since="3d")

    learned = {item.summary for item in summary.recently_learned}
    assert "fresh" in learned
    assert "ancient" not in learned


async def test_since_filters_needs_attention_by_recency() -> None:
    """``since`` also bounds the needs-attention section."""
    adapter, client = await _fresh_adapter("since_attention")
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    recent = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    await _seed_decision(
        client,
        description="old pending",
        signoff={"state": "proposed", "signer": "jin"},
        created_at=old,
    )
    await _seed_decision(
        client,
        description="recent pending",
        signoff={"state": "proposed", "signer": "silong"},
        created_at=recent,
    )

    summary = await build_project_pulse(adapter, since="3d")

    attention = {item.summary for item in summary.needs_attention}
    assert "recent pending" in attention
    assert "old pending" not in attention


async def test_bad_since_raises_before_any_query() -> None:
    """A bad ``since`` token raises ``SinceParseError`` from build_project_pulse."""
    adapter, _client = await _fresh_adapter("since_bad")
    with pytest.raises(SinceParseError):
        await build_project_pulse(adapter, since="garbage")


# ── 11. feature filter on build_project_pulse (#437 Phase 2) ──────────────


async def test_feature_filters_by_feature_hint() -> None:
    """``feature`` keeps only decisions whose ``feature_hint`` matches."""
    adapter, client = await _fresh_adapter("feature_filter")
    await _seed_decision(
        client,
        description="checkout pending",
        signoff={"state": "proposed", "signer": "jin"},
        feature_hint="checkout",
    )
    await _seed_decision(
        client,
        description="search pending",
        signoff={"state": "proposed", "signer": "silong"},
        feature_hint="search",
    )

    summary = await build_project_pulse(adapter, feature="checkout")

    attention = {item.summary for item in summary.needs_attention}
    assert "checkout pending" in attention
    assert "search pending" not in attention
    learned = {item.summary for item in summary.recently_learned}
    assert "checkout pending" in learned
    assert "search pending" not in learned


# ── 12. defaults preserve Phase 1 behavior (#437 Phase 2) ─────────────────


async def test_since_and_feature_default_none_preserves_phase1_behavior() -> None:
    """``since=None`` + ``feature=None`` (the defaults) filter nothing."""
    adapter, client = await _fresh_adapter("defaults")
    await _seed_decision(
        client,
        description="pending one",
        signoff={"state": "proposed", "signer": "jin"},
        feature_hint="checkout",
    )
    await _seed_decision(
        client,
        description="pending two",
        signoff={"state": "proposed", "signer": "silong"},
        feature_hint="",
    )

    summary = await build_project_pulse(adapter)

    # Both decisions surface — no filter applied.
    assert len(summary.needs_attention) == 2
    assert len(summary.recently_learned) == 2
