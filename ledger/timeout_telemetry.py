"""In-memory ring buffer + counters for ledger-query timeout events (#224).

Two responsibilities:

1. **Ring buffer** — last 1000 timeout records, used by the
   ``bicameral_preflight`` response (``recent_timeout_count``) so a
   Claude Code ``SessionStart`` / ``PreToolUse`` hook can fetch
   gate-time context for the model without round-tripping SurrealDB.

2. **Counter snapshot** — per-timeout-class count of events fired in
   the last 1 hour. Same backing store; surfaced via
   ``recent_timeout_counts(window_seconds=3600)``.

Scope is **process-local** and **in-memory only** by design:

- Same granularity as the session-start hook (a fresh process = a
  fresh count, which is what a session-start surfacing wants).
- Zero SurrealDB roundtrip for the dashboard / preflight read path.
- Trivial to reason about for tests (``clear_for_testing()``).

Phase C wires the audit-log emit alongside this buffer.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

_BUFFER_CAP = 1000


@dataclass(frozen=True)
class TimeoutEvent:
    sql_prefix: str
    timeout_class: str
    elapsed_seconds: float
    budget_seconds: float
    recorded_at: float  # time.time() unix seconds


_buffer: deque[TimeoutEvent] = deque(maxlen=_BUFFER_CAP)
_lock = threading.Lock()


def record_timeout(
    *,
    sql_prefix: str,
    timeout_class: str,
    elapsed_seconds: float,
    budget_seconds: float,
) -> None:
    """Append a timeout event to the ring buffer. Thread-safe; bounded
    at ``_BUFFER_CAP`` (older entries automatically dropped by deque)."""
    event = TimeoutEvent(
        sql_prefix=sql_prefix[:200],
        timeout_class=timeout_class,
        elapsed_seconds=elapsed_seconds,
        budget_seconds=budget_seconds,
        recorded_at=time.time(),
    )
    with _lock:
        _buffer.append(event)


def recent_timeout_counts(window_seconds: float = 3600.0) -> dict[str, int]:
    """Return per-class counts of timeout events recorded in the last
    ``window_seconds`` (default 1 hour). Classes always present in the
    result so hook scripts can rely on the shape: at minimum returns
    ``{"read": 0, "drift": 0}``."""
    cutoff = time.time() - window_seconds
    counts: dict[str, int] = {"read": 0, "drift": 0}
    with _lock:
        for event in _buffer:
            if event.recorded_at < cutoff:
                continue
            counts[event.timeout_class] = counts.get(event.timeout_class, 0) + 1
    return counts


def buffer_size() -> int:
    with _lock:
        return len(_buffer)


def clear_for_testing() -> None:
    """Reset the buffer. Test-only; never call from production code."""
    with _lock:
        _buffer.clear()
