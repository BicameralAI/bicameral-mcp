"""Functionality tests for team_server Phase 3 — Notion task registration."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def env_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SURREAL_URL", "memory://")
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SECRET_KEY",
                       "EYSr77qKo0UijHGnER5qYFBY5ZZePeWeE-ZMWYXyKKA=")
    # Default: point config to a non-existent path so notion is OFF unless test sets NOTION_TOKEN
    monkeypatch.setenv("BICAMERAL_CONFIG_PATH", str(tmp_path / "no_config.yml"))
    monkeypatch.delenv("NOTION_TOKEN", raising=False)


@pytest.mark.asyncio
async def test_app_starts_notion_worker_when_token_env_set(monkeypatch):
    from fastapi.testclient import TestClient
    from team_server import app as app_module

    monkeypatch.setenv("NOTION_TOKEN", "fake-token")
    monkeypatch.setattr(app_module, "NOTION_POLL_INTERVAL_SECONDS", 0)

    calls = {"notion_iter": 0}

    async def stub_iteration(db_client, token, extractor):
        calls["notion_iter"] += 1

    monkeypatch.setattr(app_module, "run_notion_iteration", stub_iteration)

    # Need to re-import config to pick up the new env var-based DEFAULT_CONFIG_PATH
    # but app.py imports DEFAULT_CONFIG_PATH at module load time.
    # The notion_client.load_token call uses the path, but env NOTION_TOKEN
    # takes precedence — so this test still works without config-path mutation.

    app = app_module.create_app()
    with TestClient(app) as _client:
        names = {t.get_name() for t in app.state.worker_tasks}
        assert "team-server-worker-notion" in names
        for _ in range(20):
            await asyncio.sleep(0.05)
            if calls["notion_iter"] >= 1:
                break
    assert calls["notion_iter"] >= 1


@pytest.mark.asyncio
async def test_app_does_not_start_notion_worker_when_token_unset(monkeypatch):
    from fastapi.testclient import TestClient
    from team_server import app as app_module

    # Ensure no token resolution succeeds
    monkeypatch.delenv("NOTION_TOKEN", raising=False)

    app = app_module.create_app()
    with TestClient(app) as _client:
        names = {t.get_name() for t in app.state.worker_tasks}
        assert "team-server-worker-slack" in names
        assert "team-server-worker-notion" not in names


@pytest.mark.asyncio
async def test_notion_worker_task_is_cancelled_on_shutdown(monkeypatch):
    from fastapi.testclient import TestClient
    from team_server import app as app_module

    monkeypatch.setenv("NOTION_TOKEN", "fake-token")
    monkeypatch.setattr(app_module, "NOTION_POLL_INTERVAL_SECONDS", 60)

    async def stub_iteration(db_client, token, extractor):
        return None

    monkeypatch.setattr(app_module, "run_notion_iteration", stub_iteration)

    app = app_module.create_app()
    captured: list = []
    with TestClient(app) as _client:
        captured.extend(app.state.worker_tasks)
    for t in captured:
        if t.get_name() == "team-server-worker-notion":
            assert t.done() is True
            return
    pytest.fail("notion task not registered")


@pytest.mark.asyncio
async def test_notion_worker_loop_continues_after_single_iteration_raises(monkeypatch):
    from fastapi.testclient import TestClient
    from team_server import app as app_module

    monkeypatch.setenv("NOTION_TOKEN", "fake-token")
    monkeypatch.setattr(app_module, "NOTION_POLL_INTERVAL_SECONDS", 0)

    state = {"calls": 0}

    async def flaky_iteration(db_client, token, extractor):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("simulated")

    monkeypatch.setattr(app_module, "run_notion_iteration", flaky_iteration)

    app = app_module.create_app()
    with TestClient(app) as _client:
        for _ in range(40):
            await asyncio.sleep(0.05)
            if state["calls"] >= 2:
                break
    assert state["calls"] >= 2
