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

Level resolution (filters ``event_type`` to suppress noise)::

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


class AuditEventType(enum.StrEnum):
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
    """Remove keys in ``_FORBIDDEN_FIELDS`` from ``fields``.

    Returns ``(cleaned, stripped)`` where ``stripped`` is the list of
    removed keys (so the emitted record can surface that redaction
    occurred). Never mutates the input.
    """
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
        payload: dict[str, Any] = getattr(record, "audit_payload", {})
        return json.dumps(payload, default=str, sort_keys=True)


def _resolve_channel() -> tuple[str, str]:
    """Return ``(kind, target)`` where ``kind`` is one of
    ``"stderr"``, ``"file"``, or ``"disabled"``."""
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
    """Return a configured handler, or ``None`` if disabled.

    On unwriteable file path, fall back to stderr and emit one warning
    record describing the fallback.
    """
    kind, target = _resolve_channel()
    if kind == "disabled":
        return None
    if kind == "stderr":
        h: logging.Handler = logging.StreamHandler(sys.stderr)
    else:
        try:
            h = logging.FileHandler(target, mode="a", encoding="utf-8", delay=False)
        except OSError as exc:
            marker = {
                "ts": time.time(),
                "level": "warn",
                "event_type": "error",
                "message": "audit_log file path unwriteable; falling back to stderr",
                "path": target,
                "reason": str(exc),
            }
            sys.stderr.write(json.dumps(marker, sort_keys=True) + "\n")
            h = logging.StreamHandler(sys.stderr)
    h.setFormatter(JsonFormatter())
    return h


_logger: logging.Logger | None = None
_min_level_rank: int = _LEVEL_RANK["info"]


def _get_logger() -> logging.Logger | None:
    """Idempotent logger factory. Returns ``None`` when channel is
    disabled."""
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

    ``event_type`` is validated against ``AuditEventType``; an unknown
    string is coerced to ``AuditEventType.ERROR`` with an
    ``original_event_type`` field so drift is observable but never
    silently dropped.
    """
    try:
        if isinstance(event_type, str) and not isinstance(event_type, AuditEventType):
            try:
                event_type_enum = AuditEventType(event_type)
            except ValueError:
                fields["original_event_type"] = event_type
                event_type_enum = AuditEventType.ERROR
        else:
            event_type_enum = event_type
        logger = _get_logger()
        if logger is None:
            return
        level_str = _LEVEL_BY_EVENT[event_type_enum]
        if _LEVEL_RANK[level_str] < _min_level_rank:
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
        record = logger.makeRecord(_LOGGER_NAME, logging.INFO, "audit_log", 0, "", (), None)
        record.audit_payload = payload  # type: ignore[attr-defined]
        logger.handle(record)
    except Exception:  # noqa: BLE001 — audit_log MUST NOT break callers
        try:
            marker = {
                "ts": time.time(),
                "level": "error",
                "event_type": "error",
                "message": "audit_log emit failed",
            }
            sys.stderr.write(json.dumps(marker, sort_keys=True) + "\n")
        except Exception:  # noqa: BLE001 — last-ditch silent drop
            pass


def _reset_for_tests() -> None:
    """Clear the cached logger so test fixtures can re-resolve env."""
    global _logger, _min_level_rank
    _logger = None
    _min_level_rank = _LEVEL_RANK["info"]
