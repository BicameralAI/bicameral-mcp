"""Phase 5 — corpus learner lifecycle wiring."""

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
    monkeypatch.setenv(
        "BICAMERAL_TEAM_SERVER_SECRET_KEY", "EYSr77qKo0UijHGnER5qYFBY5ZZePeWeE-ZMWYXyKKA="
    )
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("BICAMERAL_CONFIG_PATH", str(cfg))
    monkeypatch.setattr("team_server.config.DEFAULT_CONFIG_PATH", cfg)
    monkeypatch.setattr("team_server.app.DEFAULT_CONFIG_PATH", cfg)
    return cfg


@pytest.mark.asyncio
async def test_lifespan_starts_corpus_learner_when_enabled(env_setup, monkeypatch):
    from fastapi.testclient import TestClient

    from team_server import app as app_module

    env_setup.write_text("corpus_learner:\n  enabled: true\n  interval_seconds: 0\n")

    calls = {"n": 0}

    async def stub_iteration(client, config, *, source_type="slack"):
        calls["n"] += 1

    monkeypatch.setattr(app_module, "run_corpus_learner_iteration", stub_iteration)

    app = app_module.create_app()
    with TestClient(app) as _client:
        names = {t.get_name() for t in app.state.worker_tasks}
        assert "team-server-worker-corpus-learner" in names
        for _ in range(20):
            await asyncio.sleep(0.05)
            if calls["n"] >= 1:
                break
    assert calls["n"] >= 1


@pytest.mark.asyncio
async def test_lifespan_does_not_start_corpus_learner_when_disabled(env_setup):
    from fastapi.testclient import TestClient

    from team_server import app as app_module

    env_setup.write_text("corpus_learner:\n  enabled: false\n")

    app = app_module.create_app()
    with TestClient(app) as _client:
        names = {t.get_name() for t in app.state.worker_tasks}
        assert "team-server-worker-corpus-learner" not in names
