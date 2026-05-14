"""Admin SurrealQL panel — server-side logic (#278 Phase 3).

The dashboard HTTP sidecar exposes a single read/write surface to the
operator at `/admin/query` when explicitly enabled via env flags. The bulk
of the safety logic lives here so it can be unit-tested without spinning
up the asyncio TCP server.

Safety model (defense in depth):
  1. `admin_route_enabled()` reads BICAMERAL_ENABLE_ADMIN_PANEL — false
     disables the route entirely (server returns 404).
  2. `admin_writes_enabled()` reads BICAMERAL_ENABLE_ADMIN_PANEL_WRITES —
     false forces every query into read-only mode.
  3. `check_admin_origin()` enforces same-origin: requests with missing
     or mismatched Origin header are rejected before any DB work.
  4. Write mode requires a non-empty `signer` (handler rejects empty).
  5. Read mode wraps the SQL in a transaction that rolls back, so even
     `DELETE` queries leave the DB unchanged.
  6. Every executed query (success or failure) is audit-logged:
     - team mode → goes through the ledger adapter's attached `_writer`
     - local mode → falls back to `<repo>/.bicameral/events/_admin.jsonl`

Per #278 Phase 3 audit Pass 1 (resolved Pass 2): there is NO unaudited
admin-query code path.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from events.writer import EventEnvelope, _lock_exclusive, _unlock

logger = logging.getLogger(__name__)


# ── env-flag gates ────────────────────────────────────────────────────────


def admin_route_enabled() -> bool:
    """True iff BICAMERAL_ENABLE_ADMIN_PANEL is set to a truthy value."""
    return _truthy_env("BICAMERAL_ENABLE_ADMIN_PANEL")


def admin_writes_enabled() -> bool:
    """True iff BICAMERAL_ENABLE_ADMIN_PANEL_WRITES is set to a truthy value."""
    return _truthy_env("BICAMERAL_ENABLE_ADMIN_PANEL_WRITES")


def _truthy_env(name: str) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# ── origin lock ───────────────────────────────────────────────────────────


def check_admin_origin(origin: str | None, dashboard_port: int) -> bool:
    """Strict same-origin check for the admin route.

    Returns True iff `origin` equals `http://localhost:<dashboard_port>`.
    Missing/empty origin returns False. The dashboard's own JS supplies
    Origin automatically on same-origin fetch.
    """
    if not origin:
        return False
    expected = f"http://localhost:{dashboard_port}"
    return origin == expected


# ── read-only transaction wrap ────────────────────────────────────────────


def wrap_read_only(sql: str) -> str:
    """Wrap the user's SQL in BEGIN/CANCEL TRANSACTION so any mutations
    roll back. The result rows are still returned from inside the
    transaction body before CANCEL fires.

    Caveat (plan Open Question #1): a `DELETE` inside this wrap returns
    the "deleted" rows in the result set even though CANCEL rolls back
    the actual delete. The response payload's `mode: "read-only"` field
    is the operator-facing label that prevents misinterpretation.
    """
    return f"BEGIN TRANSACTION; {sql}; CANCEL TRANSACTION;"


# ── audit-log emission ────────────────────────────────────────────────────


def emit_admin_event_local(payload: dict, repo_path: str | Path) -> Path:
    """Local-mode audit fallback. Appends one JSONL line to
    `<repo>/.bicameral/events/_admin.jsonl` using the same EventEnvelope
    schema team mode uses. Creates the directory on first write.

    Required by audit Pass 1 Finding 1: no admin query may execute without
    leaving an audit trail.
    """
    repo = Path(repo_path)
    events_dir = repo / ".bicameral" / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    path = events_dir / "_admin.jsonl"
    envelope = EventEnvelope(
        event_type="admin_query.executed",
        author="local-admin",
        payload=payload,
    )
    line = json.dumps(envelope.model_dump(), separators=(",", ":"), default=str) + "\n"
    with open(path, "ab") as f:
        _lock_exclusive(f)
        try:
            f.write(line.encode("utf-8"))
        finally:
            _unlock(f)
    return path


def _emit_admin_event(ledger: Any, payload: dict, repo_path: str | Path) -> str:
    """Dispatch the audit event to team writer if attached, else local file.

    Returns 'team' or 'local' to indicate which path was used (test surface).
    """
    writer = getattr(ledger, "_writer", None)
    if writer is not None:
        writer.write("admin_query.executed", payload)
        return "team"
    emit_admin_event_local(payload, repo_path)
    return "local"


# ── top-level orchestrator ────────────────────────────────────────────────


async def process_admin_query(
    *,
    payload_in: dict,
    origin: str | None,
    dashboard_port: int,
    ledger: Any,
    repo_path: str | Path,
) -> tuple[int, dict]:
    """Process one admin query request.

    Returns (http_status, response_body_dict). The HTTP wrapper in
    dashboard/server.py converts these into wire bytes.
    """
    if not admin_route_enabled():
        return 404, {"error": "Admin panel not enabled"}

    if not check_admin_origin(origin, dashboard_port):
        return 403, {"error": "Origin not permitted for /admin/query"}

    sql = str(payload_in.get("sql") or "").strip()
    mode = str(payload_in.get("mode") or "read").lower()
    signer = str(payload_in.get("signer") or "")

    if not sql:
        return 400, {"error": "sql field is required"}

    if mode not in ("read", "write"):
        return 400, {"error": f"mode must be 'read' or 'write', got {mode!r}"}

    if mode == "write" and not admin_writes_enabled():
        return 403, {
            "error": (
                "Write mode requires BICAMERAL_ENABLE_ADMIN_PANEL_WRITES=1 at MCP server start."
            )
        }

    if mode == "write" and not signer.strip():
        return 400, {"error": "signer is required for write-mode queries (audit obligation)."}

    # Resolve the inner SurrealDB client (mirror handlers/ratify.py:50-55).
    inner = getattr(ledger, "_inner", ledger)
    if hasattr(ledger, "connect"):
        await ledger.connect()
    client = inner._client

    executed_sql = sql if mode == "write" else wrap_read_only(sql)
    response_mode = "write" if mode == "write" else "read-only"

    started = time.perf_counter()
    rows: list[dict] = []
    error: str | None = None
    try:
        result = await client.query(executed_sql)
        rows = _normalize_rows(result)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("[admin] query failed: %s", error)

    elapsed_ms = (time.perf_counter() - started) * 1000.0

    # Emit one audit event — success or failure, both modes.
    audit_payload = {
        "sql": sql,
        "mode": response_mode,
        "elapsed_ms": elapsed_ms,
        "error": error,
        "signer": signer,
        "ts": datetime.now(UTC).isoformat(),
    }
    _emit_admin_event(ledger, audit_payload, repo_path)

    body = {
        "mode": response_mode,
        "rows": rows,
        "elapsed_ms": elapsed_ms,
        "error": error,
    }
    return 200, body


def _normalize_rows(result: Any) -> list[dict]:
    """Coerce SurrealDB query results into a list[dict] suitable for JSON."""
    if result is None:
        return []
    if isinstance(result, list):
        out: list[dict] = []
        for r in result:
            if isinstance(r, dict):
                out.append(r)
            else:
                out.append({"value": str(r)})
        return out
    if isinstance(result, dict):
        return [result]
    return [{"value": str(result)}]
