"""Team-server FastAPI app factory.

Self-managing: lifespan runs schema migration on startup; teardown
closes the DB. Worker tasks (Slack always; Notion opt-in) are
registered via worker_loop and cancelled cleanly on shutdown.
Per CONCEPT.md literal-keyword parsing.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from team_server.auth import notion_client as nc
from team_server.auth.allowlist_sync import sync_channel_allowlist
from team_server.config import DEFAULT_CONFIG_PATH, TeamServerConfig
from team_server.db import TeamServerDB
from team_server.extraction.corpus_learner import run_corpus_learner_iteration
from team_server.extraction.llm_extractor import extract as _interim_extractor
from team_server.schema import SCHEMA_VERSION, ensure_schema
from team_server.workers.notion_runner import run_notion_iteration
from team_server.workers.runner import worker_loop
from team_server.workers.slack_runner import run_slack_iteration

logger = logging.getLogger(__name__)

SLACK_POLL_INTERVAL_SECONDS = int(os.environ.get("SLACK_POLL_INTERVAL_SECONDS", "60"))
NOTION_POLL_INTERVAL_SECONDS = int(os.environ.get("NOTION_POLL_INTERVAL_SECONDS", "60"))


def _load_config_or_default() -> TeamServerConfig:
    """Load TeamServerConfig from DEFAULT_CONFIG_PATH if it exists,
    else return a default-empty config (corpus learner off, no rules)."""
    if not DEFAULT_CONFIG_PATH.exists():
        return TeamServerConfig()
    from team_server.config import load_rules_from_config
    try:
        return load_rules_from_config(str(DEFAULT_CONFIG_PATH))
    except Exception:  # noqa: BLE001
        logger.exception("[team-server] config load failed; using defaults")
        return TeamServerConfig()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = TeamServerDB.from_env()
    await db.connect()
    await ensure_schema(db.client)
    app.state.db = db

    # Phase 1: channel allowlist sync from YAML — runs after schema +
    # before worker registration so the slack runner sees populated
    # rows on first poll.
    config = _load_config_or_default()
    app.state.team_server_config = config
    try:
        await sync_channel_allowlist(db.client, config)
    except Exception:  # noqa: BLE001
        logger.exception("[team-server] channel_allowlist sync failed; continuing")

    tasks: list[asyncio.Task] = []

    # Slack worker — always registered (no-op when workspace table empty)
    tasks.append(worker_loop(
        name="slack",
        interval_seconds=SLACK_POLL_INTERVAL_SECONDS,
        work_fn=lambda: run_slack_iteration(db.client, _interim_extractor),
    ))

    # Notion worker — registered only when token resolves (opt-in)
    try:
        notion_token = nc.load_token(config_path=str(DEFAULT_CONFIG_PATH))
        tasks.append(worker_loop(
            name="notion",
            interval_seconds=NOTION_POLL_INTERVAL_SECONDS,
            work_fn=lambda: run_notion_iteration(db.client, notion_token, _interim_extractor),
        ))
        logger.info("[team-server] notion worker registered")
    except nc.NotionAuthError:
        logger.info("[team-server] notion ingest disabled (no token)")

    # Corpus learner — opt-in via config.corpus_learner.enabled
    if config.corpus_learner.enabled:
        tasks.append(worker_loop(
            name="corpus-learner",
            interval_seconds=config.corpus_learner.interval_seconds,
            work_fn=lambda: run_corpus_learner_iteration(db.client, config),
        ))
        logger.info("[team-server] corpus learner registered")

    app.state.worker_tasks = tasks
    logger.info(
        "[team-server] started; schema_version=%s; %d worker(s)",
        SCHEMA_VERSION, len(tasks),
    )
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        await db.close()
        logger.info("[team-server] shut down")


def create_app() -> FastAPI:
    app = FastAPI(title="bicameral-team-server", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "schema_version": SCHEMA_VERSION}

    from team_server.auth.router import router as auth_router
    from team_server.api.events import router as events_router
    app.include_router(auth_router)
    app.include_router(events_router)

    return app
