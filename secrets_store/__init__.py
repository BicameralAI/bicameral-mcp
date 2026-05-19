"""Secret store wrapper (#419 Phase 0b).

Public API re-exports. Backed by the OS keyring (Linux Secret Service,
macOS Keychain, Windows Credential Locker) via the ``keyring`` package.
Falls back to an in-process dict with a loud audit-log warning when no
keyring backend is available — never silently to plaintext-on-disk.

Module name is ``secrets_store`` (not ``secrets``) because Python's
standard library claims ``secrets`` for ``secrets.token_bytes`` etc.
"""

from secrets_store.store import (
    delete_secret,
    get_secret,
    list_keys,
    put_secret,
)

__all__ = ["put_secret", "get_secret", "delete_secret", "list_keys"]
