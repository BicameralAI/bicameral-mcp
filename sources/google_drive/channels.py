"""Channel registry for Google Drive Push Notifications (#337 cycle 9).

Drive's webhook contract differs from HMAC providers (GitHub, Slack,
Linear): there is no body signature. Authenticity is asserted by
matching three values on each notification against what we registered
at ``channels.watch`` time:

1. ``X-Goog-Channel-Id`` — the UUID we sent in the ``id`` field
2. ``X-Goog-Channel-Token`` — the operator-supplied opaque string we
   sent in the ``token`` field
3. ``X-Goog-Resource-Id`` — the opaque ID Google returned in the
   ``channels.watch`` RESPONSE (not request); we MUST persist it at
   creation time because it cannot be recovered from notifications
   alone.

This module is the registry: persisted store of
``(channel_id, resource_id, token, expiration_ms, file_id)`` tuples
that the verify path looks up by channel_id.

## Persistence

v0 uses a JSON file at ``~/.bicameral/drive_channels.json`` written
atomically (write to ``.tmp`` then ``os.replace``). Same posture as
``handlers/update.py`` and ``preflight_telemetry.py``. A future cycle
will migrate this to a SurrealDB table when the renewal job lands
(per the cycle-9 research brief, atomicity for the
"create-new / persist / stop-old" sequence is the reason to move; v0
doesn't have a renewal job, so JSON suffices).

The registry MUST NOT store the OAuth token — channel tokens are
provisioned at channels.watch time, are scoped to a single channel,
and are echoed back in cleartext via HTTPS. They are NOT a secret on
the order of the OAuth refresh token; storing them in a user-readable
JSON file is acceptable. (Compare with ``secrets_store`` which keeps
OAuth tokens in the OS keyring.)

## Thread / process safety

A single ``threading.Lock`` gates in-process reads/writes. Cross-
process safety relies on the atomic-rename property of
``os.replace`` — a torn read is impossible because readers always
see either the old file or the new one. Two processes both writing
will result in a last-writer-wins race for any concurrent
``register`` calls, which is fine because channel IDs are UUIDs and
collisions are vanishingly improbable.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ChannelRecord:
    """One row in the registry. ``frozen=True`` because mutations
    must round-trip through ``register()`` for atomicity."""

    channel_id: str
    resource_id: str
    token: str
    expiration_ms: int
    file_id: str
    # ``watched_resource_kind`` distinguishes a per-file watch from a
    # future account-wide ``changes.watch`` (cycle 9b). v0 only emits
    # ``"file"``.
    watched_resource_kind: str = "file"
    # Cycle 9c: persisted so the renewal job can issue a successor
    # channel pointing at the same callback URL without operator
    # intervention. Defaults to empty for back-compat with cycle-9b
    # rows written before the field existed; renewal of those rows
    # is impossible without an operator-supplied URL (which the CLI
    # will surface as a clear error).
    callback_url: str = ""
    # Metadata captured at register-time. Reserved for renewal job.
    created_at_ms: int = 0
    extra: dict = field(default_factory=dict)


_DEFAULT_PATH = Path.home() / ".bicameral" / "drive_channels.json"


class ChannelRegistry:
    """In-memory + on-disk registry of Drive Push Notification channels.

    Usage::

        reg = ChannelRegistry()  # uses ~/.bicameral/drive_channels.json
        reg.register(ChannelRecord(channel_id="uuid-1", ...))
        rec = reg.get("uuid-1")  # ChannelRecord or None
        reg.delete("uuid-1")

    For tests, pass an explicit path::

        reg = ChannelRegistry(path=tmp_path / "ch.json")
    """

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path if path is not None else _DEFAULT_PATH
        self._lock = threading.Lock()
        # Lazy load on first access. We never cache stale data: every
        # public method takes the lock and re-reads the file, because
        # the renewal job (future) and the webhook handler may run in
        # different threads / processes.

    def _read_all(self) -> dict[str, ChannelRecord]:
        """Load every record from disk. Returns ``{}`` if no file yet."""
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Corrupt file → treat as empty. Operator can replay
            # ``channels.watch`` to repopulate. We do NOT auto-delete
            # the corrupt file (that's a footgun if the corruption was
            # transient); leave it for human inspection.
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, ChannelRecord] = {}
        for channel_id, row in raw.items():
            if not isinstance(row, dict):
                continue
            try:
                out[channel_id] = ChannelRecord(
                    channel_id=str(row["channel_id"]),
                    resource_id=str(row["resource_id"]),
                    token=str(row["token"]),
                    expiration_ms=int(row["expiration_ms"]),
                    file_id=str(row["file_id"]),
                    watched_resource_kind=str(row.get("watched_resource_kind", "file")),
                    callback_url=str(row.get("callback_url", "")),
                    created_at_ms=int(row.get("created_at_ms", 0)),
                    extra=row.get("extra") or {},
                )
            except (KeyError, ValueError, TypeError):
                # Skip malformed rows but don't fail the whole read —
                # one bad row shouldn't take the whole registry down.
                continue
        return out

    def _write_all(self, records: dict[str, ChannelRecord]) -> None:
        """Atomic write: tmp file in the same directory, then replace.

        Post-write, harden POSIX file/dir modes to 0o600/0o700 (MED-1
        review finding). Drive channel tokens aren't OAuth-grade
        secrets but knowing ``(channel_id, token, resource_id)`` is
        sufficient to forge notifications under our auth model — same
        posture as ``events/backends/google_drive.py`` already does
        for its sensitive artifacts. Windows ignores POSIX modes;
        the chmods are wrapped + skipped there.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            try:
                os.chmod(self._path.parent, 0o700)
            except OSError:
                pass
        payload = {cid: asdict(rec) for cid, rec in records.items()}
        # NamedTemporaryFile in the same directory so the rename is
        # atomic on the same filesystem (cross-fs rename is non-atomic
        # on POSIX and silently degrades on Windows).
        fd, tmp_path = tempfile.mkstemp(
            prefix=".drive_channels.", suffix=".tmp", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
            os.replace(tmp_path, self._path)
        except Exception:
            # Clean up the tmp file on any failure; otherwise it lingers.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        if os.name == "posix":
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass

    def register(self, record: ChannelRecord) -> None:
        """Persist a new channel record. Overwrites if channel_id exists."""
        if not record.channel_id:
            raise ValueError("ChannelRecord.channel_id must be non-empty")
        if not record.resource_id:
            raise ValueError("ChannelRecord.resource_id must be non-empty")
        with self._lock:
            records = self._read_all()
            records[record.channel_id] = record
            self._write_all(records)

    def get(self, channel_id: str) -> ChannelRecord | None:
        """Look up a record by channel_id. Returns None if not found."""
        if not channel_id:
            return None
        with self._lock:
            records = self._read_all()
            return records.get(channel_id)

    def delete(self, channel_id: str) -> bool:
        """Remove a record. Returns True if it existed."""
        if not channel_id:
            return False
        with self._lock:
            records = self._read_all()
            if channel_id not in records:
                return False
            del records[channel_id]
            self._write_all(records)
            return True

    def list_all(self) -> list[ChannelRecord]:
        """Return every record. Used by the (future) renewal job."""
        with self._lock:
            return list(self._read_all().values())


_singleton: ChannelRegistry | None = None
_singleton_lock = threading.Lock()


def get_registry() -> ChannelRegistry:
    """Return the process-wide registry. Lazily initialized."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ChannelRegistry()
    return _singleton


def _reset_for_tests(*, path: Path | None = None) -> None:
    """Test-only — reset the singleton, optionally pointing at a tmp path."""
    global _singleton
    with _singleton_lock:
        _singleton = ChannelRegistry(path=path) if path is not None else None
