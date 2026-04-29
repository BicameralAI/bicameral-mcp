"""Confidence fusion helpers used across CodeGenome phases."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# Default weights for the confidence model defined in the architecture
# plan; referenced by Phase 3+4 callers (continuity, drift classifier).
# Lives here so future phases import from one place without restructuring.
DEFAULT_CONFIDENCE_WEIGHTS: dict[str, float] = {
    "subject_resolution": 0.25,
    "structural_identity": 0.20,
    "content_similarity": 0.15,
    "call_graph_similarity": 0.15,
    "test_support": 0.15,
    "runtime_support": 0.10,
}


def noisy_or(confidences: Iterable[float]) -> float:
    """Fuse independent supporting confidences via noisy-OR.

    Each input clamped to [0, 1]. Result is ``1 - ∏(1 - cᵢ)`` — the
    probability at least one independent signal is correct.

    Examples:
        >>> round(noisy_or([0.7, 0.7]), 2)
        0.91
        >>> noisy_or([])
        0.0
    """
    product = 1.0
    saw_any = False
    for confidence in confidences:
        saw_any = True
        bounded = max(0.0, min(1.0, confidence))
        product *= 1.0 - bounded
    if not saw_any:
        return 0.0
    return 1.0 - product


def weighted_average(
    signals: Mapping[str, float],
    weights: Mapping[str, float],
) -> float:
    """Weighted-average ``signals`` by ``weights``.

    Keys missing from ``weights`` contribute zero weight (and so are
    dropped). Zero total weight returns ``0.0`` (not NaN).
    """
    total_weight = 0.0
    total = 0.0
    for key, value in signals.items():
        weight = weights.get(key, 0.0)
        total += value * weight
        total_weight += weight
    if total_weight == 0.0:
        return 0.0
    return total / total_weight
