"""PII / secret / PHI / PAN catalog + detector for `bicameral.ingest`
content. Closes LLM-04 + HIPAA-01 + PCI-01 fold from
`docs/research-brief-compliance-audit-2026-05-06.md` § 2.4 + § 3.

Three classes, distinct detection semantics:

- ``secret``: pure regex (cloud-provider key prefixes, JWT shape,
  PEM private-key blocks). Tight prefixes bound false positives.
- ``phi``: regex with required label-adjacency (e.g. ``MRN:`` + digits).
  Bounds false positives by requiring the medical-context label;
  legitimate documentation containing a digit sequence alone doesn't
  match.
- ``pan``: regex finds candidate digit sequences (length 13-19);
  Python helpers validate Luhn checksum AND filter out sequences
  preceded by ID-class labels (``order_id:``, ``ref:``, etc.) within
  ``_PAN_CONTEXT_LOOKBACK`` chars of preceding context. Two-stage
  validation (regex → Python).

Reference: ``qor.scripts.prompt_injection_canaries`` ships qor-logic's
governance-markdown canary catalog; this module is the bicameral-mcp
runtime equivalent for *data leakage*. The canary catalog at
``handlers/canary_patterns.py`` covers prompt-injection — distinct
domain.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import NamedTuple

_SENSITIVE_CATALOG_VERSION = "v1"
"""Bump on catalog change. Operators reading refusal `detail` strings
attribute hits to a specific catalog generation; pattern_id semantics
are stable only within a given catalog version."""


class SensitiveHit(NamedTuple):
    """One match of a sensitive-data pattern against scanned content.

    ``cls`` is one of: ``secret``, ``phi``, ``pan``.
    ``pattern_id`` indexes into the per-class pattern tuple (or 0 for
    PAN since PAN has only one candidate regex). Stable within
    catalog version.
    ``match_excerpt`` is the first ``_EXCERPT_MAX`` chars of the
    matched substring. Secret-class excerpts are additionally
    body-redacted (prefix + asterisks + suffix) so the refusal
    ``detail`` field never carries a full credential.
    """

    cls: str
    pattern_id: int
    match_excerpt: str


_EXCERPT_MAX = 64
_PAN_CONTEXT_LOOKBACK = 30  # chars before candidate to scan for ID-class labels

# ── secret patterns ──────────────────────────────────────────────────
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "github-pat",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b"),
    ),
    (
        "azure-storage-key",
        re.compile(
            r"DefaultEndpointsProtocol=https;AccountName=[a-z0-9]+;"
            r"AccountKey=[A-Za-z0-9+/]{40,};"
        ),
    ),
    (
        "private-key-pem",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    ),
)

# ── phi patterns (require label-adjacency) ───────────────────────────
_PHI_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "mrn-with-label",
        re.compile(
            r"(?i)\b(?:mrn|medical\s+record(?:\s+number)?|patient\s+id)"
            r"\s*[:=]\s*\d{5,12}\b"
        ),
    ),
    (
        "phi-field-label",
        re.compile(
            r"(?i)\b(?:patient_(?:id|name|email)|date_of_birth|dob|ssn|"
            r"social_security(?:_number)?)\s*[:=]"
        ),
    ),
)

# ── pan candidate regex (validated post-match by Luhn + context) ─────
_PAN_CANDIDATE_RE = re.compile(r"\b\d{13,19}\b")
_PAN_CONTEXT_LABEL_RE = re.compile(
    r"(?i)\b(?:order_id|order|ref|ref_id|transaction_id|txn_id|"
    r"id|user_id|account_id|invoice_id|receipt)\s*[:=]\s*$"
)


def _luhn_valid(digits: str) -> bool:
    """Luhn checksum on a string of digits. Returns True if valid.

    Standard mod-10 algorithm: from rightmost digit, every second one
    (starting from the second-rightmost) is doubled; if doubling
    yields > 9, subtract 9; sum all digits; valid iff sum % 10 == 0.
    """
    total = 0
    parity = len(digits) % 2
    for i, char in enumerate(digits):
        n = int(char)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _is_id_preceded(content: str, start: int) -> bool:
    """True if the candidate digit sequence at ``start`` is preceded
    within ``_PAN_CONTEXT_LOOKBACK`` chars by an ID-class label.

    Prevents false-positives on `order_id: 1234567890123` shapes
    where the digits are an order-tracking ID, not cardholder data.
    Bounded lookback so a legitimate ``payment_value`` 50 chars
    after a far-back ``order_id`` doesn't spuriously match.
    """
    lookback_start = max(0, start - _PAN_CONTEXT_LOOKBACK)
    preceding = content[lookback_start:start]
    return _PAN_CONTEXT_LABEL_RE.search(preceding) is not None


def _redact_excerpt(cls: str, raw: str) -> str:
    """Truncate to ``_EXCERPT_MAX`` chars; for ``secret`` class, also
    replace the body with asterisks so the refusal ``detail`` field
    doesn't carry the full credential. (`AKIAIOSFODNN7EXAMPLE` →
    `AKIA****…**`.) PHI/PAN excerpts truncate-only — the matched
    field labels carry no extra disclosure."""
    truncated = raw[:_EXCERPT_MAX]
    if cls == "secret" and len(truncated) > 8:
        return truncated[:4] + "*" * (len(truncated) - 8) + truncated[-4:]
    return truncated


def detect_sensitive(content: str) -> list[SensitiveHit]:
    """Run every pattern across all three classes; return all hits.

    Empty list = clean. PAN class candidates pass through Luhn +
    label-context validation before becoming hits. Excerpts are
    redacted-to-shape (secret) or truncated-only (PHI/PAN).
    """
    hits: list[SensitiveHit] = []
    for idx, (_label, pattern) in enumerate(_SECRET_PATTERNS):
        for match in pattern.finditer(content):
            hits.append(
                SensitiveHit(
                    cls="secret",
                    pattern_id=idx,
                    match_excerpt=_redact_excerpt("secret", match.group(0)),
                )
            )
    for idx, (_label, pattern) in enumerate(_PHI_PATTERNS):
        for match in pattern.finditer(content):
            hits.append(
                SensitiveHit(
                    cls="phi",
                    pattern_id=idx,
                    match_excerpt=_redact_excerpt("phi", match.group(0)),
                )
            )
    for match in _PAN_CANDIDATE_RE.finditer(content):
        digits = match.group(0)
        if _is_id_preceded(content, match.start()):
            continue
        if not _luhn_valid(digits):
            continue
        hits.append(
            SensitiveHit(
                cls="pan",
                pattern_id=0,
                match_excerpt=_redact_excerpt("pan", digits),
            )
        )
    return hits


_sensitive_detect: Callable[[str], list[SensitiveHit]] = detect_sensitive
"""v2 extension surface: replace this module-level pointer with a
classifier-backed implementation when one ships. Single-line swap.
``handlers.ingest._check_sensitive`` always goes through this pointer
(locked by gate-test ``test_check_sensitive_invokes_function_pointer_not_direct_detect``).
"""
