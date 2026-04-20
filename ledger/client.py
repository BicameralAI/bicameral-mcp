"""Thin async wrapper around the SurrealDB Python SDK.

Handles connection lifecycle, namespace/database selection, and query
result normalization. All callers use `client.query(sql, vars)` and get
back a plain list of dicts — no SDK types leak through.
"""

from __future__ import annotations

import logging
from typing import Any

from surrealdb import AsyncSurreal, RecordID

logger = logging.getLogger(__name__)


class LedgerError(RuntimeError):
    """Raised when SurrealDB rejects a statement at the application layer.

    SurrealDB 2.x embedded returns constraint errors (UNIQUE violations,
    field ASSERT failures, malformed queries) as string results instead
    of raising at the SDK level. Prior to v3-schema work this client
    silently discarded those strings, which meant failed writes could
    masquerade as successes. ``execute()`` and ``query()`` now convert
    error-string responses into this exception so failures surface at
    the call site.
    """


def _normalize(value: Any) -> Any:
    """Recursively convert SDK types to plain Python objects."""
    if isinstance(value, RecordID):
        return str(value)  # "intent:abc123"
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


class LedgerClient:
    """Async SurrealDB client for the decision ledger.

    Usage:
        client = LedgerClient("ws://localhost:8001")
        await client.connect()
        rows = await client.query("SELECT * FROM intent")
        await client.close()

    For embedded (testing):
        client = LedgerClient("memory://")
        await client.connect()  # no signin for memory://
    """

    def __init__(
        self,
        url: str = "ws://localhost:8001",
        ns: str = "bicameral",
        db: str = "ledger",
        username: str = "root",
        password: str = "root",
    ) -> None:
        self.url = url
        self.ns = ns
        self.db = db
        self._username = username
        self._password = password
        self._db: Any = None

    async def connect(self) -> None:
        self._db = AsyncSurreal(self.url)
        await self._db.connect()
        # Only sign in for remote servers (ws://, http://) — embedded backends
        # (memory://, surrealkv://) don't need authentication
        if self.url.startswith(("ws://", "wss://", "http://", "https://")):
            await self._db.signin({"username": self._username, "password": self._password})
        await self._db.use(self.ns, self.db)
        logger.info("[ledger] connected to %s/%s/%s", self.url, self.ns, self.db)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def query(self, sql: str, vars: dict | None = None) -> list[dict]:
        """Run a SurrealQL statement and return a list of normalized dicts.

        Raises:
            LedgerError: when SurrealDB rejects the statement (returns an
                error string instead of rows). Common causes: malformed
                SurrealQL, permission failures, ASSERT violations on the
                underlying SELECT.
        """
        if self._db is None:
            raise RuntimeError("LedgerClient not connected — call await client.connect() first")
        result = await self._db.query(sql, vars)
        if isinstance(result, str):
            raise LedgerError(f"SurrealDB rejected query: {result}\nSQL: {sql[:300]}")
        return _normalize(result) if isinstance(result, list) else []

    async def execute(self, sql: str, vars: dict | None = None) -> None:
        """Run a SurrealQL statement, discarding the result (DDL / DML).

        Raises:
            LedgerError: when SurrealDB rejects the statement. Catches
                the class of silent-failure bugs where a UNIQUE violation
                or ASSERT failure gets returned as an error string and
                the caller proceeds believing the write succeeded.
        """
        if self._db is None:
            raise RuntimeError("LedgerClient not connected")
        result = await self._db.query(sql, vars)
        if isinstance(result, str):
            raise LedgerError(f"SurrealDB rejected statement: {result}\nSQL: {sql[:300]}")

    async def execute_many(self, statements: list[str]) -> None:
        """Run multiple DDL/DML statements in sequence (one at a time)."""
        for stmt in statements:
            stmt = stmt.strip()
            if stmt:
                await self.execute(stmt)
