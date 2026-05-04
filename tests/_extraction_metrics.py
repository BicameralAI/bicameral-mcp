"""Precision/recall metrics for the M1 decision-relevance eval.

Compares a skill-extraction output against a pregenerated ground-truth
fixture. Two matchers are available:

1. **LLM-as-judge** (Haiku 4.5 via ``_extraction_matcher.llm_match``) —
   default when ``ANTHROPIC_API_KEY`` is set. Handles paraphrase-equivalent
   decisions that share meaning but not token distribution.

2. **rapidfuzz** (``fuzz.token_set_ratio`` at threshold 70) — offline
   fallback for unit tests and for runs without network access. Brittle
   on paraphrases but deterministic and free.

CI defaults to ``matcher="auto"``, which picks LLM when the key is present
and rapidfuzz otherwise.

Fixture format (one JSON file per transcript):

    tests/fixtures/extraction/<source_ref>.json
    {
      "source_ref": "adv-strat-fake",
      "generated_by": "claude-opus-4-6-20251015",
      "generated_at": "2026-04-13T22:30:00Z",
      "decisions": [ {"description": "..."}, ... ],
      "action_items": [ {"text": "...", "owner": "..."}, ... ]
    }

Metric math (identical across matchers):

      true positives  = actuals paired with an expected
      false positives = actuals with no paired expected
      false negatives = expecteds with no paired actual
      precision       = TP / (TP + FP)
      recall          = TP / (TP + FN)
      f1              = 2 * P * R / (P + R)

Skipped (``{"skipped": True, ...}``) when the fixture file is absent, so
fixture-less transcripts don't break CI before the ground-truth set is
bootstrapped.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "extraction"
MATCH_THRESHOLD = 70  # rapidfuzz token_set_ratio, 0–100 scale


def fixture_path(source_ref: str) -> Path:
    return FIXTURES_DIR / f"{source_ref}.json"


def load_fixture(source_ref: str) -> dict | None:
    """Return the committed ground-truth fixture or None if absent."""
    p = fixture_path(source_ref)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _descs(items: list[dict]) -> list[str]:
    return [str(d.get("description", "")).strip() for d in items if d.get("description")]


def _rapidfuzz_match(actual: list[str], expected: list[str]) -> list[tuple[int, int | None]]:
    """Rapidfuzz 1:1 matching. Returns (actual_idx, expected_idx | None) pairs.

    For each actual in order, pick the best remaining expected by
    ``token_set_ratio``. Match if the score meets ``MATCH_THRESHOLD``.
    Deterministic, cheap, but brittle on paraphrase-equivalent decisions
    that share meaning but not token distribution.
    """
    unmatched_expected_idx = list(range(len(expected)))
    pairs: list[tuple[int, int | None]] = []
    for ai, a in enumerate(actual):
        if not unmatched_expected_idx:
            pairs.append((ai, None))
            continue
        best_slot = -1
        best_score = -1.0
        for slot, ei in enumerate(unmatched_expected_idx):
            score = fuzz.token_set_ratio(a, expected[ei])
            if score > best_score:
                best_score = score
                best_slot = slot
        if best_score >= MATCH_THRESHOLD:
            ei = unmatched_expected_idx.pop(best_slot)
            pairs.append((ai, ei))
        else:
            pairs.append((ai, None))
    return pairs


def _pick_matcher(matcher: str) -> str:
    """Resolve matcher='auto' to a concrete choice based on env.

    Returns "llm" when ``ANTHROPIC_API_KEY`` is set, otherwise "rapidfuzz".
    """
    if matcher != "auto":
        return matcher
    return "llm" if os.environ.get("ANTHROPIC_API_KEY", "").strip() else "rapidfuzz"


def compute_extraction_metrics(
    extracted_decisions: list[dict],
    fixture: dict | None,
    *,
    matcher: str = "auto",
) -> dict[str, Any]:
    """Match extracted decisions against fixture decisions and return metrics.

    Returns ``{"skipped": True, "reason": "no fixture"}`` when the fixture
    is absent, otherwise a dict with precision, recall, f1, tp/fp/fn counts,
    unmatched lists (useful for debugging and calibration), and the
    matcher identity used.

    Args:
        extracted_decisions: list of ``{"description": str}`` dicts from
            the current skill's extraction.
        fixture: dict loaded from ``fixtures/extraction/<source_ref>.json``
            with a ``decisions`` array. ``None`` triggers the skipped path.
        matcher: ``"auto"`` (default) picks LLM-as-judge when
            ``ANTHROPIC_API_KEY`` is set, else rapidfuzz. ``"llm"`` or
            ``"rapidfuzz"`` force a specific matcher (raises RuntimeError
            if LLM is requested without the key).
    """
    if fixture is None:
        return {"skipped": True, "reason": "no fixture"}

    actual = _descs(extracted_decisions)
    expected = _descs(fixture.get("decisions") or [])

    chosen = _pick_matcher(matcher)

    if chosen == "llm":
        # Import inside the function so offline tests that force
        # matcher="rapidfuzz" don't drag in httpx / network code.
        from _extraction_matcher import llm_match  # type: ignore[import-not-found]

        pairs = llm_match(actual, expected)
    elif chosen == "rapidfuzz":
        pairs = _rapidfuzz_match(actual, expected)
    else:
        raise ValueError(f"unknown matcher: {matcher!r}")

    matched_expected_idx = {ei for _, ei in pairs if ei is not None}
    unmatched_actual = [actual[ai] for ai, ei in pairs if ei is None]
    unmatched_expected = [
        expected[i] for i in range(len(expected)) if i not in matched_expected_idx
    ]

    tp = sum(1 for _, ei in pairs if ei is not None)
    fp = len(unmatched_actual)
    fn = len(unmatched_expected)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return {
        "skipped": False,
        "matcher": chosen,
        "fixture_decisions": len(expected),
        "extracted_decisions": len(actual),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "match_threshold": MATCH_THRESHOLD if chosen == "rapidfuzz" else None,
        "unmatched_actual": unmatched_actual,
        "unmatched_expected": unmatched_expected,
    }


def aggregate_extraction_metrics(per_transcript: list[dict]) -> dict[str, Any]:
    """Aggregate per-transcript metric dicts into repo/overall totals.

    Transcripts with ``skipped=True`` are excluded from aggregation. If every
    transcript is skipped, returns ``{"skipped": True, "reason": "..."}``.
    """
    scored = [r for r in per_transcript if not r.get("skipped", False)]
    if not scored:
        return {"skipped": True, "reason": "no scored transcripts"}

    tp = sum(r["true_positives"] for r in scored)
    fp = sum(r["false_positives"] for r in scored)
    fn = sum(r["false_negatives"] for r in scored)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return {
        "skipped": False,
        "scored_transcripts": len(scored),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }
