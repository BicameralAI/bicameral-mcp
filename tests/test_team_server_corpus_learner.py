"""Phase 5 — corpus learner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def memory_url(monkeypatch):
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SURREAL_URL", "memory://")


async def _seed_team_events(client, source_type: str, summaries: list[str]):
    for i, summary in enumerate(summaries):
        await client.query(
            "CREATE team_event CONTENT { author_email: 'team-server@T.bicameral', "
            "event_type: 'ingest', sequence: $s, payload: $p }",
            {"s": i + 1, "p": {
                "source_type": source_type,
                "source_ref": f"X/{i}",
                "extraction": {
                    "decisions": [{
                        "summary": summary,
                        "context_snippet": summary,
                    }],
                },
            }},
        )


@pytest.mark.asyncio
async def test_learner_extracts_top_ngrams_from_ratified_decisions():
    from team_server.db import build_client
    from team_server.extraction.corpus_learner import learn_corpus_terms
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await _seed_team_events(client, "slack", [
            "approved by tech lead",
            "approved by tech lead",
            "approved by tech lead",
            "rejected for now",
        ])
        terms = await learn_corpus_terms(client, source_type="slack", top_n=20)
        term_strs = [t["term"] for t in terms]
        assert "approved by tech" in term_strs
        approved = next(t for t in terms if t["term"] == "approved by tech")
        assert approved["support_count"] == 6  # 3 decisions × 2 (summary+snippet)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_learner_respects_denylist():
    from team_server.db import build_client
    from team_server.extraction.corpus_learner import learn_corpus_terms
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await _seed_team_events(client, "slack", [
            "approved by lead",
            "approved by lead",
        ])
        terms = await learn_corpus_terms(
            client, source_type="slack", top_n=20, denylist=["approved by"],
        )
        term_strs = [t["term"] for t in terms]
        assert not any("approved by" in t for t in term_strs)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_learner_persists_results_to_learned_heuristic_terms_table():
    from team_server.db import build_client
    from team_server.extraction.corpus_learner import (
        learn_corpus_terms, persist_learned_terms,
    )
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await _seed_team_events(client, "slack", ["use rest api", "use rest api"])
        terms = await learn_corpus_terms(client, source_type="slack", top_n=10)
        await persist_learned_terms(client, "slack", terms)
        rows = await client.query(
            "SELECT term, support_count FROM learned_heuristic_terms "
            "WHERE source_type = 'slack'"
        )
        assert any(r["term"] == "use rest api" for r in rows)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_learn_corpus_terms_is_deterministic_for_same_input():
    from team_server.db import build_client
    from team_server.extraction.corpus_learner import learn_corpus_terms
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await _seed_team_events(client, "slack", ["x y z", "x y z", "a b"])
        a = await learn_corpus_terms(client, source_type="slack", top_n=10)
        b = await learn_corpus_terms(client, source_type="slack", top_n=10)
        assert a == b
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_resolve_rules_merges_learned_terms_into_keywords():
    from team_server.config import (
        TeamServerConfig, SlackConfig, SlackHeuristics, HeuristicGlobalRules,
        resolve_rules_for_slack,
    )
    config = TeamServerConfig(
        slack=SlackConfig(heuristics=SlackHeuristics(
            global_rules=HeuristicGlobalRules(keywords=["decided"]),
        )),
    )
    rules = resolve_rules_for_slack(
        config, channel_id="C-anything", learned=("approved by",),
    )
    assert "approved by" in rules.learned_keywords
    assert "decided" in rules.keywords
