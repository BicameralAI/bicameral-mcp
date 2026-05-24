"""Snapshot test pinning the canonical cache-key fingerprint (#136).

The point of this test: when someone edits ``_cache_key`` or
``_canonical`` and accidentally changes the serialization shape, *every*
cached entry in every deployed bicameral install silently invalidates on
the next decorator call. No error, no log, just stale-feeling slowness
that nobody traces back to the serialization change.

This test pins a canonical input → key mapping. If the key drifts, this
test fails loudly and forces the editor to:

1. Verify the drift was intentional.
2. Bump ``behavior_version`` on every decorated function (so existing
   cached entries get treated as version-mismatch misses).
3. Update the expected fingerprint here.

Coverage shape mirrors the actual inputs the decorated functions will
receive — string bodies, signature hash tuples, neighbor frozensets,
language enums. Adding a new decorated function shape should add a row
here too.
"""

from __future__ import annotations

from codegenome._content_cache import _cache_key

# ── Pinned fingerprints ────────────────────────────────────────────────
#
# Format: each entry is (test_name, fn_name, behavior_version, args,
# kwargs, expected_key). The expected_key was captured by running
# _cache_key with the listed inputs; do not edit unless the underlying
# canonicalization actually changed.


def test_fingerprint_simple_string_arg():
    """Baseline: single string positional arg."""
    key = _cache_key("module.fn", 1, ("hello",), {})
    assert key == "826a04a44db5236274e72bc3f133fc0d"


def test_fingerprint_classify_drift_shape():
    """Shape matching classify_drift: two bodies + signature hashes +
    neighbor frozensets + language. Exercise the most complex canonical
    shape we will actually deploy."""
    key = _cache_key(
        "codegenome.drift_classifier.classify_drift",
        1,
        ("def old():\n    pass\n", "def new():\n    pass\n"),
        {
            "old_signature_hash": "abc123",
            "new_signature_hash": "abc123",
            "old_neighbors": frozenset({"helper_a", "helper_b"}),
            "new_neighbors": frozenset({"helper_a", "helper_b"}),
            "language": "python",
        },
    )
    assert key == "d19449e2604fbf348a41f94ab9902274"


def test_fingerprint_none_signature_hashes():
    """``None`` is a valid value for signature hashes and neighbors —
    the classifier handles it as a no-signal sentinel. The fingerprint
    must distinguish None from missing-arg."""
    key = _cache_key(
        "codegenome.drift_classifier.classify_drift",
        1,
        ("body_a", "body_b"),
        {
            "old_signature_hash": None,
            "new_signature_hash": None,
            "old_neighbors": None,
            "new_neighbors": None,
            "language": "python",
        },
    )
    assert key == "fbae36612b4ebc9185e24f1256a99f84"


def test_fingerprint_diff_categorize_shape():
    """Shape matching categorize_diff: two bodies + language."""
    key = _cache_key(
        "codegenome.diff_categorizer.categorize_diff",
        1,
        ("x = 1\n", "x = 2\n", "python"),
        {},
    )
    assert key == "bde77e43c38c07c206acb51bee1a2266"


def test_fingerprint_kwarg_order_irrelevant():
    """Two calls with the same kwargs in different insertion order MUST
    produce the same key (canonicalization sorts by key)."""
    key_a = _cache_key("fn", 1, (), {"a": 1, "b": 2, "c": 3})
    key_b = _cache_key("fn", 1, (), {"c": 3, "a": 1, "b": 2})
    assert key_a == key_b


def test_fingerprint_version_bump_changes_key():
    """v1 and v2 must produce different keys even when args are
    identical. This is the primary invalidation lever for shipping
    logic changes to a decorated function."""
    key_v1 = _cache_key("fn", 1, ("same",), {})
    key_v2 = _cache_key("fn", 2, ("same",), {})
    assert key_v1 != key_v2


def test_fingerprint_function_name_changes_key():
    """Renaming a decorated function (or refactoring it into a different
    module) invalidates its cache namespace. This is correct behavior —
    the cache key is scoped to the fully-qualified name."""
    key_a = _cache_key("module_a.fn", 1, ("x",), {})
    key_b = _cache_key("module_b.fn", 1, ("x",), {})
    assert key_a != key_b
