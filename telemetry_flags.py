"""Centralized telemetry flag parser (issue #192).

Single source of truth for telemetry enable/disable state across the project.
Parses ``BICAMERAL_TELEMETRY`` (with backwards-compat overlay for the legacy
``BICAMERAL_PREFLIGHT_TELEMETRY*`` vars) into a frozen ``TelemetryFlags``.

Forms accepted on ``BICAMERAL_TELEMETRY``:

- **unset** (default) → ``relay=True, preflight=False, raw=False``.
  Preserves the pre-#192 default — relay path on, preflight events opt-in.
- **``0`` / ``off`` / ``false`` / ``no``** → all sources off.
- **``1`` / ``on`` / ``true`` / ``yes``** → relay only (legacy bool form
  preserves the pre-#192 default; does NOT auto-enable preflight).
- **csv list** (e.g. ``relay,preflight`` or ``preflight,raw``) → explicit
  per-source enable. What's listed is on; what's not is off.

Recognized csv source names: ``relay``, ``preflight``, ``raw``. Unknown
sources emit a stderr warning and are ignored.

Semantic invariants:

- ``raw`` always implies ``preflight`` (raw capture is a mode of the
  preflight events writer).
- Legacy vars ``BICAMERAL_PREFLIGHT_TELEMETRY=1`` /
  ``BICAMERAL_PREFLIGHT_TELEMETRY_RAW=1`` continue to work as **additive**
  overlays — they can force a source ON, never OFF. First read of either
  legacy var emits a one-line stderr deprecation warning per process.
  Removed in v1.x.

Cache: ``get_flags()`` is ``lru_cache``-d once per process. Tests that
monkeypatch env vars must call ``_reset_for_tests()`` to flush.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache

_OFF = frozenset({"0", "off", "false", "no", ""})
_BOOL_ON = frozenset({"1", "on", "true", "yes"})
_RECOGNIZED_SOURCES = frozenset({"relay", "preflight", "raw"})


@dataclass(frozen=True)
class TelemetryFlags:
    """Parsed telemetry source flags. Immutable; constructed by
    ``get_flags()``."""

    relay: bool
    preflight: bool
    raw: bool


_warnings_emitted: set[str] = set()


def _warn_once(key: str, msg: str) -> None:
    """Emit a single stderr deprecation/diagnostic warning per ``key`` per
    process. ``key`` is the dedup key; ``msg`` is the user-facing text."""
    if key in _warnings_emitted:
        return
    _warnings_emitted.add(key)
    print(f"[bicameral] {msg}", file=sys.stderr)


def _parse_consolidated() -> TelemetryFlags:
    """Parse ``BICAMERAL_TELEMETRY`` in unset / 0 / 1 / csv form."""
    raw_val = os.getenv("BICAMERAL_TELEMETRY", "1").strip().lower()

    if raw_val in _OFF:
        return TelemetryFlags(relay=False, preflight=False, raw=False)

    if raw_val in _BOOL_ON:
        # Legacy bool ON form — preserves pre-#192 relay-only default.
        return TelemetryFlags(relay=True, preflight=False, raw=False)

    # CSV form — explicit per-source enable.
    sources = {s.strip() for s in raw_val.split(",") if s.strip()}
    recognized = sources & _RECOGNIZED_SOURCES
    unrecognized = sources - _RECOGNIZED_SOURCES

    if not recognized:
        # No recognized source names at all — treat as **legacy truthy**
        # form. Pre-#192 behavior was that any non-_OFF value of
        # BICAMERAL_TELEMETRY enabled relay (e.g. ``enabled``, ``t``, custom
        # marker strings). Preserve that for upgraders by mapping to
        # relay-only, which matches the documented ``1`` form. Emit a
        # one-line stderr warning pointing the operator at the canonical
        # csv shape.
        _warn_once(
            f"legacy_truthy:{raw_val!r}",
            f"BICAMERAL_TELEMETRY={raw_val!r} is not a recognized source list. "
            f"Treating as legacy truthy form (relay only — pre-#192 behavior). "
            f"Recognized csv sources: {sorted(_RECOGNIZED_SOURCES)}. "
            f"Use BICAMERAL_TELEMETRY=1 for the canonical form.",
        )
        return TelemetryFlags(relay=True, preflight=False, raw=False)

    if unrecognized:
        _warn_once(
            f"unrecognized:{sorted(unrecognized)}",
            f"BICAMERAL_TELEMETRY contains unrecognized sources: {sorted(unrecognized)}. "
            f"Recognized: {sorted(_RECOGNIZED_SOURCES)}. Unknown sources ignored.",
        )

    raw = "raw" in sources
    # raw implies preflight (raw is a mode of preflight events).
    preflight = ("preflight" in sources) or raw
    relay = "relay" in sources

    return TelemetryFlags(relay=relay, preflight=preflight, raw=raw)


def _parse_legacy_overlay(flags: TelemetryFlags) -> TelemetryFlags:
    """Apply legacy var overlays. Each legacy var, if set truthy, forces its
    corresponding source ON in the consolidated flags AND emits a one-line
    deprecation warning. Overlay is **additive** — never forces a source OFF."""
    pf_legacy = os.getenv("BICAMERAL_PREFLIGHT_TELEMETRY", "").strip().lower()
    raw_legacy = os.getenv("BICAMERAL_PREFLIGHT_TELEMETRY_RAW", "").strip().lower()

    preflight = flags.preflight
    raw = flags.raw

    if pf_legacy and pf_legacy not in _OFF:
        _warn_once(
            "legacy:BICAMERAL_PREFLIGHT_TELEMETRY",
            "BICAMERAL_PREFLIGHT_TELEMETRY is deprecated. "
            "Use BICAMERAL_TELEMETRY=preflight (or include 'preflight' in your csv list). "
            "Removed in v1.x.",
        )
        preflight = True

    if raw_legacy and raw_legacy not in _OFF:
        _warn_once(
            "legacy:BICAMERAL_PREFLIGHT_TELEMETRY_RAW",
            "BICAMERAL_PREFLIGHT_TELEMETRY_RAW is deprecated. "
            "Use BICAMERAL_TELEMETRY=preflight,raw. "
            "Removed in v1.x.",
        )
        preflight = True  # raw implies preflight
        raw = True

    return TelemetryFlags(relay=flags.relay, preflight=preflight, raw=raw)


@lru_cache(maxsize=1)
def _cached_flags() -> TelemetryFlags:
    return _parse_legacy_overlay(_parse_consolidated())


def get_flags() -> TelemetryFlags:
    """Return the parsed telemetry flags. Cached per-process — callers must
    not mutate env vars and expect a re-parse without calling
    :func:`_reset_for_tests`."""
    return _cached_flags()


def _reset_for_tests() -> None:
    """Test-only: flush the lru_cache and clear the once-per-process warning
    set so monkeypatched env vars take effect on the next ``get_flags()``."""
    _cached_flags.cache_clear()
    _warnings_emitted.clear()
