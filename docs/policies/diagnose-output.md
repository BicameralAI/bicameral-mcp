# `bicameral-mcp diagnose` output policy

Closes **#252 Layer 3** of the privacy-preserving ledger-remediation strategy (`docs/research-brief-252-privacy-preserving-ledger-remediation.md`).

The `bicameral-mcp diagnose` CLI emits a markdown-styled report containing **structural metadata only** — versions, file metadata, table row counts, schema-revision sentinel state, recent warn|error event tail. This document enumerates every field that may appear in the output + the privacy posture for each, so operators can paste the rendered text directly into bug reports without privacy review.

## Allowlist of emitted fields

| Field | Type | Source | Privacy class |
|---|---|---|---|
| `bicameral_version` | str | `importlib.metadata.version("bicameral-mcp")` | structural |
| `python_version` | str | `sys.version.split()[0]` | structural |
| `platform_str` | str | `platform.platform()` | structural |
| `surrealdb_running` | str | `importlib.metadata.version("surrealdb")` | structural |
| `ledger_url` | str | `os.getenv("SURREAL_URL")` or default | structural (path-bearing — operator may redact pre-paste if install path is sensitive) |
| `ledger_size_bytes` | int \| None | `Path.stat().st_size` | structural |
| `ledger_mtime_iso` | str \| None | `Path.stat().st_mtime` ISO-formatted | structural |
| `schema_version_recorded` | int \| None | `SELECT version FROM schema_meta` | structural |
| `schema_version_expected` | int | `ledger.schema.SCHEMA_VERSION` | structural |
| `surrealdb_first_write` | str \| None | `bicameral_meta.surrealdb_client_version_at_first_write` (#252 Layer 2) | structural |
| `surrealdb_last_write` | str \| None | same — `at_last_write` | structural |
| `last_write_at` | str \| None | `bicameral_meta.last_write_at` ISO-formatted | structural |
| `drift_status` | str | computed: `first-write` / `match` / `drift` | structural |
| `audit_log_channel` | str | `os.getenv("BICAMERAL_AUDIT_LOG")` resolved to `stderr` / `<path>` / `disabled` | structural (path-bearing — operator may redact pre-paste) |
| `table_counts` | dict[str, int] | `SELECT count() FROM <table>` per `_CANONICAL_TABLES` | structural — counts only, never row content |
| `recent_events` | list[dict] | last 5 warn\|error lines from `~/.bicameral/preflight_events.jsonl` (+ audit-log file when configured) | pre-redacted at write site by `preflight_telemetry` + `audit_log._strip_forbidden`; Layer 3 emits `event_type` + `level` + `ts` only |
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

- `cli/diagnose.py` — module source (Diagnosis dataclass + format + main)
- `cli/_diagnose_gather.py` — gather + private readers (split per Razor headroom)
- `tests/test_diagnose_*.py` — functional test suite (~31 tests across 4 files)
- `docs/research-brief-252-privacy-preserving-ledger-remediation.md` — Layer 3 strategy
- `docs/policies/audit-log.md` — sister surface (#227); the audit-log forbid-list catches accidents at the write site; Layer 3's allowlist catches accidents at the read site
