"""Deterministic synthetic ledger generator for cost/latency baseline tests.

Produces a dict shaped like ``bicameral.history()``'s ``HistoryResponse`` output
(see ``contracts.HistoryResponse``). Same generator output for a given
``(n_features, decisions_per_feature, seed)`` triple — bumping
``GENERATOR_VERSION`` invalidates all baselines so a content drift in this file
forces explicit re-recording.

Realism trade-off: feature names and decision summaries are drawn from a small
fixed corpus and parameterized by index, so the payload feels plausible (not
"lorem ipsum") but generation stays deterministic and zero-network.
"""
from __future__ import annotations

import random


GENERATOR_VERSION = "1"


_FEATURE_NAMES: list[str] = [
    "auth", "billing", "payments", "logging", "audit", "search", "api",
    "webhooks", "retention", "indexing", "ingestion", "drift-detection",
    "ratification", "rate-limiting", "caching", "locking", "dedup", "ttl",
    "sync", "scheduling",
]


_DECISION_TEMPLATES: list[tuple[str, str]] = [
    (
        "Customers cannot be billed twice for the same order in {feature}",
        "duplicate-prevention guarantee enforced by SETNX with 24h TTL",
    ),
    (
        "{feature} tokens expire after 60 minutes; refresh requires re-authentication",
        "shorter than industry-standard but tightens security envelope",
    ),
    (
        "All {feature} events are deduplicated via Redis SETNX with 24h TTL",
        "idempotency key generated from request body hash",
    ),
    (
        "PII in {feature} logs must be redacted before storage",
        "post-processor strips email, phone, SSN, credit-card patterns",
    ),
    (
        "{feature} compliance trail captures every admin action with actor + timestamp",
        "audit log immutable; SOC2 compliance trail required",
    ),
    (
        "Permission checks for {feature} always run server-side; clients never assume their grant set",
        "defense-in-depth — assume client cannot be trusted",
    ),
    (
        "Pro-rate {feature} refunds on plan downgrade",
        "billing reconciliation with proration based on days remaining",
    ),
    (
        "Webhook payloads for {feature} use snake_case JSON; timestamps in ISO 8601 UTC",
        "consistent across consumer integrations; deviation breaks downstream parsing",
    ),
    (
        "{feature} retries follow exponential backoff with jitter, max 5 attempts",
        "1s, 2s, 4s, 8s, 16s with plus or minus 25% jitter; gives up after 5",
    ),
    (
        "{feature} session cookies expire after 12 hours of inactivity",
        "balances UX continuity against unauthorized-access risk",
    ),
]


_STATUS_DISTRIBUTION: list[tuple[str, float]] = [
    ("reflected", 0.70),
    ("drifted", 0.20),
    ("pending", 0.05),
    ("ungrounded", 0.05),
]


def _pick_status(rng: random.Random) -> str:
    r = rng.random()
    cumulative = 0.0
    for status, weight in _STATUS_DISTRIBUTION:
        cumulative += weight
        if r < cumulative:
            return status
    return "reflected"


def _feature_id(index: int) -> str:
    base = _FEATURE_NAMES[index % len(_FEATURE_NAMES)]
    bucket = index // len(_FEATURE_NAMES)
    return base if bucket == 0 else f"{base}-{bucket}"


def _make_decision(
    rng: random.Random,
    feature_id: str,
    decision_index: int,
) -> dict:
    template_summary, template_quote = _DECISION_TEMPLATES[
        decision_index % len(_DECISION_TEMPLATES)
    ]
    summary = template_summary.format(feature=feature_id)
    quote = template_quote
    status = _pick_status(rng)

    decision: dict = {
        "id": f"decision:{feature_id}-{decision_index}",
        "summary": summary,
        "featureId": feature_id,
        "status": status,
        "signoff_state": None,
        "sources": [
            {
                "source_ref": f"sprint-{rng.randint(1, 30)}-planning",
                "source_type": "transcript",
                "date": f"2026-{rng.randint(1, 4):02d}-{rng.randint(1, 28):02d}",
                "speaker": None,
                "quote": quote,
            },
        ],
        "fulfillments": [],
        "drift_evidence": None,
        "signoff": None,
        "decision_level": None,
        "parent_decision_id": None,
        "ephemeral": False,
    }

    if status in {"reflected", "drifted"}:
        baseline_hash = f"{decision_index:064x}"[-64:]
        current_hash = baseline_hash if status == "reflected" else f"{decision_index + 1:064x}"[-64:]
        decision["fulfillments"] = [
            {
                "file_path": f"{feature_id}/handler_{decision_index}.py",
                "symbol": f"{feature_id}_handler_{decision_index}",
                "start_line": rng.randint(1, 50),
                "end_line": rng.randint(60, 200),
                "git_url": None,
                "grounded_at_ref": "HEAD",
                "baseline_hash": baseline_hash,
                "current_hash": current_hash,
            },
        ]

    if status == "drifted":
        decision["drift_evidence"] = (
            f"Symbol moved or signature changed in {feature_id} module since baseline; "
            f"current implementation does not match the {decision_index}-th committed version."
        )

    return decision


def generate_ledger(
    n_features: int,
    decisions_per_feature: int = 3,
    seed: int = 42,
) -> dict:
    """Deterministic synthetic HistoryResponse-shaped dict.

    Args:
        n_features: Number of feature groups to generate.
        decisions_per_feature: Decisions per feature (default 3).
        seed: PRNG seed; same seed → same output across runs.

    Returns:
        Dict matching ``contracts.HistoryResponse`` shape, plus a private
        ``_generator_version`` field for baseline-cache invalidation. Token
        counts are taken on the JSON serialization of this dict.
    """
    if n_features < 0:
        raise ValueError(f"n_features must be >= 0, got {n_features}")
    if decisions_per_feature < 0:
        raise ValueError(
            f"decisions_per_feature must be >= 0, got {decisions_per_feature}"
        )

    rng = random.Random(seed)

    features: list[dict] = []
    for i in range(n_features):
        feature_id = _feature_id(i)
        decisions = [
            _make_decision(rng, feature_id, j)
            for j in range(decisions_per_feature)
        ]
        features.append({
            "id": feature_id,
            "name": feature_id.replace("-", " ").title(),
            "decisions": decisions,
        })

    return {
        "features": features,
        "truncated": False,
        "total_features": n_features,
        "as_of": "HEAD",
        "sync_metrics": None,
        "_generator_version": GENERATOR_VERSION,
    }
