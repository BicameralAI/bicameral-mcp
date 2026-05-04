"""Functionality tests for team_server Phase 0.5 — worker-task lifecycle pattern."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def env_setup(monkeypatch):
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SURREAL_URL", "memory://")
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SECRET_KEY",
                       "EYSr77qKo0UijHGnER5qYFBY5ZZePeWeE-ZMWYXyKKA=")


@pytest.mark.asyncio
async def test_lifespan_starts_slack_worker_when_workspaces_exist(monkeypatch):
    from fastapi.testclient import TestClient
    from team_server import app as app_module
    from team_server.app import create_app

    monkeypatch.setattr(app_module, "SLACK_POLL_INTERVAL_SECONDS", 0)

    calls = {"poll_once": 0}

    async def stub_poll_once(**kwargs):
        calls["poll_once"] += 1

    monkeypatch.setattr(
        "team_server.workers.slack_runner.poll_once", stub_poll_once
    )

    # Stub AsyncWebClient construction to avoid needing slack_sdk installed
    import team_server.workers.slack_runner as sr_mod

    class _StubClient:
        def __init__(self, token):
            self.token = token

    async def fake_run_iteration(db_client, extractor):
        # Bypass slack_sdk import by re-implementing the runner logic
        from team_server.auth.encryption import decrypt_token, load_key_from_env
        key = load_key_from_env()
        workspaces = await db_client.query(
            "SELECT id, slack_team_id, oauth_token_encrypted FROM workspace"
        )
        for ws in workspaces or []:
            ciphertext = ws["oauth_token_encrypted"].encode("utf-8")
            token = decrypt_token(ciphertext, key)
            await stub_poll_once(
                db_client=db_client,
                slack_client=_StubClient(token),
                workspace_team_id=ws["slack_team_id"],
                channels=[],
                extractor=extractor,
            )

    monkeypatch.setattr(app_module, "run_slack_iteration", fake_run_iteration)

    # Pre-seed a workspace by directly hooking into lifespan
    app = create_app()
    with TestClient(app) as _client:
        # Seed AFTER lifespan opened the DB
        from team_server.auth.encryption import encrypt_token, load_key_from_env
        key = load_key_from_env()
        encrypted = encrypt_token("xoxb-test", key).decode("utf-8")
        await app.state.db.client.query(
            "CREATE workspace CONTENT { name: 'W1', slack_team_id: 'T1', "
            "oauth_token_encrypted: $enc }",
            {"enc": encrypted},
        )
        # Wait briefly for the worker to fire at least once
        for _ in range(20):
            await asyncio.sleep(0.05)
            if calls["poll_once"] >= 1:
                break
    assert calls["poll_once"] >= 1


@pytest.mark.asyncio
async def test_lifespan_does_not_invoke_slack_poll_when_workspaces_empty(monkeypatch):
    from fastapi.testclient import TestClient
    from team_server import app as app_module
    from team_server.app import create_app

    monkeypatch.setattr(app_module, "SLACK_POLL_INTERVAL_SECONDS", 0)

    calls = {"poll_once": 0}

    async def stub_poll_once(**kwargs):
        calls["poll_once"] += 1

    async def fake_run_iteration(db_client, extractor):
        from team_server.auth.encryption import load_key_from_env
        load_key_from_env()
        workspaces = await db_client.query(
            "SELECT id, slack_team_id, oauth_token_encrypted FROM workspace"
        )
        for _ws in workspaces or []:
            await stub_poll_once()

    monkeypatch.setattr(app_module, "run_slack_iteration", fake_run_iteration)

    app = create_app()
    with TestClient(app) as _client:
        # Verify the slack task IS spawned even with empty workspaces
        names = {t.get_name() for t in app.state.worker_tasks}
        assert "team-server-worker-slack" in names
        # Allow the worker timer to fire
        for _ in range(10):
            await asyncio.sleep(0.05)
    assert calls["poll_once"] == 0


@pytest.mark.asyncio
async def test_lifespan_cancels_slack_worker_task_on_shutdown(monkeypatch):
    from fastapi.testclient import TestClient
    from team_server import app as app_module
    from team_server.app import create_app

    monkeypatch.setattr(app_module, "SLACK_POLL_INTERVAL_SECONDS", 60)

    async def fake_run_iteration(db_client, extractor):
        return None

    monkeypatch.setattr(app_module, "run_slack_iteration", fake_run_iteration)

    app = create_app()
    captured_tasks: list = []
    with TestClient(app) as _client:
        captured_tasks.extend(app.state.worker_tasks)
    # After context manager exits, lifespan teardown has cancelled tasks
    for t in captured_tasks:
        assert t.done() is True


@pytest.mark.asyncio
async def test_slack_worker_loop_continues_after_single_iteration_raises(monkeypatch):
    from team_server.workers.runner import worker_loop

    state = {"calls": 0}

    async def work_fn():
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("simulated")

    task = worker_loop("test", interval_seconds=0, work_fn=work_fn)
    try:
        for _ in range(40):
            await asyncio.sleep(0.01)
            if state["calls"] >= 2:
                break
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert state["calls"] >= 2


@pytest.mark.asyncio
async def test_slack_worker_iterates_all_workspaces_per_poll(monkeypatch):
    """Run run_slack_iteration directly with two workspace rows; assert
    the inner poll_once is invoked exactly twice with the per-workspace
    decrypted token (the encrypt round-trip is exercised end-to-end)."""
    from team_server.auth.encryption import encrypt_token, load_key_from_env
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import slack_runner

    captured = []

    async def stub_poll_once(**kwargs):
        captured.append({
            "team_id": kwargs["workspace_team_id"],
            "client_token": getattr(kwargs["slack_client"], "token", None),
        })

    monkeypatch.setattr(slack_runner, "poll_once", stub_poll_once)

    class _StubAWC:
        def __init__(self, token):
            self.token = token

    import sys as _sys
    fake_module = type(_sys)("slack_sdk")
    fake_web = type(_sys)("slack_sdk.web")
    fake_async = type(_sys)("slack_sdk.web.async_client")
    fake_async.AsyncWebClient = _StubAWC
    fake_web.async_client = fake_async
    fake_module.web = fake_web
    monkeypatch.setitem(_sys.modules, "slack_sdk", fake_module)
    monkeypatch.setitem(_sys.modules, "slack_sdk.web", fake_web)
    monkeypatch.setitem(_sys.modules, "slack_sdk.web.async_client", fake_async)

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        key = load_key_from_env()
        for tid, plaintext in [("T1", "xoxb-1"), ("T2", "xoxb-2")]:
            enc = encrypt_token(plaintext, key).decode("utf-8")
            await client.query(
                "CREATE workspace CONTENT { name: $n, slack_team_id: $t, "
                "oauth_token_encrypted: $e }",
                {"n": tid, "t": tid, "e": enc},
            )

        async def stub_extractor(text):
            return {"decisions": []}

        await slack_runner.run_slack_iteration(client, stub_extractor)
        captured.sort(key=lambda c: c["team_id"])
        assert captured == [
            {"team_id": "T1", "client_token": "xoxb-1"},
            {"team_id": "T2", "client_token": "xoxb-2"},
        ]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_slack_worker_skips_workspace_on_decrypt_failure(monkeypatch):
    from team_server.auth.encryption import encrypt_token, load_key_from_env
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import slack_runner

    captured = []

    async def stub_poll_once(**kwargs):
        captured.append(kwargs["workspace_team_id"])

    monkeypatch.setattr(slack_runner, "poll_once", stub_poll_once)

    real_decrypt = slack_runner.decrypt_token
    bad_ciphertext_marker = {"value": None}

    def selective_decrypt(ciphertext, key):
        # Fail only on the workspace whose plaintext was xoxb-bad
        decrypted = real_decrypt(ciphertext, key)
        if decrypted == "xoxb-bad":
            raise RuntimeError("simulated decrypt failure")
        return decrypted

    monkeypatch.setattr(slack_runner, "decrypt_token", selective_decrypt)

    class _StubAWC:
        def __init__(self, token):
            self.token = token

    import sys as _sys
    fake_module = type(_sys)("slack_sdk")
    fake_web = type(_sys)("slack_sdk.web")
    fake_async = type(_sys)("slack_sdk.web.async_client")
    fake_async.AsyncWebClient = _StubAWC
    fake_web.async_client = fake_async
    fake_module.web = fake_web
    monkeypatch.setitem(_sys.modules, "slack_sdk", fake_module)
    monkeypatch.setitem(_sys.modules, "slack_sdk.web", fake_web)
    monkeypatch.setitem(_sys.modules, "slack_sdk.web.async_client", fake_async)

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        key = load_key_from_env()
        for tid, plaintext in [("T1-bad", "xoxb-bad"), ("T2-ok", "xoxb-good")]:
            enc = encrypt_token(plaintext, key).decode("utf-8")
            await client.query(
                "CREATE workspace CONTENT { name: $n, slack_team_id: $t, "
                "oauth_token_encrypted: $e }",
                {"n": tid, "t": tid, "e": enc},
            )

        async def stub_extractor(text):
            return {"decisions": []}

        await slack_runner.run_slack_iteration(client, stub_extractor)
        # The bad workspace's decrypt raises; the good workspace's
        # poll_once is still invoked despite the failure isolation.
        assert "T2-ok" in captured
        assert "T1-bad" not in captured
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_slack_runner_decrypts_workspace_token_with_loaded_key(monkeypatch):
    """Round-trip test: encrypt+store -> read -> decrypt -> token reaches
    AsyncWebClient. Closes the audit blind spot from round 2."""
    from team_server.auth.encryption import encrypt_token, load_key_from_env
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import slack_runner

    captured = {"token": None}

    async def stub_poll_once(**kwargs):
        captured["token"] = getattr(kwargs["slack_client"], "token", None)

    monkeypatch.setattr(slack_runner, "poll_once", stub_poll_once)

    class _StubAWC:
        def __init__(self, token):
            self.token = token

    import sys as _sys
    fake_module = type(_sys)("slack_sdk")
    fake_web = type(_sys)("slack_sdk.web")
    fake_async = type(_sys)("slack_sdk.web.async_client")
    fake_async.AsyncWebClient = _StubAWC
    fake_web.async_client = fake_async
    fake_module.web = fake_web
    monkeypatch.setitem(_sys.modules, "slack_sdk", fake_module)
    monkeypatch.setitem(_sys.modules, "slack_sdk.web", fake_web)
    monkeypatch.setitem(_sys.modules, "slack_sdk.web.async_client", fake_async)

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        key = load_key_from_env()
        encrypted = encrypt_token("xoxb-test-token", key).decode("utf-8")
        await client.query(
            "CREATE workspace CONTENT { name: 'W', slack_team_id: 'T', "
            "oauth_token_encrypted: $e }",
            {"e": encrypted},
        )

        async def stub_extractor(text):
            return {"decisions": []}

        await slack_runner.run_slack_iteration(client, stub_extractor)
        assert captured["token"] == "xoxb-test-token"
    finally:
        await client.close()
