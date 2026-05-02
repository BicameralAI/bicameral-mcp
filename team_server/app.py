"""Team-server FastAPI app factory.

Self-managing: lifespan runs schema migration on startup; teardown closes
the DB. No human-ops surface. Per CONCEPT.md literal-keyword parsing
(`docs/SHADOW_GENOME.md` Failure Entry #6 addendum).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from team_server.db import TeamServerDB
from team_server.schema import SCHEMA_VERSION, ensure_schema

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = TeamServerDB.from_env()
    await db.connect()
    await ensure_schema(db.client)
    app.state.db = db
    logger.info("[team-server] started; schema_version=%s", SCHEMA_VERSION)
    try:
        yield
    finally:
        await db.close()
        logger.info("[team-server] shut down")


def create_app() -> FastAPI:
    app = FastAPI(title="bicameral-team-server", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "schema_version": SCHEMA_VERSION}

    return app
