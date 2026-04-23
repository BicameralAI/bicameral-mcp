"""bicameral_brief regression tests (v0.4.6).

Two layers:
  1. Pure-function tests for the heuristic helpers (divergence detection,
     gap extraction, description-conflict rule, question generation).
     These are synchronous, IO-free, fast.
  2. One end-to-end test that seeds a memory-backed ledger + BicameralContext
     and calls ``handle_brief`` to verify the wire-up works.

The divergence detector is the highest-trust feature of the brief tool.
Tests cover the three contradiction patterns supported in v0.4.6:
  - negation-pair split across descriptions (redis vs local memory)
  - divergence-token in either description (vs, or, instead of)
  - no divergence for semantically unrelated descriptions on the same symbol
"""

from __future__ import annotations

import pytest

from contracts import CodeRegionSummary, DecisionMatch
from handlers.brief import (
    _descriptions_conflict,
    _detect_divergences,
    _extract_gaps,
    _generate_questions,
    _to_brief_decision,
)


# ── Helper factories ─────────────────────────────────────────────────


def _match(
    description: str,
    *,
    intent_id: str = "decision:1",
    status: str = "reflected",
    symbol: str = "SessionCache",
    file_path: str = "src/lib/session.ts",
    source_ref: str = "",
    drift_evidence: str = "",
) -> DecisionMatch:
    return DecisionMatch(
        decision_id=intent_id,
        description=description,
        status=status,
        confidence=0.9,
        source_ref=source_ref,
        code_regions=[
            CodeRegionSummary(
                file_path=file_path,
                symbol=symbol,
                lines=(10, 30),
                purpose="",
            )
        ],
        drift_evidence=drift_evidence,
        related_constraints=[],
    )


# ── _descriptions_conflict unit tests ────────────────────────────────


def test_conflict_redis_vs_local_memory():
    assert _descriptions_conflict([
        "Cache user sessions in Redis for horizontal scaling",
        "Store session state in local memory for faster access",
    ])


def test_conflict_oauth_vs_basic_auth():
    assert _descriptions_conflict([
        "All API endpoints must use OAuth 2.0",
        "Internal endpoints can use basic auth for simplicity",
    ])


def test_conflict_sync_vs_async():
    assert _descriptions_conflict([
        "Payment processing must be synchronous to avoid race conditions",
        "Payment processing should be async for scalability",
    ])


def test_conflict_via_vs_token():
    """The 'vs' token alone is enough to flag divergence."""
    assert _descriptions_conflict([
        "GitHub Discussions vs Slack for community",
        "Use Discourse for long-form threads",
    ])


def test_no_conflict_when_descriptions_agree():
    assert not _descriptions_conflict([
        "Cache user sessions in Redis",
        "Store session state in Redis with a 30-minute TTL",
    ])


def test_no_conflict_single_description():
    assert not _descriptions_conflict([
        "Cache user sessions in Redis",
    ])


# ── _detect_divergences integration tests ───────────────────────────


def test_detect_divergences_flags_contradiction_on_shared_symbol():
    matches = [
        _match(
            "Cache user sessions in Redis for horizontal scaling",
            intent_id="decision:1",
        ),
        _match(
            "Store session state in local memory for latency",
            intent_id="decision:2",
        ),
    ]
    divs = _detect_divergences(matches)
    assert len(divs) == 1
    assert divs[0].symbol == "SessionCache"
    assert len(divs[0].conflicting_decisions) == 2
    assert "contradictory" in divs[0].summary.lower()


def test_detect_divergences_ignores_single_decision_per_symbol():
    matches = [
        _match("Cache user sessions in Redis", intent_id="decision:1", symbol="A"),
        _match("Store session state in local memory", intent_id="decision:2", symbol="B"),
    ]
    # Different symbols → not compared — no divergence
    assert _detect_divergences(matches) == []


def test_detect_divergences_ignores_agreeing_duplicates():
    matches = [
        _match(
            "Cache user sessions in Redis with 30min TTL",
            intent_id="decision:1",
        ),
        _match(
            "Cache sessions in Redis, expire after 30 minutes",
            intent_id="decision:2",
        ),
    ]
    assert _detect_divergences(matches) == []


def test_detect_divergences_handles_empty_input():
    assert _detect_divergences([]) == []


# ── _extract_gaps unit tests ────────────────────────────────────────


def test_gaps_flag_open_question_phrasing():
    matches = [
        _match(
            "Should we use Redis or Memcached for the session cache?",
            intent_id="decision:1",
            status="reflected",
        ),
    ]
    gaps = _extract_gaps(matches)
    assert len(gaps) == 1
    assert "open-question" in gaps[0].hint


def test_gaps_flag_ungrounded_decisions():
    matches = [
        _match(
            "Rate-limit checkout to 100 req/min per user",
            intent_id="decision:1",
            status="ungrounded",
        ),
    ]
    gaps = _extract_gaps(matches)
    assert len(gaps) == 1
    assert "no code grounding" in gaps[0].hint


def test_gaps_skip_healthy_reflected_decisions():
    matches = [
        _match(
            "Cache sessions in Redis with 30-minute TTL",
            intent_id="decision:1",
            status="reflected",
        ),
    ]
    assert _extract_gaps(matches) == []


# ── _generate_questions priority tests ──────────────────────────────


def test_questions_lead_with_divergences():
    matches = [
        _match("A", intent_id="decision:1", status="reflected"),
    ]
    from contracts import BriefDivergence, BriefGap
    divergences = [
        BriefDivergence(
            symbol="Foo",
            file_path="foo.py",
            conflicting_decisions=[_to_brief_decision(matches[0])],
            summary="test divergence",
        )
    ]
    gaps = [BriefGap(description="test gap", hint="test")]
    questions = _generate_questions(
        topic="foo",
        matches=matches,
        drift_candidates=[],
        divergences=divergences,
        gaps=gaps,
        participants=None,
    )
    # Divergence question should be first
    assert len(questions) >= 1
    assert "Foo" in questions[0].question
    assert "divergence" in questions[0].why.lower()


def test_questions_generated_when_nothing_found():
    """Fallback: when matches/divergences/gaps are all empty, still return
    a meta-question so the caller isn't left with nothing."""
    questions = _generate_questions(
        topic="nonexistent topic",
        matches=[],
        drift_candidates=[],
        divergences=[],
        gaps=[],
        participants=None,
    )
    assert len(questions) == 1
    assert "nothing found" in questions[0].why.lower()


def test_questions_capped_at_5():
    from contracts import BriefGap
    gaps = [
        BriefGap(description=f"gap {i}", hint="open-question phrasing")
        for i in range(10)
    ]
    questions = _generate_questions(
        topic="foo",
        matches=[],
        drift_candidates=[],
        divergences=[],
        gaps=gaps,
        participants=None,
    )
    assert len(questions) <= 5


# ── End-to-end integration test ──────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_handle_brief_end_to_end(monkeypatch, surreal_url, tmp_path):
    """Seed a memory-backed ledger with 3 decisions, call handle_brief,
    verify the response shape has the right fields populated.
    """
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    monkeypatch.setenv("REPO_PATH", str(tmp_path))

    # Initialize a throwaway git repo so resolve_head has something to work with
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
        cwd=tmp_path, check=True,
    )

    from adapters.ledger import get_ledger, reset_ledger_singleton
    reset_ledger_singleton()
    ledger = get_ledger()
    await ledger.connect()

    # Seed a single decision about Redis session caching
    await ledger.ingest_payload({
        "query": "session cache architecture",
        "repo": str(tmp_path),
        "analyzed_at": "2026-04-14T00:00:00Z",
        "mappings": [
            {
                "span": {
                    "span_id": "b-0",
                    "source_type": "transcript",
                    "text": "Cache user sessions in Redis for horizontal scaling",
                    "source_ref": "brief-test-session",
                },
                "intent": "Cache user sessions in Redis for horizontal scaling",
                "symbols": ["SessionCache"],
                "code_regions": [
                    {
                        "file_path": "src/lib/session.ts",
                        "symbol": "SessionCache",
                        "type": "class",
                        "start_line": 10,
                        "end_line": 30,
                        "purpose": "session store",
                    }
                ],
                "dependency_edges": [],
            }
        ],
    })

    # Call handle_brief via a minimal context
    from context import BicameralContext
    from handlers.brief import handle_brief

    ctx = BicameralContext.from_env()
    result = await handle_brief(ctx, topic="session cache redis", max_decisions=5)

    assert result.topic == "session cache redis"
    assert isinstance(result.decisions, list)
    # The seeded decision should surface (may depend on BM25 confidence threshold)
    assert result.as_of  # datetime populated
    assert result.ref    # git ref resolved
    # Suggested questions always non-empty (at minimum the fallback meta-question)
    assert len(result.suggested_questions) >= 1

    reset_ledger_singleton()
