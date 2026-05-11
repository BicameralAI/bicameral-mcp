"""Synthetic dataset for the M6 preflight retrieval recall eval (#58 Phase A).

Each row is an ``M6Case`` describing a developer's preflight call (topic
+ optional file_paths) plus the ground-truth decision that *should*
surface from the ledger. The seeder at ``tests/eval/_preflight_m6_seeder.py``
populates a fresh memory:// ledger with each case's intended decision
(applying realistic status + binding distributions); the runner drives
``handle_preflight`` and checks whether the intended decision_id appears
in ``response.decisions``.

Three miss-mode categories, balanced (8 / 8 / 9 = 25 total):

  vocabulary_mismatch — topic uses one vocabulary, decision uses another.
                        BM25 + search_hint should bridge this; eval
                        measures how well in practice.
  unbound_decision   — decision exists but has no ``binds_to`` edge
                        (status="ungrounded"). Region path skips; only
                        BM25 sees it via description.
  transitive_relevance — decision is bound to file X; developer names
                          file Y which depends on X. 1-hop graph
                          expansion (#174) should surface; eval measures
                          whether it does.

``GENERATOR_VERSION`` invalidates downstream caches when bumped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

GENERATOR_VERSION = "1"


@dataclass(frozen=True)
class M6Case:
    """One row in the M6 preflight retrieval fixture."""

    case_id: str
    miss_mode: str  # "vocabulary_mismatch" | "unbound_decision" | "transitive_relevance"
    topic: str  # what the developer types into bicameral.preflight
    intended_description: str  # ground-truth decision description, seeded into ledger
    file_paths: tuple[str, ...] = field(default_factory=tuple)
    # ↑ caller-supplied file_paths. Empty tuple = topic-only call (BM25 path).
    intended_file_path: str = ""
    intended_symbol: str = ""
    # ↑ for transitive cases: file the decision is BOUND to in the ledger
    #   (different from `file_paths` which is what the caller named).
    #   For vocab and unbound cases, left empty.
    decision_status: str = "ratified"
    # ↑ status to write into the synthetic ledger row. Default ratified
    #   (vocab + transitive cases). Unbound cases set to "ungrounded".
    source_type: str = "transcript"
    notes: str = ""  # human-readable notes for triage; not used by the runner


# ─── Category V: vocabulary mismatch ──────────────────────────────────────


CASES_VOCAB: list[M6Case] = [
    M6Case(
        case_id="V1_throttling_vs_rate_limit",
        miss_mode="vocabulary_mismatch",
        topic="implement throttling middleware for the checkout endpoints",
        intended_description=(
            "Apply rate limiting on /checkout/* endpoints — 100 req/min per tenant per the "
            "Enterprise SLA. Key on tenant_id from auth claims."
        ),
        notes="topic='throttling'; description='rate limiting' — common D→C vocab gap",
    ),
    M6Case(
        case_id="V2_retry_budget_vs_cap",
        miss_mode="vocabulary_mismatch",
        topic="set a retry budget for the payment webhook handler",
        intended_description=(
            "Cap retries on payment webhooks at 3 per Stripe contract — after 3 declines the "
            "webhook returns hard error to the caller."
        ),
        notes="'retry budget' vs 'cap retries at 3' — same concept, different vocab",
    ),
    M6Case(
        case_id="V3_circuit_breaker_vs_fail_fast",
        miss_mode="vocabulary_mismatch",
        topic="add a circuit breaker on the payment processor integration",
        intended_description=(
            "Fail fast on payment processor calls after 5 consecutive errors in 60s — open the "
            "breaker for 30s before retry. Avoids cascading timeouts during processor incidents."
        ),
        notes="'circuit breaker' is the implementation pattern; description says 'fail fast'",
    ),
    M6Case(
        case_id="V4_session_timeout_vs_idle_logout",
        miss_mode="vocabulary_mismatch",
        topic="reduce the session timeout to 15 minutes",
        intended_description=(
            "Log users out after 30 minutes of idle activity — required by SOC2 CC6.1 access "
            "controls. Absolute session cap remains 24h regardless of activity."
        ),
        notes="'session timeout' (dev-speak) vs 'log users out after 30min idle' (policy-speak)",
    ),
    M6Case(
        case_id="V5_body_size_limit_vs_payload_max",
        miss_mode="vocabulary_mismatch",
        topic="set a request body size limit on the API gateway",
        intended_description=(
            "Reject inbound payloads larger than 1 MB at the API gateway — protects downstream "
            "services from memory exhaustion attacks. Returns 413 Payload Too Large."
        ),
        notes="'body size limit' vs 'reject payloads larger than 1MB' — phrasing diverges",
    ),
    M6Case(
        case_id="V6_breadcrumbs_vs_telemetry",
        miss_mode="vocabulary_mismatch",
        topic="add telemetry on checkout step transitions",
        intended_description=(
            "Emit Sentry breadcrumbs on every checkout step transition (cart → address → "
            "payment → confirm) — supports postmortem reconstruction of abandoned-cart drop-offs."
        ),
        notes="'telemetry' is generic; 'breadcrumbs' is the specific Sentry primitive",
    ),
    M6Case(
        case_id="V7_jwt_rotation_vs_signing_key",
        miss_mode="vocabulary_mismatch",
        topic="how do we rotate JWTs",
        intended_description=(
            "Rotate the JWT signing key quarterly per NIST SP 800-57 key-management lifecycle. "
            "Old keys retained in the verifier set for 1 quarter to bridge in-flight tokens."
        ),
        notes="'rotate JWTs' (token-speak) vs 'signing key' (crypto-primitive-speak)",
    ),
    M6Case(
        case_id="V8_audit_log_vs_compliance_trail",
        miss_mode="vocabulary_mismatch",
        topic="implement compliance trail for admin actions",
        intended_description=(
            "Write a structured audit log row for every admin action (user-impersonate, "
            "data-export, billing-override) with actor_id + timestamp + payload hash. Retention "
            "90 days minimum per SOC2 CC7.2."
        ),
        notes="'compliance trail' (auditor-speak) vs 'audit log' (engineer-speak)",
    ),
]


# ─── Category U: unbound decision (status=ungrounded, no binds_to edge) ──


CASES_UNBOUND: list[M6Case] = [
    M6Case(
        case_id="U1_ship_soc2_session_storage",
        miss_mode="unbound_decision",
        topic="how do we plan to store session state for SOC2 compliance",
        intended_description=(
            "Ship SOC2-compliant server-side session storage by Q3 2026 — replaces the current "
            "stateless JWT-only model. Storage backend TBD; whatever we pick must support "
            "selective invalidation."
        ),
        decision_status="ungrounded",
        notes="strategic / behavioral — no code yet, region path will skip",
    ),
    M6Case(
        case_id="U2_decommission_legacy_auth",
        miss_mode="unbound_decision",
        topic="status of the legacy auth shim",
        intended_description=(
            "Decommission the legacy auth shim before EOY 2026 — all callers must migrate to "
            "the new OAuth2 flow by then. Tracking issue in #312."
        ),
        decision_status="ungrounded",
        notes="commitment with deadline; no code surface to bind to",
    ),
    M6Case(
        case_id="U3_activation_growth_target",
        miss_mode="unbound_decision",
        topic="what are we targeting for activation growth in the new tier",
        intended_description=(
            "20% week-over-week activation growth in the new pricing tier — measured as "
            "(week-N activations / week-N-1 activations) per tier_id over rolling 4-week window."
        ),
        decision_status="ungrounded",
        notes="metric/goal — code surface is the dashboard, not engineering",
    ),
    M6Case(
        case_id="U4_audit_log_retention",
        miss_mode="unbound_decision",
        topic="audit log retention policy",
        intended_description=(
            "Audit log retention is 90 days minimum, 1 year for security-tagged events. "
            "Per SOC2 CC7.2; verified annually by the auditor."
        ),
        decision_status="ungrounded",
        notes="policy — informs config, not bound to a specific symbol",
    ),
    M6Case(
        case_id="U5_pair_review_for_soc2",
        miss_mode="unbound_decision",
        topic="when do we require pair review",
        intended_description=(
            "PR-review etiquette: require pair-review (≥ 2 approvers) on any diff that touches "
            "SOC2-relevant surfaces — auth, audit log, data retention, secrets management."
        ),
        decision_status="ungrounded",
        notes="process — affects CODEOWNERS but not bindable to a single symbol",
    ),
    M6Case(
        case_id="U6_oncall_escalation_sla",
        miss_mode="unbound_decision",
        topic="oncall escalation SLA",
        intended_description=(
            "Incident escalation SLA: critical alerts page the primary oncall within 5 minutes; "
            "primary has 15 minutes to ack before automatic escalation to secondary."
        ),
        decision_status="ungrounded",
        notes="operations policy — config in pagerduty, not code",
    ),
    M6Case(
        case_id="U7_stripe_renewal",
        miss_mode="unbound_decision",
        topic="Stripe contract renewal",
        intended_description=(
            "Renegotiate Stripe enterprise pricing before Q2 2026 renewal — current contract "
            "expires 2026-06-30. Target: 30% volume discount or migration to a competing PSP."
        ),
        decision_status="ungrounded",
        notes="business — no code surface",
    ),
    M6Case(
        case_id="U8_data_residency",
        miss_mode="unbound_decision",
        topic="EU data residency commitments",
        intended_description=(
            "EU tenant data must reside in EU regions per GDPR Art. 44+ — applies to ledger, "
            "audit log, and backups. US replicas allowed for disaster recovery only with prior "
            "DPA in place."
        ),
        decision_status="ungrounded",
        notes="policy with regulatory driver — affects infra config, not a single code symbol",
    ),
]


# ─── Category T: transitive relevance ────────────────────────────────────


CASES_TRANSITIVE: list[M6Case] = [
    M6Case(
        case_id="T1_login_imports_jwt",
        miss_mode="transitive_relevance",
        topic="add MFA to the login handler",
        intended_description=(
            "JWT signing key rotation policy (quarterly per NIST SP 800-57). Old keys retained "
            "in verifier set for 1 quarter to bridge in-flight tokens."
        ),
        file_paths=("src/handlers/login.py",),
        intended_file_path="src/lib/auth/jwt.py",
        intended_symbol="rotate_signing_key",
        notes="login.py imports lib/auth/jwt.py — 1-hop expansion should surface the JWT decision",
    ),
    M6Case(
        case_id="T2_router_mounts_middleware",
        miss_mode="transitive_relevance",
        topic="reorder middleware chain in the API router",
        intended_description=(
            "Tenant rate limiter MUST run before auth in the middleware chain — protects auth "
            "from credential stuffing volume before any DB lookup. Order is load-bearing."
        ),
        file_paths=("src/server/router.py",),
        intended_file_path="src/middleware/rate_limit.py",
        intended_symbol="TenantRateLimiter.check",
        notes="router mounts rate_limit middleware — expansion should surface the order constraint",
    ),
    M6Case(
        case_id="T3_schema_calls_migrations",
        miss_mode="transitive_relevance",
        topic="add a new column to the orders table",
        intended_description=(
            "All schema migrations must be backward-compatible (additive only). Drop columns "
            "via two-deploy pattern: ignore in code, then drop in migration. Per the v0 zero-"
            "downtime commitment."
        ),
        file_paths=("src/db/schema.py",),
        intended_file_path="src/db/migrations.py",
        intended_symbol="apply_migration",
        notes="schema.py calls migrations.py — expansion should surface the backward-compat rule",
    ),
    M6Case(
        case_id="T4_cart_imports_payment",
        miss_mode="transitive_relevance",
        topic="refactor the cart checkout flow",
        intended_description=(
            "Idempotency on payment processor calls — every charge MUST include a unique "
            "idempotency_key derived from cart_id + version. Prevents double-charges on retries."
        ),
        file_paths=("src/checkout/cart.py",),
        intended_file_path="src/checkout/payment.py",
        intended_symbol="charge",
        notes="cart imports payment — idempotency decision lives on payment, applies to cart flow",
    ),
    M6Case(
        case_id="T5_sender_loads_templates",
        miss_mode="transitive_relevance",
        topic="add a new transactional email type",
        intended_description=(
            "Email template renders MUST go through the template registry — direct string "
            "concatenation is forbidden (XSS and i18n compliance). All templates listed in "
            "templates/MANIFEST.toml."
        ),
        file_paths=("src/email/sender.py",),
        intended_file_path="src/email/templates.py",
        intended_symbol="render_template",
        notes="sender imports templates.py — template-registry rule applies",
    ),
    M6Case(
        case_id="T6_session_store_uses_redis",
        miss_mode="transitive_relevance",
        topic="add session affinity for the new pricing tier",
        intended_description=(
            "Redis connection pool is shared process-wide (singleton). Per-handler instances "
            "are forbidden — they cause socket exhaustion under load. Config in REDIS_URL env."
        ),
        file_paths=("src/services/session_store.py",),
        intended_file_path="src/cache/redis_pool.py",
        intended_symbol="get_pool",
        notes="session_store uses redis_pool — pool-singleton rule applies",
    ),
    M6Case(
        case_id="T7_endpoints_import_serializers",
        miss_mode="transitive_relevance",
        topic="add a new fields to the orders API response",
        intended_description=(
            "API serializers MUST strip internal-only fields (audit_metadata, raw_payment_data, "
            "internal_notes) before serializing for external clients. Whitelist enforced at the "
            "serializer layer."
        ),
        file_paths=("src/api/endpoints.py",),
        intended_file_path="src/api/serializers.py",
        intended_symbol="OrderSerializer",
        notes="endpoints import serializers — internal-field-strip rule applies",
    ),
    M6Case(
        case_id="T8_tokens_use_crypto",
        miss_mode="transitive_relevance",
        topic="add a new token type for the partner integration",
        intended_description=(
            "All cryptographic primitives go through src/utils/crypto — never use stdlib hashlib "
            "directly. Crypto module enforces constant-time comparison and approved algorithm set "
            "(SHA-256, HMAC-SHA-256, AES-256-GCM only)."
        ),
        file_paths=("src/auth/tokens.py",),
        intended_file_path="src/utils/crypto.py",
        intended_symbol="hmac_sha256",
        notes="tokens.py imports crypto — crypto-primitives rule applies",
    ),
    M6Case(
        case_id="T9_email_worker_imports_dispatcher",
        miss_mode="transitive_relevance",
        topic="add retry logic to the email worker",
        intended_description=(
            "Queue dispatcher uses exponential backoff with jitter (base=2s, max=60s, jitter=±20%) "
            "for all worker types. Worker-specific retry logic is forbidden — must use the "
            "dispatcher's retry policy."
        ),
        file_paths=("src/workers/email_worker.py",),
        intended_file_path="src/queue/dispatcher.py",
        intended_symbol="enqueue_with_retry",
        notes="worker imports dispatcher — exp-backoff rule applies",
    ),
]


ALL_CASES: list[M6Case] = CASES_VOCAB + CASES_UNBOUND + CASES_TRANSITIVE


def cases_by_miss_mode(miss_mode: str) -> list[M6Case]:
    return [c for c in ALL_CASES if c.miss_mode == miss_mode]


def case_by_id(case_id: str) -> M6Case:
    for c in ALL_CASES:
        if c.case_id == case_id:
            return c
    raise KeyError(f"unknown M6 case_id: {case_id}")


# Sanity check at import time — fail loud if the dataset shape regresses.
def _validate_dataset() -> None:
    seen_ids: set[str] = set()
    valid_miss_modes = {"vocabulary_mismatch", "unbound_decision", "transitive_relevance"}
    for c in ALL_CASES:
        if c.case_id in seen_ids:
            raise AssertionError(f"duplicate M6 case_id: {c.case_id}")
        seen_ids.add(c.case_id)
        if c.miss_mode not in valid_miss_modes:
            raise AssertionError(f"{c.case_id}: invalid miss_mode {c.miss_mode!r}")
        if not c.topic.strip() or not c.intended_description.strip():
            raise AssertionError(f"{c.case_id}: topic/intended_description must be non-empty")
        if c.miss_mode == "transitive_relevance":
            if not c.file_paths or not c.intended_file_path:
                raise AssertionError(
                    f"{c.case_id}: transitive cases must have file_paths AND intended_file_path"
                )
            if c.intended_file_path in c.file_paths:
                raise AssertionError(
                    f"{c.case_id}: caller's file_paths cannot include the intended_file_path "
                    "(that would be a direct pin, not transitive)"
                )
        if c.miss_mode == "unbound_decision" and c.decision_status != "ungrounded":
            raise AssertionError(
                f"{c.case_id}: unbound_decision cases must have decision_status='ungrounded'"
            )


_validate_dataset()
