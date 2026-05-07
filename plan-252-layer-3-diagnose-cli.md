# Plan: #252 Layer 3 — `bicameral-mcp diagnose` CLI for privacy-preserving operator bug reports

**change_class**: feature

**doc_tier**: standard

**terms_introduced**:
- term: diagnose CLI
  home: cli/diagnose.py
- term: Diagnosis (dataclass)
  home: cli/diagnose.py
- term: diagnose-output allowlist
  home: cli/diagnose.py
- term: diagnose suggestion engine
  home: cli/diagnose.py
- term: diagnose-output policy
  home: docs/policies/diagnose-output.md

**boundaries**:
- limitations: Allowlist-by-field-name discipline (more restrictive than #227's audit-log forbid-list because the diagnose output is operator-pasteable verbatim into bug reports). Output is plain markdown — designed for direct paste into GitHub issues. The audit-log tail covers two surfaces in parallel: `~/.bicameral/preflight_events.jsonl` (always available — populated by the existing `write_ingest_refusal_event` / `write_bypass_event` writers + #227's dual-write) AND the audit-log file when `BICAMERAL_AUDIT_LOG=<path>` is set (operator-configured channel from #227). Tailing both gives full utility regardless of audit-log channel choice; the channel resolution itself is reported in the output so operators see which feed they're viewing. Schema-revision sentinel reads (`bicameral_meta` table from #252 Layer 2) tolerate the table being absent (pre-v16 ledger); falls back to "schema_version_recorded: unknown (pre-Layer-2 ledger)". Suggestion engine is 5 hardcoded heuristics (drift / recommended-version-mismatch / audit-log-disabled / ledger-size / schema-version-old); not plugin-extensible in v1. Operators with custom diagnostic needs run the JSON internals directly.
- non_goals: do not emit JSON output in v1 — markdown-styled is the right shape for bug-report paste; YAGNI on `--json` flag until telemetry shows machine-parsing demand. Do not add an interactive review prompt before printing — operator-side review of the rendered output is sufficient (allowlist enforcement at write-time is the airtight surface). Do not auto-upload the diagnostic to a remote endpoint — operator owns the lifecycle of the rendered text end-to-end. Do not extend the suggestion engine via plugins or config in v1. Do not add row-content sampling (e.g. "first 3 decision descriptions") — table row counts only.
- exclusions: not modifying `audit_log.py`, `ledger/schema.py`, or `ledger/adapter.py` (Layer 3 reads from the surfaces those modules expose; no extension). Not modifying `preflight_telemetry.py`'s JSONL writers (Layer 3 reads the JSONL file as an external consumer). Not extending the existing `tests/test_compliance_policy_docs.py::test_audit_log_policy_doc_documents_event_taxonomy` — Layer 3 ships its own `docs/policies/diagnose-output.md` content-contract test as a separate function. Not adding new audit-log event types — `bicameral-mcp diagnose` is a CLI subcommand, not a server-runtime event source.

## Open Questions

All resolved during /qor-plan dialogue 2026-05-07:

- **Module location**: `cli/diagnose.py` (option a) — matches `cli/link_commit_cli.py` pattern.
- **Output format**: plain markdown (option a) — operators paste into GitHub issues; markdown-styled is the right shape; YAGNI on `--json` flag.
- **Audit-log tail source**: hybrid (option a — full utility) — tail `~/.bicameral/preflight_events.jsonl` + ALSO tail `BICAMERAL_AUDIT_LOG=<path>` if set; report channel resolution in the output.
- **Recent event tail count**: last 5 events at warn|error level (option a) — bounded output.
- **Row-count source**: hardcoded canonical table list + tolerate-missing-table (option a + c) — covers the bicameral_meta-not-yet-present case for pre-v16 ledgers.
- **Privacy enforcement**: explicit allowlist field-by-field at the `Diagnosis` dataclass level (option b) — more restrictive than #227's forbid-list because the output is operator-pasteable verbatim.
- **Suggestion engine**: 5 hardcoded heuristics (option a) — drift detected / recommended-version-mismatch / audit-log-disabled / ledger-size / schema-version-old.

## Dependencies

- **Requires #252 Layer 2 (#256) merged to dev** for the `bicameral_meta` table to exist. Layer 3 reads the table tolerantly (handles missing table via "pre-Layer-2 ledger" fallback), so the implementation can land before #256 merges, but the integration tests that exercise the bicameral_meta read path require Layer 2 to be available. Implementer rebases onto `upstream/dev` after #256 merges before running CI.

## Phase 1: `Diagnosis` dataclass + `gather_diagnosis()` pure-data function

### Affected Files

- `tests/test_diagnose_gather.py` — **new** functionality tests for `gather_diagnosis(adapter)` returning the populated `Diagnosis` dataclass; tests the allowlist enforcement, tolerant bicameral_meta reads, and the per-field privacy posture
- `tests/test_diagnose_allowlist.py` — **new** functionality tests for the `_ALLOWED_FIELDS` frozenset membership + the dataclass field set parity
- `cli/diagnose.py` — **new** module: `Diagnosis` dataclass, `_ALLOWED_FIELDS` frozenset, `gather_diagnosis(adapter)` async function, helper readers (`_read_ledger_metadata`, `_read_bicameral_meta`, `_read_table_counts`, `_tail_recent_events`, `_compute_suggestions`)

### Changes

**`cli/diagnose.py`** (new):

```python
"""bicameral-mcp diagnose — privacy-preserving operator bug-report tool.

Closes #252 Layer 3 per
docs/research-brief-252-privacy-preserving-ledger-remediation.md.

Emits a markdown-styled report containing structural metadata only:
versions, file metadata, table row counts, schema-revision sentinel
state, recent warn|error event tail. No decision content, no source
attribution, no row contents. Operators copy-paste the rendered output
into GitHub bug reports without privacy concerns.

Allowlist discipline: the ``Diagnosis`` dataclass enumerates every
field that may appear in the output. ``_ALLOWED_FIELDS`` (frozenset)
locks the field set; any future expansion requires editing both the
dataclass and the frozenset, which is how we lock the privacy posture
at code-review time.
"""

from __future__ import annotations

import dataclasses
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Frozen field allowlist — every emitted field appears here. Adding a
# new field requires adding the dataclass attribute AND the entry in
# this frozenset; the parity is locked by
# `tests/test_diagnose_allowlist.py::test_dataclass_fields_match_allowlist`.
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
        "drift_status",  # "first-write" | "match" | "drift" | "unavailable"
        "audit_log_channel",  # "stderr" | "<path>" | "disabled"
        "table_counts",  # dict[str, int]
        "recent_events",  # list[dict] — pre-redacted per write site
        "suggestions",  # list[str]
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
)


@dataclasses.dataclass(frozen=True)
class Diagnosis:
    """Structural-metadata-only diagnostic report. Every field is in
    `_ALLOWED_FIELDS`. None / "unknown" / empty values are valid; missing
    fields would mean the dataclass is malformed (locked by tests)."""

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
    recent_events: list[dict[str, Any]]
    suggestions: list[str]


async def gather_diagnosis(adapter) -> Diagnosis:
    """Collect every allowlisted field from the running install + ledger.

    Pure-data: no rendering, no I/O beyond the ledger queries and file
    stat operations. Returns a frozen ``Diagnosis`` instance suitable
    for direct dataclasses.asdict() serialization or
    ``format_diagnosis()`` rendering.

    Tolerant of missing surfaces: pre-v16 ledgers (no bicameral_meta)
    return ``drift_status="unavailable"``; surrealdb-not-installed
    returns ``surrealdb_running="unknown"``; the ledger file might not
    exist yet (memory:// URL) — emits ``ledger_size_bytes=None``.
    """
    # ... see Implementer notes for full body shape ...
```

The body of `gather_diagnosis` orchestrates the small private readers (`_read_ledger_metadata`, `_read_bicameral_meta`, `_read_table_counts`, `_tail_recent_events`, `_compute_suggestions`) and assembles the `Diagnosis` instance. Each reader is ≤25 LOC; the orchestrator is ≤30 LOC.

**Reader signatures** (private helpers in `cli/diagnose.py`):

```python
def _read_ledger_metadata(adapter) -> tuple[str, int | None, str | None]:
    """Return (ledger_url, size_bytes_or_None, mtime_iso_or_None).
    Handles memory:// (no file) + missing-file (size=None) cases."""

async def _read_bicameral_meta(adapter) -> tuple[str | None, str | None, str | None, str]:
    """Return (first_write, last_write, last_write_at_iso, drift_status).
    Returns ("...", "...", "...", "drift" | "match" | "first-write")
    when bicameral_meta exists; ("unknown", "unknown", None, "unavailable")
    when the table doesn't exist (pre-v16 ledger). Drift status is
    computed against importlib.metadata.version("surrealdb")."""

async def _read_table_counts(adapter) -> dict[str, int]:
    """Iterate _CANONICAL_TABLES; SELECT count() FROM <table>; tolerate
    missing tables (e.g., bicameral_meta on pre-v16 ledgers)."""

def _tail_recent_events(jsonl_path: Path, audit_log_path: Path | None, limit: int = 5) -> list[dict]:
    """Read the last `limit` warn|error-level lines from the JSONL.
    Pre-redacted at write site by preflight_telemetry's existing
    discipline + audit_log's _strip_forbidden — Layer 3 trusts those
    surfaces and re-asserts the redaction at allowlist-check time.
    Reads both paths in parallel and merges by timestamp; if
    audit_log_path is None (stderr-channel default) skips that source."""

def _compute_suggestions(d: Diagnosis) -> list[str]:
    """Run 5 hardcoded heuristics against the assembled Diagnosis;
    return list of operator-actionable suggestion strings.

    Heuristics:
      1. drift_status == "drift" → "Schema-revision drift: recorded {X} ≠ running {Y}; pip install --upgrade surrealdb=={X} to match writer, OR back up ledger and bicameral-mcp reset."
      2. bicameral_version != fetched_recommended_version → "Recommended version {Y} available; bicameral.update {action: 'apply'} to upgrade."
      3. audit_log_channel == "stderr" → "Audit log not file-persisted. Set BICAMERAL_AUDIT_LOG=<path> to capture incident events for SOC 2 evidence."
      4. ledger_size_bytes > 100 * 1024 * 1024 → "Ledger file > 100 MiB; consider future bicameral-mcp ledger-export (Layer 4) for backup."
      5. schema_version_recorded < schema_version_expected → "Ledger schema {X} < binary schema {Y}; run bicameral-mcp once to apply pending migrations."

    Empty list when no heuristic fires (clean install)."""
```

### Unit Tests

- `tests/test_diagnose_allowlist.py` (**new**):
  - `test_diagnosis_dataclass_fields_match_allowlist` — every field in `dataclasses.fields(Diagnosis)` has its name in `_ALLOWED_FIELDS`; reverse — every name in `_ALLOWED_FIELDS` is a Diagnosis field. Locks parity between the dataclass shape and the privacy allowlist.
  - `test_allowlist_excludes_known_content_field_names` — `_ALLOWED_FIELDS` does NOT contain any of `decision_text`, `description`, `source_ref`, `text`, `body`, `content`, `arguments` (the canonical content carriers from #227's forbid-list). Locks the negative discipline.
  - `test_diagnosis_is_frozen_dataclass` — `dataclasses.fields(Diagnosis)` returns frozen=True; mutation attempts raise `FrozenInstanceError`. Locks immutability.

- `tests/test_diagnose_gather.py` (**new**):
  - `test_gather_diagnosis_returns_complete_dataclass_on_fresh_memory_ledger` — invoke `gather_diagnosis(adapter)` against a fresh `memory://` adapter; assert returned object is a `Diagnosis` instance; assert all 17 fields are populated (no `None` for required fields like `bicameral_version`, `python_version`, `platform_str`, `surrealdb_running`, `ledger_url`, `schema_version_expected`, `drift_status`, `audit_log_channel`, `table_counts`, `recent_events`, `suggestions`).
  - `test_gather_diagnosis_returns_first_write_status_on_fresh_ledger` — fresh memory ledger; assert `drift_status == "first-write"` (Layer 2's sentinel populates the row at first connect).
  - `test_gather_diagnosis_returns_unavailable_status_when_bicameral_meta_missing` — pre-populate adapter at v15 (no `bicameral_meta` table); skip Layer 2's init path; assert `drift_status == "unavailable"` and `surrealdb_first_write/last_write` are both `"unknown"`.
  - `test_gather_diagnosis_returns_drift_status_when_versions_differ` — pre-populate `bicameral_meta` with `at_last_write="2.0.0"`; monkeypatch `importlib.metadata.version` to return `"2.1.0"`; reconnect; assert `drift_status == "drift"` and `surrealdb_first_write == "2.0.0"`, `surrealdb_running == "2.1.0"`.
  - `test_gather_diagnosis_table_counts_includes_all_canonical_tables` — assert returned `table_counts.keys()` is a subset of `_CANONICAL_TABLES`; missing tables (e.g., bicameral_meta on pre-v16) are silently absent rather than raising.
  - `test_gather_diagnosis_recent_events_tails_warn_error_only` — pre-populate `~/.bicameral/preflight_events.jsonl` with mix of info/warn/error lines (mocked via tmp_path); assert returned `recent_events` has only warn|error level entries, max length 5.
  - `test_gather_diagnosis_recent_events_merges_audit_log_path_when_set` — set `BICAMERAL_AUDIT_LOG=<tmp_path>` + populate both files; assert merged tail respects timestamp order + 5-event cap.
  - `test_gather_diagnosis_audit_log_channel_reflects_env_resolution` — toggle `BICAMERAL_AUDIT_LOG` env between unset / `disabled` / `<path>`; assert `audit_log_channel` reflects each.
  - `test_gather_diagnosis_suggestions_drift_heuristic_fires` — set up a drift state; assert `suggestions` list contains the drift-suggestion string.
  - `test_gather_diagnosis_suggestions_audit_log_disabled_heuristic_fires` — `BICAMERAL_AUDIT_LOG=disabled`; assert audit-log-disabled suggestion fires.
  - `test_gather_diagnosis_suggestions_empty_on_clean_install` — fresh memory ledger, no drift, audit log file-configured, ledger small, schema current; assert `suggestions == []`.
  - `test_gather_diagnosis_does_not_emit_decision_content` — ingest one decision into the ledger via `adapter.upsert_decision_*` (or fixture); assert no decision text/description/source_ref appears in any string field of the returned `Diagnosis` (negative-content lock; mirrors #227's forbid-list discipline at the consumer side).

## Phase 2: `format_diagnosis()` markdown renderer + CLI subparser wiring

### Affected Files

- `tests/test_diagnose_format.py` — **new** functionality tests for `format_diagnosis(d)` returning the operator-pasteable markdown string
- `tests/test_diagnose_cli.py` — **new** functionality tests for the CLI entrypoint (`bicameral-mcp diagnose`) end-to-end via subprocess
- `cli/diagnose.py` — extend with `format_diagnosis(d)` markdown renderer + `main(repo_path)` CLI entrypoint
- `server.py` — add `subparsers.add_parser("diagnose", ...)` registration + `if args.command == "diagnose":` dispatch arm

### Changes

**`cli/diagnose.py`** (extension):

```python
def format_diagnosis(d: Diagnosis) -> str:
    """Render a Diagnosis instance as operator-pasteable markdown.

    Output shape: section headers (## Versions, ## Ledger, ## Schema
    revision sentinel, ## Table row counts, ## Recent events, ## Suggested
    remediation) + the standard operator-paste-instruction footer.
    """
    # ~50 LOC pure-string-building, reads only allowlisted fields.
    ...


def main(repo_path: str | None = None) -> int:
    """CLI entrypoint for `bicameral-mcp diagnose`.

    Connects an adapter against the resolved ledger URL, gathers the
    Diagnosis, prints the rendered markdown to stdout, returns 0.
    Exits non-zero only on adapter-connect failure (which the operator
    needs to see in the bug report anyway).
    """
    import asyncio

    async def _run() -> Diagnosis:
        from ledger.adapter import SurrealDBLedgerAdapter
        adapter = SurrealDBLedgerAdapter()
        await adapter.connect()
        return await gather_diagnosis(adapter)

    try:
        diagnosis = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — operator needs the failure context
        print(f"# bicameral-mcp diagnose — adapter connect failed\n\n```\n{exc}\n```\n")
        return 1

    print(format_diagnosis(diagnosis))
    return 0
```

**`server.py`** (extensions):

In `_register_subparsers`, add after the existing `link` registration:

```python
subparsers.add_parser(
    "diagnose",
    help="emit a privacy-preserving operator bug-report (#252 Layer 3)",
)
```

In `_dispatch` (or `cli_main`'s command-dispatch chain), add after the `link_commit` arm:

```python
if args.command == "diagnose":
    from cli.diagnose import main as diagnose_main

    return diagnose_main(getattr(args, "repo_path", None))
```

### Unit Tests

- `tests/test_diagnose_format.py` (**new**):
  - `test_format_diagnosis_emits_markdown_with_required_section_headers` — fixture `Diagnosis` with all fields populated; assert rendered string contains `## Versions`, `## Ledger`, `## Schema revision sentinel`, `## Table row counts`, `## Recent events`, `## Suggested remediation`.
  - `test_format_diagnosis_emits_versions_section_with_bicameral_python_surrealdb` — assert each version string appears in the rendered output.
  - `test_format_diagnosis_emits_table_counts_as_indented_list` — fixture with `table_counts={"decision": 47, "code_region": 89}`; assert both lines appear with the `decision: 47` shape.
  - `test_format_diagnosis_emits_drift_status_in_schema_section` — fixture with `drift_status="drift"`; assert section contains "DRIFT" or similar visible-flag.
  - `test_format_diagnosis_emits_recent_events_with_event_type_only` — fixture with `recent_events=[{"event_type": "ingest_refusal", "level": "warn", "ts": ...}]`; assert event_type appears, no `decision_text` or content keys appear.
  - `test_format_diagnosis_emits_operator_paste_instruction_footer` — assert footer instructs operator to paste output into bug report at the GitHub issues URL; assert it explicitly notes "no decision content or source attribution will be included".
  - `test_format_diagnosis_renders_empty_suggestions_list_as_clean_install_message` — fixture with `suggestions=[]`; assert section reads "No issues detected; install looks healthy" or similar; not an empty section.
  - `test_format_diagnosis_does_not_emit_any_forbidden_content_field_names` — render full fixture; assert none of `decision_text`, `description`, `source_ref`, `text`, `body`, `content`, `arguments` appear in the rendered string. Doctrine lock against future field-name expansion.

- `tests/test_diagnose_cli.py` (**new**):
  - `test_diagnose_cli_subprocess_returns_zero_on_fresh_memory_ledger` — subprocess.run `python -m server diagnose` (or equivalent) with `SURREAL_URL=memory://` env; assert exit 0.
  - `test_diagnose_cli_subprocess_emits_markdown_with_required_sections` — same shape; capture stdout; assert contains the six section headers from format_diagnosis tests.
  - `test_diagnose_cli_subprocess_returns_one_on_adapter_connect_failure` — set `SURREAL_URL` to an invalid URL; assert exit 1 and stdout contains the failure message.

## Phase 3: operator policy doc + content-contract test

### Affected Files

- `tests/test_compliance_policy_docs.py` — extend with `test_diagnose_output_policy_doc_lists_allowlisted_fields` and `test_diagnose_output_policy_doc_documents_suggestion_heuristics`
- `docs/policies/diagnose-output.md` — **new** operator-readable policy: enumerated allowlist of emitted fields with their privacy rationale; suggestion heuristic catalog (the 5 heuristics + their trigger conditions); operator-paste discipline (always-safe-to-paste guarantee)
- `README.md` — extend "Compliance posture" section with one bullet pointing to `docs/policies/diagnose-output.md` (mirrors the pattern from #218 LLM-06 / #227 audit-log)

### Changes

**`docs/policies/diagnose-output.md`** (new):

```markdown
# `bicameral-mcp diagnose` output policy

Closes #252 Layer 3 of the privacy-preserving ledger-remediation
strategy (`docs/research-brief-252-privacy-preserving-ledger-remediation.md`).

The `bicameral-mcp diagnose` CLI emits a markdown-styled report
containing **structural metadata only** — versions, file metadata,
table row counts, schema-revision sentinel state, recent warn|error
event tail. This document enumerates every field that may appear in
the output + the privacy posture for each, so operators can paste the
rendered text directly into bug reports without privacy review.

## Allowlist of emitted fields

| Field | Type | Source | Privacy class |
|---|---|---|---|
| `bicameral_version` | str | `importlib.metadata.version("bicameral-mcp")` | structural |
| `python_version` | str | `sys.version` | structural |
| `platform_str` | str | `platform.platform()` | structural |
| `surrealdb_running` | str | `importlib.metadata.version("surrealdb")` | structural |
| `ledger_url` | str | `os.getenv("SURREAL_URL")` or default `surrealkv://~/.bicameral/ledger.db` | structural (path-bearing — operator may redact pre-paste if install path is sensitive) |
| `ledger_size_bytes` | int \| None | `Path.stat().st_size` | structural |
| `ledger_mtime_iso` | str \| None | `Path.stat().st_mtime` ISO-formatted | structural |
| `schema_version_recorded` | int \| None | `SELECT version FROM schema_meta` | structural |
| `schema_version_expected` | int | `ledger.schema.SCHEMA_VERSION` | structural |
| `surrealdb_first_write` | str \| None | `bicameral_meta.surrealdb_client_version_at_first_write` (#252 Layer 2) | structural |
| `surrealdb_last_write` | str \| None | same — `at_last_write` | structural |
| `last_write_at` | str \| None | `bicameral_meta.last_write_at` ISO-formatted | structural |
| `drift_status` | str | computed: `first-write` / `match` / `drift` / `unavailable` | structural |
| `audit_log_channel` | str | `os.getenv("BICAMERAL_AUDIT_LOG")` resolved to `stderr` / `<path>` / `disabled` | structural (path-bearing — operator may redact pre-paste) |
| `table_counts` | dict[str, int] | `SELECT count() FROM <table>` per `_CANONICAL_TABLES` | structural — counts only, never row content |
| `recent_events` | list[dict] | last 5 warn\|error lines from `~/.bicameral/preflight_events.jsonl` (+ audit-log file when configured) | pre-redacted at write site by `preflight_telemetry` + `audit_log._strip_forbidden`; Layer 3 emits `event_type` + `level` + `ts` only, never event-detail strings |
| `suggestions` | list[str] | `_compute_suggestions(d)` static heuristics | structural — emit only the trigger condition + suggested operator action |

## Suggestion heuristic catalog

5 hardcoded heuristics fire based on the assembled Diagnosis fields:

1. **drift detected** — `drift_status == "drift"` → recommend `pip install --upgrade surrealdb==<recorded>` or `bicameral-mcp reset` after backup.
2. **recommended-version mismatch** — `bicameral_version` differs from the fetched `RECOMMENDED_VERSION` → recommend `bicameral.update {action: "apply"}`.
3. **audit log disabled** — `audit_log_channel == "stderr"` (default) → recommend setting `BICAMERAL_AUDIT_LOG=<path>` for SOC 2 evidence capture.
4. **ledger > 100 MiB** — `ledger_size_bytes > 100 * 1024 * 1024` → recommend future `bicameral-mcp ledger-export` (Layer 4) for backup.
5. **schema version old** — `schema_version_recorded < schema_version_expected` → recommend running `bicameral-mcp` once to apply pending migrations.

Operators with custom diagnostic needs run `python -m cli.diagnose` directly and inspect the `Diagnosis` dataclass; the suggestion engine is a UX layer over the structural data, not a gate.

## Operator paste discipline

The rendered output is **always safe to paste** into a public bug report. The allowlist above is enforced at write-time by the `Diagnosis` dataclass + `_ALLOWED_FIELDS` frozenset; any drift between the dataclass and the allowlist is caught by `tests/test_diagnose_allowlist.py::test_diagnosis_dataclass_fields_match_allowlist`. The forbidden-field name lock (`tests/test_diagnose_format.py::test_format_diagnosis_does_not_emit_any_forbidden_content_field_names`) catches any future field whose name matches the #227 forbid-list.

Two operator-judgment items remain (not server-enforced):
- **`ledger_url`** can carry an install path (e.g., `surrealkv:///home/jdoe/.bicameral/ledger.db`). If the path is sensitive, redact before paste.
- **`audit_log_channel`** can carry a configured file path. Same redaction guidance.

## References

- `cli/diagnose.py` — module source
- `tests/test_diagnose_*.py` — functional test suite (~25 tests across 4 files)
- `docs/research-brief-252-privacy-preserving-ledger-remediation.md` — Layer 3 strategy
- `docs/policies/audit-log.md` — sister surface (#227); the audit-log forbid-list catches accidents at the write site; Layer 3's allowlist catches accidents at the read site
```

**`README.md`** (extension) — bump compliance-posture section from 4 → 5 policy files; add `docs/policies/diagnose-output.md` row.

### Unit Tests

- `tests/test_compliance_policy_docs.py` (extension):
  - `test_diagnose_output_policy_doc_lists_allowlisted_fields` — read `docs/policies/diagnose-output.md`; assert every field name in `cli.diagnose._ALLOWED_FIELDS` appears in the doc's allowlist table. Locks doc/code drift between the allowlist constant and the operator-facing policy doc.
  - `test_diagnose_output_policy_doc_documents_suggestion_heuristics` — assert each of the 5 heuristic identifiers (`drift detected`, `recommended-version mismatch`, `audit log disabled`, `ledger > 100 MiB`, `schema version old`) appears in the doc's heuristic catalog section.

## CI Commands

- `pytest tests/test_diagnose_allowlist.py tests/test_diagnose_gather.py -v` — Phase 1 (allowlist + dataclass + gather_diagnosis pure-data).
- `pytest tests/test_diagnose_format.py tests/test_diagnose_cli.py -v` — Phase 2 (renderer + CLI entrypoint).
- `pytest tests/test_compliance_policy_docs.py -v` — Phase 3 (policy-doc content contract).
- `pytest tests/test_diagnose_*.py tests/test_compliance_policy_docs.py tests/test_audit_log_*.py tests/test_ledger_bicameral_meta_*.py -q` — full Layer-3-and-sister regression.
- `pytest tests/ -q` — broader regression baseline (1000+ tests).
- `ruff check cli/diagnose.py server.py tests/test_diagnose_*.py` — lint clean.
- `ruff format --check cli/diagnose.py server.py tests/test_diagnose_*.py` — format clean.

## Implementer notes

- **Allowlist enforcement is the load-bearing privacy mechanism**: any new field added to the `Diagnosis` dataclass MUST be added to `_ALLOWED_FIELDS` in the same commit. The `test_diagnosis_dataclass_fields_match_allowlist` test fails on drift; treat that test as a gate (not a hint).
- **Suggestion-engine evolution discipline**: heuristics live in `_compute_suggestions`. Adding a new heuristic requires updating the policy doc's catalog section in the same commit; the `test_diagnose_output_policy_doc_documents_suggestion_heuristics` test fails on drift.
- **Pre-v16 ledger fallback**: the `bicameral_meta` table won't exist until #252 Layer 2 (#256) merges and downstream installs upgrade. `_read_bicameral_meta` MUST tolerate the table being absent; return `("unknown", "unknown", None, "unavailable")` on `LedgerError` referencing `bicameral_meta`. Locked by `test_gather_diagnosis_returns_unavailable_status_when_bicameral_meta_missing`.
- **Audit-log tail merging**: when `BICAMERAL_AUDIT_LOG=<path>` and the file exists, parallel-read both `~/.bicameral/preflight_events.jsonl` and the audit-log path; merge by `ts` field (ISO-format string sort works for the 8601 RFC-3339 shape both writers emit); cap merged result at 5. When audit-log path is unset / `stderr` / `disabled` / file-missing, only tail the JSONL.
- **CLI subprocess test discipline**: `tests/test_diagnose_cli.py` runs `python -c "from cli.diagnose import main; sys.exit(main())"` (or similar) under controlled env vars. Avoid spawning a full `bicameral-mcp` subprocess that includes the MCP-server bootstrap path; the diagnose CLI is a stand-alone subcommand.
- **Implementation can land before #256 merges** — Layer 3's bicameral_meta read is tolerant of the table being absent, so the implementation tests pass on either pre-Layer-2 or post-Layer-2 dev. The integration test `test_gather_diagnosis_returns_first_write_status_on_fresh_ledger` requires Layer 2 to be available; mark `xfail(condition=<bicameral_meta missing>, reason="requires #252 Layer 2 / #256 merge")` until #256 lands. Implementer flips the marker to a regular pass when CI runs against post-#256 dev.
- **Operator-paste guarantee in the rendered footer**: the markdown footer says "no decision content or source attribution will be included." This claim is locked by `test_format_diagnosis_does_not_emit_any_forbidden_content_field_names` + the allowlist enforcement. Don't weaken the footer wording without re-validating the discipline.
