"""Behavioral tests for the unified `IngestResponse.brief` envelope (#187).

Replaces the dual `judgment_payload` + `judgment_payloads` shape with a
single `brief: BriefEnvelope | None` field. Server-side populates
`brief.gaps` from the gap-judge auto-chain output instead of leaving
the caller to render two separate sections.

Locked invariants:
- `brief` is populated whenever the gap-judge auto-chain produces findings
- `brief` is None when there's no signal (silent-on-no-signal — matches
  the existing PreflightResponse contract)
- `brief.action_hints` is the merged drift+gap hints
- Removed fields (`judgment_payload`, `judgment_payloads`) do not
  re-appear in `IngestResponse.model_dump(exclude_none=True)`
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from contracts import BriefEnvelope, BriefGap, GapRubric, GapRubricCategory


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
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        "seed",
    )


@pytest.fixture
def _isolated_ledger(monkeypatch, tmp_path):
    """Seed a tmp git repo + memory:// SurrealDB so handle_ingest has a real
    ledger + repo to operate against. Mirrors the fixture in
    tests/test_v0416_gap_judge.py."""
    from adapters.ledger import reset_ledger_singleton

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


def _payload_with_topic(intent: str = "Apply 10% discount on orders ≥ $100") -> dict:
    """Ingest payload with a topic that derives a feature_group, so the
    gap-judge auto-chain has something to fire against."""
    return {
        "query": intent,
        "repo": "test-repo",
        "commit_hash": "deadbeef00000000000000000000000000000000",
        "analyzed_at": "2026-04-29T12:00:00Z",
        "mappings": [
            {
                "span": {
                    "span_id": "span-brief-test",
                    "source_type": "transcript",
                    "text": intent,
                    "speaker": "Tester",
                    "source_ref": "brief-test-mtg",
                },
                "intent": intent,
                "symbols": [],
                "code_regions": [],
                "dependency_edges": [],
            }
        ],
    }


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_response_brief_populated_when_gaps_judged(_isolated_ledger):
    """When gap-judge auto-chain produces findings, IngestResponse.brief
    is populated AND brief.gaps carries at least one BriefGap entry."""
    from adapters.ledger import get_ledger
    from context import BicameralContext
    from handlers.ingest import handle_ingest

    ledger = get_ledger()
    await ledger.connect()
    ctx = BicameralContext.from_env()

    payload = _payload_with_topic()
    response = await handle_ingest(ctx, payload)

    assert response.brief is not None, (
        "brief should be populated when gap-judge auto-chain produces findings"
    )
    assert isinstance(response.brief, BriefEnvelope)
    assert len(response.brief.gaps) >= 1, (
        f"brief.gaps should carry findings; got {response.brief.gaps!r}"
    )
    for gap in response.brief.gaps:
        assert isinstance(gap, BriefGap)


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_response_brief_carries_rubric(_isolated_ledger):
    """brief.rubric carries the GapRubric reference (with its 5 fixed
    categories) that previously lived on judgment_payload.rubric."""
    from adapters.ledger import get_ledger
    from context import BicameralContext
    from handlers.ingest import handle_ingest

    ledger = get_ledger()
    await ledger.connect()
    ctx = BicameralContext.from_env()

    response = await handle_ingest(ctx, _payload_with_topic())

    assert response.brief is not None
    assert response.brief.rubric is not None, (
        "brief.rubric must be populated when brief is non-null"
    )
    assert isinstance(response.brief.rubric, GapRubric)
    assert len(response.brief.rubric.categories) == 5, (
        "rubric must carry the 5 fixed v0.4.19 categories"
    )
    for cat in response.brief.rubric.categories:
        assert isinstance(cat, GapRubricCategory)


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_response_brief_is_none_when_no_signal(_isolated_ledger):
    """When the ingest payload produces no decisions and no gap-judge
    findings, brief is None — silent-on-no-signal matches the
    PreflightResponse contract."""
    from adapters.ledger import get_ledger
    from context import BicameralContext
    from handlers.ingest import handle_ingest

    ledger = get_ledger()
    await ledger.connect()
    ctx = BicameralContext.from_env()

    # Empty mappings → no decisions extracted → no topics derivable →
    # gap-judge chain skipped → no brief populated.
    payload = {
        "query": "",
        "repo": "test-repo",
        "commit_hash": "deadbeef00000000000000000000000000000000",
        "analyzed_at": "2026-04-29T12:00:00Z",
        "mappings": [],
    }
    response = await handle_ingest(ctx, payload)

    assert response.brief is None, (
        f"brief must be None when there's no signal; got {response.brief!r}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_response_has_no_judgment_payload_field(_isolated_ledger):
    """The IngestResponse schema must NOT carry the legacy `judgment_payload`
    or `judgment_payloads` fields after #187. Locks against accidental
    re-introduction. Also covers the model_dump serialization path that
    external callers depend on."""
    from adapters.ledger import get_ledger
    from context import BicameralContext
    from handlers.ingest import handle_ingest

    ledger = get_ledger()
    await ledger.connect()
    ctx = BicameralContext.from_env()

    response = await handle_ingest(ctx, _payload_with_topic())
    dumped = response.model_dump()

    assert "judgment_payload" not in dumped, (
        "judgment_payload must be removed from IngestResponse per #187"
    )
    assert "judgment_payloads" not in dumped, (
        "judgment_payloads must be removed from IngestResponse per #187"
    )
    # And from the schema itself, not just the runtime dump:
    assert "judgment_payload" not in type(response).model_fields, (
        "judgment_payload must be removed from IngestResponse.model_fields"
    )
    assert "judgment_payloads" not in type(response).model_fields, (
        "judgment_payloads must be removed from IngestResponse.model_fields"
    )
