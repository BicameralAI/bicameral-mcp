"""Sociable tests for ``codegenome._content_cache`` (#136 wedge).

Real SQLite via ``tmp_path``; no MagicMock per CLAUDE.md. The primitive
exists to ship in production code paths, so the tests exercise its real
storage, real eviction, real concurrency primitives.
"""

from __future__ import annotations

import pickle
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest

from codegenome._content_cache import (
    ContentCache,
    _cache_key,
    _canonical,
    _is_allowed_arg,
    content_cached,
)


# Module-level dataclass: pickle requires the class to be importable by
# its fully-qualified name. A local class defined inside a test function
# would fail to pickle ("can't pickle local object"). The production
# return types (DriftClassification, DiffStats, GovernancePolicyResult)
# are all module-level, so this matches deployed shape.
@dataclass(frozen=True)
class _SampleResult:
    verdict: str
    score: float
    signals: tuple[str, ...]
    evidence: tuple[str, ...] = ()


# ── ContentCache primitive ─────────────────────────────────────────────


def test_set_then_get_roundtrips(tmp_path):
    cache = ContentCache(tmp_path / "c.db")
    cache.set("k1", {"value": 42, "nested": [1, 2]}, behavior_version=1)
    hit, value = cache.get("k1", behavior_version=1)
    assert hit is True
    assert value == {"value": 42, "nested": [1, 2]}


def test_get_miss_on_unknown_key(tmp_path):
    cache = ContentCache(tmp_path / "c.db")
    hit, value = cache.get("nonexistent", behavior_version=1)
    assert hit is False
    assert value is None


def test_get_miss_on_version_mismatch(tmp_path):
    """Bumping ``behavior_version`` invalidates prior entries without
    requiring an explicit purge. This is the load-bearing safety lever
    for shipping a logic change to a memoized function."""
    cache = ContentCache(tmp_path / "c.db")
    cache.set("k1", "v_at_version_1", behavior_version=1)
    hit, value = cache.get("k1", behavior_version=2)
    assert hit is False
    assert value is None


def test_get_returns_legit_none_as_hit(tmp_path):
    """``None`` is a valid cached value, distinct from a miss. The wrapper
    relies on ``(hit, value)`` tuple semantics for this — using ``None``
    as a sentinel would silently re-invoke the function on every call to
    a function that legitimately returns ``None``."""
    cache = ContentCache(tmp_path / "c.db")
    cache.set("k1", None, behavior_version=1)
    hit, value = cache.get("k1", behavior_version=1)
    assert hit is True
    assert value is None


def test_set_overwrites_existing_key(tmp_path):
    cache = ContentCache(tmp_path / "c.db")
    cache.set("k1", "first", behavior_version=1)
    cache.set("k1", "second", behavior_version=1)
    _, value = cache.get("k1", behavior_version=1)
    assert value == "second"


def test_cache_survives_reopen(tmp_path):
    """Entries written by one ContentCache instance are visible to a
    second instance pointing at the same file — pins the
    cross-process-shape guarantee. We use two instances rather than
    forking because the test exercises SQLite WAL durability, not
    OS-level process isolation."""
    db_path = tmp_path / "c.db"
    cache1 = ContentCache(db_path)
    cache1.set("k1", "persistent", behavior_version=1)
    cache2 = ContentCache(db_path)
    hit, value = cache2.get("k1", behavior_version=1)
    assert hit is True
    assert value == "persistent"


def test_eviction_triggers_when_over_cap(tmp_path):
    """LRU-by-(hits, created_at) drops cold entries first. Cap is set
    small enough that 4 entries of ~1KB each force eviction on the 4th
    insert."""
    cache = ContentCache(tmp_path / "c.db", max_bytes=2000)
    payload = "x" * 800
    cache.set("k1", payload, behavior_version=1)
    cache.set("k2", payload, behavior_version=1)
    # Bump k2 hit count so it's NOT the eviction victim
    cache.get("k2", behavior_version=1)
    cache.set("k3", payload, behavior_version=1)
    cache.set("k4", payload, behavior_version=1)
    stats = cache.stats()
    assert stats["bytes"] <= 2000, f"expected eviction but bytes={stats['bytes']}"
    # k2 had a hit, should survive; k1 had no hits and is oldest
    hit_k1, _ = cache.get("k1", behavior_version=1)
    hit_k2, _ = cache.get("k2", behavior_version=1)
    assert hit_k1 is False, "expected k1 (least-hits, oldest) to be evicted"
    assert hit_k2 is True, "expected k2 (hit before eviction) to survive"


def test_oversized_entry_is_refused_silently(tmp_path):
    """An entry larger than the entire cap is logged + skipped, not
    inserted-then-immediately-evicted. Prevents thrash when a transform
    happens to return an unexpectedly huge value."""
    cache = ContentCache(tmp_path / "c.db", max_bytes=100)
    cache.set("k1", "x" * 1000, behavior_version=1)
    hit, _ = cache.get("k1", behavior_version=1)
    assert hit is False
    assert cache.stats()["entries"] == 0


def test_get_treats_unpicklable_blob_as_miss(tmp_path):
    """If the on-disk blob is corrupt (e.g. partial write from an old
    bug), ``get`` returns a miss instead of raising. The wrapper then
    computes + overwrites — self-healing."""
    cache = ContentCache(tmp_path / "c.db")
    cache.set("k1", "fine", behavior_version=1)
    # Corrupt the stored blob via the same SQLite file
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "c.db"), isolation_level=None)
    try:
        conn.execute("UPDATE content_cache SET value=? WHERE key=?", (b"\x00garbage", "k1"))
    finally:
        conn.close()
    hit, _ = cache.get("k1", behavior_version=1)
    assert hit is False


def test_stats_reflects_inserts_and_hits(tmp_path):
    cache = ContentCache(tmp_path / "c.db")
    cache.set("k1", "v1", behavior_version=1)
    cache.set("k2", "v2", behavior_version=1)
    cache.get("k1", behavior_version=1)
    cache.get("k1", behavior_version=1)
    cache.get("k2", behavior_version=1)
    stats = cache.stats()
    assert stats["entries"] == 2
    assert stats["hits"] == 3
    assert stats["bytes"] > 0


def test_concurrent_writes_serialize_safely(tmp_path):
    """Threading smoke test — eight threads each writing 25 keys against
    the same cache file. All entries must land; no SQLite contention
    errors. Catches missing WAL pragma / missing lock / connection-pool
    bugs."""
    cache = ContentCache(tmp_path / "c.db")

    def worker(prefix: str):
        for i in range(25):
            cache.set(f"{prefix}:{i}", i, behavior_version=1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, [f"t{i}" for i in range(8)]))

    assert cache.stats()["entries"] == 200


# ── _cache_key + canonicalization ──────────────────────────────────────


def test_cache_key_is_stable_across_calls():
    """Same inputs → same key, every call. Load-bearing for cache hits."""
    args = ("hello", 42, True)
    kwargs = {"option": "x"}
    k1 = _cache_key("module.func", 1, args, kwargs)
    k2 = _cache_key("module.func", 1, args, kwargs)
    assert k1 == k2


def test_cache_key_differs_on_arg_change():
    k1 = _cache_key("module.func", 1, ("a",), {})
    k2 = _cache_key("module.func", 1, ("b",), {})
    assert k1 != k2


def test_cache_key_differs_on_kwarg_change():
    k1 = _cache_key("module.func", 1, (), {"option": "a"})
    k2 = _cache_key("module.func", 1, (), {"option": "b"})
    assert k1 != k2


def test_cache_key_differs_on_version_bump():
    k1 = _cache_key("module.func", 1, ("a",), {})
    k2 = _cache_key("module.func", 2, ("a",), {})
    assert k1 != k2


def test_cache_key_differs_on_function_rename():
    """Memoization is scoped to a fully-qualified function name; two
    different functions with the same args do NOT collide."""
    k1 = _cache_key("module.func_a", 1, ("x",), {})
    k2 = _cache_key("module.func_b", 1, ("x",), {})
    assert k1 != k2


def test_canonical_distinguishes_tuple_and_list_shapes():
    """A frozenset and a tuple of the same items must NOT produce the
    same canonical form — otherwise their cache keys would collide
    despite being different Python types with different semantics."""
    a = _canonical((1, 2, 3))
    b = _canonical(frozenset([1, 2, 3]))
    assert a != b


def test_canonical_dict_order_is_stable():
    """Insertion order doesn't matter — dicts canonicalize by sorted keys."""
    a = _canonical({"a": 1, "b": 2})
    b = _canonical({"b": 2, "a": 1})
    assert a == b


def test_canonical_rejects_unsupported_type():
    class CustomObj:
        pass

    with pytest.raises(TypeError, match="unsupported arg type"):
        _canonical(CustomObj())


def test_is_allowed_arg_accepts_nested_primitives():
    assert _is_allowed_arg("x") is True
    assert _is_allowed_arg(42) is True
    assert _is_allowed_arg(None) is True
    assert _is_allowed_arg((1, "x", None)) is True
    assert _is_allowed_arg(frozenset(["a", "b"])) is True
    assert _is_allowed_arg({"a": 1, "b": (2, 3)}) is True


def test_is_allowed_arg_rejects_bytes_and_objects():
    """Bytes are deliberately excluded — pickle/json asymmetry causes
    silent bugs. Callers must convert to str (decoded) or list[int]."""
    assert _is_allowed_arg(b"raw") is False
    assert _is_allowed_arg([1, 2, 3]) is False  # list, not tuple
    assert _is_allowed_arg({"a": object()}) is False


# ── @content_cached decorator ──────────────────────────────────────────


def test_decorator_caches_call(tmp_path):
    cache = ContentCache(tmp_path / "c.db")
    counter = {"n": 0}

    @content_cached(behavior_version=1, cache=cache)
    def expensive(x: int) -> int:
        counter["n"] += 1
        return x * 2

    assert expensive(5) == 10
    assert expensive(5) == 10
    assert counter["n"] == 1, f"expected 1 call, got {counter['n']}"


def test_decorator_misses_on_different_args(tmp_path):
    cache = ContentCache(tmp_path / "c.db")
    counter = {"n": 0}

    @content_cached(behavior_version=1, cache=cache)
    def expensive(x: int) -> int:
        counter["n"] += 1
        return x * 2

    expensive(5)
    expensive(6)
    expensive(5)
    expensive(6)
    assert counter["n"] == 2, f"expected 2 calls (one per unique arg), got {counter['n']}"


def test_decorator_kwargs_participate_in_key(tmp_path):
    cache = ContentCache(tmp_path / "c.db")
    counter = {"n": 0}

    @content_cached(behavior_version=1, cache=cache)
    def fn(x: int, *, mode: str = "a") -> str:
        counter["n"] += 1
        return f"{x}-{mode}"

    assert fn(1, mode="a") == "1-a"
    assert fn(1, mode="b") == "1-b"
    assert fn(1, mode="a") == "1-a"
    assert counter["n"] == 2


def test_decorator_version_bump_recomputes(tmp_path):
    """Two decorated wrappers around the same underlying function, with
    different ``behavior_version``s, each have their own cache namespace.
    Bumping the version on a deployed function therefore forces fresh
    computation without leaving stale entries readable."""
    cache = ContentCache(tmp_path / "c.db")
    counter = {"n": 0}

    def real(x: int) -> int:
        counter["n"] += 1
        return x

    v1 = content_cached(behavior_version=1, cache=cache)(real)
    v2 = content_cached(behavior_version=2, cache=cache)(real)
    v1(5)
    v2(5)
    assert counter["n"] == 2


def test_decorator_returns_cached_none(tmp_path):
    cache = ContentCache(tmp_path / "c.db")
    counter = {"n": 0}

    @content_cached(behavior_version=1, cache=cache)
    def maybe_none(x: int) -> int | None:
        counter["n"] += 1
        return None if x == 0 else x

    assert maybe_none(0) is None
    assert maybe_none(0) is None
    assert counter["n"] == 1, "None must be cached, not re-computed"


def test_decorator_rejects_async_at_bind_time():
    """An ``async def`` wrapped function would produce a coroutine the
    cache can't pickle. Fail loudly at decorator-application rather than
    at first call, when the rejection is most surprising."""
    with pytest.raises(TypeError, match="cannot wrap async function"):

        @content_cached(behavior_version=1)
        async def coro(x: int) -> int:
            return x


def test_decorator_rejects_disallowed_arg_type_at_call(tmp_path):
    cache = ContentCache(tmp_path / "c.db")

    @content_cached(behavior_version=1, cache=cache)
    def fn(x: object) -> str:
        return str(x)

    class Custom:
        pass

    with pytest.raises(TypeError, match="not in the allowlist"):
        fn(Custom())


def test_decorator_rejects_disallowed_kwarg_type(tmp_path):
    cache = ContentCache(tmp_path / "c.db")

    @content_cached(behavior_version=1, cache=cache)
    def fn(**kwargs: object) -> str:
        return str(kwargs)

    with pytest.raises(TypeError, match="kwarg 'bad'"):
        fn(bad=object())


def test_decorator_preserves_function_metadata(tmp_path):
    cache = ContentCache(tmp_path / "c.db")

    @content_cached(behavior_version=3, cache=cache)
    def my_special_fn(x: int) -> int:
        """Docstring preserved."""
        return x

    assert my_special_fn.__name__ == "my_special_fn"
    assert my_special_fn.__doc__ == "Docstring preserved."
    assert my_special_fn._content_cached_version == 3  # type: ignore[attr-defined]
    assert "my_special_fn" in my_special_fn._content_cached_fn_name  # type: ignore[attr-defined]
    # __wrapped__ exposes the underlying function — enables benchmark tests
    # that need to call the un-cached version for comparison
    assert my_special_fn.__wrapped__.__name__ == "my_special_fn"  # type: ignore[attr-defined]


def test_decorator_serializes_concurrent_callers_on_first_miss(tmp_path):
    """Two threads calling the same key concurrently: the underlying
    function may run twice (TOCTOU between get + set) but both threads
    receive a correct result. The pure-function precondition makes
    duplicate computation safe — the writes are identical."""
    cache = ContentCache(tmp_path / "c.db")
    call_count = {"n": 0}
    counter_lock = threading.Lock()

    @content_cached(behavior_version=1, cache=cache)
    def slow_fn(x: int) -> int:
        with counter_lock:
            call_count["n"] += 1
        time.sleep(0.01)
        return x * 10

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(slow_fn, [7] * 8))

    assert all(r == 70 for r in results)
    # Under contention, n is in [1, 4] — bounded by pool size, not 8.
    # Pinning a strict 1 would be a stronger guarantee but requires
    # cross-thread locking around the get→set window, which costs more
    # than the rare duplicate compute it would prevent.
    assert call_count["n"] <= 4


# ── End-to-end: confirms primitive's surface against pickle quirks ──────


def test_decorator_with_dataclass_return_value(tmp_path):
    """Dataclass returns roundtrip via pickle. This is the shape of
    every real wrapped function (``DriftClassification``, ``DiffStats``,
    ``GovernancePolicyResult``) — a tight test now prevents discovering
    a pickle quirk while wiring the real transforms."""
    cache = ContentCache(tmp_path / "c.db")
    counter = {"n": 0}

    @content_cached(behavior_version=1, cache=cache)
    def classify(body: str) -> _SampleResult:
        counter["n"] += 1
        return _SampleResult(verdict="cosmetic", score=0.95, signals=("a", "b"))

    r1 = classify("hello")
    r2 = classify("hello")
    assert r1 == r2
    assert r1.verdict == "cosmetic"
    assert counter["n"] == 1


def test_pickle_size_estimate_for_typical_payload():
    """Sanity check: cached payloads are small. A typical
    DriftClassification-shaped dataclass should fit in a few hundred
    bytes. If this jumps significantly, revisit the cache cap."""
    r = _SampleResult(
        verdict="cosmetic",
        score=0.95,
        signals=("signature", "neighbors", "diff_lines", "no_new_calls"),
        evidence=("score:0.950", "signature:1.00", "neighbors:1.00"),
    )
    blob = pickle.dumps(r)
    assert len(blob) < 1000, f"unexpected payload size {len(blob)} bytes"
