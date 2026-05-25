"""Behavior-equivalence tests for classify_drift after content-cache wiring.

The cache wedge (#136 Step 2) split ``classify_drift`` into a thin
public wrapper (normalizes iterables → frozenset) plus a decorated
``_classify_drift_cached``. These tests pin three properties:

1. **Behavior equivalence** — the cached path and the underlying
   uncached function produce byte-identical ``DriftClassification``
   outputs across a representative corpus.

2. **Cache participation** — calling the public ``classify_drift``
   twice with the same args (after redirecting the default cache to
   tmp_path) does NOT re-invoke the inner compute. Verifies the cache
   is actually wired, not bypassed.

3. **Normalization neutrality** — passing neighbors as a list, set, or
   tuple all hit the same cache entry. The frozenset normalization
   inside the wrapper makes ordering irrelevant by construction.
"""

from __future__ import annotations

import os

import pytest

from codegenome._content_cache import _reset_default_cache_for_tests
from codegenome.drift_classifier import (
    _classify_drift_cached,
    classify_drift,
)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Each test gets a fresh cache. Without this, equivalence tests
    would silently rely on stale state from prior tests."""
    db = tmp_path / "cache.db"
    monkeypatch.setenv("BICAMERAL_CONTENT_CACHE_PATH", str(db))
    _reset_default_cache_for_tests()
    yield
    _reset_default_cache_for_tests()
    if "BICAMERAL_CONTENT_CACHE_PATH" in os.environ:
        del os.environ["BICAMERAL_CONTENT_CACHE_PATH"]


# ── Corpus — representative inputs spanning all four signals ───────────


_COSMETIC_PAIR = (
    'def f():\n    """Old."""\n    return 1\n',
    'def f():\n    """New."""\n    return 1\n',
)

_STRUCTURAL_PAIR = (
    "def compute(x):\n    if x > 0:\n        return x * 2\n    return 0\n",
    "def compute(x):\n    if x > 0:\n        return helper(x)\n    return 0\n",
)

_RENAMED_PAIR = (
    "def alpha():\n    return 1\n",
    "def beta():\n    return 1\n",
)

_BIG_DIFF_PAIR = (
    "def f():\n    x = 1\n    y = 2\n    return x + y\n",
    "def f():\n    x = 100\n    y = 200\n    z = 300\n    return x + y + z\n",
)


def _make_args(old: str, new: str, **overrides):
    """Default classify_drift kwargs with sensible test values."""
    base = {
        "old_signature_hash": "sig_abc",
        "new_signature_hash": "sig_abc",
        "old_neighbors": ("helper_a", "helper_b"),
        "new_neighbors": ("helper_a", "helper_b"),
        "language": "python",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "old,new,case",
    [
        (*_COSMETIC_PAIR, "cosmetic"),
        (*_STRUCTURAL_PAIR, "structural"),
        (*_RENAMED_PAIR, "renamed"),
        (*_BIG_DIFF_PAIR, "big_diff"),
    ],
)
def test_cached_matches_uncached_python(old, new, case):
    """For every corpus pair, the cached public function and the
    uncached inner produce equal DriftClassification."""
    args = _make_args(old, new)
    # The cached wrapper expects neighbors as frozenset (post-normalize);
    # to call the truly-uncached path we go through the public wrapper
    # which does the normalization, then peel back to __wrapped__ with
    # already-normalized args.
    nbrs = frozenset(args["old_neighbors"])
    direct_args = dict(args, old_neighbors=nbrs, new_neighbors=nbrs)
    cached = classify_drift(old, new, **args)
    uncached = _classify_drift_cached.__wrapped__(old, new, **direct_args)
    assert cached == uncached, f"case={case}: cached != uncached"


@pytest.mark.parametrize(
    "language",
    ["javascript", "typescript", "go", "rust", "java", "c_sharp"],
)
def test_cached_matches_uncached_other_languages(language):
    """Spot-check non-Python supported languages. classify_drift's
    diff_lines + no_new_calls signals dispatch on language, so a
    behavioral split between cached and uncached would show up here."""
    old = "function f() { return 1; }"
    new = "function f() { return 2; }"
    args = _make_args(old, new, language=language)
    nbrs = frozenset(args["old_neighbors"])
    direct_args = dict(args, old_neighbors=nbrs, new_neighbors=nbrs)
    cached = classify_drift(old, new, **args)
    uncached = _classify_drift_cached.__wrapped__(old, new, **direct_args)
    assert cached == uncached, f"language={language}: cached != uncached"


def test_unsupported_language_short_circuits_equal_to_uncached():
    """Unsupported language path returns verdict=uncertain with no
    signals — cache should mirror that behavior."""
    args = _make_args("body_a", "body_b", language="clojure")
    nbrs = frozenset(args["old_neighbors"])
    direct_args = dict(args, old_neighbors=nbrs, new_neighbors=nbrs)
    cached = classify_drift("body_a", "body_b", **args)
    uncached = _classify_drift_cached.__wrapped__("body_a", "body_b", **direct_args)
    assert cached == uncached
    assert cached.verdict == "uncertain"


def test_unsupported_language_does_not_touch_cache():
    """Unsupported-language guard (#515) lives in the public wrapper —
    classify_drift returns the sentinel WITHOUT a cache lookup. Pins
    "no SQLite roundtrip on a deterministic short-circuit." Regression
    test: if a future change moves the guard back inside the cached
    inner, this fails and forces explicit acknowledgement."""
    from codegenome._content_cache import default_cache

    cache = default_cache()
    entries_before = cache.stats()["entries"]

    # Three calls against different unsupported languages; if any of
    # them paid a cache lookup, the entry count would go up by 1 per
    # unique (body, body, lang) tuple.
    classify_drift(
        "body_a",
        "body_b",
        old_signature_hash=None,
        new_signature_hash=None,
        old_neighbors=None,
        new_neighbors=None,
        language="clojure",
    )
    classify_drift(
        "body_c",
        "body_d",
        old_signature_hash=None,
        new_signature_hash=None,
        old_neighbors=None,
        new_neighbors=None,
        language="ruby",
    )
    classify_drift(
        "body_e",
        "body_f",
        old_signature_hash=None,
        new_signature_hash=None,
        old_neighbors=None,
        new_neighbors=None,
        language="kotlin",
    )

    entries_after = cache.stats()["entries"]
    assert entries_after == entries_before, (
        f"unsupported-language calls touched the cache: "
        f"entries went from {entries_before} to {entries_after}. "
        f"The #515 guard must short-circuit before the cache lookup."
    )


def test_none_signature_and_neighbors_equivalent():
    """None inputs (no signature captured, no neighbors available) must
    flow through the cache unchanged."""
    args = _make_args(
        *_STRUCTURAL_PAIR,
        old_signature_hash=None,
        new_signature_hash=None,
        old_neighbors=None,
        new_neighbors=None,
    )
    direct_args = dict(args, old_neighbors=None, new_neighbors=None)
    cached = classify_drift(*_STRUCTURAL_PAIR, **args)
    uncached = _classify_drift_cached.__wrapped__(*_STRUCTURAL_PAIR, **direct_args)
    assert cached == uncached


# ── Cache participation ────────────────────────────────────────────────


def test_cache_participation_repeat_call_short_circuits(monkeypatch):
    """Second call with identical args must NOT re-invoke the inner
    function. If this fails, the decorator isn't wired (or the args
    differ in some non-obvious way that drifts the cache key)."""
    call_counter = {"n": 0}
    original = _classify_drift_cached.__wrapped__

    def tracking_inner(*args, **kwargs):
        call_counter["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "codegenome.drift_classifier._classify_drift_cached.__wrapped__",
        tracking_inner,
        raising=False,
    )
    # Note: monkeypatching __wrapped__ doesn't affect the closure inside
    # the decorator's wrapper. We need to patch the decorated function
    # itself. Use a different approach: count the underlying signal
    # helpers, which run on every cold call.
    inner_calls = {"n": 0}
    real_signal = None

    def tracking_signal(old_body, new_body, language):
        inner_calls["n"] += 1
        return real_signal(old_body, new_body, language)

    import codegenome.drift_classifier as dc

    real_signal = dc._signal_diff_lines
    monkeypatch.setattr(dc, "_signal_diff_lines", tracking_signal)

    args = _make_args(*_BIG_DIFF_PAIR)
    classify_drift(*_BIG_DIFF_PAIR, **args)
    n_after_first = inner_calls["n"]
    classify_drift(*_BIG_DIFF_PAIR, **args)
    n_after_second = inner_calls["n"]

    assert n_after_first == 1, f"first call should invoke once, got {n_after_first}"
    assert n_after_second == 1, (
        f"second call should hit cache (no new inner invocation), got {n_after_second} total calls"
    )


def test_cache_partitions_by_argument_change():
    """Different args → different cache entries → both invocations
    run. Pins the cache-key-on-args contract."""
    inner_calls = {"n": 0}
    import codegenome.drift_classifier as dc

    real_signal = dc._signal_diff_lines

    def tracking_signal(old_body, new_body, language):
        inner_calls["n"] += 1
        return real_signal(old_body, new_body, language)

    dc._signal_diff_lines = tracking_signal
    try:
        classify_drift(*_BIG_DIFF_PAIR, **_make_args(*_BIG_DIFF_PAIR))
        classify_drift(*_STRUCTURAL_PAIR, **_make_args(*_STRUCTURAL_PAIR))
        assert inner_calls["n"] == 2, (
            f"different args should miss cache, got {inner_calls['n']} calls"
        )
    finally:
        dc._signal_diff_lines = real_signal


# ── Normalization neutrality ───────────────────────────────────────────


def test_list_set_tuple_neighbors_all_hit_same_cache_entry():
    """List vs set vs tuple of the same elements → one cache entry.
    frozenset normalization inside the wrapper collapses input shape."""
    inner_calls = {"n": 0}
    import codegenome.drift_classifier as dc

    real_signal = dc._signal_diff_lines

    def tracking_signal(old_body, new_body, language):
        inner_calls["n"] += 1
        return real_signal(old_body, new_body, language)

    dc._signal_diff_lines = tracking_signal
    try:
        base = _make_args(*_BIG_DIFF_PAIR)
        # List
        classify_drift(
            *_BIG_DIFF_PAIR,
            **dict(base, old_neighbors=["a", "b", "c"], new_neighbors=["a", "b", "c"]),
        )
        # Tuple, different order
        classify_drift(
            *_BIG_DIFF_PAIR,
            **dict(base, old_neighbors=("c", "a", "b"), new_neighbors=("c", "a", "b")),
        )
        # Set
        classify_drift(
            *_BIG_DIFF_PAIR,
            **dict(base, old_neighbors={"b", "c", "a"}, new_neighbors={"b", "c", "a"}),
        )
        assert inner_calls["n"] == 1, (
            f"all three iterables should hit one cache entry, got {inner_calls['n']} calls"
        )
    finally:
        dc._signal_diff_lines = real_signal


# ── Cache key collision smoke ──────────────────────────────────────────


def test_different_languages_partition():
    """Same bodies in different languages → different cache entries.
    Language participates in the key per the cached function signature."""
    inner_calls = {"n": 0}
    import codegenome.drift_classifier as dc

    real_signal = dc._signal_diff_lines

    def tracking_signal(old_body, new_body, language):
        inner_calls["n"] += 1
        return real_signal(old_body, new_body, language)

    dc._signal_diff_lines = tracking_signal
    try:
        body_a = "function f() { return 1; }"
        body_b = "function f() { return 2; }"
        classify_drift(body_a, body_b, **_make_args(body_a, body_b, language="javascript"))
        classify_drift(body_a, body_b, **_make_args(body_a, body_b, language="typescript"))
        assert inner_calls["n"] == 2
    finally:
        dc._signal_diff_lines = real_signal
