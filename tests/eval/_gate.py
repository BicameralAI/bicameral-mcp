"""Two-tier CI gate policy shared by the LLM-driven eval runners (#537).

Quality thresholds (recall / precision / abort-rate / fp-rate) are advisory in
``warn`` mode because the underlying metric is produced by a non-deterministic
caller LLM and flakes around the threshold. A separate *catastrophic floor*
hard-fails CI in any mode — it fires only when the metric collapses far below
the quality target, which signals a genuinely broken grounding / preflight path
rather than ordinary run-to-run variance.

Originating flake: M2 grounding-recall swung 0.783–0.957 on an identical ``main``
base across PRs #534 / #535 / #536 (a docs-only diff tripped the 0.80 hard gate).
See GitHub #537. This codifies the gating-as-observability doctrine: WARN + emit
on the quality signal, hard-fail only on a real lower-layer break.
"""

from __future__ import annotations

from collections.abc import Sequence


def gate_exit_code(
    *,
    quality_breaches: Sequence[str],
    catastrophic_breaches: Sequence[str],
    gate_mode: str,
) -> int:
    """Decide a CI exit code from two breach tiers.

    - A non-empty ``catastrophic_breaches`` always returns ``1`` (hard floor),
      regardless of ``gate_mode`` — a collapsed metric is a real failure.
    - ``quality_breaches`` returns ``1`` only when ``gate_mode == "hard"``,
      preserving the legacy opt-in hard-gate contract.
    - Otherwise returns ``0`` (clean, or a warn-only quality breach).
    """
    if catastrophic_breaches:
        return 1
    if quality_breaches and gate_mode == "hard":
        return 1
    return 0


def is_inconclusive(error_count: int, total: int, *, max_error_rate: float = 0.5) -> bool:
    """True when too many eval cases failed to *execute* (not failed to ground).

    A run where most cases erred — e.g. a missing API key or network outage on a
    CI re-run — zeroes the quality metric without producing any real signal. That
    is inconclusive, not catastrophic: the catastrophic floor (#537) must abstain
    on it rather than hard-fail, otherwise an auth/infra hiccup masquerades as a
    grounding collapse. A genuine collapse shows as low recall with the eval
    actually running (a low error rate).

    Returns True when ``total <= 0`` (nothing ran) or the error rate reaches
    ``max_error_rate`` (default 0.5).
    """
    if total <= 0:
        return True
    return (error_count / total) >= max_error_rate
