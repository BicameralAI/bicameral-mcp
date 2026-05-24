"""Content-addressed memoization for deterministic transforms (#136 wedge).

Wraps a synchronous pure function so repeat calls with the same inputs hit
a SQLite-backed content cache instead of re-invoking. Designed for the
"tool froze" UX problem (#136 + #431): the same cosmetic diff is
classified once, every subsequent sweep reads from disk in single-digit ms.

NOT a general-purpose memoize. Constraints by design:

- **Synchronous only.** Async transforms entangle with ledger writes; the
  wedge scope (plan-136) leaves those for daemon Phase 2c-6+.
- **Allowlist-typed args.** Validated at every call, fail-loud on misuse.
  Anything outside ``(str | int | float | bool | None | tuple |
  frozenset | dict[str, primitive])`` is a programming error.
- **Pickle for outputs.** Acceptable because the cache lives per-machine,
  per-user. Not a trust boundary. Unpickle errors are treated as misses.
- **``behavior_version`` is mandatory** on the decorator. Bump on any
  logic change inside the wrapped function — the snapshot test in
  ``tests/test_content_cache_fingerprint.py`` catches accidental drift.

Cache file location resolution:

1. Explicit ``cache=`` arg to the decorator (used in tests)
2. ``BICAMERAL_CONTENT_CACHE_PATH`` env var
3. Fallback: ``~/.bicameral/content_cache.db``

The default-cache location will be revisited when daemon Phase 2c-6 lands
and ``codegenome/`` moves into ``daemon/``. The cache primitive itself
travels with no daemon-specific imports; only the path constant changes.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import os
import pickle
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_ALLOWED_PRIMITIVES = (str, int, float, bool, type(None))
_ALLOWED_CONTAINERS = (tuple, frozenset)


def _is_allowed_arg(value: Any) -> bool:
    """Recursively check that a value is in the canonical-serializable set."""
    if isinstance(value, _ALLOWED_PRIMITIVES):
        return True
    if isinstance(value, _ALLOWED_CONTAINERS):
        return all(_is_allowed_arg(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_allowed_arg(v) for k, v in value.items())
    return False


def _canonical(value: Any) -> Any:
    """Convert a value into a JSON-serializable canonical form.

    Tuples become ``["__tuple__", ...]`` so a tuple and a list of the same
    items hash differently. Frozensets become sorted lists tagged
    ``__frozenset__`` for the same reason.
    """
    if isinstance(value, _ALLOWED_PRIMITIVES):
        return value
    if isinstance(value, tuple):
        return ["__tuple__", *(_canonical(v) for v in value)]
    if isinstance(value, frozenset):
        return ["__frozenset__", *sorted((_canonical(v) for v in value), key=repr)]
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    raise TypeError(f"unsupported arg type for content_cached: {type(value).__name__}")


def _cache_key(
    func_name: str,
    behavior_version: int,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> str:
    """Stable 32-hex-char (128-bit) sha256 key derived from canonical args."""
    payload = {
        "fn": func_name,
        "v": behavior_version,
        "args": [_canonical(a) for a in args],
        "kwargs": {k: _canonical(v) for k, v in sorted(kwargs.items())},
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:32]


class ContentCache:
    """SQLite-backed content cache for deterministic transforms.

    Thread-safe within a process via ``threading.Lock``; multi-process
    safety comes from SQLite's WAL mode. Each call opens its own
    connection — premature pooling isn't worth the complexity at the
    expected per-request rate (single-digit ops per sweep).

    Eviction: when an insert pushes total bytes above ``max_bytes``,
    rows are deleted in least-hit / oldest-first order until under cap.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS content_cache (
            key TEXT PRIMARY KEY,
            value BLOB NOT NULL,
            behavior_version INTEGER NOT NULL,
            created_at REAL NOT NULL,
            hits INTEGER NOT NULL DEFAULT 0,
            bytes INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_content_cache_lru
            ON content_cache (hits, created_at);
    """

    def __init__(self, path: Path | str, max_bytes: int = 100 * 1024 * 1024):
        self._path = Path(path)
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._schema_initialized = False

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), isolation_level=None, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._schema_initialized:
            return
        conn.executescript(self._SCHEMA)
        self._schema_initialized = True

    def get(self, key: str, behavior_version: int) -> tuple[bool, Any]:
        """Return ``(hit, value)``.

        ``hit=False`` means: key not present, version mismatch, SQLite
        error, or unpickling failure. The wrapper treats all four
        identically — compute and overwrite.

        ``hit=True, value=None`` is a legitimate cached ``None`` return.
        """
        try:
            with self._lock:
                conn = self._connect()
                try:
                    self._ensure_schema(conn)
                    row = conn.execute(
                        "SELECT value, behavior_version FROM content_cache WHERE key=?",
                        (key,),
                    ).fetchone()
                    if row is None:
                        return False, None
                    value_blob, stored_version = row
                    if stored_version != behavior_version:
                        return False, None
                    conn.execute(
                        "UPDATE content_cache SET hits=hits+1 WHERE key=?",
                        (key,),
                    )
                    return True, pickle.loads(value_blob)
                finally:
                    conn.close()
        except (sqlite3.Error, pickle.UnpicklingError, EOFError) as exc:
            logger.debug("[content_cache] get treated as miss on error: %s", exc)
            return False, None

    def set(self, key: str, value: Any, behavior_version: int) -> None:
        """Store ``value`` under ``key``. Refuses entries larger than the cap."""
        blob = pickle.dumps(value)
        size = len(blob)
        if size > self._max_bytes:
            logger.warning(
                "[content_cache] entry size %d > cap %d for key=%s; skipping",
                size,
                self._max_bytes,
                key,
            )
            return
        with self._lock:
            conn = self._connect()
            try:
                self._ensure_schema(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO content_cache "
                    "(key, value, behavior_version, created_at, hits, bytes) "
                    "VALUES (?, ?, ?, ?, 0, ?)",
                    (key, blob, behavior_version, time.time(), size),
                )
                self._evict_if_over_cap(conn)
            finally:
                conn.close()

    def _evict_if_over_cap(self, conn: sqlite3.Connection) -> None:
        """Drop least-hit / oldest entries until total bytes <= cap."""
        total = conn.execute("SELECT COALESCE(SUM(bytes), 0) FROM content_cache").fetchone()[0]
        if total <= self._max_bytes:
            return
        rows = conn.execute(
            "SELECT key, bytes FROM content_cache ORDER BY hits ASC, created_at ASC"
        ).fetchall()
        evicted = 0
        for key, byts in rows:
            if total - evicted <= self._max_bytes:
                break
            conn.execute("DELETE FROM content_cache WHERE key=?", (key,))
            evicted += byts

    def stats(self) -> dict[str, int]:
        """Diagnostic snapshot: ``entries``, ``bytes``, ``hits``."""
        with self._lock:
            conn = self._connect()
            try:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(bytes), 0), COALESCE(SUM(hits), 0) "
                    "FROM content_cache"
                ).fetchone()
                return {"entries": int(row[0]), "bytes": int(row[1]), "hits": int(row[2])}
            finally:
                conn.close()


_DEFAULT_CACHE: ContentCache | None = None
_DEFAULT_LOCK = threading.Lock()


def default_cache() -> ContentCache:
    """Lazily resolve the process-default content cache.

    Order: ``$BICAMERAL_CONTENT_CACHE_PATH`` → ``~/.bicameral/content_cache.db``.
    """
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is not None:
        return _DEFAULT_CACHE
    with _DEFAULT_LOCK:
        if _DEFAULT_CACHE is None:
            path_str = os.environ.get(
                "BICAMERAL_CONTENT_CACHE_PATH",
                str(Path.home() / ".bicameral" / "content_cache.db"),
            )
            _DEFAULT_CACHE = ContentCache(path_str)
    return _DEFAULT_CACHE


def _reset_default_cache_for_tests() -> None:
    """Test hook: clear the module-level default so the next call rebuilds.

    Test code that mutates ``BICAMERAL_CONTENT_CACHE_PATH`` after the
    default has already been resolved would otherwise get a stale cache.
    Not part of the public API; named with leading underscore.
    """
    global _DEFAULT_CACHE
    with _DEFAULT_LOCK:
        _DEFAULT_CACHE = None


def content_cached(
    *,
    behavior_version: int,
    cache: ContentCache | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Memoize a pure synchronous function with a content-addressed cache.

    Mandatory ``behavior_version``: bump on any logic change inside the
    wrapped function. Snapshot test in
    ``tests/test_content_cache_fingerprint.py`` pins the key fingerprint
    of a representative input so accidental drift fails CI.

    Rejects async functions at decorator-bind time (fail-loud). Rejects
    non-allowlist arg types at first call (fail-loud).
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if inspect.iscoroutinefunction(fn):
            raise TypeError(
                f"content_cached cannot wrap async function {fn.__qualname__}; "
                "scope intentionally synchronous (plan-136)"
            )
        fn_name = f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            cache_inst = cache if cache is not None else default_cache()
            for a in args:
                if not _is_allowed_arg(a):
                    raise TypeError(
                        f"content_cached({fn_name}): positional arg of type "
                        f"{type(a).__name__} is not in the allowlist"
                    )
            for k, v in kwargs.items():
                if not _is_allowed_arg(v):
                    raise TypeError(
                        f"content_cached({fn_name}): kwarg {k!r} of type "
                        f"{type(v).__name__} is not in the allowlist"
                    )
            key = _cache_key(fn_name, behavior_version, args, kwargs)
            hit, value = cache_inst.get(key, behavior_version)
            if hit:
                return value  # type: ignore[no-any-return]
            result = fn(*args, **kwargs)
            cache_inst.set(key, result, behavior_version)
            return result

        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        wrapper._content_cached_version = behavior_version  # type: ignore[attr-defined]
        wrapper._content_cached_fn_name = fn_name  # type: ignore[attr-defined]
        return wrapper

    return decorator
