"""OS-keyring-backed secret storage for per-source credentials (#419 Phase 0b).

Used by future Phase 1+ source adapters (Linear, Notion, GitHub, Slack,
Google Drive) to persist OAuth tokens / API keys without ever writing them
to ``.bicameral/config.yaml`` in plaintext.

Backend selection:
- Linux: Secret Service (gnome-keyring, kwallet, ...)
- macOS: Keychain
- Windows: Credential Locker
- Fallback: in-process dict + warn-level audit event so the operator
  sees that secrets are NOT persisted across server restart.

Service-name convention: ``bicameral-mcp::<source_id>``. An operator can
manually inspect or wipe via the OS-native keyring tool:
    keyring del bicameral-mcp::linear api_key

Hardening:
- ``source_id`` and ``key`` whitelisted to ``[A-Za-z0-9._-]+`` (no traversal,
  no spaces, no path separators).
- ``value`` length-capped at 8 KiB — no legitimate OAuth token is bigger,
  and the cap bounds memory misuse if a confused caller passes a payload.
- ``BICAMERAL_KEYRING_DISABLE=1`` short-circuits to the dict backend
  deterministically — required for CI sandboxes where no keyring daemon
  is running.

Audit-log emission:
- ``put_secret`` emits ``SOURCE_AUTH_GRANTED`` (warn) with no value.
- ``delete_secret`` emits ``SOURCE_AUTH_REVOKED`` (warn) with no value.
- Backend-degraded path emits ``GATE_FIRED`` (warn) once per process to
  notify the operator that secrets are not persisted.

The forbid-list discipline in ``audit_log._strip_forbidden`` already
catches accidental ``value`` / ``content`` / ``payload`` keys — the
``put_secret`` emit deliberately passes only ``source_id`` + ``key`` so
the value cannot leak via misuse.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any

_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_VALUE_MAX_BYTES = 8 * 1024  # 8 KiB — bigger than any legitimate OAuth token

_DICT_FALLBACK: dict[tuple[str, str], str] = {}
_DICT_FALLBACK_LOCK = threading.Lock()
_BACKEND_NOTICE_FIRED = False
_BACKEND_NOTICE_LOCK = threading.Lock()


def _validate_identifier(name: str, field_label: str) -> None:
    if not name or not _SERVICE_NAME_RE.match(name):
        raise ValueError(
            f"{field_label} must match [A-Za-z0-9._-]+ (got {name!r}). "
            "Path-traversal characters and spaces are rejected to prevent "
            "keyring-service-name attacks."
        )


def _service_for(source_id: str) -> str:
    """Build the keyring service name from a validated source_id."""
    return f"bicameral-mcp::{source_id}"


def _keyring_disabled() -> bool:
    return os.getenv("BICAMERAL_KEYRING_DISABLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _get_backend() -> Any | None:
    """Return the live keyring module, or None when unavailable / disabled.

    The first call when no backend is available emits one warn-level
    audit event so the operator sees that secrets are not persisted.
    Subsequent calls are silent to avoid log spam.
    """
    if _keyring_disabled():
        return None
    try:
        import keyring  # type: ignore[import-not-found]
        from keyring.errors import NoKeyringError  # type: ignore[import-not-found]
    except ImportError:
        _emit_degraded_once(reason="keyring package not installed")
        return None
    # Probe the backend — `keyring.get_keyring()` returns even when no
    # functional backend exists (it returns a fail-keyring). Check for
    # the fail-keyring shape by exercising a no-op read; NoKeyringError
    # is the typed signal.
    try:
        keyring.get_password("bicameral-mcp::__probe__", "__probe__")
    except NoKeyringError:
        _emit_degraded_once(reason="no functional keyring backend available")
        return None
    except Exception:  # noqa: BLE001 — defensive; treat any backend hiccup as no-backend
        _emit_degraded_once(reason="keyring backend raised during probe")
        return None
    return keyring


def _emit_degraded_once(*, reason: str) -> None:
    """Fire the backend-degraded notice exactly once per process."""
    global _BACKEND_NOTICE_FIRED
    with _BACKEND_NOTICE_LOCK:
        if _BACKEND_NOTICE_FIRED:
            return
        _BACKEND_NOTICE_FIRED = True
    try:
        from audit_log import AuditEventType
        from audit_log import emit as audit_emit

        audit_emit(
            AuditEventType.GATE_FIRED,
            message="secrets_store backend unavailable — falling back to in-process dict",
            reason=reason,
            persisted=False,
        )
    except Exception:  # noqa: BLE001 — audit failure must not block secret access
        pass


def _audit_lifecycle(event_type_name: str, *, source_id: str, key: str) -> None:
    """Emit a SOURCE_AUTH_* event without ever logging the value."""
    try:
        from audit_log import AuditEventType
        from audit_log import emit as audit_emit

        audit_emit(
            AuditEventType(event_type_name),
            source_id=source_id,
            secret_key=key,
        )
    except Exception:  # noqa: BLE001 — lifecycle emit must not block ops
        pass


def put_secret(*, source_id: str, key: str, value: str) -> None:
    """Persist ``value`` under ``(source_id, key)``.

    Raises ``ValueError`` on whitelist violation or oversized value.
    Emits ``SOURCE_AUTH_GRANTED`` on success (or on fallback-dict write).
    """
    _validate_identifier(source_id, "source_id")
    _validate_identifier(key, "key")
    encoded_size = len(value.encode("utf-8"))
    if encoded_size > _VALUE_MAX_BYTES:
        raise ValueError(
            f"value too large: {encoded_size} bytes > {_VALUE_MAX_BYTES} cap. "
            "Tokens are kilobyte-class at most."
        )

    backend = _get_backend()
    if backend is None:
        with _DICT_FALLBACK_LOCK:
            _DICT_FALLBACK[(source_id, key)] = value
    else:
        backend.set_password(_service_for(source_id), key, value)

    _audit_lifecycle("source_auth_granted", source_id=source_id, key=key)


def get_secret(*, source_id: str, key: str) -> str | None:
    """Return the persisted value, or None if not set.

    No audit emit on read — read traffic would dominate the audit-log
    surface; readers should rely on the granted/revoked lifecycle for
    state-of-the-world reconstruction.
    """
    _validate_identifier(source_id, "source_id")
    _validate_identifier(key, "key")
    backend = _get_backend()
    if backend is None:
        with _DICT_FALLBACK_LOCK:
            return _DICT_FALLBACK.get((source_id, key))
    return backend.get_password(_service_for(source_id), key)


def delete_secret(*, source_id: str, key: str) -> None:
    """Remove the persisted value. Idempotent — no-op if not present.

    Emits ``SOURCE_AUTH_REVOKED`` whether or not the key existed; the
    revocation signal is operator-visible regardless of prior state.
    """
    _validate_identifier(source_id, "source_id")
    _validate_identifier(key, "key")
    backend = _get_backend()
    if backend is None:
        with _DICT_FALLBACK_LOCK:
            _DICT_FALLBACK.pop((source_id, key), None)
    else:
        try:
            backend.delete_password(_service_for(source_id), key)
        except Exception:  # noqa: BLE001 — keyring raises PasswordDeleteError on missing key
            pass
    _audit_lifecycle("source_auth_revoked", source_id=source_id, key=key)


def list_keys(*, source_id: str) -> list[str]:
    """Return the list of keys currently set under ``source_id``.

    Keyring does not expose a portable "list" primitive — Secret Service
    supports it, Windows Credential Locker does not. Phase 0b returns
    only the dict-backend keys reliably; backend-backed installs require
    the OS-native tool (``keyring`` CLI) to enumerate. Documented gap;
    the per-source adapters track their own key inventories via the
    ledger anyway.
    """
    _validate_identifier(source_id, "source_id")
    with _DICT_FALLBACK_LOCK:
        return [k for (sid, k) in _DICT_FALLBACK.keys() if sid == source_id]


def _reset_for_tests() -> None:
    """Clear the dict fallback + reset the one-shot notice. Test-only."""
    global _BACKEND_NOTICE_FIRED
    with _DICT_FALLBACK_LOCK:
        _DICT_FALLBACK.clear()
    with _BACKEND_NOTICE_LOCK:
        _BACKEND_NOTICE_FIRED = False
