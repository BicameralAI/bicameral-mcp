"""Synthetic grounding-recall dataset for the M2 caller-LLM eval (#280).

Each row is a `GroundingCase` describing a decision text plus the
ground-truth (file, symbol) it should bind to. The fixture repo at
``tests/fixtures/grounding_recall/repo/`` contains the intended symbols
plus deliberate distractors; the LLM is given access to the repo and
must pick the right symbol to clear the precision gate.

`GENERATOR_VERSION` invalidates the eval cache when bumped — change the
value if a row is added/edited/removed so the next CI run re-records.
"""

from __future__ import annotations

from dataclasses import dataclass, field

GENERATOR_VERSION = "1"


@dataclass(frozen=True)
class GroundingCase:
    """One row in the M2 grounding-recall fixture."""

    case_id: str
    case_type: str  # "same_name_different_module" | "similar_intent" | "cross_language"
    description: str  # the decision text the LLM reads
    intended_file: str  # ground-truth file (relative to repo root)
    intended_symbol: str  # ground-truth symbol name
    distractors: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # ↑ list of (file, symbol) plausible-but-wrong candidates that exist
    #   in the fixture repo. Used by the runner to verify the wrong-bind
    #   path actually has somewhere to land (i.e. the LLM could plausibly
    #   pick a distractor — measures real precision, not "no other option").


# ─── Case A: same-name-different-module ─────────────────────────────────────

CASES_A: list[GroundingCase] = [
    GroundingCase(
        case_id="A1_process_order_checkout",
        case_type="same_name_different_module",
        description=(
            "Customer checkout flow caps payment retries at 3 per Stripe contract — "
            "after 3 declines the user sees a hard error and the cart unlocks."
        ),
        intended_file="src/checkout/orders.py",
        intended_symbol="process_order",
        distractors=(
            ("src/admin/orders.py", "process_order"),
            ("src/billing/refunds.py", "process_order"),
        ),
    ),
    GroundingCase(
        case_id="A2_process_order_admin",
        case_type="same_name_different_module",
        description=(
            "Support team manually replays finance-flagged orders through the "
            "refund pipeline — runs under elevated permissions, never touches "
            "the customer payment-auth path."
        ),
        intended_file="src/admin/orders.py",
        intended_symbol="process_order",
        distractors=(
            ("src/checkout/orders.py", "process_order"),
            ("src/billing/refunds.py", "process_order"),
        ),
    ),
    GroundingCase(
        case_id="A3_process_order_billing",
        case_type="same_name_different_module",
        description=(
            "Bulk-refund batch job + chargeback webhook handler push refund "
            "requests through the billing pipeline (credit-to-source + "
            "accounting reconciliation). Distinct from manual admin replay "
            "and from customer checkout."
        ),
        intended_file="src/billing/refunds.py",
        intended_symbol="process_order",
        distractors=(
            ("src/checkout/orders.py", "process_order"),
            ("src/admin/orders.py", "process_order"),
        ),
    ),
    GroundingCase(
        case_id="A4_cancel_order_checkout",
        case_type="same_name_different_module",
        description=(
            "User-initiated order cancellation from the storefront — refunds "
            "within the 24-hour cancellation window, otherwise blocks with a "
            "clear error to the customer."
        ),
        intended_file="src/checkout/orders.py",
        intended_symbol="cancel_order",
        distractors=(
            ("src/billing/refunds.py", "cancel_order"),
        ),
    ),
    GroundingCase(
        case_id="A5_cancel_order_billing",
        case_type="same_name_different_module",
        description=(
            "Stripe chargeback webhook + failed-renewal subscription job both "
            "trigger billing-side cancellation — bypasses the user-facing "
            "24-hour window because the trigger is external, records a "
            "billing cancellation event, refunds to source of funds."
        ),
        intended_file="src/billing/refunds.py",
        intended_symbol="cancel_order",
        distractors=(
            ("src/checkout/orders.py", "cancel_order"),
        ),
    ),
]


# ─── Case B: similar-intent-different-symbol ────────────────────────────────

CASES_B: list[GroundingCase] = [
    GroundingCase(
        case_id="B1_tenant_rate_limit",
        case_type="similar_intent",
        description=(
            "Enterprise contract clause: each tenant gets 1000 req/min on "
            "checkout endpoints. Identity is the tenant_id from auth claims, "
            "scope is the checkout-endpoint family. Implements the tenant-"
            "scoped SLA, NOT the cluster-wide protective ceiling."
        ),
        intended_file="src/middleware/tenant_rate_limit.py",
        intended_symbol="TenantCheckoutRateLimiter.check",
        distractors=(
            ("src/middleware/global_rate_limit.py", "GlobalRateLimiter.check"),
            ("src/checkout/throttle.py", "throttle_checkout"),
        ),
    ),
    GroundingCase(
        case_id="B2_retry_cap",
        case_type="similar_intent",
        description=(
            "Stripe contract clause: never retry a declined authorization "
            "more than 3 times within 24 hours. Enforced before each retry "
            "by raising MaxRetriesExceeded when the attempt counter hits 3. "
            "Distinct from the backoff-delay configuration."
        ),
        intended_file="src/checkout/retry.py",
        intended_symbol="CheckoutRetryGuard.check_cap",
        distractors=(
            ("src/checkout/retry.py", "StripeRetryPolicy.delay_for"),
            ("src/checkout/orders.py", "process_order"),
        ),
    ),
    GroundingCase(
        case_id="B3_throttle_double_submit",
        case_type="similar_intent",
        description=(
            "Prevent double-submit on the 'pay now' button by adding a "
            "30-second cooldown per session. This is session-level cooldown, "
            "NOT per-tenant rate limit — the trigger is the user's recent "
            "click history, not the tenant's request volume."
        ),
        intended_file="src/checkout/throttle.py",
        intended_symbol="throttle_checkout",
        distractors=(
            ("src/middleware/tenant_rate_limit.py", "TenantCheckoutRateLimiter.check"),
            ("src/middleware/global_rate_limit.py", "GlobalRateLimiter.check"),
        ),
    ),
    GroundingCase(
        case_id="B4_validate_jwt",
        case_type="similar_intent",
        description=(
            "API gateway middleware validates JWT signature, expiry, and "
            "audience on every inbound request. Stateless bearer credentials — "
            "the gateway does NOT consult any session store. Used on the "
            "API surface, not the web UI."
        ),
        intended_file="src/auth/tokens.py",
        intended_symbol="validate_token",
        distractors=(
            ("src/auth/session.py", "validate_session"),
        ),
    ),
    GroundingCase(
        case_id="B5_validate_session",
        case_type="similar_intent",
        description=(
            "Web UI middleware validates the server-side session cookie "
            "before serving authenticated pages. Sessions support invalidation "
            "(unlike JWTs); a request carries a session cookie OR a JWT, "
            "never both, and this function handles the cookie path."
        ),
        intended_file="src/auth/session.py",
        intended_symbol="validate_session",
        distractors=(
            ("src/auth/tokens.py", "validate_token"),
        ),
    ),
    GroundingCase(
        case_id="B6_refresh_jwt",
        case_type="similar_intent",
        description=(
            "Exchange a long-lived refresh token for a fresh access token. "
            "Refresh tokens are JWTs marked with type='refresh' and have "
            "their own expiry separate from access tokens. Mints a new "
            "access JWT for the same subject."
        ),
        intended_file="src/auth/tokens.py",
        intended_symbol="refresh_token",
        distractors=(
            ("src/auth/session.py", "refresh_session"),
        ),
    ),
    GroundingCase(
        case_id="B7_refresh_session",
        case_type="similar_intent",
        description=(
            "Extend the server-side session's expiry on every authenticated "
            "UI page-load — slides the idle-timeout window forward, but caps "
            "at the absolute 24-hour creation timeout regardless of activity. "
            "Server-side state, not a token mint."
        ),
        intended_file="src/auth/session.py",
        intended_symbol="refresh_session",
        distractors=(
            ("src/auth/tokens.py", "refresh_token"),
        ),
    ),
    GroundingCase(
        case_id="B8_verify_webhook_python",
        case_type="similar_intent",
        description=(
            "Verify HMAC-SHA256 signature on inbound webhook payloads before "
            "any processing. Computed over the raw body using the per-source "
            "shared secret. Constant-time comparison via hmac.compare_digest. "
            "Implemented in the Python webhook ingress, not the auth path."
        ),
        intended_file="src/webhooks/verify.py",
        intended_symbol="verify_webhook_signature",
        distractors=(
            ("src/auth/tokens.py", "validate_token"),
            ("src/auth/session.py", "validate_session"),
        ),
    ),
    GroundingCase(
        case_id="B9_request_metrics",
        case_type="similar_intent",
        description=(
            "API gateway emits per-request metrics — latency histogram, "
            "status counter, route + tenant tags — after the response is "
            "generated. One metric emission per inbound HTTP request, "
            "tagged with tenant_id for SLA dashboards."
        ),
        intended_file="src/metrics/collect.py",
        intended_symbol="collect_request_metrics",
        distractors=(
            ("src/metrics/collect.py", "collect_handler_metrics"),
        ),
    ),
    GroundingCase(
        case_id="B10_handler_metrics",
        case_type="similar_intent",
        description=(
            "Each handler invocation emits its own latency + outcome metrics — "
            "finer grain than per-request, since one inbound request fans out "
            "to many handler invocations through the middleware chain. Used "
            "to attribute regressions to specific handlers."
        ),
        intended_file="src/metrics/collect.py",
        intended_symbol="collect_handler_metrics",
        distractors=(
            ("src/metrics/collect.py", "collect_request_metrics"),
        ),
    ),
]


# ─── Case C: cross-language ─────────────────────────────────────────────────

CASES_C: list[GroundingCase] = [
    GroundingCase(
        case_id="C1_verify_python",
        case_type="cross_language",
        description=(
            "Python API service verifies HMAC-SHA256 signatures on inbound "
            "webhook requests using the per-source shared secret with "
            "constant-time comparison. Runtime: CPython on the API workers."
        ),
        intended_file="src/webhooks/verify.py",
        intended_symbol="verify_webhook_signature",
        distractors=(
            ("src/webhooks/verify.ts", "verifyWebhookSignature"),
        ),
    ),
    GroundingCase(
        case_id="C2_verify_typescript",
        case_type="cross_language",
        description=(
            "TypeScript edge-worker verifies HMAC-SHA256 webhook signatures "
            "before forwarding to the origin. Same security contract as the "
            "Python API path, but runs at the CDN edge so verification "
            "happens before the request hits the origin."
        ),
        intended_file="src/webhooks/verify.ts",
        intended_symbol="verifyWebhookSignature",
        distractors=(
            ("src/webhooks/verify.py", "verify_webhook_signature"),
        ),
    ),
    GroundingCase(
        case_id="C3_dispatch_python",
        case_type="cross_language",
        description=(
            "Python webhook ingress routes verified events to the subscriber "
            "handler chain — after signature verification, fans out to each "
            "handler with per-handler retry. Errors in one handler do not "
            "abort the rest. Runs in the Python ingestion path."
        ),
        intended_file="src/webhooks/dispatch.py",
        intended_symbol="dispatch_event",
        distractors=(
            ("src/webhooks/dispatch.ts", "dispatchEvent"),
        ),
    ),
    GroundingCase(
        case_id="C4_dispatch_typescript",
        case_type="cross_language",
        description=(
            "TypeScript runtime routes verified webhook events to subscriber "
            "handlers, fanning out with per-handler retry. Same routing "
            "contract as the Python sibling, but runs in the TS ingress (e.g. "
            "the edge worker after sig-verify)."
        ),
        intended_file="src/webhooks/dispatch.ts",
        intended_symbol="dispatchEvent",
        distractors=(
            ("src/webhooks/dispatch.py", "dispatch_event"),
        ),
    ),
    GroundingCase(
        case_id="C5_enqueue_python",
        case_type="cross_language",
        description=(
            "Python webhook handler queues events for asynchronous dispatch "
            "when the inbound request must respond inside Stripe's 5-second "
            "window — the queue lives in-process, drained by a background "
            "worker. Used only on the Python path."
        ),
        intended_file="src/webhooks/dispatch.py",
        intended_symbol="enqueue_dispatch",
        distractors=(
            ("src/webhooks/dispatch.ts", "enqueueDispatch"),
        ),
    ),
    GroundingCase(
        case_id="C6_enqueue_typescript",
        case_type="cross_language",
        description=(
            "TypeScript edge worker queues webhook events for async dispatch "
            "to satisfy Stripe's 5-second response budget. Same async-queue "
            "contract as the Python sibling, runs in the TS runtime where "
            "edge workers must respond fast."
        ),
        intended_file="src/webhooks/dispatch.ts",
        intended_symbol="enqueueDispatch",
        distractors=(
            ("src/webhooks/dispatch.py", "enqueue_dispatch"),
        ),
    ),
    GroundingCase(
        case_id="C7_metrics_python",
        case_type="cross_language",
        description=(
            "Python API gateway emits per-request latency and status metrics "
            "tagged with tenant_id. Runs in-process on the Python workers, "
            "fires after the response is generated by the gateway middleware."
        ),
        intended_file="src/metrics/collect.py",
        intended_symbol="collect_request_metrics",
        distractors=(
            ("src/metrics/collect.ts", "collectRequestMetrics"),
        ),
    ),
    GroundingCase(
        case_id="C8_metrics_typescript",
        case_type="cross_language",
        description=(
            "TypeScript edge proxy emits per-request latency and status "
            "metrics tagged with tenant_id. Runs in the TS edge runtime, "
            "fires after each proxied response. Same metrics contract as "
            "the Python gateway, different runtime."
        ),
        intended_file="src/metrics/collect.ts",
        intended_symbol="collectRequestMetrics",
        distractors=(
            ("src/metrics/collect.py", "collect_request_metrics"),
        ),
    ),
]


ALL_CASES: list[GroundingCase] = CASES_A + CASES_B + CASES_C


def cases_by_type(case_type: str) -> list[GroundingCase]:
    return [c for c in ALL_CASES if c.case_type == case_type]


def case_by_id(case_id: str) -> GroundingCase:
    for c in ALL_CASES:
        if c.case_id == case_id:
            return c
    raise KeyError(f"unknown case_id: {case_id}")


# Sanity check at import time — fail loud if the dataset shape regresses.
def _validate_dataset() -> None:
    seen_ids: set[str] = set()
    for c in ALL_CASES:
        if c.case_id in seen_ids:
            raise AssertionError(f"duplicate case_id: {c.case_id}")
        seen_ids.add(c.case_id)
        if c.case_type not in ("same_name_different_module", "similar_intent", "cross_language"):
            raise AssertionError(f"{c.case_id}: invalid case_type {c.case_type!r}")
        if not c.intended_file or not c.intended_symbol:
            raise AssertionError(f"{c.case_id}: intended_file/symbol must be non-empty")
        for df, ds in c.distractors:
            if (df, ds) == (c.intended_file, c.intended_symbol):
                raise AssertionError(f"{c.case_id}: distractor matches intended")


_validate_dataset()
