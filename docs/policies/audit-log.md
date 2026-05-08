# Audit log policy

Closes **SOC2-06** + **OWASP-06** fold from `docs/research-brief-compliance-audit-2026-05-06.md` § 2.2 + § 2.3.

The audit log is bicameral-mcp's **operator-facing incident-readability surface** — one structured JSON line per tool invocation, server lifecycle event, or gate-fired event. It is distinct from `preflight_telemetry.py`'s machine-join JSONL writers (`~/.bicameral/preflight_events.jsonl`) and `telemetry.py`'s PostHog outbound (anonymized product analytics). Operators consume the audit log via stderr in foreground sessions or via a configured file path in self-hosted deployments.

## Channel resolution

| `BICAMERAL_AUDIT_LOG` value | Behavior |
|---|---|
| unset (default) | Emit to `stderr` |
| `stderr` | Emit to `stderr` |
| `<path>` (any other value) | Append-mode file at `<path>` |
| `disabled` | No-op (every emit returns immediately) |
| `<unwriteable path>` | Fall back to `stderr` and write one warning marker describing the fallback |

## Level resolution

`BICAMERAL_AUDIT_LOG_LEVEL` filters event types by their per-class level:

| Level | Event types emitted |
|---|---|
| `info` (default) | All event types |
| `warn` | `ingest_refusal`, `preflight_bypass`, `gate_fired`, `error` only |
| `error` | `error` only |

## Event taxonomy

The closed enum `audit_log.AuditEventType` captures every event class:

| `event_type` | Level | Source | Fields |
|---|---|---|---|
| `tool_invocation` | info | `@server.call_tool()` wrapper | `tool_name`, `duration_ms`, `outcome_class` (`ok` / `refused` / `error`), `session_id` (if present in args) |
| `server_start` | info | `serve_stdio()` entry | `version` |
| `server_shutdown` | info | `serve_stdio()` `finally` | `version` |
| `config_load` | info | First `BicameralContext.from_env()` per process (idempotent guard) | `ingest_max_bytes`, `ingest_rate_limit_burst`, `ingest_rate_limit_refill_per_sec`, `guided_mode` |
| `ingest_refusal` | warn | `handlers.ingest._emit_ingest_refusal_telemetry` (dual-write) | `reason`, `session_id` |
| `preflight_bypass` | warn | (deferred — v1 bypass surface reverted; will activate when the surface returns) | `reason`, `session_id` |
| `gate_fired` | warn | (extension surface for future gate handlers) | (caller-defined) |
| `error` | error | catch-all for unknown `event_type` strings (coerced from string with `original_event_type` field) | (caller-defined) |
| `ledger_schema_verified` | info | `adapter.connect()` after `init_schema` + `migrate` (#252 Layer 2) | `surrealdb_client_version_running`, `bicameral_schema_version`, `status` (`first-write` / `match`) |
| `ledger_version_drift` | warn | `adapter.connect()` after `init_schema` + `migrate` (#252 Layer 2) | `surrealdb_client_version_recorded`, `surrealdb_client_version_running`, `bicameral_schema_version` |

## Forbid-list discipline

The audit log enforces a static frozenset of forbidden field keys. Any field with one of these names is stripped before serialization, and the rendered record gains a `forbidden_keys_stripped` list so the operator sees the redaction event without ever seeing the content:

```
{decision_text, file_paths, transcript, arguments, payload,
 content, text, body, output, result_text}
```

The forbid-list is checked at write-time in `audit_log._strip_forbidden`. It is symmetric with `telemetry.py`'s type-shape filtering for PostHog outbound (which drops non-int/float/bool diagnostic values). v1 ships the static set; runtime extension via env var (e.g. `BICAMERAL_AUDIT_LOG_FORBID_EXTRA`) is deferred to v2 if telemetry shows operators want runtime control.

## Failure semantics

The audit log is fire-and-forget. `audit_log.emit()` MUST NOT raise. Every emit is wrapped in:

1. An outer `try / except Exception` that swallows any internal error
2. A last-ditch stderr-marker write so the operator sees the surface failed without the server crashing
3. A final `try / except` around the marker write itself (silent drop is the only remaining option in catastrophic triple-failure)

This matches industry-standard application-audit-log behavior (POSIX syslog precedent). Operators who need delivery guarantees configure `BICAMERAL_AUDIT_LOG=<path>` to a reliable mount and run a separate collector.

## Dual-write at gate sites

Existing local-telemetry JSONL writers (`preflight_telemetry.write_ingest_refusal_event`) **dual-write** to both the JSONL surface and the audit log. The dual-write helper enforces bidirectional exception isolation: failure of either surface MUST NOT block the other, and the original gate-raise (`_IngestRefused`) propagates cleanly via the caller's `raise`.

The two surfaces have distinct consumers:
- The JSONL surface is **machine-join**: telemetry pipeline consumes events via `preflight_id` / `session_id` joins for product-analytics shape
- The audit log is **operator-readable**: incident-response shape, one line per event, optional file-collector pipeline

## Integration patterns

### logrotate

```conf
# /etc/logrotate.d/bicameral-audit
/var/log/bicameral-audit.log {
    rotate 14
    daily
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
}
```

Then run with `BICAMERAL_AUDIT_LOG=/var/log/bicameral-audit.log`.

### journalctl (systemd)

When bicameral-mcp runs under a systemd unit, stderr is captured by journald automatically. No additional config; query with `journalctl -u <unit-name>`.

### File collectors (Vector, Fluent Bit, Promtail)

Point the collector at the configured file path (e.g. `/var/log/bicameral-audit.log`); each line is a complete JSON object — standard `parser.json` config applies.

## Log retention guidance

- **Local-developer-tool deployments**: stderr capture in shell history; no retention obligation.
- **Self-hosted operator deployments**: 90-day retention is the SOC 2 CC norm; log-rotation + collector pipeline cover it.
- **Auditor evidence**: pair this audit log with `docs/RELEASE_EVIDENCE_PROCEDURE.md` for the per-release change-control trail.

## Out of scope (v1)

- **Log rotation** built into the application — operators use logrotate / journald / collector-side rotation
- **Remote log shipping** — the audit log is a local emission surface; collectors handle ship
- **Per-event-type Pydantic schemas** — closed enum + flat-dict payload is the v1 contract; per-class schemas are YAGNI until telemetry shows operators want field-level validation guarantees
- **Runtime forbid-list extension** — v2 question (`BICAMERAL_AUDIT_LOG_FORBID_EXTRA` env var)
- **Per-tool argument logging** — explicitly forbidden by the forbid-list; the wrapper records `tool_name` + `duration_ms` + `outcome_class` only

## References

- `audit_log.py` — module source
- `tests/test_audit_log_*.py` — functional test suite (28 tests across 6 files)
- `docs/research-brief-compliance-audit-2026-05-06.md` § 2.2 SOC2-06, § 2.3 OWASP-06
- `tests/test_compliance_policy_docs.py::test_audit_log_policy_doc_includes_channel_resolution_table` — content-contract test locking the channel-resolution table presence
