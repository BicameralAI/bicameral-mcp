"""v0.4.8 — ingest → brief auto-chain regression tests.

After a successful ``bicameral.ingest`` call, the handler derives a
topic from the payload and auto-fires ``handle_brief`` on it. The
brief response is returned embedded in ``IngestResponse.brief`` so
callers can surface divergences, drift candidates, and suggested
questions in the same round-trip that produced the new decisions.

These tests lock in:

  1. Topic derivation priority (query → longest decision → title → "")
  2. ``IngestResponse.brief`` is populated when the payload has a
     derivable topic
  3. ``IngestResponse.brief`` is ``None`` when the topic is empty
  4. Fresh decisions are visible in the chained brief's matches
  5. Brief-chain failure doesn't fail the ingest (the brief field is
     set to None and the ingest still returns a valid response)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from adapters.ledger import get_ledger, reset_ledger_singleton
from context import BicameralContext
from handlers.ingest import _derive_brief_topic, _word_truncate, handle_ingest


# ── Pure helpers ────────────────────────────────────────────────────


def test_word_truncate_under_limit_returns_unchanged():
    assert _word_truncate("hello world", 20) == "hello world"


def test_word_truncate_drops_half_word():
    # 20 chars: "the quick brown fox "
    # clipped at 18: "the quick brown fo" → rsplit → "the quick brown"
    out = _word_truncate("the quick brown fox jumps", 18)
    assert out == "the quick brown"


def test_word_truncate_single_long_token_raw_clip():
    # No space in the slice → fall back to raw slice
    out = _word_truncate("supercalifragilisticexpialidocious", 10)
    assert out == "supercalif"


def test_derive_topic_prefers_query():
    payload = {
        "query": "rate limiting strategy",
        "decisions": [{"description": "unrelated long decision text"}],
    }
    assert _derive_brief_topic(payload) == "rate limiting strategy"


def test_derive_topic_longest_decision():
    payload = {
        "query": "",
        "decisions": [
            {"description": "short"},
            {"description": "this is the longest description by a wide margin"},
            {"description": "medium length"},
        ],
    }
    assert (
        _derive_brief_topic(payload)
        == "this is the longest description by a wide margin"
    )


def test_derive_topic_skips_action_item_prefix():
    """Action items get prefixed with '[Action: owner]' during normalization.
    The topic deriver reads raw payload.decisions, so it never sees the
    prefix — confirmed here by absence of a decisions field entirely.
    """
    payload = {
        "query": "",
        "decisions": [],  # only action_items in a real payload
        "title": "fallback title works",
    }
    assert _derive_brief_topic(payload) == "fallback title works"


def test_derive_topic_returns_empty_when_nothing_usable():
    assert _derive_brief_topic({}) == ""
    assert _derive_brief_topic({"query": "", "decisions": [], "title": ""}) == ""


def test_derive_topic_uses_title_field_on_decision():
    payload = {
        "decisions": [
            {"title": "auth rewrite", "description": ""},
        ],
    }
    assert _derive_brief_topic(payload) == "auth rewrite"


def test_derive_topic_word_boundary_on_200_cap():
    long_desc = " ".join(["rate"] * 80)  # ~400 chars, word-separated
    payload = {"decisions": [{"description": long_desc}]}
    topic = _derive_brief_topic(payload)
    assert len(topic) <= 200
    assert not topic.endswith(" ")
    # Must not end mid-word — each word is "rate", so truncation has to
    # land on a word boundary.
    assert all(tok == "rate" for tok in topic.split())


# ── End-to-end chain test ───────────────────────────────────────────


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


def _payload_with_decision(repo: str, description: str) -> dict:
    """Minimal payload with pre-resolved code regions so we skip the
    grounding BM25 path and stay laser-focused on the chain wiring.
    """
    return {
        "query": description,
        "repo": repo,
        "mappings": [
            {
                "span": {
                    "span_id": "chain-0",
                    "source_type": "transcript",
                    "text": description,
                    "source_ref": "v048-chain-test",
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


class _Ctx:
    """Wrap BicameralContext.from_env() and expose it uniformly."""
    @staticmethod
    def fresh() -> BicameralContext:
        return BicameralContext.from_env()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_populates_brief_field(_isolated_ledger):
    """A successful ingest with a derivable topic must return
    ``IngestResponse.brief`` as a non-null ``BriefResponse``.
    """
    ledger = get_ledger()
    await ledger.connect()

    ctx = _Ctx.fresh()
    payload = _payload_with_decision(
        repo=str(_isolated_ledger),
        description="Apply 10% discount on orders of $100 or more",
    )

    response = await handle_ingest(ctx, payload)

    assert response.brief is not None, (
        "IngestResponse.brief must be populated when the payload has a "
        "derivable topic; got None"
    )
    assert response.brief.topic.strip() != ""
    # The chained brief ran against the fresh ingest — should see at
    # least the just-ingested decision in its decisions list.
    assert len(response.brief.decisions) >= 1, (
        f"Chained brief should see at least the fresh decision, "
        f"got {len(response.brief.decisions)} matches"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_skips_brief_when_topic_empty(_isolated_ledger):
    """When the payload has no query, no decisions, and no title, the
    auto-chain skips brief entirely. ``IngestResponse.brief`` is ``None``.
    """
    ledger = get_ledger()
    await ledger.connect()

    ctx = _Ctx.fresh()
    # Manually build a payload with only an action_item (produces a
    # mapping but no raw decisions, no query, no title).
    payload = {
        "repo": str(_isolated_ledger),
        "action_items": [
            {"action": "write unit tests", "owner": ""},
        ],
    }

    response = await handle_ingest(ctx, payload)

    assert response.brief is None, (
        "IngestResponse.brief must be None when the payload has no "
        "derivable topic; got a BriefResponse"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_brief_chain_failure_doesnt_fail_ingest(_isolated_ledger):
    """If the chained ``handle_brief`` raises, the ingest itself must
    still return a valid IngestResponse with ``brief=None``.
    """
    ledger = get_ledger()
    await ledger.connect()

    ctx = _Ctx.fresh()
    payload = _payload_with_decision(
        repo=str(_isolated_ledger),
        description="Apply 10% discount on orders of $100 or more",
    )

    # Force brief to blow up on entry.
    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated brief crash")

    with patch("handlers.brief.handle_brief", side_effect=_boom):
        response = await handle_ingest(ctx, payload)

    assert response.ingested is True, (
        "Ingest must still report ingested=True when the brief chain "
        "crashes — brief is a post-phase, never load-bearing"
    )
    assert response.brief is None, (
        "On brief chain failure, IngestResponse.brief must be None"
    )
