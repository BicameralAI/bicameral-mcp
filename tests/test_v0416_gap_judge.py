"""v0.4.16 — caller-session LLM gap judge tests.

Three layers:

  1. Pure rubric-shape tests (IO-free) — guards against silent rubric
     drift and locks in the 5 canonical categories + their output
     shapes.
  2. Context pack builder tests against the real surreal ledger —
     honest empty path, related-decision cross-linking, phrasing-gap
     forwarding.
  3. End-to-end ingest → brief → judgment_payload auto-chain tests.
     Also locks in: standalone brief never carries a judgment_payload,
     gap-judge chain failure is non-fatal to ingest.

All tests use the same in-memory surreal ledger + seeded git repo
pattern as ``tests/test_v048_ingest_brief_chain.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from adapters.ledger import get_ledger, reset_ledger_singleton
from context import BicameralContext
from contracts import BriefResponse, GapRubric, GapRubricCategory
from handlers.brief import handle_brief
from handlers.gap_judge import (
    _JUDGMENT_PROMPT,
    _build_context_decisions,
    _build_rubric,
    handle_judge_gaps,
)
from handlers.ingest import handle_ingest


# ── Layer 1: pure rubric shape tests ────────────────────────────────


def test_rubric_has_five_canonical_categories():
    """Guards against silent rubric drift. Order is load-bearing —
    the skill renders sections in this exact sequence."""
    rubric = _build_rubric()
    assert isinstance(rubric, GapRubric)
    assert rubric.version == "v0.4.16"
    keys = [c.key for c in rubric.categories]
    assert keys == [
        "missing_acceptance_criteria",
        "underdefined_edge_cases",
        "infrastructure_gap",
        "underspecified_integration",
        "missing_data_requirements",
    ], f"Rubric category keys or order drifted: got {keys}"


def test_rubric_category_shapes_match_contract():
    """Each category has its documented output_shape + crawl flag."""
    rubric = _build_rubric()
    by_key = {c.key: c for c in rubric.categories}

    assert by_key["missing_acceptance_criteria"].output_shape == "bullet_list"
    assert by_key["missing_acceptance_criteria"].requires_codebase_crawl is False

    assert by_key["underdefined_edge_cases"].output_shape == "happy_sad_table"
    assert by_key["underdefined_edge_cases"].requires_codebase_crawl is False

    infra = by_key["infrastructure_gap"]
    assert infra.output_shape == "checklist"
    assert infra.requires_codebase_crawl is True
    assert len(infra.canonical_paths) >= 5, (
        "infrastructure_gap must ship with a non-trivial canonical_paths "
        f"list for the caller agent to crawl; got {infra.canonical_paths}"
    )
    assert ".github/workflows/" in infra.canonical_paths
    assert "Dockerfile" in infra.canonical_paths

    assert by_key["underspecified_integration"].output_shape == "dependency_radar"
    assert by_key["underspecified_integration"].requires_codebase_crawl is False

    assert by_key["missing_data_requirements"].output_shape == "checklist"
    assert by_key["missing_data_requirements"].requires_codebase_crawl is False


def test_judgment_prompt_mentions_verbatim_contract():
    """The judgment_prompt is the per-call reinforcement of the
    VERBATIM surfacing rule. Without it, skills may drift."""
    assert "VERBATIM" in _JUDGMENT_PROMPT
    assert "caller-session" in _JUDGMENT_PROMPT


def test_rubric_category_literal_rejects_bogus_key():
    """Guard: Pydantic Literal on GapRubricCategory.key must reject
    anything outside the 5 canonical keys."""
    with pytest.raises(Exception):
        GapRubricCategory(
            key="bogus_category",  # type: ignore[arg-type]
            title="x",
            prompt="x",
            output_shape="bullet_list",
        )


def test_build_context_decisions_groups_related_by_symbol():
    """Decisions on the same (symbol, file_path) tuple cross-reference
    each other via related_decision_ids. Decisions never self-reference."""
    from contracts import CodeRegionSummary, DecisionMatch

    m1 = DecisionMatch(
        intent_id="i1",
        description="use token bucket",
        status="pending",
        confidence=0.9,
        source_ref="r1",
        code_regions=[
            CodeRegionSummary(
                file_path="src/limit.py", symbol="Limiter", lines=(1, 10), purpose="",
            )
        ],
        drift_evidence="",
        related_constraints=[],
        source_excerpt="e1",
        meeting_date="2026-04-01",
    )
    m2 = DecisionMatch(
        intent_id="i2",
        description="actually use leaky bucket",
        status="pending",
        confidence=0.9,
        source_ref="r2",
        code_regions=[
            CodeRegionSummary(
                file_path="src/limit.py", symbol="Limiter", lines=(1, 10), purpose="",
            )
        ],
        drift_evidence="",
        related_constraints=[],
        source_excerpt="e2",
        meeting_date="2026-04-02",
    )
    m3 = DecisionMatch(
        intent_id="i3",
        description="unrelated — different symbol",
        status="pending",
        confidence=0.9,
        source_ref="r3",
        code_regions=[
            CodeRegionSummary(
                file_path="src/other.py", symbol="Other", lines=(1, 10), purpose="",
            )
        ],
        drift_evidence="",
        related_constraints=[],
        source_excerpt="e3",
        meeting_date="2026-04-03",
    )

    ctx_decisions = _build_context_decisions([m1, m2, m3])
    by_id = {d.intent_id: d for d in ctx_decisions}

    # i1 and i2 share (Limiter, src/limit.py) → related to each other
    assert by_id["i1"].related_decision_ids == ["i2"]
    assert by_id["i2"].related_decision_ids == ["i1"]
    # i3 on a different symbol → no cross-reference
    assert by_id["i3"].related_decision_ids == []
    # Self never appears in related set
    assert "i1" not in by_id["i1"].related_decision_ids
    # source_excerpt + meeting_date forwarded
    assert by_id["i1"].source_excerpt == "e1"
    assert by_id["i1"].meeting_date == "2026-04-01"


# ── Layer 2 & 3: integration against real surreal ledger ───────────


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _seed_repo(repo_root: Path, body: str) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@e.com")
    _git(repo_root, "config", "user.name", "t")
    (repo_root / "pricing.py").write_text(dedent(body).strip() + "\n")
    _git(repo_root, "add", ".")
    _git(
        repo_root,
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", "seed",
    )


@pytest.fixture
def _isolated_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", "memory://")
    repo_root = tmp_path / "repo"
    _seed_repo(
        repo_root,
        """
        def calculate_discount(order_total):
            if order_total >= 100:
                return order_total * 0.10
            return 0
        """,
    )
    monkeypatch.setenv("REPO_PATH", str(repo_root))
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "main")
    monkeypatch.chdir(repo_root)
    reset_ledger_singleton()
    yield repo_root
    reset_ledger_singleton()


def _payload_with_decision(
    repo: str,
    description: str,
    span_text: str | None = None,
    span_id: str = "v0416-chain-0",
    source_ref: str = "v0416-gap-judge-test",
    meeting_date: str = "2026-04-15",
) -> dict:
    """Matches the shape from test_v048_ingest_brief_chain._payload_with_decision
    but lets the caller set span.text (the source excerpt) and meeting_date
    so the v0.4.14 source_excerpt plumbing flows through."""
    return {
        "query": description,
        "repo": repo,
        "mappings": [
            {
                "span": {
                    "span_id": span_id,
                    "source_type": "transcript",
                    "text": span_text or description,
                    "source_ref": source_ref,
                    "meeting_date": meeting_date,
                },
                "intent": description,
                "symbols": ["calculate_discount"],
                "code_regions": [
                    {
                        "file_path": "pricing.py",
                        "symbol": "calculate_discount",
                        "type": "function",
                        "start_line": 1,
                        "end_line": 4,
                        "purpose": "pricing rule",
                    }
                ],
                "dependency_edges": [],
            }
        ],
    }


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_judge_gaps_honest_empty_path(_isolated_ledger):
    """No matches → handler returns None, not an empty payload.

    The caller agent's skill contract depends on this: None means
    'skip rendering entirely'; an empty payload would force an
    awkward 'no gaps found' across all 5 categories for nothing."""
    ledger = get_ledger()
    await ledger.connect()
    ctx = BicameralContext.from_env()

    payload = await handle_judge_gaps(
        ctx, topic="topic-that-has-no-decisions-anywhere",
    )
    assert payload is None


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_judge_gaps_builds_context_pack(_isolated_ledger):
    """After ingesting a decision, judge_gaps returns a populated
    payload carrying the decision, its source_excerpt, and the full
    rubric + judgment prompt."""
    ledger = get_ledger()
    await ledger.connect()
    ctx = BicameralContext.from_env()

    # Seed one decision with a known span.text so we can round-trip it.
    payload = _payload_with_decision(
        repo=str(_isolated_ledger),
        description="Apply 10% discount on orders of $100 or more",
        span_text=(
            "discounts are 10% on orders of $100 or more. below that, "
            "no discount. we can revisit pricing tiers next quarter"
        ),
        meeting_date="2026-03-12",
    )
    await handle_ingest(ctx, payload)

    # Search BM25 against the decision terms directly — generic topics
    # like "discount pricing" don't rank above min_confidence=0.3.
    judgment = await handle_judge_gaps(
        ctx, topic="apply 10% discount on orders",
    )
    assert judgment is not None, "judge_gaps must build a pack on matches"
    assert judgment.topic == "apply 10% discount on orders"
    assert judgment.rubric.version == "v0.4.16"
    assert len(judgment.rubric.categories) == 5
    assert "VERBATIM" in judgment.judgment_prompt
    assert judgment.as_of, "as_of must be populated with ISO datetime"

    assert len(judgment.decisions) >= 1, (
        "judge_gaps should see the just-ingested decision"
    )
    decision = judgment.decisions[0]
    assert "10%" in decision.description or "discount" in decision.description.lower()
    assert "10%" in decision.source_excerpt or "$100" in decision.source_excerpt
    assert decision.meeting_date == "2026-03-12"


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_chain_attaches_judgment_payload(_isolated_ledger):
    """End-to-end: a successful ingest with a derivable topic must
    return IngestResponse.judgment_payload alongside the brief."""
    ledger = get_ledger()
    await ledger.connect()
    ctx = BicameralContext.from_env()

    payload = _payload_with_decision(
        repo=str(_isolated_ledger),
        description="Apply 10% discount on orders of $100 or more",
        span_text="10% on orders ≥ $100, below that no discount",
    )
    response = await handle_ingest(ctx, payload)

    assert response.brief is not None, "v0.4.8 brief attach must still work"
    assert response.judgment_payload is not None, (
        "v0.4.16: judgment_payload must be attached when ingest→brief "
        "chain produces decisions"
    )
    assert response.judgment_payload.topic == response.brief.topic
    assert len(response.judgment_payload.decisions) >= 1
    assert len(response.judgment_payload.rubric.categories) == 5


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_chain_skips_judgment_when_brief_empty(_isolated_ledger):
    """When the payload has no derivable topic, brief is None —
    therefore judgment_payload must also be None."""
    ledger = get_ledger()
    await ledger.connect()
    ctx = BicameralContext.from_env()

    # action_item-only payload has no derivable topic → brief skipped
    payload = {
        "repo": str(_isolated_ledger),
        "action_items": [{"action": "write unit tests", "owner": ""}],
    }
    response = await handle_ingest(ctx, payload)

    assert response.brief is None
    assert response.judgment_payload is None, (
        "judgment_payload must be None when the brief chain skipped"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_standalone_brief_never_carries_judgment_payload():
    """BriefResponse must not carry a judgment_payload field at all.
    Only IngestResponse.judgment_payload exists — calling bicameral.brief
    directly should never produce a judgment pack."""
    assert "judgment_payload" not in BriefResponse.model_fields, (
        "BriefResponse must not have a judgment_payload field — "
        "the ingest→brief auto-chain is the only path that attaches it. "
        "Adding it to BriefResponse would force every standalone brief "
        "call to pay the judgment cost."
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_gap_judge_chain_failure_is_non_fatal(_isolated_ledger):
    """If the chained handle_judge_gaps raises, the ingest itself
    must still return a valid IngestResponse with brief populated
    and judgment_payload set to None."""
    ledger = get_ledger()
    await ledger.connect()
    ctx = BicameralContext.from_env()

    payload = _payload_with_decision(
        repo=str(_isolated_ledger),
        description="Apply 10% discount on orders of $100 or more",
    )

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated gap-judge crash")

    with patch("handlers.gap_judge.handle_judge_gaps", side_effect=_boom):
        response = await handle_ingest(ctx, payload)

    assert response.ingested is True
    # Brief still ran (this patches only gap_judge, not brief)
    assert response.brief is not None
    # Gap judge exploded → payload is None, ingest still returned
    assert response.judgment_payload is None


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_judge_gaps_forwards_phrasing_gaps(_isolated_ledger):
    """Decisions with tbd/open-question markers should produce
    phrasing_gaps entries on the payload — pre-existing evidence
    the caller agent can cite without re-discovering."""
    ledger = get_ledger()
    await ledger.connect()
    ctx = BicameralContext.from_env()

    payload = _payload_with_decision(
        repo=str(_isolated_ledger),
        description="Cache expiry tbd — decide before multi-tenant rollout",
        span_text="cache expiry is tbd until we decide multi-tenant scope",
    )
    await handle_ingest(ctx, payload)

    judgment = await handle_judge_gaps(ctx, topic="cache expiry")
    assert judgment is not None
    assert len(judgment.phrasing_gaps) >= 1, (
        "_extract_gaps catches 'tbd' — the forwarded phrasing_gaps "
        "should be non-empty so the caller agent can cite them as "
        "pre-existing evidence"
    )
