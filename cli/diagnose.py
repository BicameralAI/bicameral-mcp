"""bicameral-mcp diagnose — privacy-preserving operator bug-report tool.

Closes #252 Layer 3 per
``docs/research-brief-252-privacy-preserving-ledger-remediation.md``.

Emits a markdown-styled report containing **structural metadata only**:
versions, file metadata, table row counts, schema-revision sentinel
state, recent warn|error event tail. No decision content, no source
attribution, no row contents. Operators copy-paste the rendered output
into GitHub bug reports without privacy review.

Allowlist discipline: the ``Diagnosis`` dataclass enumerates every
field that may appear in the output. ``_ALLOWED_FIELDS`` (frozenset)
locks the field set; any future expansion requires editing both the
dataclass and the frozenset, which is how we lock the privacy posture
at code-review time.
"""

from __future__ import annotations

import dataclasses
import sys
from typing import Any

# Frozen field allowlist — every emitted field appears here. Adding a
# new field requires adding the dataclass attribute AND the entry in
# this frozenset; the parity is locked by
# ``tests/test_diagnose_allowlist.py::test_diagnosis_dataclass_fields_match_allowlist``.
_ALLOWED_FIELDS = frozenset(
    {
        "bicameral_version",
        "python_version",
        "platform_str",
        "surrealdb_running",
        "ledger_url",
        "ledger_size_bytes",
        "ledger_mtime_iso",
        "schema_version_recorded",
        "schema_version_expected",
        "surrealdb_first_write",
        "surrealdb_last_write",
        "last_write_at",
        "drift_status",
        "audit_log_channel",
        "table_counts",
        "row_probe_warnings",
        "recent_events",
        "suggestions",
    }
)

# Hardcoded canonical bicameral table list — Layer 3 emits row counts
# for these only. Tolerant of missing tables (pre-v16 ledgers won't
# have bicameral_meta).
_CANONICAL_TABLES = (
    "decision",
    "input_span",
    "code_region",
    "code_subject",
    "subject_identity",
    "binds_to",
    "yields",
    "locates",
    "schema_meta",
    "bicameral_meta",
    "ledger_sync",
)


@dataclasses.dataclass(frozen=True)
class Diagnosis:
    """Structural-metadata-only diagnostic report. Every field is in
    ``_ALLOWED_FIELDS``; parity locked by content-contract test."""

    bicameral_version: str
    python_version: str
    platform_str: str
    surrealdb_running: str
    ledger_url: str
    ledger_size_bytes: int | None
    ledger_mtime_iso: str | None
    schema_version_recorded: int | None
    schema_version_expected: int
    surrealdb_first_write: str | None
    surrealdb_last_write: str | None
    last_write_at: str | None
    drift_status: str
    audit_log_channel: str
    table_counts: dict[str, int]
    row_probe_warnings: list[str]
    recent_events: list[dict[str, Any]]
    suggestions: list[str]


def _format_versions_section(d: Diagnosis) -> str:
    return (
        "## Versions\n\n"
        f"- bicameral-mcp: {d.bicameral_version}\n"
        f"- Python: {d.python_version}\n"
        f"- Platform: {d.platform_str}\n"
        f"- surrealdb (running): {d.surrealdb_running}\n"
    )


def _format_ledger_section(d: Diagnosis) -> str:
    size = "None" if d.ledger_size_bytes is None else f"{d.ledger_size_bytes} bytes"
    mtime = d.ledger_mtime_iso if d.ledger_mtime_iso is not None else "None"
    return (
        "## Ledger\n\n"
        f"- URL: `{d.ledger_url}` (redact if install path is sensitive)\n"
        f"- Size: {size}\n"
        f"- Last modified: {mtime}\n"
    )


def _format_schema_section(d: Diagnosis) -> str:
    rec = d.schema_version_recorded if d.schema_version_recorded is not None else "unknown"
    first = d.surrealdb_first_write if d.surrealdb_first_write is not None else "unknown"
    last = d.surrealdb_last_write if d.surrealdb_last_write is not None else "unknown"
    last_at = d.last_write_at if d.last_write_at is not None else "unknown"
    return (
        "## Schema revision sentinel\n\n"
        f"- bicameral schema (recorded): {rec}\n"
        f"- bicameral schema (expected): {d.schema_version_expected}\n"
        f"- surrealdb (at first write): {first}\n"
        f"- surrealdb (at last write): {last}\n"
        f"- last write at: {last_at}\n"
        f"- drift status: **{d.drift_status.upper()}**\n"
    )


def _format_table_counts_section(d: Diagnosis) -> str:
    if not d.table_counts:
        return "## Table row counts\n\n_No tables visible_\n"
    lines = ["## Table row counts\n"]
    for table in sorted(d.table_counts):
        lines.append(f"- {table}: {d.table_counts[table]}")
    return "\n".join(lines) + "\n"


def _format_row_probe_section(d: Diagnosis) -> str:
    if not d.row_probe_warnings:
        return "## Row-level probe\n\nAll operational tables readable.\n"
    lines = ["## Row-level probe\n"]
    for w in d.row_probe_warnings:
        lines.append(f"- ⚠ {w}")
    return "\n".join(lines) + "\n"


def _format_recent_events_section(d: Diagnosis) -> str:
    header = f"## Recent events (warn|error, last {len(d.recent_events)})\n\n"
    header += f"_Audit log channel: {d.audit_log_channel} (redact if path is sensitive)_\n\n"
    if not d.recent_events:
        return header + "_No warn|error events recorded._\n"
    lines = []
    for evt in d.recent_events:
        ts = evt.get("ts", "?")
        lvl = evt.get("level", "?")
        et = evt.get("event_type", "?")
        lines.append(f"- [{ts}] {lvl} {et}")
    return header + "\n".join(lines) + "\n"


def _format_suggestions_section(d: Diagnosis) -> str:
    if not d.suggestions:
        return "## Suggested remediation\n\nNo issues detected; install looks healthy.\n"
    lines = ["## Suggested remediation\n"]
    for s in d.suggestions:
        lines.append(f"- {s}")
    return "\n".join(lines) + "\n"


_PASTE_FOOTER = (
    "\n---\n\n_Paste the section above into the bug report at "
    "https://github.com/BicameralAI/bicameral-mcp/issues — no decision "
    "content or source attribution will be included. Redact local file "
    "paths above if your install path is sensitive._\n"
)


def format_diagnosis(d: Diagnosis) -> str:
    """Render a Diagnosis instance as operator-pasteable markdown."""
    return (
        "# bicameral-mcp diagnose\n\n"
        + _format_versions_section(d)
        + "\n"
        + _format_ledger_section(d)
        + "\n"
        + _format_schema_section(d)
        + "\n"
        + _format_table_counts_section(d)
        + "\n"
        + _format_row_probe_section(d)
        + "\n"
        + _format_recent_events_section(d)
        + "\n"
        + _format_suggestions_section(d)
        + _PASTE_FOOTER
    )


def main(repo_path: str | None = None) -> int:
    """CLI entrypoint for ``bicameral-mcp diagnose``.

    Connects an adapter against the resolved ledger URL, gathers the
    Diagnosis, prints the rendered markdown to stdout, returns 0.
    Exits 1 on adapter-connect failure (operator needs the failure
    context in the bug report).
    """
    import asyncio

    from cli._diagnose_gather import gather_diagnosis
    from ledger.adapter import SurrealDBLedgerAdapter

    async def _run() -> Diagnosis:
        adapter = SurrealDBLedgerAdapter()
        await adapter.connect()
        return await gather_diagnosis(adapter)

    try:
        diagnosis = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — operator needs failure context
        sys.stdout.write(f"# bicameral-mcp diagnose — adapter connect failed\n\n```\n{exc}\n```\n")
        return 1

    sys.stdout.write(format_diagnosis(diagnosis))
    return 0
