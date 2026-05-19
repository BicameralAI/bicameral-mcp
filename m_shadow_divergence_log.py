"""Shadow-mode divergence event log (#399 Stage C).

Owns the contract for the ``m_shadow_divergence`` event emitted by
``code_locator/indexing/symbol_extractor.py``'s ``_extract_definitions``
when a language is running in ``shadow-substrate`` or ``shadow-walker``
mode. Each call writes:

  1. A JSONL row to ``~/.bicameral/m_shadow_divergence.jsonl`` — the
     local mirror that the dashboard panel reads (and that the
     ``M_shadow_parity`` CI gate consumes via ``tests/eval_shadow_parity.py``).
     Always appended (subject to size-based rotation), regardless of
     telemetry consent.
  2. A PostHog event via ``telemetry.send_event`` — only when the
     consolidated ``BICAMERAL_TELEMETRY`` flag includes ``relay``.
     The relay payload carries **numeric aggregates only** — counts
     and the ``divergence_kind`` enum. Symbol-name lists never reach
     the relay; they exist only in the local mirror.

Privacy invariants (mirrors ``m2_grounding_log.py:14-37``):
  - ``file_hash`` (sha256 of ``rel_path``) replaces the raw path in
    both local mirror and relay — the relay never sees user file paths.
  - ``walker_only`` / ``substrate_only`` symbol-name lists live in the
    local mirror only. The relay carries only ``walker_count``,
    ``substrate_count``, ``divergence_kind`` — no user code identifiers
    cross the network boundary.
  - The ``telemetry.send_event`` relay's defensive `isinstance` filter
    (``telemetry.py:140``) strips any non-numeric ``diagnostic`` value
    that does slip through, so the symbol-name fields here are safe
    even if a future refactor accidentally routes them through diagnostic.

Sampling budget (per #399 plan):
  - Every disagreement (``divergence_kind != "equal"``) → log.
  - Agreements → 1-in-50 via deterministic file-hash modulo. Same file
    always samples or doesn't — useful for reproducibility when an
    investigator wants to know "was this file ever shadow-logged?"
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_FILE = Path.home() / ".bicameral" / "m_shadow_divergence.jsonl"
_LOCK = threading.Lock()

# Local-mirror rotation: 10 MB cap, keep most recent 3 files. Matches
# m2_grounding_log.py — the dashboard panel reads the live file plus
# rotated siblings, so panels survive log roll.
_MAX_BYTES = 10 * 1024 * 1024
_MAX_ROTATIONS = 3

# 1-in-N agreement sampling — every disagreement always logs.
_AGREEMENT_SAMPLE_RATE = 50

# divergence_kind → relay-safe numeric encoding for `diagnostic`.
# Strings are not relayable; numeric makes PostHog aggregation trivial.
_DIVERGENCE_KIND_TO_INT = {
    "equal": 0,
    "substrate-superset": 1,  # substrate has extras — expected, allowed
    "substrate-subset": 2,  # walker has extras — forbidden, signals substrate bug
    "symmetric": 3,  # neither is a subset; both have unique entries
}

# Modes that warrant logging (the two shadow modes — single-path
# modes never call into here).
_LOGGABLE_MODES = frozenset({"shadow-substrate", "shadow-walker"})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _hash_path(rel_path: str) -> str:
    """SHA-256 of the relative path. Privacy-preserving identity that
    is stable across runs (so dashboards can group by file) but reveals
    no path component."""
    return hashlib.sha256(rel_path.encode("utf-8")).hexdigest()


def _should_sample_agreement(file_hash: str) -> bool:
    """Deterministic 1-in-50 sampling: same file_hash always returns
    the same boolean. Uses the first 8 hex chars as a uniform-ish
    32-bit integer, mod the sample rate.

    Deterministic (not probabilistic) so an investigator chasing a
    divergence can ask "would this file have been sampled?" and get a
    repeatable answer without re-running the indexer.
    """
    return int(file_hash[:8], 16) % _AGREEMENT_SAMPLE_RATE == 0


def classify_divergence(walker_set: set, substrate_set: set) -> str:
    """Compute the ``divergence_kind`` label from two (name, type) sets.

    Returns one of:
      - ``"equal"`` — sets are identical
      - ``"substrate-superset"`` — substrate has extras; walker is a
        strict subset (expected, allowed direction)
      - ``"substrate-subset"`` — walker has extras; substrate is a
        strict subset (forbidden — signals a substrate gap)
      - ``"symmetric"`` — neither side is a subset; both have unique
        entries (rare; usually means a (name, type) pair has a
        different type label between walker and substrate)

    Symmetric difference matters as much as missing direction — if a
    walker emits ``("foo", "function")`` and substrate emits
    ``("foo", "method")``, both sides have unique entries even though
    the symbol is "the same". Investigate as a vocabulary mismatch.
    """
    if walker_set == substrate_set:
        return "equal"
    walker_only = walker_set - substrate_set
    substrate_only = substrate_set - walker_set
    if not walker_only and substrate_only:
        return "substrate-superset"
    if walker_only and not substrate_only:
        return "substrate-subset"
    return "symmetric"


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
        logger.debug("[m_shadow_divergence] rotation failed (non-fatal): %s", exc)


def _append_jsonl(row: dict) -> None:
    """Write a single JSONL row to the local mirror. Best-effort, never raises."""
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            _maybe_rotate(_LOG_FILE)
            with _LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.debug("[m_shadow_divergence] local-mirror append failed (non-fatal): %s", exc)


def _send_relay(diagnostic: dict, language_id: str, mode: str, divergence_kind: str) -> None:
    """Forward to PostHog via ``telemetry.send_event``. The relay payload
    carries only:

      - ``language_id`` (top-level, controlled enum)
      - ``mode`` (top-level, controlled enum)
      - ``divergence_kind`` (top-level, controlled enum)
      - ``diagnostic`` — int-only counts and the divergence_kind int

    No symbol names. No file paths. ``send_event``'s own defensive
    filter strips any non-numeric ``diagnostic`` value as belt-and-
    suspenders against future refactors.
    """
    try:
        from server import SERVER_VERSION  # local import — avoid circular
        from telemetry import send_event

        send_event(
            SERVER_VERSION,
            event_type="m_shadow_divergence",
            language_id=language_id,
            mode=mode,
            divergence_kind=divergence_kind,
            diagnostic=diagnostic,
        )
    except Exception as exc:
        logger.debug("[m_shadow_divergence] relay send failed (non-fatal): %s", exc)


# ── Public API ──────────────────────────────────────────────────────────────


def record_divergence(
    *,
    language_id: str,
    mode: str,
    rel_path: str,
    walker_set: set,
    substrate_set: set,
) -> None:
    """Record a shadow-mode comparison between walker and substrate output.

    ``walker_set`` and ``substrate_set`` are sets of ``(name, type)`` tuples
    pre-filtered to the walker vocabulary. The caller is responsible for
    that filtering — same as the parity gate in
    ``tests/test_tags_extractor_parity.py``.

    No-op when ``mode`` is not a shadow mode (defensive: callers shouldn't
    invoke for ``walker-only`` / ``substrate-only`` but a guard here makes
    the contract explicit).

    No-op for sampled-out agreements. Disagreements always log.
    """
    if mode not in _LOGGABLE_MODES:
        return

    divergence_kind = classify_divergence(walker_set, substrate_set)
    file_hash = _hash_path(rel_path)

    if divergence_kind == "equal" and not _should_sample_agreement(file_hash):
        return

    walker_only = sorted(walker_set - substrate_set)
    substrate_only = sorted(substrate_set - walker_set)

    # Local mirror: full event including symbol-name lists. file_hash
    # (not rel_path) is used for the on-disk record too, matching the
    # #399 issue body's privacy-preserving event shape — investigators
    # who need to map a hash back to a path keep that mapping locally.
    _append_jsonl(
        {
            "ts": _now_iso(),
            "event_type": "m_shadow_divergence",
            "language_id": language_id,
            "mode": mode,
            "file_hash": file_hash,
            "walker_count": len(walker_set),
            "substrate_count": len(substrate_set),
            "walker_only": [f"{n}:{t}" for n, t in walker_only],
            "substrate_only": [f"{n}:{t}" for n, t in substrate_only],
            "divergence_kind": divergence_kind,
        }
    )

    # Relay: numeric aggregates only. The defensive filter in
    # telemetry.send_event silently drops any non-int diagnostic value,
    # so even if someone refactors this dict to include strings, the
    # network boundary stays clean.
    diagnostic = {
        "walker_count": len(walker_set),
        "substrate_count": len(substrate_set),
        "walker_only_count": len(walker_only),
        "substrate_only_count": len(substrate_only),
        "divergence_kind_int": _DIVERGENCE_KIND_TO_INT.get(divergence_kind, -1),
    }
    _send_relay(diagnostic, language_id, mode, divergence_kind)


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


# Test-environment hook: redirect log file when BICAMERAL_M_SHADOW_LOG_PATH
# is set. Mirrors m2_grounding_log.py's _override_log_path_for_tests so
# pytest tmp_path can isolate per-test mirrors without touching $HOME.
def _override_log_path_for_tests() -> None:
    override = os.getenv("BICAMERAL_M_SHADOW_LOG_PATH")
    if override:
        global _LOG_FILE
        _LOG_FILE = Path(override)


_override_log_path_for_tests()
