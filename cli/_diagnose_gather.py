"""Pure-data gather for ``bicameral-mcp diagnose`` (#252 Layer 3).

Split out of ``cli/diagnose.py`` per round-1 audit advisory to keep
that module under the 250-LOC Razor ceiling. Imports ``Diagnosis`` from
``cli.diagnose`` and reads from the ledger / filesystem / env to
populate every allowlisted field.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cli.diagnose import _CANONICAL_TABLES, Diagnosis

_LARGE_LEDGER_BYTES = 100 * 1024 * 1024
_RECENT_EVENT_TAIL = 5


def _read_ledger_metadata(adapter) -> tuple[str, int | None, str | None]:
    """Return (ledger_url, size_bytes_or_None, mtime_iso_or_None)."""
    url = getattr(adapter, "_url", "")
    if not url.startswith("surrealkv://"):
        return url, None, None
    path_str = url.removeprefix("surrealkv://")
    p = Path(path_str)
    if not p.exists():
        return url, None, None
    stat = p.stat()
    mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
    return url, stat.st_size, mtime_iso


async def _read_bicameral_meta(
    adapter,
) -> tuple[str | None, str | None, str | None, str, str]:
    """Return (first_write, last_write, last_write_at_iso, drift_status, running).

    ``drift_status`` is one of: ``"first-write"`` / ``"match"`` / ``"drift"`` /
    ``"unavailable"`` (table missing, e.g., pre-Layer-2 ledger).
    """
    try:
        running = importlib.metadata.version("surrealdb")
    except importlib.metadata.PackageNotFoundError:
        running = "unknown"

    try:
        rows = await adapter._client.query("SELECT * FROM bicameral_meta LIMIT 1")
    except Exception:  # noqa: BLE001 — table missing is the load-bearing case
        return None, None, None, "unavailable", running

    if not rows:
        return None, None, None, "first-write", running

    row = rows[0]
    first = row.get("surrealdb_client_version_at_first_write")
    last = row.get("surrealdb_client_version_at_last_write")
    last_at_raw = row.get("last_write_at")
    last_at_iso = last_at_raw.isoformat() if last_at_raw is not None else None

    if first is None:
        return None, last, last_at_iso, "first-write", running
    if last == running:
        return first, last, last_at_iso, "match", running
    return first, last, last_at_iso, "drift", running


async def _read_schema_version(adapter) -> int | None:
    try:
        rows = await adapter._client.query("SELECT version FROM schema_meta LIMIT 1")
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    val = rows[0].get("version")
    return int(val) if val is not None else None


async def _read_table_counts(adapter) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in _CANONICAL_TABLES:
        try:
            rows = await adapter._client.query(f"SELECT count() AS n FROM {table} GROUP ALL")
        except Exception:  # noqa: BLE001 — missing table is acceptable (pre-v16)
            continue
        if rows:
            n = rows[0].get("n", 0)
            counts[table] = int(n) if n is not None else 0
    return counts


def _resolve_audit_log_channel() -> tuple[str, Path | None]:
    """Return (channel_label, configured_file_path_or_None)."""
    raw = os.getenv("BICAMERAL_AUDIT_LOG", "stderr").strip()
    if raw == "disabled":
        return "disabled", None
    if raw in ("", "stderr"):
        return "stderr", None
    return raw, Path(raw)


def _read_jsonl_warn_error_lines(path: Path, limit: int) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        lvl = evt.get("level", "")
        if lvl in ("warn", "error"):
            out.append(evt)
    return out[-limit:]


def _tail_recent_events(audit_path: Path | None, limit: int) -> list[dict]:
    """Tail warn|error events from preflight + audit log, merge by ts."""
    preflight = Path.home() / ".bicameral" / "preflight_events.jsonl"
    merged = _read_jsonl_warn_error_lines(preflight, limit)
    if audit_path is not None:
        merged.extend(_read_jsonl_warn_error_lines(audit_path, limit))
    merged.sort(key=lambda e: str(e.get("ts", "")))
    safe: list[dict] = []
    for evt in merged[-limit:]:
        safe.append(
            {
                "ts": evt.get("ts", "?"),
                "level": evt.get("level", "?"),
                "event_type": evt.get("event_type", "?"),
            }
        )
    return safe


def _remediation_recipe() -> str:
    """One-line export → reset → import recipe + policy-doc pointer.

    Used by three suggestion branches in `_compute_suggestions` (drift,
    unavailable, large-ledger) so the wording lives at one source of truth.
    """
    return (
        "back up ledger and re-roundtrip via "
        "`bicameral-mcp ledger-export > backup.jsonl && bicameral-mcp reset && "
        "bicameral-mcp ledger-import --from-file backup.jsonl` "
        "(see docs/policies/ledger-export.md)"
    )


def _compute_suggestions(d_partial: dict[str, Any]) -> list[str]:
    """Run 6 hardcoded heuristics against the assembled partial Diagnosis dict."""
    suggestions: list[str] = []
    drift = d_partial.get("drift_status")
    bv = d_partial.get("bicameral_version", "")
    recipe = _remediation_recipe()
    if drift == "drift":
        rec = d_partial.get("surrealdb_last_write")
        run = d_partial.get("surrealdb_running")
        suggestions.append(
            f"Schema-revision drift: recorded {rec} != running {run}; "
            f"`pip install --upgrade surrealdb=={rec}` to match writer, OR {recipe}."
        )
    if drift == "unavailable" and bv and bv != "unknown":
        suggestions.append(f"Ledger predates Layer 2 wire-format sentinel (bicameral_meta missing); to acquire the sentinel, {recipe}.")
    rec_v = _fetch_recommended()
    if rec_v and bv and rec_v != bv:
        suggestions.append(f"Recommended version {rec_v} available; `bicameral.update {{action: 'apply'}}` to upgrade.")
    if d_partial.get("audit_log_channel") == "stderr":
        suggestions.append("Audit log not file-persisted. Set `BICAMERAL_AUDIT_LOG=<path>` to capture incident events for SOC 2 evidence.")
    size = d_partial.get("ledger_size_bytes")
    if isinstance(size, int) and size > _LARGE_LEDGER_BYTES:
        suggestions.append(f"Ledger file > 100 MiB (current: {size} bytes); {recipe}.")
    rec_schema = d_partial.get("schema_version_recorded")
    exp_schema = d_partial.get("schema_version_expected")
    if isinstance(rec_schema, int) and isinstance(exp_schema, int) and rec_schema < exp_schema:
        suggestions.append(f"Ledger schema {rec_schema} < binary schema {exp_schema}; run `bicameral-mcp` once to apply pending migrations.")
    return suggestions


def _fetch_recommended() -> str | None:
    try:
        from handlers.update import fetch_recommended_version

        return fetch_recommended_version()
    except Exception:  # noqa: BLE001 — network failure must not break diagnose
        return None


async def gather_diagnosis(adapter) -> Diagnosis:
    """Collect every allowlisted field from the running install + ledger."""
    try:
        bicameral_version = importlib.metadata.version("bicameral-mcp")
    except importlib.metadata.PackageNotFoundError:
        bicameral_version = "unknown"

    from ledger.schema import SCHEMA_VERSION

    ledger_url, size_bytes, mtime_iso = _read_ledger_metadata(adapter)
    first, last, last_at_iso, drift_status, running = await _read_bicameral_meta(adapter)
    schema_recorded = await _read_schema_version(adapter)
    table_counts = await _read_table_counts(adapter)
    channel_label, audit_path = _resolve_audit_log_channel()
    recent_events = _tail_recent_events(audit_path, _RECENT_EVENT_TAIL)

    partial: dict[str, Any] = {
        "drift_status": drift_status,
        "surrealdb_last_write": last,
        "surrealdb_running": running,
        "bicameral_version": bicameral_version,
        "audit_log_channel": channel_label,
        "ledger_size_bytes": size_bytes,
        "schema_version_recorded": schema_recorded,
        "schema_version_expected": SCHEMA_VERSION,
    }
    suggestions = _compute_suggestions(partial)

    return Diagnosis(
        bicameral_version=bicameral_version,
        python_version=sys.version.split()[0],
        platform_str=platform.platform(),
        surrealdb_running=running,
        ledger_url=ledger_url,
        ledger_size_bytes=size_bytes,
        ledger_mtime_iso=mtime_iso,
        schema_version_recorded=schema_recorded,
        schema_version_expected=SCHEMA_VERSION,
        surrealdb_first_write=first,
        surrealdb_last_write=last,
        last_write_at=last_at_iso,
        drift_status=drift_status,
        audit_log_channel=channel_label,
        table_counts=table_counts,
        recent_events=recent_events,
        suggestions=suggestions,
    )
