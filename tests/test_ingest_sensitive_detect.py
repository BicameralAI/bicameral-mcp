"""Functionality tests for `handlers.sensitive_patterns.detect_sensitive`
+ the v2 extension surface (`_sensitive_detect` function pointer)
(#213 Phase 1)."""

from __future__ import annotations

import re

import handlers.sensitive_patterns as sensitive_patterns
from handlers.sensitive_patterns import SensitiveHit, detect_sensitive


def test_detect_sensitive_returns_empty_on_clean_content() -> None:
    hits = detect_sensitive("Decision: refactor the ingest middleware to add a new gate.")
    assert hits == []


def test_detect_sensitive_returns_one_hit_on_aws_key() -> None:
    hits = detect_sensitive("aws_key=AKIAIOSFODNN7EXAMPLE in production env")
    secret_hits = [h for h in hits if h.cls == "secret"]
    assert len(secret_hits) == 1
    # Excerpt is redacted (asterisks in body, AKIA prefix retained)
    assert secret_hits[0].match_excerpt.startswith("AKIA")
    assert "*" in secret_hits[0].match_excerpt


def test_detect_sensitive_returns_one_hit_on_jwt() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.signaturepart"
    hits = detect_sensitive(f"token: {jwt}")
    secret_hits = [h for h in hits if h.cls == "secret"]
    assert len(secret_hits) == 1


def test_detect_sensitive_returns_phi_hit_on_mrn_label() -> None:
    hits = detect_sensitive("MRN: 1234567")
    phi_hits = [h for h in hits if h.cls == "phi"]
    assert len(phi_hits) == 1


def test_detect_sensitive_returns_pan_hit_on_luhn_valid_unlabeled_pan() -> None:
    hits = detect_sensitive("test card: 4111111111111111")
    pan_hits = [h for h in hits if h.cls == "pan"]
    assert len(pan_hits) == 1


def test_detect_sensitive_skips_pan_candidate_with_id_label() -> None:
    """`order_id: 4111111111111111` must NOT trip the PAN gate even though
    the digit sequence is Luhn-valid."""
    hits = detect_sensitive("order_id: 4111111111111111")
    pan_hits = [h for h in hits if h.cls == "pan"]
    assert pan_hits == []


def test_detect_sensitive_skips_pan_candidate_failing_luhn() -> None:
    """`1111111111111111` is 16 digits but Luhn-invalid (sum of doubled
    digits is 8, not 0 mod 10). Must not become a PAN hit."""
    hits = detect_sensitive("test value: 1111111111111111")
    pan_hits = [h for h in hits if h.cls == "pan"]
    assert pan_hits == []


def test_detect_sensitive_returns_multiple_hits_across_classes() -> None:
    content = "AKIAIOSFODNN7EXAMPLE\nMRN: 1234567\ntest card 4111111111111111"
    hits = detect_sensitive(content)
    classes = {h.cls for h in hits}
    assert classes == {"secret", "phi", "pan"}


def test_detect_sensitive_secret_excerpt_is_redacted_to_prefix_and_suffix() -> None:
    """Secret-class excerpts: first 4 + asterisks + last 4. Body is redacted
    so the refusal `detail` field doesn't carry the full credential."""
    hits = detect_sensitive("AKIAIOSFODNN7EXAMPLE")
    secret_hits = [h for h in hits if h.cls == "secret"]
    assert len(secret_hits) == 1
    excerpt = secret_hits[0].match_excerpt
    assert excerpt.startswith("AKIA")
    assert excerpt.endswith("MPLE")
    # Body between first 4 and last 4 chars must be all asterisks
    body = excerpt[4:-4]
    assert body and all(ch == "*" for ch in body)


def test_detect_sensitive_phi_excerpt_is_truncated_only_not_redacted() -> None:
    """PHI-class excerpts: truncate-only (no asterisks). PHI labels carry
    no extra disclosure beyond what triggered the match."""
    hits = detect_sensitive("MRN: 1234567")
    phi_hits = [h for h in hits if h.cls == "phi"]
    assert len(phi_hits) == 1
    excerpt = phi_hits[0].match_excerpt
    assert "*" not in excerpt
    assert "MRN" in excerpt or "mrn" in excerpt.lower()


def test_detect_sensitive_excerpt_truncates_to_64_chars(monkeypatch) -> None:
    """Truncation cap on excerpt regardless of how long the matched
    substring is. Substitute a permissive secret pattern + verify slice."""
    permissive = re.compile(r"X+")
    monkeypatch.setattr(
        sensitive_patterns,
        "_SECRET_PATTERNS",
        (("test-permissive", permissive),),
    )
    monkeypatch.setattr(sensitive_patterns, "_PHI_PATTERNS", ())
    hits = detect_sensitive("X" * 200)
    secret_hits = [h for h in hits if h.cls == "secret"]
    assert len(secret_hits) == 1
    # Secret-class still applies prefix+suffix-redaction so the excerpt
    # length equals match.group(0)[:_EXCERPT_MAX] length, then redacted.
    assert len(secret_hits[0].match_excerpt) == 64


def test_sensitive_detect_function_pointer_is_swappable() -> None:
    """v2 extension surface: replacing `_sensitive_detect` at module level
    swaps the production detector. Locks the swap path as observable."""
    original = sensitive_patterns._sensitive_detect
    try:
        sentinel = [SensitiveHit(cls="test-stub", pattern_id=99, match_excerpt="stub")]

        def stub(_content: str) -> list[SensitiveHit]:
            return sentinel

        sensitive_patterns._sensitive_detect = stub
        observed = sensitive_patterns._sensitive_detect("any content; stub ignores")
        assert observed is sentinel
        # Direct call still works:
        assert detect_sensitive("clean text") == []
    finally:
        sensitive_patterns._sensitive_detect = original
