"""M2 grounding-precision event log (#280 PR-3).

Owns the contract for the three `m2_grounding_*` events emitted by
`handlers/bind.py` (attempts) and `handlers/resolve_compliance.py`
(ratifications). Each call writes:

  1. A JSONL row to ``~/.bicameral/m2_grounding.jsonl`` — the local
     mirror that the dashboard panel reads. Always appended (subject
     to size-based rotation), regardless of telemetry consent.
  2. A PostHog event via ``telemetry.send_event`` — only when the
     consolidated ``BICAMERAL_TELEMETRY`` flag includes ``relay``.
     The relay enforces numeric-only `diagnostic` values; this module
     respects the same shape (no decision_id / file_path / symbol_name
     in the payload — those are user content).

Privacy invariants (per ``telemetry.py:14-37``):
  - decision_source is a controlled enum (``transcript`` / ``spec`` /
    ``chat`` / ``manual`` / ``document``); safe to relay.
  - diagnostic.* fields are bool/int only.
  - decision_id is written to the LOCAL mirror only (never relayed).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_FILE = Path.home() / ".bicameral" / "m2_grounding.jsonl"
_LOCK = threading.Lock()

# Local-mirror rotation: 10 MB cap, keep most recent 3 files. The dashboard
# panel reads the live file plus rotated siblings, so panels survive log roll.
_MAX_BYTES = 10 * 1024 * 1024
_MAX_ROTATIONS = 3

# Enum maps for relayable numeric encodings of caller-supplied strings.
_CONFIDENCE_TO_INT = {"low": 0, "medium": 1, "high": 2}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _maybe_rotate(path: Path) -> None:
    """Roll ``path`` → ``path.1`` when over _MAX_BYTES. Best-effort."""
    try:
        if not path.exists() or path.stat().st_size < _MAX_BYTES:
            return
        for i in range(_MAX_ROTATIONS, 0, -1):
            src = path.with_suffix(path.suffix + f".{i}")
            if src.exists():
                if i == _MAX_ROTATIONS:
                    src.unlink()
                else:
                    src.rename(path.with_suffix(path.suffix + f".{i + 1}"))
        path.rename(path.with_suffix(path.suffix + ".1"))
    except OSError as exc:
        logger.debug("[m2_grounding] rotation failed (non-fatal): %s", exc)


def _append_jsonl(row: dict) -> None:
    """Write a single JSONL row to the local mirror. Best-effort, never raises."""
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            _maybe_rotate(_LOG_FILE)
            with _LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.debug("[m2_grounding] local-mirror append failed (non-fatal): %s", exc)


def _send_relay(event_type: str, decision_source: str, diagnostic: dict) -> None:
    """Forward to PostHog via ``telemetry.send_event``. Lazy-imports both
    ``telemetry`` and ``server`` to avoid module-init circular dependencies
    (``handlers/*`` is loaded *by* ``server.py`` at server boot).
    """
    try:
        from server import SERVER_VERSION  # local import — see docstring
        from telemetry import send_event

        send_event(
            SERVER_VERSION,
            event_type=event_type,
            decision_source=decision_source,
            diagnostic=diagnostic,
        )
    except Exception as exc:
        logger.debug("[m2_grounding] relay send failed (non-fatal): %s", exc)


# ── Public API ──────────────────────────────────────────────────────────────


def record_attempt(
    *,
    decision_id: str,
    decision_source: str | None,
    success: bool,
    handler_rejected: bool,
) -> None:
    """Record a `bicameral_bind` attempt.

    Outcomes are mutually exclusive across (success, handler_rejected):
      - success=True, handler_rejected=False  → bound a region successfully
      - success=False, handler_rejected=True  → #280 PR-1 reject path fired
      - success=False, handler_rejected=False → other error (ledger / IO)

    `decision_id` lands in the local mirror only — it's an opaque ledger
    UUID but treated as user-linked for relay safety. The PostHog event
    sees only the controlled enum `decision_source`.
    """
    source = decision_source or "unknown"
    diagnostic = {
        "success": success,
        "handler_rejected": handler_rejected,
    }

    _append_jsonl(
        {
            "ts": _now_iso(),
            "event_type": "m2_grounding_attempt",
            "decision_id": decision_id,
            "decision_source": source,
            **diagnostic,
        }
    )
    _send_relay("m2_grounding_attempt", source, diagnostic)


def record_ratification(
    *,
    decision_id: str,
    decision_source: str | None,
    verdict: str,
    confidence: str | None,
) -> None:
    """Record a caller-LLM compliance verdict on a previously-bound region.

    `verdict` is one of: ``compliant`` / ``drifted`` / ``not_relevant``
    (per the v0.5.0 three-way enum from ``handle_resolve_compliance``).
    `compliant` → m2_grounding_ratified_correct
    Anything else → m2_grounding_ratified_incorrect (drifted = code
    changed; not_relevant = retrieval mistake — both signal the
    original bind was wrong from the caller's perspective).
    """
    source = decision_source or "unknown"
    is_correct = verdict == "compliant"
    event_type = (
        "m2_grounding_ratified_correct" if is_correct else "m2_grounding_ratified_incorrect"
    )
    confidence_int = _CONFIDENCE_TO_INT.get(confidence or "", 1)

    diagnostic = {
        "confidence": confidence_int,
    }

    _append_jsonl(
        {
            "ts": _now_iso(),
            "event_type": event_type,
            "decision_id": decision_id,
            "decision_source": source,
            "verdict": verdict,
            **diagnostic,
        }
    )
    _send_relay(event_type, source, diagnostic)


def reset_for_tests() -> None:
    """Truncate the local mirror — test-only hook."""
    if _LOG_FILE.exists():
        try:
            _LOG_FILE.unlink()
        except OSError:
            pass
    for i in range(1, _MAX_ROTATIONS + 1):
        sibling = _LOG_FILE.with_suffix(_LOG_FILE.suffix + f".{i}")
        if sibling.exists():
            try:
                sibling.unlink()
            except OSError:
                pass


# Test-environment hook: redirect log file when BICAMERAL_M2_LOG_PATH is set.
# Mirrors the pattern used by preflight_telemetry's salt/event paths so
# pytest tmp_path can isolate per-test mirrors without touching $HOME.
def _override_log_path_for_tests() -> None:
    override = os.getenv("BICAMERAL_M2_LOG_PATH")
    if override:
        global _LOG_FILE
        _LOG_FILE = Path(override)


_override_log_path_for_tests()
