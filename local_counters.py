"""Local-only tool-usage counters (issue #39).

Append-only JSONL sink for the user's own machine. Independent of the
network telemetry relay (``telemetry.py``); counters are written for
every tool invocation regardless of consent state, so users can see
their own usage even with telemetry opted out.

Privacy invariant:
  - Only ``tool_name`` (string) + ``delta`` (int) + ``timestamp`` are
    recorded. No payload, no path, no diagnostic dict.
  - File is mode 0o600 on POSIX (user-only).
  - No network egress.

Kill switch: ``BICAMERAL_LOCAL_COUNTERS=0`` disables all writes.

API:
  ``increment(tool_name)``     — record a call
  ``read_counters()``          — aggregate counts by tool name
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_COUNTERS_FILE = Path.home() / ".bicameral" / "counters.jsonl"
_OFF_VALUES = frozenset({"0", "false", "no", "off"})
_LOCK = threading.Lock()


def _enabled() -> bool:
    val = os.getenv("BICAMERAL_LOCAL_COUNTERS", "1").strip().lower()
    return val not in _OFF_VALUES


def _open_for_append_secure(path: Path) -> "os.PathLike":
    """Open the counters file with 0o600 mode on POSIX (user-only)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(str(path), flags, 0o600)
    return os.fdopen(fd, "ab")


def increment(tool_name: str, *, delta: int = 1) -> None:
    """Append one counter event. Never raises. Thread-safe."""
    if not _enabled():
        return
    try:
        _COUNTERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "tool": tool_name,
            "delta": int(delta),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with _LOCK:
            with _open_for_append_secure(_COUNTERS_FILE) as f:
                f.write(line.encode("utf-8"))
    except Exception as exc:
        logger.debug("[counters] increment failed (non-fatal): %s", exc)


def read_counters() -> dict[str, int]:
    """Aggregate the JSONL into ``{tool_name: total_delta}``."""
    if not _COUNTERS_FILE.exists():
        return {}
    counts: Counter = Counter()
    try:
        with open(_COUNTERS_FILE, "rb") as f:
            for raw in f:
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                tool = rec.get("tool")
                delta = rec.get("delta", 1)
                if isinstance(tool, str) and isinstance(delta, int):
                    counts[tool] += delta
    except Exception as exc:
        logger.debug("[counters] read failed: %s", exc)
    return dict(counts)
