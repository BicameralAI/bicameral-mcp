"""Phase 4 — pipeline integration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from team_server.config import RulesDisabled
from team_server.extraction.heuristic_classifier import TriggerRules
from team_server.extraction.pipeline import extract_decision_pipeline


@pytest.mark.asyncio
async def test_pipeline_short_circuits_on_negative_classification():
    calls = {"n": 0}

    async def stub_llm(text, triggers):
        calls["n"] += 1
        return {"decisions": [], "extractor_version": "stub"}

    rules = TriggerRules(keywords=("decided",))
    result = await extract_decision_pipeline(
        text="random chatter",
        message={"text": "random chatter"},
        context={},
        rules_or_disabled=rules,
        llm_extract_fn=stub_llm,
    )
    assert calls["n"] == 0
    assert result["decisions"] == []
    assert result["extractor_version"] is None
    assert result["skipped"] is False


@pytest.mark.asyncio
async def test_pipeline_invokes_llm_on_positive_classification():
    received = {}

    async def stub_llm(text, triggers):
        received["text"] = text
        received["triggers"] = triggers
        return {
            "decisions": [{"summary": "use REST"}],
            "extractor_version": "stub-v1",
        }

    rules = TriggerRules(keywords=("decided",))
    result = await extract_decision_pipeline(
        text="we decided REST",
        message={"text": "we decided REST"},
        context={},
        rules_or_disabled=rules,
        llm_extract_fn=stub_llm,
    )
    assert received["text"] == "we decided REST"
    assert "decided" in received["triggers"]
    assert result["decisions"] == [{"summary": "use REST"}]
    assert result["extractor_version"] == "stub-v1"
    assert "decided" in result["matched_triggers"]


@pytest.mark.asyncio
async def test_slack_worker_routes_through_pipeline_with_thread_context(monkeypatch):
    """Phase 4 — slack_worker passes the slack message's reactions and
    position-in-batch to the pipeline as context."""
    import os as _os

    _os.environ["BICAMERAL_TEAM_SERVER_SURREAL_URL"] = "memory://"
    _os.environ["BICAMERAL_TEAM_SERVER_SECRET_KEY"] = "EYSr77qKo0UijHGnER5qYFBY5ZZePeWeE-ZMWYXyKKA="
    from team_server.config import (
        HeuristicGlobalRules,
        SlackConfig,
        SlackHeuristics,
        TeamServerConfig,
    )
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers.slack_worker import poll_once

    config = TeamServerConfig(
        slack=SlackConfig(
            heuristics=SlackHeuristics(
                global_rules=HeuristicGlobalRules(keywords=["decided"]),
            )
        ),
    )
    captured = {}

    async def stub_pipeline(*, text, message, context, rules_or_disabled, llm_extract_fn):
        captured["context"] = context
        return {
            "decisions": [],
            "classifier_version": "h-test",
            "matched_triggers": [],
            "extractor_version": None,
            "skipped": False,
        }

    import team_server.workers.slack_worker as sw

    monkeypatch.setattr(sw, "extract_decision_pipeline", stub_pipeline)

    class _SlackStub:
        def conversations_history(self, channel):
            return {
                "ok": True,
                "messages": [
                    {
                        "ts": "1.0",
                        "text": "we decided REST",
                        "thread_ts": "1.0",
                        "reactions": [{"name": "white_check_mark", "count": 1}],
                    },
                ],
            }

    async def stub_extractor(t):
        return {"decisions": []}

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await poll_once(
            db_client=client,
            slack_client=_SlackStub(),
            workspace_team_id="T1",
            channels=["C1"],
            extractor=stub_extractor,
            config=config,
        )
        assert captured["context"]["thread_ts"] == "1.0"
        assert captured["context"]["reactions"][0]["name"] == "white_check_mark"
        assert captured["context"]["thread_position"] == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_notion_worker_routes_through_pipeline_with_edit_context(monkeypatch):
    """Phase 4 — notion_worker passes last_edited_by + edit_count context."""
    import os as _os

    _os.environ["BICAMERAL_TEAM_SERVER_SURREAL_URL"] = "memory://"
    from team_server.config import (
        HeuristicGlobalRules,
        NotionConfig,
        NotionHeuristics,
        TeamServerConfig,
    )
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import notion_worker

    config = TeamServerConfig(
        notion=NotionConfig(
            heuristics=NotionHeuristics(
                global_rules=HeuristicGlobalRules(keywords=["approved"]),
            )
        ),
    )
    captured = {}

    async def stub_pipeline(*, text, message, context, rules_or_disabled, llm_extract_fn):
        captured["context"] = context
        return {
            "decisions": [],
            "classifier_version": "h-test",
            "matched_triggers": [],
            "extractor_version": None,
            "skipped": False,
        }

    monkeypatch.setattr(notion_worker, "extract_decision_pipeline", stub_pipeline)

    async def fake_list_databases(token):
        return [("db1", "D1")]

    async def fake_query_database(token, db_id, watermark):
        yield {
            "id": "p1",
            "last_edited_time": "2026-05-02T10:00:00Z",
            "last_edited_by": {"id": "user-42"},
            "edit_count": 7,
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "approved"}]},
            },
        }

    async def fake_fetch_page_blocks(token, page_id):
        return []

    monkeypatch.setattr(notion_worker.nc, "list_databases", fake_list_databases)
    monkeypatch.setattr(notion_worker.nc, "query_database", fake_query_database)
    monkeypatch.setattr(notion_worker.nc, "fetch_page_blocks", fake_fetch_page_blocks)

    async def stub_extractor(t):
        return {"decisions": []}

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await notion_worker.poll_once(client, "tok", stub_extractor, config=config)
        assert captured["context"]["last_edited_by"] == "user-42"
        assert captured["context"]["edit_count"] == 7
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_pipeline_skips_when_rules_disabled():
    calls = {"n": 0}

    async def stub_llm(text, triggers):
        calls["n"] += 1
        return {"decisions": []}

    result = await extract_decision_pipeline(
        text="anything",
        message={"text": "anything"},
        context={},
        rules_or_disabled=RulesDisabled(),
        llm_extract_fn=stub_llm,
    )
    assert calls["n"] == 0
    assert result["skipped"] is True
    assert result["decisions"] == []
    assert result["extractor_version"] is None
