"""Thin async wrapper around the SurrealDB Python SDK.

Handles connection lifecycle, namespace/database selection, and query
result normalization. All callers use `client.query(sql, vars)` and get
back a plain list of dicts — no SDK types leak through.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Literal

from surrealdb import AsyncSurreal, RecordID

try:
    from surrealdb import SurrealError
except ImportError:
    SurrealError = Exception  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# #224: per-query wallclock timeout budgets. Defaults match
# ``context.py::_DEFAULT_QUERY_TIMEOUT_{READ,DRIFT}`` so a
# bare ``LedgerClient(url)`` constructed in tests / adapters gets
# safe defaults without requiring a ``BicameralContext`` injection.
# Operator-configured values flow through
# ``BicameralContext.query_timeout_{read,drift}_seconds`` → the
# adapter passes them to ``LedgerClient.__init__``.
_DEFAULT_QUERY_TIMEOUT_READ_SECONDS = 5.0
_DEFAULT_QUERY_TIMEOUT_DRIFT_SECONDS = 30.0

# #224: env-override for the timeout wrap. Mirror the
# ``BICAMERAL_INGEST_RATE_LIMIT_DISABLE`` precedent at
# ``handlers/ingest.py:368``. Use cases: data export, recovery,
# intentionally long-running operator query.
_QUERY_TIMEOUT_DISABLE_ENV = "BICAMERAL_QUERY_TIMEOUT_DISABLE"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _query_timeout_disabled() -> bool:
    """Read the env-override fresh on every call so test fixtures
    can toggle it without restarting the module."""
    return os.getenv(_QUERY_TIMEOUT_DISABLE_ENV, "").strip().lower() in _TRUTHY


# Windows-drive-letter detector at the start of an embedded URL path.
# Matches "C:\..." or "C:/...". Used to spot URLs that contain a
# Windows-style file path which needs slash-normalization before
# urllib.parse can read them.
_WINDOWS_DRIVE_AT_PATH_START = re.compile(r"^([A-Za-z]):[\\/]")


def normalize_surrealkv_url(url: str) -> str:
    """Normalize ``surrealkv://`` URLs containing Windows file paths.

    Issue #68: ``urllib.parse.urlparse("surrealkv://C:\\Users\\...")``
    treats everything after the scheme as a netloc and raises
    ``ValueError: Port could not be cast to integer value`` on
    ``parsed.port``. The SurrealDB Python SDK reads ``parsed.port``
    in its ``Url`` wrapper, so passing an unmodified Windows backslash
    path crashes every embedded test that builds its URL from a
    ``tmp_path`` fixture.

    Fix: replace backslashes with forward slashes inside the path.

        surrealkv://C:\\Users\\foo\\bar.db    →    surrealkv://C:/Users/foo/bar.db

    The forward-slash form parses cleanly through ``urllib.parse``
    (netloc=``C:``, path=``/Users/foo/bar.db``, port=None — the path
    after the colon doesn't look like an int, but ``urlparse`` only
    raises when the port-position content is non-empty AND non-numeric;
    here the colon is immediately followed by ``/`` so the port-position
    is empty and parsing succeeds). The SurrealKV Rust backend accepts
    this form on Windows.

    POSIX URLs, in-memory URLs (``memory://``), and remote URLs
    (``ws://``, ``http://``) pass through unchanged because they
    contain no backslashes.
    """
    if not url.startswith(("surrealkv://", "surrealkv+versioned://", "file://")):
        return url

    # Find the path portion (everything after scheme://)
    scheme_end = url.find("://") + len("://")
    after_scheme = url[scheme_end:]

    # Only rewrite if the URL contains a Windows-style backslash or a
    # bare drive-letter prefix that would confuse urllib. Pure POSIX
    # paths and already-normalized Windows paths pass through unchanged.
    if "\\" not in after_scheme:
        return url

    if not _WINDOWS_DRIVE_AT_PATH_START.match(after_scheme):
        # Has backslashes but no drive letter — likely a malformed URL,
        # but we fix the slashes anyway to give urllib a fighting chance.
        return url[:scheme_end] + after_scheme.replace("\\", "/")

    return url[:scheme_end] + after_scheme.replace("\\", "/")


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


class LedgerDeserializationError(LedgerError):
    """Raised when SurrealDB's embedded SDK can't decode a row on read (#301).

    The on-disk SurrealKV record header carries a revision number that must
    match the surrealdb-py deserializer. A mismatch surfaces as
    ``Invalid revision `N` for type `Value```. This is *below* the schema
    layer — `init_schema`/`migrate` don't help because the migration code
    can't read the row either. The fix is to wipe the affected cursor and
    replay from the event log via
    ``bicameral_reset(wipe_mode="ledger", replay_from_events=True, confirm=True)``.

    Subclass of ``LedgerError`` so existing ``except LedgerError`` handlers
    catch it; callers that surface a recovery hint to the agent match on
    ``LedgerDeserializationError`` specifically.
    """

    RECOVERY_HINT = (
        "Row-level deserialization failed — likely a SurrealDB embedded SDK "
        "revision mismatch on persisted rows. Recover via the shell (works "
        "even when the MCP `bicameral_reset` tool isn't reachable in this "
        "session, #410):\n"
        "  `bicameral-mcp reset --confirm --wipe-mode=ledger --replay-from-events`\n"
        "or, if the DB is fully unreadable:\n"
        "  `bicameral-mcp reset --confirm --wipe-mode=full`\n"
        "MCP equivalent: `bicameral_reset(wipe_mode='ledger', "
        "replay_from_events=True, confirm=True)`. "
        "Run `bicameral-mcp diagnose` first for a full report."
    )

    def __init__(self, *, raw: str, sql_prefix: str) -> None:
        self.raw = raw
        self.sql_prefix = sql_prefix
        super().__init__(
            f"SurrealDB row deserialization failed: {raw}\n"
            f"SQL: {sql_prefix}\n"
            f"Recovery: {self.RECOVERY_HINT}"
        )


_DESERIALIZATION_SIGNATURES = ("Invalid revision", "deserialization error")


def _is_deserialization_error(raw: str) -> bool:
    """Return True when ``raw`` looks like a SurrealKV row-format mismatch.

    Matches the two signatures observed in #301 and the related ``yields``
    incident: the SDK's ``Invalid revision `N` for type `T``` and the more
    general ``A deserialization error occured`` wrapper. Both come back as
    strings the caller sees as ``LedgerError`` today — this helper is the
    classification seam.
    """
    return any(sig in raw for sig in _DESERIALIZATION_SIGNATURES)


class LedgerTimeoutError(LedgerError):
    """Raised when a ledger query exceeds its wallclock timeout budget.

    Carries the timeout class, elapsed seconds, configured budget,
    and a 200-char SQL prefix for operator triage. Subclass of
    ``LedgerError`` so existing ``except LedgerError`` handler blocks
    catch it by default; callers that need to distinguish a timeout
    from other ledger errors can match on ``LedgerTimeoutError``.

    The wrap that produces this is the deterministic server-side gate
    for #224 — it fires identically regardless of which MCP client is
    on the other end of the transport. Per #205 doctrine, governance
    is enforced here, not in skill text.
    """

    def __init__(
        self,
        *,
        sql_prefix: str,
        timeout_class: str,
        elapsed_seconds: float,
        budget_seconds: float,
    ) -> None:
        self.sql_prefix = sql_prefix
        self.timeout_class = timeout_class
        self.elapsed_seconds = elapsed_seconds
        self.budget_seconds = budget_seconds
        super().__init__(
            f"Ledger query exceeded {timeout_class} timeout "
            f"({elapsed_seconds:.2f}s > {budget_seconds:.1f}s budget): "
            f"{sql_prefix}"
        )


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
        *,
        query_timeout_read_seconds: float = _DEFAULT_QUERY_TIMEOUT_READ_SECONDS,
        query_timeout_drift_seconds: float = _DEFAULT_QUERY_TIMEOUT_DRIFT_SECONDS,
    ) -> None:
        # Normalize embedded Windows paths so the SurrealDB SDK's internal
        # urllib.parse.urlparse() doesn't choke on the drive-letter colon.
        # See ``normalize_surrealkv_url`` and issue #68.
        self.url = normalize_surrealkv_url(url)
        self.ns = ns
        self.db = db
        self._username = username
        self._password = password
        self._db: Any = None
        self._timeout_read = query_timeout_read_seconds
        self._timeout_drift = query_timeout_drift_seconds

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

    def _budget_for(self, timeout_class: Literal["read", "drift"]) -> float:
        return self._timeout_drift if timeout_class == "drift" else self._timeout_read

    async def _run_with_timeout(
        self,
        sql: str,
        vars: dict | None,
        timeout_class: Literal["read", "drift"],
    ) -> Any:
        """Execute the underlying SDK query under the configured timeout
        wallclock. ``BICAMERAL_QUERY_TIMEOUT_DISABLE=1`` bypasses the
        wrap (debugging knob for intentionally long-running queries).
        """
        if _query_timeout_disabled():
            return await self._db.query(sql, vars)
        budget = self._budget_for(timeout_class)
        started = time.perf_counter()
        try:
            return await asyncio.wait_for(self._db.query(sql, vars), timeout=budget)
        except TimeoutError:
            elapsed = time.perf_counter() - started
            _emit_timeout_telemetry(
                sql=sql,
                timeout_class=timeout_class,
                elapsed_seconds=elapsed,
                budget_seconds=budget,
            )
            raise LedgerTimeoutError(
                sql_prefix=sql[:200],
                timeout_class=timeout_class,
                elapsed_seconds=elapsed,
                budget_seconds=budget,
            ) from None

    async def query(
        self,
        sql: str,
        vars: dict | None = None,
        *,
        timeout_class: Literal["read", "drift"] = "read",
    ) -> list[dict]:
        """Run a SurrealQL statement and return a list of normalized dicts.

        Raises:
            LedgerError: when SurrealDB rejects the statement (returns an
                error string instead of rows). Common causes: malformed
                SurrealQL, permission failures, ASSERT violations on the
                underlying SELECT.
            LedgerTimeoutError: when the query exceeds the configured
                wallclock budget for its ``timeout_class`` (default
                ``"read"`` = 5s; pass ``timeout_class="drift"`` for
                heavy traversal / replay paths = 30s default). #224.
        """
        if self._db is None:
            raise RuntimeError("LedgerClient not connected — call await client.connect() first")
        try:
            result = await self._run_with_timeout(sql, vars, timeout_class)
        except SurrealError as exc:
            msg = str(exc)
            if _is_deserialization_error(msg):
                raise LedgerDeserializationError(raw=msg, sql_prefix=sql[:300]) from exc
            raise LedgerError(f"SurrealDB rejected query: {exc}\nSQL: {sql[:300]}") from exc
        if isinstance(result, str):
            if _is_deserialization_error(result):
                raise LedgerDeserializationError(raw=result, sql_prefix=sql[:300])
            raise LedgerError(f"SurrealDB rejected query: {result}\nSQL: {sql[:300]}")
        return _normalize(result) if isinstance(result, list) else []

    async def execute(
        self,
        sql: str,
        vars: dict | None = None,
        *,
        timeout_class: Literal["read", "drift"] = "read",
    ) -> None:
        """Run a SurrealQL statement, discarding the result (DDL / DML).

        Raises:
            LedgerError: when SurrealDB rejects the statement. Catches
                the class of silent-failure bugs where a UNIQUE violation
                or ASSERT failure gets returned as an error string and
                the caller proceeds believing the write succeeded.
            LedgerTimeoutError: when the statement exceeds the configured
                wallclock budget. See ``query`` for details.
        """
        if self._db is None:
            raise RuntimeError("LedgerClient not connected")
        try:
            result = await self._run_with_timeout(sql, vars, timeout_class)
        except SurrealError as exc:
            msg = str(exc)
            if _is_deserialization_error(msg):
                raise LedgerDeserializationError(raw=msg, sql_prefix=sql[:300]) from exc
            raise LedgerError(f"SurrealDB rejected statement: {exc}\nSQL: {sql[:300]}") from exc
        if isinstance(result, str):
            if _is_deserialization_error(result):
                raise LedgerDeserializationError(raw=result, sql_prefix=sql[:300])
            raise LedgerError(f"SurrealDB rejected statement: {result}\nSQL: {sql[:300]}")

    async def execute_many(
        self,
        statements: list[str],
        *,
        timeout_class: Literal["read", "drift"] = "read",
    ) -> None:
        """Run multiple DDL/DML statements in sequence (one at a time)."""
        for stmt in statements:
            stmt = stmt.strip()
            if stmt:
                await self.execute(stmt, timeout_class=timeout_class)


def _emit_timeout_telemetry(
    *,
    sql: str,
    timeout_class: str,
    elapsed_seconds: float,
    budget_seconds: float,
) -> None:
    """Forward a timeout event to the ring-buffer + audit-log telemetry
    layer. Imported lazily so a ``LedgerClient`` constructed before
    ``ledger.timeout_telemetry`` is importable (e.g. early in module
    import) still raises a useful timeout, just without the recorded
    event. Phase C-pre wires up the ring buffer; Phase C wires up
    the audit-log emit.
    """
    try:
        from ledger.timeout_telemetry import record_timeout

        record_timeout(
            sql_prefix=sql[:200],
            timeout_class=timeout_class,
            elapsed_seconds=elapsed_seconds,
            budget_seconds=budget_seconds,
        )
    except Exception:  # noqa: BLE001 — telemetry must never break a query
        pass
