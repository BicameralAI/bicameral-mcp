"""Deterministic structural drift classifier.

Phase 4 (#61) — issue-mandated weighted scoring:

  signature_unchanged   * 0.30
  neighbors_jaccard     * 0.25
  diff_lines_cosmetic   * 0.30
  no_new_calls          * 0.15

Score >= 0.80  → ``cosmetic``  (auto-resolve as semantically_preserved)
Score <= 0.30  → ``semantic``  (emit PendingComplianceCheck normally)
otherwise      → ``uncertain`` (emit with pre_classification hint)

No LLM. No embeddings. Purely structural.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

from .continuity import _jaccard
from .diff_categorizer import categorize_diff
from code_locator.indexing.call_site_extractor import extract_call_sites


# ── Constants pinned by issue #61 ────────────────────────────────────

_W_SIGNATURE_UNCHANGED = 0.30
_W_NEIGHBORS_JACCARD = 0.25
_W_DIFF_LINES_COSMETIC = 0.30
_W_NO_NEW_CALLS = 0.15

_T_COSMETIC = 0.80
_T_SEMANTIC = 0.30

_SUPPORTED_LANGUAGES = frozenset({
    "python", "javascript", "typescript", "go", "rust", "java", "c_sharp",
})


@dataclass(frozen=True)
class DriftClassification:
    """Outcome of one drift-classification call.

    ``verdict`` partitions the score line:
      - ``cosmetic``: score >= 0.80; the change is structurally
        whitespace/comment/docstring-only and the binding can
        auto-resolve as ``semantically_preserved``.
      - ``semantic``: score <= 0.30; clear semantic change, no hint
        needed.
      - ``uncertain``: score in [0.30, 0.80) OR unsupported language;
        emit the pending check with a ``pre_classification`` hint so
        the caller LLM has structured evidence.
    """
    verdict: Literal["cosmetic", "semantic", "uncertain"]
    confidence: float
    signals: dict[str, float]
    evidence_refs: list[str] = field(default_factory=list)


# ── Per-signal helpers (each ≤ 30 lines) ──────────────────────────────


def _signal_signature(old: str | None, new: str | None) -> float:
    """1.0 if both non-None and equal; 0.5 if either None; 0.0 if differ."""
    if old is None or new is None:
        return 0.5
    return 1.0 if old == new else 0.0


def _signal_neighbors(
    old: Iterable[str] | None, new: Iterable[str] | None,
) -> float:
    """Jaccard of neighbor address sets, with the issue-mandated 0.95
    threshold acting as a step function over the raw ratio.

    ``0.0`` if either input is ``None`` (no signal). The Phase 3
    matcher's threshold is 0.95 — values >= 0.95 vote "cosmetic"
    fully; below is graded by the raw ratio.
    """
    if old is None or new is None:
        return 0.0
    raw = _jaccard(old, new)
    return 1.0 if raw >= 0.95 else raw


def _signal_diff_lines(
    old_body: str, new_body: str, language: str,
) -> float:
    """Ratio of changed cosmetic lines (comment + docstring + blank)
    to total changed lines. Returns 1.0 if no lines changed (no diff
    = trivially cosmetic). Returns 0.5 if ``categorize_diff`` raises
    (degraded extraction)."""
    try:
        stats = categorize_diff(old_body, new_body, language)
    except Exception:
        return 0.5
    if stats.total == 0:
        return 1.0
    return stats.cosmetic_ratio


def _signal_no_new_calls(
    old_body: str, new_body: str, language: str,
) -> float:
    """1.0 if call set in ``new`` ⊆ call set in ``old`` (no new
    callees introduced, including the trivial ``set() ⊆ set()`` case
    when both functions make no calls).

    0.0 if a new callee appears in ``new`` that wasn't in ``old``.

    0.5 when the language is unsupported (extractor returns empty for
    both sides regardless of content) — the classifier downgrades to
    'uncertain' rather than asserting cosmetic on extraction failure.
    """
    if language not in (
        "python", "javascript", "typescript", "go", "rust", "java", "c_sharp",
    ):
        return 0.5
    new_calls = extract_call_sites(new_body, language)
    old_calls = extract_call_sites(old_body, language)
    return 1.0 if new_calls.issubset(old_calls) else 0.0


# ── Verdict + evidence helpers ───────────────────────────────────────


def _verdict_from_score(
    score: float,
) -> Literal["cosmetic", "semantic", "uncertain"]:
    if score >= _T_COSMETIC:
        return "cosmetic"
    if score <= _T_SEMANTIC:
        return "semantic"
    return "uncertain"


def _build_evidence_refs(
    signals: dict[str, float], score: float,
) -> list[str]:
    """Free-form audit-trail strings round-tripped to
    ``compliance_check.evidence_refs``."""
    refs = [f"score:{score:.3f}"]
    for name, value in signals.items():
        refs.append(f"{name}:{value:.2f}")
    return refs


# ── Public entry point (≤ 40 lines per Section 4 razor) ──────────────


def classify_drift(
    old_body: str,
    new_body: str,
    *,
    old_signature_hash: str | None,
    new_signature_hash: str | None,
    old_neighbors: Iterable[str] | None,
    new_neighbors: Iterable[str] | None,
    language: str,
) -> DriftClassification:
    """Deterministic structural drift classifier.

    Unsupported languages return ``verdict='uncertain'`` so the caller
    LLM still sees the pending check (just without a meaningful hint).
    """
    if language not in _SUPPORTED_LANGUAGES:
        return DriftClassification(
            verdict="uncertain", confidence=0.0,
            signals={},
            evidence_refs=[f"language:unsupported:{language}"],
        )
    signals = {
        "signature": _signal_signature(old_signature_hash, new_signature_hash),
        "neighbors": _signal_neighbors(old_neighbors, new_neighbors),
        "diff_lines": _signal_diff_lines(old_body, new_body, language),
        "no_new_calls": _signal_no_new_calls(old_body, new_body, language),
    }
    score = (
        signals["signature"] * _W_SIGNATURE_UNCHANGED
        + signals["neighbors"] * _W_NEIGHBORS_JACCARD
        + signals["diff_lines"] * _W_DIFF_LINES_COSMETIC
        + signals["no_new_calls"] * _W_NO_NEW_CALLS
    )
    verdict = _verdict_from_score(score)
    return DriftClassification(
        verdict=verdict, confidence=score, signals=signals,
        evidence_refs=_build_evidence_refs(signals, score),
    )
