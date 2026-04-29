"""Preflight telemetry capture loop (#65, pieces 1-4).

Local-only, opt-in capture of bicameral.preflight events and downstream tool
engagement, scoped to per-install attribution for failure-mode triage.

This module is **separate from `telemetry.py`** — that one relays anonymized
counters to PostHog via a Cloudflare worker. This module writes JSONL files
under ``~/.bicameral/`` and never leaves the machine.

Privacy model
=============

Default mode (``BICAMERAL_PREFLIGHT_TELEMETRY=1``): hashed-only.

  - ``topic_hash``       : 16-hex-char SHA-256 of (per-install salt || topic).
  - ``file_paths_hash``  : 16-hex-char SHA-256 of the salt-prefixed, sorted,
                            null-byte-delimited path set. Order-independent.
  - ``surfaced_ids``     : **WRITTEN RAW** (audit S1 invariant). These are
                            opaque ledger ``decision_id`` strings — already
                            non-PII inside the ledger, useful for triage joins
                            against ``failure_review.jsonl``. We document this
                            here rather than hashing them, because hashing
                            would defeat the only useful triage join.
  - ``fired``, ``reason``, ``attribution`` : opaque enums / booleans.

Raw mode (``BICAMERAL_PREFLIGHT_TELEMETRY_RAW=1``): adds plaintext ``topic``
and ``file_paths`` alongside the hashed fields. User explicitly opts in.

Salt (``~/.bicameral/salt``) is per-install, generated once with ``os.urandom(32)``,
stored mode 0o600 on POSIX. Race-safe init: ``os.O_EXCL`` create with a
``FileExistsError`` fallback to read the winning writer's bytes.

Retention: ``_maybe_rotate`` rolls files at 50 MB or 30-day mtime, keeping the
most recent 5 rotations.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

_SALT_FILE = Path.home() / ".bicameral" / "salt"
_EVENTS_FILE = Path.home() / ".bicameral" / "preflight_events.jsonl"
_ENGAGEMENTS_FILE = Path.home() / ".bicameral" / "engagements.jsonl"
_LOCK = threading.Lock()
_OFF = frozenset({"0", "false", "no", "off", ""})

_MAX_BYTES = 50 * 10**6  # 50 MB
_MAX_AGE_DAYS = 30
_KEEP_ROTATIONS = 5


# ── Env gates ────────────────────────────────────────────────────────


def telemetry_enabled() -> bool:
    """True when ``BICAMERAL_PREFLIGHT_TELEMETRY`` is set to a truthy value.

    Default off — caller-side opt-in only.
    """
    return os.getenv("BICAMERAL_PREFLIGHT_TELEMETRY", "0").strip().lower() not in _OFF


def raw_capture_enabled() -> bool:
    """True when ``BICAMERAL_PREFLIGHT_TELEMETRY_RAW`` is set to a truthy value.

    Default off — even with telemetry enabled, raw plaintext capture is a
    separate opt-in.
    """
    return os.getenv("BICAMERAL_PREFLIGHT_TELEMETRY_RAW", "0").strip().lower() not in _OFF


# ── Salt + hash helpers ──────────────────────────────────────────────


def _get_or_create_salt() -> bytes:
    """Per-install salt at ``~/.bicameral/salt``. Mode 0o600 on POSIX.

    Race-safe: two processes starting simultaneously on first install both
    enter the create branch; ``O_EXCL`` ensures exactly one wins. The loser
    catches ``FileExistsError`` and reads back the winner's salt bytes.

    Audit MF1: must wrap the ``os.open`` call so a race-loser doesn't crash.
    """
    if _SALT_FILE.exists():
        return _SALT_FILE.read_bytes()
    _SALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(str(_SALT_FILE), flags, 0o600)
    except FileExistsError:
        # Race-loser path — the winner already wrote the salt; read it back.
        return _SALT_FILE.read_bytes()
    with os.fdopen(fd, "wb") as f:
        f.write(salt)
    return salt


def hash_topic(topic: str) -> str:
    """Salted SHA-256 of the topic, truncated to 16 hex chars (~64 bits)."""
    return hashlib.sha256(_get_or_create_salt() + (topic or "").encode("utf-8")).hexdigest()[:16]


def hash_file_paths(paths: list[str]) -> str:
    """Order-independent salted hash of a path set.

    Empty/whitespace-only entries are skipped; remaining paths are sorted
    and concatenated with a null-byte delimiter so adjacent paths never
    collide ("ab" + "cd" vs "a" + "bcd"). Truncated to 16 hex chars.
    """
    sorted_paths = sorted((p or "").strip() for p in (paths or []) if (p or "").strip())
    h = hashlib.sha256(_get_or_create_salt())
    for p in sorted_paths:
        h.update(b"\x00")
        h.update(p.encode("utf-8"))
    return h.hexdigest()[:16]


def new_preflight_id() -> str:
    """Fresh UUIDv4 string. Stable across the preflight → downstream-tool chain."""
    return str(uuid4())


# ── Retention rotation ───────────────────────────────────────────────


def _maybe_rotate(path: Path) -> None:
    """Rotate ``path`` to ``path.1`` if it exceeds size/age thresholds.

    Shifts ``.1 → .2``, ``.2 → .3``, etc., dropping anything past
    ``_KEEP_ROTATIONS``. Uses ``os.replace`` for atomic-on-Windows-and-POSIX
    semantics. No-op when the file doesn't exist.
    """
    if not path.exists():
        return
    try:
        st = path.stat()
        too_big = st.st_size > _MAX_BYTES
        too_old = (datetime.now(UTC).timestamp() - st.st_mtime) > _MAX_AGE_DAYS * 86400
    except OSError:
        return
    if not (too_big or too_old):
        return
    # Shift .N -> .(N+1), drop the oldest beyond _KEEP_ROTATIONS.
    oldest = path.with_suffix(path.suffix + f".{_KEEP_ROTATIONS}")
    if oldest.exists():
        try:
            oldest.unlink()
        except OSError:
            pass
    for i in range(_KEEP_ROTATIONS - 1, 0, -1):
        src = path.with_suffix(path.suffix + f".{i}")
        dst = path.with_suffix(path.suffix + f".{i + 1}")
        if src.exists():
            try:
                os.replace(src, dst)
            except OSError:
                pass
    try:
        os.replace(path, path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


# ── JSONL append ─────────────────────────────────────────────────────


def _append(path: Path, record: dict) -> None:
    """Append a single record as a JSONL line, mode 0o600.

    Rotates first if size/age thresholds are exceeded. Serializes appends
    via a process-local lock; cross-process serialization is bounded by
    rotation rarity (acceptable for telemetry).
    """
    _maybe_rotate(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":")) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    with _LOCK:
        fd = os.open(str(path), flags, 0o600)
        with os.fdopen(fd, "ab") as f:
            f.write(line.encode("utf-8"))


def _read_last_n(path: Path, n: int = 200) -> list[dict]:
    """Read at most the last ``n`` JSONL records from ``path``.

    Naive implementation: reads the whole file. The rotation in
    ``_maybe_rotate`` bounds file size to 50 MB, so this is acceptable for
    triage workloads but documented as a limitation for high-volume reads.
    """
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out[-n:]


# ── Writers ──────────────────────────────────────────────────────────


def write_preflight_event(
    *,
    session_id: str,
    preflight_id: str,
    topic: str,
    file_paths: list[str] | None,
    fired: bool,
    surfaced_ids: list[str],
    reason: str,
) -> None:
    """Append one row to ``~/.bicameral/preflight_events.jsonl``.

    No-op when telemetry is disabled. ``surfaced_ids`` is written raw per
    the privacy model documented at module level — these are opaque ledger
    decision_ids, useful for triage joins.
    """
    if not telemetry_enabled():
        return
    record: dict = {
        "ts": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "preflight_id": preflight_id,
        "topic_hash": hash_topic(topic),
        "file_paths_hash": hash_file_paths(file_paths or []),
        "fired": fired,
        "surfaced_ids": list(surfaced_ids or []),
        "reason": reason,
    }
    if raw_capture_enabled():
        record["topic"] = topic
        record["file_paths"] = list(file_paths or [])
    _append(_EVENTS_FILE, record)


def _resolve_fallback_attribution(file_paths: list[str]) -> str | None:
    """Subset-match: return the preflight_id of the most recent event whose
    ``file_paths_hash`` matches the given paths.

    Note: pure subset semantics requires raw paths. In hashed mode we can
    only check exact set match (since hashing is order-independent but not
    subset-preserving). Documented in the module docstring.
    """
    target_hash = hash_file_paths(file_paths or [])
    if not _EVENTS_FILE.exists():
        return None
    recent = _read_last_n(_EVENTS_FILE, n=200)
    for ev in reversed(recent):
        if ev.get("file_paths_hash") == target_hash:
            pid = ev.get("preflight_id")
            if isinstance(pid, str):
                return pid
            return None
    return None


def write_engagement(
    *,
    session_id: str,
    tool: str,
    decision_id: str | None,
    preflight_id: str | None,
    file_paths: list[str] | None,
) -> None:
    """Append one engagement row to ``~/.bicameral/engagements.jsonl``.

    No-op when telemetry is disabled. When called without an explicit
    ``preflight_id`` but with ``file_paths``, attempts subset-match
    fallback attribution against recent preflight events; the row carries
    ``attribution=fallback`` in that case.
    """
    if not telemetry_enabled():
        return
    attribution = "explicit" if preflight_id else "fallback"
    if not preflight_id and file_paths:
        preflight_id = _resolve_fallback_attribution(file_paths)
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "tool": tool,
        "decision_id": decision_id,
        "preflight_id": preflight_id,
        "file_paths_hash": hash_file_paths(file_paths or []),
        "attribution": attribution,
    }
    _append(_ENGAGEMENTS_FILE, record)
