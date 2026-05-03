"""Phase 1 — allowlist sync runs at lifespan startup."""

from __future__ import annotations

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
    cfg.write_text(
        "slack:\n  workspaces:\n    - team_id: T-LIFESPAN\n      channels: [C-LIFE-1, C-LIFE-2]\n"
    )
    monkeypatch.setenv("BICAMERAL_CONFIG_PATH", str(cfg))
    monkeypatch.setattr("team_server.config.DEFAULT_CONFIG_PATH", cfg)
    monkeypatch.setattr("team_server.app.DEFAULT_CONFIG_PATH", cfg)
    return cfg


@pytest.mark.asyncio
async def test_lifespan_invokes_sync_channel_allowlist_with_loaded_config(env_setup, monkeypatch):
    """Behavior: lifespan calls sync_channel_allowlist exactly once at
    startup, with the loaded TeamServerConfig (workspace[0].team_id ==
    'T-LIFESPAN' and channels == ['C-LIFE-1', 'C-LIFE-2']).
    Functionality — exercises the lifespan→sync wiring."""
    from fastapi.testclient import TestClient

    from team_server import app as app_module

    captured = []

    async def stub_sync(client, config):
        captured.append(
            {
                "ws_count": len(config.slack.workspaces),
                "team_id": config.slack.workspaces[0].team_id if config.slack.workspaces else None,
                "channels": list(config.slack.workspaces[0].channels)
                if config.slack.workspaces
                else [],
            }
        )

    monkeypatch.setattr(app_module, "sync_channel_allowlist", stub_sync)

    app = app_module.create_app()
    with TestClient(app) as _client:
        pass
    assert len(captured) == 1
    assert captured[0]["team_id"] == "T-LIFESPAN"
    assert captured[0]["channels"] == ["C-LIFE-1", "C-LIFE-2"]


@pytest.mark.asyncio
async def test_lifespan_continues_when_sync_raises(env_setup, monkeypatch):
    """Behavior: if sync_channel_allowlist raises mid-startup, the
    lifespan logs and continues — DB stays connected, app.state.db is
    set, workers still register. Failure isolation invariant."""
    from fastapi.testclient import TestClient

    from team_server import app as app_module

    async def raising_sync(client, config):
        raise RuntimeError("simulated sync failure")

    monkeypatch.setattr(app_module, "sync_channel_allowlist", raising_sync)

    app = app_module.create_app()
    with TestClient(app) as client:
        # Health endpoint still serves; app.state.db is set.
        resp = client.get("/health")
        assert resp.status_code == 200
        assert app.state.db is not None
