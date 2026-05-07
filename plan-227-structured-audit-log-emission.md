# Plan: #227 — SOC2-06 + OWASP-06 fold: Structured audit-log emission for self-hosted operators

**change_class**: feature

**doc_tier**: standard

**terms_introduced**:
- term: audit-log surface
  home: audit_log.py
- term: audit-log event_type enum
  home: audit_log.py
- term: audit-log forbid-list
  home: audit_log.py
- term: BICAMERAL_AUDIT_LOG
  home: audit_log.py
- term: BICAMERAL_AUDIT_LOG_LEVEL
  home: audit_log.py
- term: lifespan event (start / shutdown / config-load)
  home: server.py

**boundaries**:
- limitations: stderr-default JSON-line emission with file-path override; not a remote log shipper. Operators with central-log infrastructure (CloudWatch, Loki, ELK) point at the file path and consume via their own collectors. The audit log is the **operator-facing incident-readability surface**, not a replacement for `~/.bicameral/preflight_events.jsonl` (which remains the machine-join telemetry surface). Existing JSONL writers (`write_ingest_refusal_event`, `write_bypass_event`, etc.) **dual-write**: the JSONL stays unchanged; an audit-log line emits in parallel. The forbid-list (`decision_text`, `file_paths`, `transcript`, `arguments`, `payload`, `content`, `text`, `body`, `output`, `result_text`) is enforced at write-time; a record carrying any forbidden key gets that key stripped + a `forbidden_keys_stripped` field added so the operator sees the redaction event but never the content.
- non_goals: do not add a new pip dependency (no `structlog`); stdlib `logging` + custom `JsonFormatter` covers the spec. Do not enumerate per-event-type Pydantic schemas in v1 (closed `event_type` enum + flat-dict payload is the lighter contract; per-class schemas are YAGNI until telemetry shows operators want field-validation guarantees). Do not re-emit existing telemetry-shaped JSONL events as audit-log events with a transformation layer; dual-write at the existing emit sites is simpler and the failure surfaces stay independent (audit-log write failure must never break ingest, telemetry write failure must never break audit).
- exclusions: not modifying `telemetry.py` (PostHog outbound stays as-is). Not modifying `preflight_telemetry.py`'s JSONL writers (their callers gain one new line of audit-log emission). Not adding per-tool argument logging (the wrapper records tool name + duration + outcome class only; arguments stay forbidden by the forbid-list). Not adding log rotation in v1 (operator points the file path at a logrotate-managed location if rotation is needed; mirroring `preflight_telemetry._maybe_rotate` is a v2 question).

## Open Questions

All resolved during /qor-plan dialogue 2026-05-07:

- **Channel** (option c): stderr is the default; `BICAMERAL_AUDIT_LOG=<path>` overrides to a file; `BICAMERAL_AUDIT_LOG=disabled` fully silences the surface. Matches the issue body's spec verbatim. Preserves the no-config default for fresh installs and gives self-hosted operators a single env knob.
- **Format machinery** (option a): stdlib `logging` + a custom `JsonFormatter` subclass. Zero new pip deps; `JsonFormatter` is ~30 LOC; future operator-side log handlers (file rotation, syslog, central shippers) compose with stdlib `logging` directly.
- **Event taxonomy** (option c): closed `event_type` enum + flat-dict payload. Enum prevents drift (a typo `tool_invokation` would fail validation at write-time); flat dict avoids per-class Pydantic-schema rev burden. v1 enum: `tool_invocation`, `server_start`, `server_shutdown`, `config_load`, `ingest_refusal`, `preflight_bypass`, `gate_fired`, `error`.
- **Already-logged-surface treatment** (option c — dual-write): existing `write_ingest_refusal_event` / `write_bypass_event` callers add ONE line: `audit_log.emit(event_type="ingest_refusal", ...)` next to the existing JSONL write. Two consumers (machine-join JSONL + operator-readable audit log); both writes are independent and either's failure is non-fatal to the other.
- **Server-lifecycle hook injection sites** (option c): explicit instrumentation. `serve_stdio()` gets `server_start` emit at entry and `server_shutdown` emit at exit. `BicameralContext.from_env()` gets a `config_load` emit on first call (idempotent module-level guard so per-tool calls don't re-emit). No `__main__`-time emit (would log on every CLI invocation including `--smoke-test`, which is noise).
- **Tool-invocation wrapper** (`@server.call_tool()` at server.py:862): single insertion point. Wrapper captures `tool_name`, `session_id`, `duration_ms`, `outcome_class` (`ok` | `refused` | `error`); never the arguments or the response body. Forbid-list enforces at write-time even if a future caller passes one of those keys.
- **Allowlist policy** (option b — forbid-list): `_FORBIDDEN_FIELDS = frozenset({"decision_text", "file_paths", "transcript", "arguments", "payload", "content", "text", "body", "output", "result_text"})`. Symmetric with `telemetry.py`'s type-shape filtering. Forbid-list is easier to extend at code-maintenance time than per-event-type allowlists or Pydantic schemas would be (one-line frozenset edit, no schema rev). Runtime-configurable extension via env var (e.g. `BICAMERAL_AUDIT_LOG_FORBID_EXTRA=foo,bar`) is explicitly out of scope for v1; v1 ships the static frozenset and adds the env-var surface in v2 if telemetry shows operators want runtime control.

## Phase 1: audit-log module + forbid-list + JsonFormatter

### Affected Files

- `tests/test_audit_log_forbid_list.py` — **new** functionality tests for `_strip_forbidden`, `_FORBIDDEN_FIELDS` membership, and the `forbidden_keys_stripped` redaction-event field
- `tests/test_audit_log_format.py` — **new** functionality tests for the `JsonFormatter`: emits valid JSON, includes `ts`/`level`/`event_type`/`session_id`/`message`/structured-fields, never emits forbidden keys
- `tests/test_audit_log_channel.py` — **new** functionality tests for channel resolution (stderr-default, file-path override, `disabled` no-op, fail-closed-to-stderr on unwriteable path)
- `audit_log.py` — **new** module: `AuditEventType` enum, `_FORBIDDEN_FIELDS`, `_strip_forbidden`, `JsonFormatter`, `_resolve_channel`, `_get_logger`, `emit` public API

### Changes

**`audit_log.py`** (**new**):

```python
"""Structured audit-log emission for self-hosted operators.

Closes SOC2-06 + OWASP-06 fold from
``docs/research-brief-compliance-audit-2026-05-06.md`` § 2.2 + § 2.3.

The audit log is the **operator-facing incident-readability surface**:
one JSON line per tool invocation, server lifecycle event, or gate-fired
event. Distinct from ``preflight_telemetry.py``'s JSONL writers (machine-
join telemetry) and ``telemetry.py``'s PostHog outbound (anonymized
product analytics). Operators consume the audit log via stderr in
foreground sessions or via a configured file path in self-hosted
deployments.

Channel resolution::

    BICAMERAL_AUDIT_LOG unset           -> stderr (default)
    BICAMERAL_AUDIT_LOG="stderr"        -> stderr
    BICAMERAL_AUDIT_LOG="<path>"        -> append-mode file at <path>
    BICAMERAL_AUDIT_LOG="disabled"      -> no-op (every emit returns)
    BICAMERAL_AUDIT_LOG="<unwriteable>" -> fall back to stderr +
                                            one warning at first emit

Level resolution (filters `event_type` to suppress noise)::

    BICAMERAL_AUDIT_LOG_LEVEL unset  -> "info"  (everything)
    BICAMERAL_AUDIT_LOG_LEVEL="warn" -> errors + refusals + bypasses
    BICAMERAL_AUDIT_LOG_LEVEL="error" -> errors only

Forbid-list discipline mirrors ``telemetry.py``'s type-shape filtering:
fields in ``_FORBIDDEN_FIELDS`` are stripped before serialization;
the record gains a ``forbidden_keys_stripped`` list so the operator
sees that redaction occurred without ever seeing the content.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import sys
import time
from typing import Any

_LOGGER_NAME = "bicameral.audit"
_FORBIDDEN_FIELDS = frozenset(
    {
        "decision_text",
        "file_paths",
        "transcript",
        "arguments",
        "payload",
        "content",
        "text",
        "body",
        "output",
        "result_text",
    }
)


class AuditEventType(str, enum.Enum):
    TOOL_INVOCATION = "tool_invocation"
    SERVER_START = "server_start"
    SERVER_SHUTDOWN = "server_shutdown"
    CONFIG_LOAD = "config_load"
    INGEST_REFUSAL = "ingest_refusal"
    PREFLIGHT_BYPASS = "preflight_bypass"
    GATE_FIRED = "gate_fired"
    ERROR = "error"


_LEVEL_BY_EVENT: dict[AuditEventType, str] = {
    AuditEventType.TOOL_INVOCATION: "info",
    AuditEventType.SERVER_START: "info",
    AuditEventType.SERVER_SHUTDOWN: "info",
    AuditEventType.CONFIG_LOAD: "info",
    AuditEventType.INGEST_REFUSAL: "warn",
    AuditEventType.PREFLIGHT_BYPASS: "warn",
    AuditEventType.GATE_FIRED: "warn",
    AuditEventType.ERROR: "error",
}

_LEVEL_RANK = {"info": 10, "warn": 20, "error": 30}


def _strip_forbidden(fields: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Remove any keys in `_FORBIDDEN_FIELDS` from `fields`. Return the
    cleaned dict + the list of stripped keys (so the emitted record can
    surface that redaction occurred). Never mutates the input."""
    stripped: list[str] = []
    cleaned: dict[str, Any] = {}
    for key, val in fields.items():
        if key in _FORBIDDEN_FIELDS:
            stripped.append(key)
            continue
        cleaned[key] = val
    return cleaned, stripped


class JsonFormatter(logging.Formatter):
    """Emits one JSON object per log record; flat top-level fields."""

    def format(self, record: logging.LogRecord) -> str:
        # `record.audit_payload` set by `emit()`; never use record.msg/.args.
        payload: dict[str, Any] = getattr(record, "audit_payload", {})
        return json.dumps(payload, default=str, sort_keys=True)


def _resolve_channel() -> tuple[str, str]:
    """Return (kind, target). kind ∈ {"stderr", "file", "disabled"}."""
    raw = os.getenv("BICAMERAL_AUDIT_LOG", "stderr").strip()
    if raw == "disabled":
        return "disabled", ""
    if raw in ("", "stderr"):
        return "stderr", ""
    return "file", raw


def _resolve_min_level_rank() -> int:
    raw = os.getenv("BICAMERAL_AUDIT_LOG_LEVEL", "info").strip().lower()
    return _LEVEL_RANK.get(raw, _LEVEL_RANK["info"])


def _build_handler() -> logging.Handler | None:
    """Return a configured handler, or None if `disabled`. On unwriteable
    file path, fall back to stderr and write one warning record."""
    kind, target = _resolve_channel()
    if kind == "disabled":
        return None
    if kind == "stderr":
        h: logging.Handler = logging.StreamHandler(sys.stderr)
    else:
        try:
            h = logging.FileHandler(target, mode="a", encoding="utf-8", delay=False)
        except OSError as exc:
            sys.stderr.write(
                f'{{"ts":{time.time()!r},"level":"warn","event_type":"error",'
                f'"message":"audit_log file path unwriteable; falling back to stderr",'
                f'"path":{target!r},"reason":{str(exc)!r}}}\n'
            )
            h = logging.StreamHandler(sys.stderr)
    h.setFormatter(JsonFormatter())
    return h


_logger: logging.Logger | None = None
_min_level_rank: int = _LEVEL_RANK["info"]


def _get_logger() -> logging.Logger | None:
    """Idempotent logger factory. Returns None when channel is disabled."""
    global _logger, _min_level_rank
    if _logger is not None:
        return _logger
    handler = _build_handler()
    if handler is None:
        return None
    logger = logging.getLogger(_LOGGER_NAME)
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    _logger = logger
    _min_level_rank = _resolve_min_level_rank()
    return logger


def emit(
    event_type: AuditEventType | str,
    *,
    session_id: str | None = None,
    message: str = "",
    **fields: Any,
) -> None:
    """Emit one structured audit-log record. Fire-and-forget; never raises.

    `event_type` is validated against `AuditEventType`; an unknown string
    is coerced to `AuditEventType.ERROR` with `original_event_type` field
    so drift is observable but never silently dropped.
    """
    try:
        if isinstance(event_type, str):
            try:
                event_type_enum = AuditEventType(event_type)
            except ValueError:
                fields["original_event_type"] = event_type
                event_type_enum = AuditEventType.ERROR
        else:
            event_type_enum = event_type
        level_str = _LEVEL_BY_EVENT[event_type_enum]
        if _LEVEL_RANK[level_str] < _min_level_rank:
            return
        logger = _get_logger()
        if logger is None:
            return
        cleaned, stripped = _strip_forbidden(fields)
        payload: dict[str, Any] = {
            "ts": time.time(),
            "level": level_str,
            "event_type": event_type_enum.value,
            "message": message,
            **cleaned,
        }
        if session_id is not None:
            payload["session_id"] = session_id
        if stripped:
            payload["forbidden_keys_stripped"] = stripped
        record = logger.makeRecord(
            _LOGGER_NAME, logging.INFO, "audit_log", 0, "", (), None
        )
        record.audit_payload = payload  # type: ignore[attr-defined]
        logger.handle(record)
    except Exception:
        # Audit log MUST NOT break ingest / tool dispatch. Last-ditch:
        # write a minimal failure marker to stderr so the operator can
        # see the surface failed without crashing the server.
        try:
            sys.stderr.write(
                '{"ts":' + repr(time.time()) + ',"level":"error",'
                '"event_type":"error","message":"audit_log emit failed"}\n'
            )
        except Exception:
            pass


def _reset_for_tests() -> None:
    """Clear the cached logger so test fixtures can re-resolve env."""
    global _logger, _min_level_rank
    _logger = None
    _min_level_rank = _LEVEL_RANK["info"]
```

### Unit Tests

- `tests/test_audit_log_forbid_list.py` (**new**):
  - `test_strip_forbidden_removes_decision_text` — `_strip_forbidden({"a": 1, "decision_text": "secret"})` returns `({"a": 1}, ["decision_text"])`.
  - `test_strip_forbidden_removes_all_listed_keys_in_one_pass` — input contains every member of `_FORBIDDEN_FIELDS`; assert all removed; `stripped` is sorted-stable per insertion order.
  - `test_strip_forbidden_does_not_mutate_input` — pass dict; assert original retains all keys after call.
  - `test_strip_forbidden_returns_empty_stripped_on_clean_input` — clean dict; `stripped == []`.
  - `test_forbidden_fields_includes_canonical_secret_carriers` — assert each of `decision_text`, `file_paths`, `transcript`, `arguments`, `payload`, `content`, `text`, `body`, `output`, `result_text` is in the frozenset.
  - `test_emit_strips_forbidden_and_surfaces_redaction_field` — `emit(TOOL_INVOCATION, decision_text="leak", a=1)` produces a record where the JSON has `a=1`, no `decision_text`, and `forbidden_keys_stripped == ["decision_text"]`.

- `tests/test_audit_log_format.py` (**new**):
  - `test_json_formatter_emits_valid_json_with_required_fields` — format a record with `audit_payload`; assert `json.loads(formatted)` returns a dict with `ts` (float), `level` (str), `event_type` (str), `message` (str).
  - `test_emit_omits_session_id_when_not_provided` — call `emit(SERVER_START, message="boot")`; assert emitted JSON has no `session_id` key.
  - `test_emit_includes_session_id_when_provided` — call `emit(TOOL_INVOCATION, session_id="abc", duration_ms=42)`; assert `"session_id": "abc"` and `"duration_ms": 42` present.
  - `test_emit_unknown_event_type_string_is_coerced_to_error_with_original_field` — call `emit("typo_event", message="x")`; assert emitted `event_type == "error"` and `original_event_type == "typo_event"`.
  - `test_emit_with_enum_value_uses_enum_string` — call `emit(AuditEventType.TOOL_INVOCATION)`; assert `event_type == "tool_invocation"`.
  - `test_emit_swallows_exceptions_and_writes_marker_to_stderr` — monkeypatch `_get_logger` to raise; capture stderr; call `emit(...)`; assert no exception propagated and stderr contains the failure marker.
  - `test_emit_below_min_level_is_dropped` — set `BICAMERAL_AUDIT_LOG_LEVEL=warn`; call `emit(AuditEventType.TOOL_INVOCATION, ...)` (info-level event); assert no record written.
  - `test_emit_at_or_above_min_level_passes` — set `BICAMERAL_AUDIT_LOG_LEVEL=warn`; call `emit(AuditEventType.INGEST_REFUSAL, ...)` (warn-level); assert record written.

- `tests/test_audit_log_channel.py` (**new**):
  - `test_resolve_channel_default_is_stderr` — env unset; `_resolve_channel()` returns `("stderr", "")`.
  - `test_resolve_channel_explicit_stderr_string` — `BICAMERAL_AUDIT_LOG=stderr`; returns `("stderr", "")`.
  - `test_resolve_channel_disabled_string` — `BICAMERAL_AUDIT_LOG=disabled`; returns `("disabled", "")`.
  - `test_resolve_channel_path_string` — `BICAMERAL_AUDIT_LOG=/tmp/bicameral-audit.log`; returns `("file", "/tmp/bicameral-audit.log")`.
  - `test_emit_disabled_channel_writes_nothing` — set `BICAMERAL_AUDIT_LOG=disabled`; `_reset_for_tests()`; call `emit(...)`; assert nothing on stderr and no file created.
  - `test_emit_file_channel_writes_to_file` — `tmp_path / "audit.jsonl"`; set env; `_reset_for_tests()`; call `emit(...)`; read file; assert one valid JSON line with expected `event_type`.
  - `test_emit_file_channel_unwriteable_falls_back_to_stderr_with_warning` — set env to a path inside a non-existent directory; capture stderr; `_reset_for_tests()`; call `emit(...)`; assert stderr contains both the fallback-warning marker AND the actual record.
  - `test_resolve_min_level_rank_default_is_info` — env unset; `_resolve_min_level_rank()` returns `_LEVEL_RANK["info"]`.

## Phase 2: integration — server lifecycle + tool-invocation wrapper + dual-write at JSONL emit sites

### Affected Files

- `tests/test_audit_log_server_lifecycle.py` — **new** functionality tests verifying `server_start` and `server_shutdown` emit at the right boundaries; `config_load` emits exactly once across multiple `BicameralContext.from_env()` calls
- `tests/test_audit_log_tool_invocation.py` — **new** functionality tests for the `@server.call_tool()` wrapper: emits `tool_invocation` with `tool_name`, `duration_ms`, `outcome_class`; outcome is `refused` on `_IngestRefused`, `error` on other exception, `ok` on normal return; arguments never appear in the emitted record (forbid-list locked)
- `tests/test_audit_log_dual_write.py` — **new** functionality tests verifying ingest-refusal and preflight-bypass emit BOTH the existing JSONL line AND an audit-log line; audit-log write failure does not break the JSONL write (and vice versa)
- `server.py` — wrap `serve_stdio()` with `audit_log.emit(SERVER_START, ...)` at entry + `SERVER_SHUTDOWN` in a `finally`; wrap the body of `call_tool()` with timing + outcome capture; emit `tool_invocation` on every dispatch
- `context.py` — module-level `_config_load_emitted` guard; `BicameralContext.from_env()` first call emits `config_load` event with surfaced config keys (NOT values for any forbidden key)
- `handlers/ingest.py` — single-line addition next to `_emit_ingest_refusal_telemetry`: `audit_log.emit(AuditEventType.INGEST_REFUSAL, session_id=..., reason=exc.reason)` (dual-write to existing JSONL)
- `handlers/preflight.py` — single-line addition at the bypass-event site: `audit_log.emit(AuditEventType.PREFLIGHT_BYPASS, session_id=..., reason=...)` (dual-write)

### Changes

**`server.py`** — new wrapper around the existing `call_tool` body:

```python
@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    import json
    import time
    from audit_log import AuditEventType, emit as audit_emit

    ctx = BicameralContext.from_env()
    t0 = time.monotonic()
    outcome = "ok"
    session_id_for_audit = arguments.get("session_id") if isinstance(arguments, dict) else None
    try:
        # ... existing body unchanged ...
        return result
    except _IngestRefused:
        outcome = "refused"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        audit_emit(
            AuditEventType.TOOL_INVOCATION,
            session_id=session_id_for_audit,
            tool_name=name,
            duration_ms=int((time.monotonic() - t0) * 1000),
            outcome_class=outcome,
        )
```

`serve_stdio()` extension:

```python
async def serve_stdio() -> None:
    from audit_log import AuditEventType, emit as audit_emit

    audit_emit(AuditEventType.SERVER_START, version=VERSION)
    try:
        # ... existing body unchanged ...
    finally:
        audit_emit(AuditEventType.SERVER_SHUTDOWN, version=VERSION)
```

**`context.py`** — module-level guard + first-call emit in `from_env`:

```python
_config_load_emitted = False


@classmethod
def from_env(cls) -> BicameralContext:
    global _config_load_emitted
    instance = cls(
        # ... existing body unchanged ...
    )
    if not _config_load_emitted:
        from audit_log import AuditEventType, emit as audit_emit

        audit_emit(
            AuditEventType.CONFIG_LOAD,
            ingest_max_bytes=instance.ingest_max_bytes,
            ingest_rate_limit_burst=instance.ingest_rate_limit_burst,
            ingest_rate_limit_refill_per_sec=instance.ingest_rate_limit_refill_per_sec,
            # NOTE: never emit `surreal_url`, `repo_path`, or any path-bearing field;
            # forbid-list catches `file_paths` but we additionally do not source path
            # values into the emit at all.
        )
        _config_load_emitted = True
    return instance
```

**`handlers/ingest.py`** — dual-write with bidirectional exception isolation:

```python
def _emit_ingest_refusal_telemetry(reason: str, session_id: str) -> None:
    """Dual-write the refusal event to both the JSONL telemetry file and
    the operator-facing audit log. Each write is exception-isolated; a
    failure on either surface MUST NOT block the other (the original
    ``_IngestRefused`` exception that triggered this helper must propagate
    cleanly via the caller's ``raise``, regardless of which surface
    fails).

    JSONL writes are nominally trusted not to raise (existing boundary),
    but the explicit try/except formalizes that trust at the helper level
    and is required for the bidirectional-independence test contract.
    """
    from audit_log import AuditEventType, emit as audit_emit

    try:
        preflight_telemetry.write_ingest_refusal_event(reason=reason, session_id=session_id)
    except Exception:  # noqa: BLE001 — audit-log surface must not be blocked
        pass
    try:
        audit_emit(AuditEventType.INGEST_REFUSAL, session_id=session_id, reason=reason)
    except Exception:  # noqa: BLE001 — refusal flow must not be broken by emit
        pass
```

`audit_log.emit()` already swallows exceptions internally (Phase 1), but the second `try/except` here covers the test-monkeypatch case where `emit` is replaced with a raising stub, which bypasses the internal isolation. Without the outer wrap, the test `test_audit_log_write_failure_does_not_break_jsonl_write` would observe exception propagation; with it, the helper's contract holds for both real and stubbed emit functions.

**`handlers/preflight.py`** — same shape at the bypass-event site:

```python
# At the existing write_bypass_event call (mirror the helper pattern above):
try:
    preflight_telemetry.write_bypass_event(...)
except Exception:  # noqa: BLE001
    pass
try:
    from audit_log import AuditEventType, emit as audit_emit
    audit_emit(AuditEventType.PREFLIGHT_BYPASS, session_id=..., reason=...)
except Exception:  # noqa: BLE001
    pass
```

If the bypass site is currently a single inline call rather than a helper, factor it into a small helper (`_emit_bypass_telemetry`) mirroring the ingest helper's shape — keeps the dual-write discipline locatable in one place per handler. Helper extraction is a Phase 2 deliverable; the test `test_preflight_bypass_writes_both_jsonl_and_audit_log` exercises the helper, not the call site.

### Unit Tests

- `tests/test_audit_log_server_lifecycle.py` (**new**):
  - `test_serve_stdio_emits_server_start_at_entry` — monkeypatch `audit_log.emit`; call (or stub-call) `serve_stdio()`; assert first call is `(SERVER_START, ...)`.
  - `test_serve_stdio_emits_server_shutdown_in_finally` — force the body to raise; assert `SERVER_SHUTDOWN` still emits.
  - `test_config_load_emits_exactly_once_across_multiple_from_env_calls` — call `BicameralContext.from_env()` three times in one process; assert exactly one `CONFIG_LOAD` audit-log line.
  - `test_config_load_payload_includes_int_config_values_not_paths` — assert emitted record contains `ingest_max_bytes` (int) and excludes any field named in `_FORBIDDEN_FIELDS`; explicitly assert no `surreal_url` or `repo_path` field.

- `tests/test_audit_log_tool_invocation.py` (**new**):
  - `test_tool_invocation_emits_with_tool_name_and_duration_ms` — invoke `call_tool("bicameral.history", {...})`; capture audit record; assert `tool_name == "bicameral.history"`, `duration_ms` is int >= 0.
  - `test_tool_invocation_outcome_class_is_ok_on_normal_return` — assert `outcome_class == "ok"`.
  - `test_tool_invocation_outcome_class_is_refused_on_ingest_refused` — invoke `call_tool("bicameral.ingest", payload-with-canary)`; assert `outcome_class == "refused"`.
  - `test_tool_invocation_outcome_class_is_error_on_unexpected_exception` — monkeypatch handler to raise `RuntimeError`; assert `outcome_class == "error"`.
  - `test_tool_invocation_emit_does_not_include_arguments` — invoke with arbitrary arguments; assert emitted record has no `arguments` key (forbid-list locked).
  - `test_tool_invocation_session_id_extracted_from_arguments_when_present` — call with `arguments = {"session_id": "abc-123"}`; assert `session_id == "abc-123"` in emitted record.
  - `test_tool_invocation_session_id_omitted_when_arguments_lack_field` — call with `arguments = {}`; assert no `session_id` in emitted record.

- `tests/test_audit_log_dual_write.py` (**new**):
  - `test_ingest_refusal_writes_both_jsonl_and_audit_log` — trigger `_emit_ingest_refusal_telemetry`; capture both `preflight_telemetry.write_ingest_refusal_event` call AND audit-log emit; assert both invoked with matching `reason` + `session_id`.
  - `test_audit_log_write_failure_does_not_break_jsonl_write` — monkeypatch `audit_log.emit` to raise; call `_emit_ingest_refusal_telemetry`; assert JSONL write still succeeded and exception did not propagate.
  - `test_jsonl_write_failure_does_not_break_audit_log_write` — monkeypatch `preflight_telemetry.write_ingest_refusal_event` to raise; call `_emit_ingest_refusal_telemetry`; assert audit-log emit still completed.
  - `test_preflight_bypass_writes_both_jsonl_and_audit_log` — same shape, exercising the bypass site.

## Phase 3: documentation + research-brief closure

### Affected Files

- `docs/policies/audit-log.md` — **new** operator-readable policy doc: channel resolution table, level filter table, event_type catalog, forbid-list rationale, integration examples (logrotate, journalctl, file collectors)
- `docs/research-brief-compliance-audit-2026-05-06.md` — mark SOC2-06 (line ref TBD at implement time) AND OWASP-06 (line ref TBD) entries closed; cross-reference `audit_log.py` + `docs/policies/audit-log.md`
- `tests/test_compliance_policy_docs.py` — extend (existing file from #220/#225/#226 origin, most-recently extended in #249 LLM-06) with `test_audit_log_policy_doc_includes_channel_resolution_table` content-contract assertion
- `README.md` — extend "Compliance posture" section with one bullet pointing to `docs/policies/audit-log.md` (mirrors the pattern PR #248 established for install-trust-model.md)

### Unit Tests

- `tests/test_compliance_policy_docs.py::test_audit_log_policy_doc_includes_channel_resolution_table` — content-contract test asserting the policy doc includes the literal channel-resolution markdown table (matches the same anti-presence-only doctrine as PR #248's host-trust-model row test).

## CI Commands

- `pytest tests/test_audit_log_forbid_list.py tests/test_audit_log_format.py tests/test_audit_log_channel.py -v` — Phase 1 (module + forbid-list + JsonFormatter + channel resolution).
- `pytest tests/test_audit_log_server_lifecycle.py tests/test_audit_log_tool_invocation.py tests/test_audit_log_dual_write.py -v` — Phase 2 (server lifecycle + tool wrapper + dual-write integration).
- `pytest tests/test_compliance_policy_docs.py -v` — Phase 3 (policy-doc content contract).
- `pytest tests/test_audit_log_*.py tests/test_ingest_canary_*.py tests/test_ingest_sensitive_*.py tests/test_ingest_size_limit.py tests/test_ingest_rate_limit.py tests/test_server_ingest_refusal.py -v` — full ingest-gate-and-audit regression including PR #229 / #234 / #235 sister surfaces.
- `pytest tests/ -v` — full repo regression.
- `ruff check audit_log.py server.py context.py handlers/ingest.py handlers/preflight.py tests/test_audit_log_*.py` — lint clean on every touched + new Python file.
- `ruff format --check audit_log.py server.py context.py handlers/ingest.py handlers/preflight.py tests/test_audit_log_*.py` — format clean (ruff format CI parity per #199 lesson).

## Implementer notes

- The `_LEVEL_BY_EVENT` table is the canonical mapping; do not let `level_str` drift to a per-call argument. If a future event_type needs a non-standard level, add the enum entry + the table row in one commit.
- `_get_logger()` uses module-level singleton state for cheap per-emit dispatch; `_reset_for_tests()` is the test seam — production never calls it.
- The existing `_emit_ingest_refusal_telemetry` helper centralises the dual-write; do not scatter `audit_log.emit(INGEST_REFUSAL, ...)` calls at every refusal site — they go through the helper. Same pattern at preflight bypass.
- The `outcome_class` Pydantic-style three-value enum (`ok`/`refused`/`error`) is intentionally string-not-Enum at the emit site (the audit log is operator-facing flat JSON); the value is lowercased at write-time and not validated against a closed set in v1.
- If `release/skills_verify.py`'s deferred sigstore-python wiring lands during this work-cycle, a `verification_bypassed` audit-log emit at `_install_skills` is a one-line addition (mirrors the existing severity-3 ledger event pattern from #237 / #249); add it to Phase 2 if scope holds.
