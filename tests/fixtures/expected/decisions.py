"""Ground truth decision fixtures — derived from real meeting transcripts.

Each entry defines:
  - description: the intent text the system should extract (or find via search)
  - source_ref: which transcript it came from
  - keywords: BM25 search terms that should surface this decision
  - expected_symbols: code symbols this decision should map to
  - expected_file_patterns: substring patterns for expected file paths
  - prd_failure_mode: which PRD failure mode this tests (CONSTRAINT_LOST etc.)
  - adversarial_type: adversarial dimension (negation, temporal, blast_radius, etc.) or None

These fixtures define what a correct system must do. Tests that ingest these
transcripts and query back should match these expectations.
"""

from __future__ import annotations

# ── Medusa: Payment Timeout (medusa-payment-timeout.md) ───────────────
MEDUSA_PAYMENT_TIMEOUT = [
    {
        "description": "Add 12-second timeout ceiling on payment provider authorize calls; return requires_more status on timeout",
        "source_ref": "medusa-payment-timeout",
        "keywords": ["payment timeout", "authorize call", "12 second", "requires_more", "checkout timeout"],
        "expected_symbols": [
            "PaymentProviderService",
            "CartCompletionStrategy",
        ],
        "expected_file_patterns": ["payment", "checkout", "cart"],
        "prd_failure_mode": "CONSTRAINT_LOST",  # Rate limit / timeout ceiling is a hard constraint
        "status_at_ingest": "pending",
    },
    {
        "description": "Background sweeper job via JobSchedulerService: void payment sessions stuck in pending state for more than 5 minutes",
        "source_ref": "medusa-payment-timeout",
        "keywords": ["sweeper job", "pending payment session", "void", "5 minutes", "job scheduler"],
        "expected_symbols": [
            "JobSchedulerService",
            "PaymentProviderService",
            "PaymentSessionService",
        ],
        "expected_file_patterns": ["payment", "job", "scheduler"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",  # Easy to skip the sweeper, not in obvious place
        "status_at_ingest": "ungrounded",  # No existing code matches this
    },
    {
        "description": "Emit payment.authorization_timeout event through EventBus when authorize call times out",
        "source_ref": "medusa-payment-timeout",
        "keywords": ["authorization_timeout", "event bus", "emit event", "payment event"],
        "expected_symbols": [
            "EventBusService",
            "PaymentProviderService",
        ],
        "expected_file_patterns": ["payment", "event"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Guard against garbage responses from community payment providers — throw typed error if authorize returns undefined or malformed object",
        "source_ref": "medusa-payment-timeout",
        "keywords": ["validate provider response", "community provider", "undefined response", "typed error", "authorize response"],
        "expected_symbols": [
            "PaymentProviderService",
        ],
        "expected_file_patterns": ["payment"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "status_at_ingest": "pending",
    },
]

# ── Medusa: Plugin Migration (medusa-plugin-migration.md) ─────────────
MEDUSA_PLUGIN_MIGRATION = [
    {
        "description": "Migrate plugin service classes from TransactionBaseService to AbstractModuleService using @Module decorator",
        "source_ref": "medusa-plugin-migration",
        "keywords": ["plugin migration", "AbstractModuleService", "@Module decorator", "TransactionBaseService", "v2 module"],
        "expected_symbols": [
            "AbstractModuleService",
            "PluginManager",
        ],
        "expected_file_patterns": ["plugin", "module", "service"],
        "prd_failure_mode": "CONTEXT_SCATTERED",
        "status_at_ingest": "pending",
    },
    {
        "description": "Convert plugin subscribers to createWorkflow/createStep pattern; subscribers directory no longer auto-registers in v2",
        "source_ref": "medusa-plugin-migration",
        "keywords": ["subscribers", "createWorkflow", "createStep", "workflow migration", "event subscriber"],
        "expected_symbols": [
            "createWorkflow",
            "createStep",
        ],
        "expected_file_patterns": ["workflow", "subscriber"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "status_at_ingest": "pending",
    },
    {
        "description": "Service injection must go through Modules registry — no direct imports of core services from other modules",
        "source_ref": "medusa-plugin-migration",
        "keywords": ["Modules registry", "service injection", "no direct imports", "awilix scoping", "module isolation"],
        "expected_symbols": [
            "Modules",
            "OrderService",
        ],
        "expected_file_patterns": ["module", "plugin"],
        "prd_failure_mode": "CONSTRAINT_LOST",  # This is a hard architectural constraint
        "adversarial_type": "negation",  # "CAN'T reach into another module's internal services"
        "status_at_ingest": "pending",
    },
    {
        "description": "Run v1 and v2 API routes in parallel for one release cycle using middlewares.ts pattern",
        "source_ref": "medusa-plugin-migration",
        "keywords": ["backward compat", "v1 routes", "parallel routes", "middlewares.ts", "legacy API"],
        "expected_symbols": [
            "middlewares",
        ],
        "expected_file_patterns": ["middleware", "router", "api"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "status_at_ingest": "pending",
    },
]

# ── Medusa: Webhook Notifications (medusa-webhook-notifications.md) ───
MEDUSA_WEBHOOKS = [
    {
        "description": "Create WebhookEndpoint model with fields: URL, HMAC secret, subscribed event types, per-merchant",
        "source_ref": "medusa-webhook-notifications",
        "keywords": ["WebhookEndpoint", "merchant webhook", "webhook model", "HMAC secret", "event subscription"],
        "expected_symbols": [
            "WebhookEndpoint",
            "AbstractNotificationProviderService",
        ],
        "expected_file_patterns": ["webhook", "model", "notification"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Exponential backoff retry: 30s initial delay, max 4h, 6 retries then dead-letter queue to Redis Streams",
        "source_ref": "medusa-webhook-notifications",
        "keywords": ["exponential backoff", "retry webhook", "dead letter queue", "6 retries", "Redis DLQ"],
        "expected_symbols": [],
        "expected_file_patterns": ["webhook", "retry"],
        "prd_failure_mode": "CONSTRAINT_LOST",  # Retry policy is an explicit constraint
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Per-endpoint rate limiter: token bucket, max 10 requests/second, overflow queued",
        "source_ref": "medusa-webhook-notifications",
        "keywords": ["rate limit", "token bucket", "10 per second", "webhook rate"],
        "expected_symbols": [],
        "expected_file_patterns": ["webhook", "rate"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Include idempotency key (UUID per delivery attempt) in webhook payload so merchants can deduplicate",
        "source_ref": "medusa-webhook-notifications",
        "keywords": ["idempotency key", "webhook deduplication", "UUID delivery", "delivery attempt"],
        "expected_symbols": [],
        "expected_file_patterns": ["webhook"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "adversarial_type": "blast_radius",  # Missing this causes double-processing at merchant side
        "status_at_ingest": "ungrounded",
    },
]

# ── Saleor: Checkout Extensibility (saleor-checkout-extensibility.md) ─
SALEOR_CHECKOUT = [
    {
        "description": "Synchronous validation hooks in checkout pipeline that can reject operations — plugin raises ValidationError that propagates through GraphQL",
        "source_ref": "saleor-checkout-extensibility",
        "keywords": ["checkout validation", "synchronous hooks", "ValidationError", "reject operation", "pre-validation"],
        "expected_symbols": [
            "PluginManager",
            "CheckoutError",
        ],
        "expected_file_patterns": ["checkout", "plugin", "validation"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "status_at_ingest": "pending",
    },
    {
        "description": "Circuit breaker: 3 consecutive validation endpoint timeouts — skip that plugin for subsequent checkouts; per-app per-event-type tracking in Redis sliding window",
        "source_ref": "saleor-checkout-extensibility",
        "keywords": ["circuit breaker", "validation timeout", "3 consecutive failures", "skip plugin", "sliding window"],
        "expected_symbols": [],
        "expected_file_patterns": ["checkout", "plugin", "circuit"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "adversarial_type": "multi_hop",  # Requires: checkout validation → timeout detection → circuit breaker → Redis state
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Cache checkout validation results in Redis keyed by last_change timestamp with TTL; invalidate on line changes, address updates, or shipping method changes",
        "source_ref": "saleor-checkout-extensibility",
        "keywords": ["cache validation", "last_change", "Redis TTL", "checkout cache", "validation cache"],
        "expected_symbols": [
            "Checkout",
        ],
        "expected_file_patterns": ["checkout", "cache"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Plugins receive serialized checkout data, not raw querysets — security boundary to prevent third-party data access",
        "source_ref": "saleor-checkout-extensibility",
        "keywords": ["plugin data access", "serialized data", "security boundary", "not raw queryset"],
        "expected_symbols": [
            "PluginManager",
        ],
        "expected_file_patterns": ["plugin", "checkout"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "adversarial_type": "negation",  # "Don't expose raw querysets to third-party plugins"
        "status_at_ingest": "pending",
    },
]

# ── Saleor: GraphQL Permissions (saleor-graphql-permissions.md) ───────
SALEOR_PERMISSIONS = [
    {
        "description": "Channel-scoped JWT permissions: permission claim becomes dict mapping codename to list of channel slugs or ['*'] for global; existing flat format treated as all-channels for backward compat",
        "source_ref": "saleor-graphql-permissions",
        "keywords": ["channel permissions", "JWT scoped", "channel slug", "permission_required", "backward compat"],
        "expected_symbols": [
            "check_permissions",
            "effective_permissions",
        ],
        "expected_file_patterns": ["permission", "jwt", "auth"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "status_at_ingest": "pending",
    },
    {
        "description": "Gate checkoutComplete mutation on channel permission before any side effects — order creation, payment processing, webhooks",
        "source_ref": "saleor-graphql-permissions",
        "keywords": ["checkoutComplete permission", "gate before side effects", "early permission check"],
        "expected_symbols": [
            "checkoutComplete",
            "check_permissions",
        ],
        "expected_file_patterns": ["checkout", "mutation", "permission"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "adversarial_type": "temporal",  # Must gate BEFORE side effects, not after
        "status_at_ingest": "pending",
    },
    {
        "description": "App model: add channel_access relationship so third-party apps only access channels they are installed for",
        "source_ref": "saleor-graphql-permissions",
        "keywords": ["app channel access", "channel_access", "third-party app permission", "app installed channels"],
        "expected_symbols": [
            "App",
        ],
        "expected_file_patterns": ["app", "channel"],
        "prd_failure_mode": "CONTEXT_SCATTERED",
        "status_at_ingest": "pending",
    },
]

# ── Saleor: Order Workflows (saleor-order-workflows.md) ───────────────
SALEOR_ORDERS = [
    {
        "description": "Wrap decrease_stock and allocation cleanup in transaction.atomic — currently separate operations causing orphaned allocation records when decrease_stock succeeds but cleanup fails",
        "source_ref": "saleor-order-workflows",
        "keywords": ["transaction.atomic", "decrease_stock", "allocation cleanup", "orphaned allocation", "stock transaction"],
        "expected_symbols": [
            "decrease_stock",
            "orderFulfill",
        ],
        "expected_file_patterns": ["warehouse", "stock", "fulfillment"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "status_at_ingest": "pending",
    },
    {
        "description": "Defer FULFILLMENT_CREATED webhook dispatch to Django on_commit hook — currently fires before stock operations complete causing stale data in downstream systems",
        "source_ref": "saleor-order-workflows",
        "keywords": ["on_commit", "webhook timing", "FULFILLMENT_CREATED", "defer webhook", "after transaction"],
        "expected_symbols": [
            "on_commit",
            "FULFILLMENT_CREATED",
        ],
        "expected_file_patterns": ["fulfillment", "webhook", "order"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "adversarial_type": "temporal",  # Timing bug: wrong order of operations
        "status_at_ingest": "pending",
    },
    {
        "description": "Fix update_order_status: missing RETURNED status handling causes orders to stay FULFILLED even after all fulfillments are returned",
        "source_ref": "saleor-order-workflows",
        "keywords": ["update_order_status", "RETURNED status", "fulfillment status sync", "order status bug"],
        "expected_symbols": [
            "update_order_status",
        ],
        "expected_file_patterns": ["order", "fulfillment", "status"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "status_at_ingest": "pending",
    },
    {
        "description": "Database constraint on Stock: quantity cannot go negative; decrease_stock can produce negative values in race condition",
        "source_ref": "saleor-order-workflows",
        "keywords": ["stock constraint", "negative quantity", "race condition", "database constraint"],
        "expected_symbols": [
            "Stock",
            "decrease_stock",
        ],
        "expected_file_patterns": ["warehouse", "stock", "migration"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "status_at_ingest": "pending",
    },
]

# ── Vendure: Channel Pricing (vendure-channel-pricing.md) ─────────────
VENDURE_PRICING = [
    {
        "description": "Custom ProductVariantPriceUpdateStrategy: strip tax in source channel, convert currency using TaxRateService, reapply destination zone rate; iterate per currency per channel not per channel",
        "source_ref": "vendure-channel-pricing",
        "keywords": ["ProductVariantPriceUpdateStrategy", "currency conversion", "tax stripping", "multi-channel pricing", "InjectableStrategy"],
        "expected_symbols": [
            "ProductVariantPriceUpdateStrategy",
            "TaxRateService",
            "ProductVariantService",
        ],
        "expected_file_patterns": ["pricing", "variant", "channel"],
        "prd_failure_mode": "TRIBAL_KNOWLEDGE",  # Complex tax/currency logic only discussed once
        "adversarial_type": "blast_radius",  # 30,000 records, must batch
        "status_at_ingest": "pending",
    },
    {
        "description": "Batch conversion lookups in price update strategy — 5,000 variants across 3 channels with 2 currencies = 30,000 price records, cannot use N+1 queries",
        "source_ref": "vendure-channel-pricing",
        "keywords": ["batch price update", "N+1 queries", "30000 records", "batch conversion"],
        "expected_symbols": [
            "createOrUpdateProductVariantPrice",
        ],
        "expected_file_patterns": ["pricing", "variant"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "status_at_ingest": "pending",
    },
]

# ── Vendure: Custom Fields (vendure-custom-fields.md) ─────────────────
VENDURE_CUSTOM_FIELDS = [
    {
        "description": "loyaltyPoints: int custom field on Customer, non-nullable, default 0, readonly from storefront mutations",
        "source_ref": "vendure-custom-fields",
        "keywords": ["loyaltyPoints", "custom field", "VendureConfig", "readonly", "Customer"],
        "expected_symbols": [
            "CustomFieldConfig",
            "VendureConfig",
        ],
        "expected_file_patterns": ["config", "vendure-config"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "status_at_ingest": "pending",
    },
    {
        "description": "struct type custom field warning: stores as simple-json, no SQL-level querying or indexing on sub-fields — do not use struct if you need to filter on nested values",
        "source_ref": "vendure-custom-fields",
        "keywords": ["struct custom field", "simple-json", "no SQL indexing", "nested field warning"],
        "expected_symbols": [],
        "expected_file_patterns": ["custom-field"],
        "prd_failure_mode": "TRIBAL_KNOWLEDGE",
        "adversarial_type": "negation",  # "If you need to filter... don't use struct"
        "status_at_ingest": "ungrounded",
    },
]

# ── Vendure: Search Reindexing (vendure-search-reindexing.md) ─────────
VENDURE_SEARCH = [
    {
        "description": "Enable bufferUpdates on DefaultSearchPlugin to deduplicate by entity ID during bulk imports; switch from SqlJobQueueStrategy to BullMQJobQueuePlugin",
        "source_ref": "vendure-search-reindexing",
        "keywords": ["bufferUpdates", "BullMQJobQueuePlugin", "search reindex", "SqlJobQueueStrategy", "bulk import"],
        "expected_symbols": [
            "DefaultSearchPlugin",
            "BullMQJobQueuePlugin",
            "SqlJobQueueStrategy",
        ],
        "expected_file_patterns": ["search", "plugin", "queue"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "status_at_ingest": "pending",
    },
    {
        "description": "Split workers using activeQueues option: dedicated search worker plus general worker so reindex does not block order confirmation emails",
        "source_ref": "vendure-search-reindexing",
        "keywords": ["activeQueues", "split workers", "dedicated search worker", "worker isolation"],
        "expected_symbols": [],
        "expected_file_patterns": ["worker", "config"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Performance targets: reindex p95 search latency under 200ms (was 800ms during reindex), database CPU under 50% during full reindex",
        "source_ref": "vendure-search-reindexing",
        "keywords": ["search latency 200ms", "database CPU reindex", "p95 latency", "reindex performance"],
        "expected_symbols": ["DefaultSearchPlugin"],
        "expected_file_patterns": ["search-plugin", "search-strategy", "reindex"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "status_at_ingest": "ungrounded",  # Performance targets — code exists but decision is aspirational
    },
]

# ── Aggregated registry ───────────────────────────────────────────────

ALL_DECISIONS = (
    MEDUSA_PAYMENT_TIMEOUT
    + MEDUSA_PLUGIN_MIGRATION
    + MEDUSA_WEBHOOKS
    + SALEOR_CHECKOUT
    + SALEOR_PERMISSIONS
    + SALEOR_ORDERS
    + VENDURE_PRICING
    + VENDURE_CUSTOM_FIELDS
    + VENDURE_SEARCH
)

# Grouped by failure mode for PRD failure mode tests
BY_FAILURE_MODE: dict[str, list[dict]] = {}
for d in ALL_DECISIONS:
    mode = d.get("prd_failure_mode", "UNKNOWN")
    BY_FAILURE_MODE.setdefault(mode, []).append(d)

# Adversarial cases only
ADVERSARIAL = [d for d in ALL_DECISIONS if d.get("adversarial_type")]

# Decisions that should be ungrounded (no code exists yet)
UNGROUNDED = [d for d in ALL_DECISIONS if d["status_at_ingest"] == "ungrounded"]


# ── Transcript discovery map (M1 decision-relevance eval) ────────────
#
# Extends ground-truth fixtures into a corpus registry. The M1 runner reads
# this to discover which transcript file feeds which repo. Add a new entry
# here to onboard a new transcript — the runner picks it up automatically,
# no code change required. `transcript` is resolved relative to the repo
# root (the parent of pilot/mcp).
#
# `repo_key` is matched against the --multi-repo JSON the caller passes:
#   python tests/eval_decision_relevance.py \
#     --multi-repo '{"medusa": "test-results/.repos/medusa", "bicameral": "."}'
# Only entries whose repo_key is in the mapping will run in a given
# invocation, so partial runs are first-class.

TRANSCRIPT_SOURCES: dict[str, dict] = {
    # ── Adversarial corpus (M1 stress categories) ─────────────────
    # M1 evaluates exclusively against this adversarial corpus. Each
    # transcript deliberately exercises a failure mode documented in
    # visual-plans/quality_metrics/m1-decision-relevance.html. The CI
    # workflow aliases repo_key="adversarial" to the cloned medusa
    # tree (any indexed code works — adversarial transcripts measure
    # extraction quality, not grounding precision against a specific
    # codebase). Ground truth for each lives at
    # pilot/mcp/tests/fixtures/extraction/adv-*.json and is hand-editable.
    #
    # Note: the friendly e-commerce corpus (medusa-*, saleor-*, vendure-*)
    # used to live here as well, but was removed once it became clear that
    # the friendly transcripts are too easy to be informative about
    # real-world reliability. The transcript .md files remain in
    # pilot/ml/data/transcripts/ for reference and other tooling.
    "adv-strat-fake": {
        "transcript": "pilot/ml/data/transcripts/adv-strat-fake.md",
        "repo_key": "adversarial",
    },
    "adv-vocab-collide": {
        "transcript": "pilot/ml/data/transcripts/adv-vocab-collide.md",
        "repo_key": "adversarial",
    },
    "adv-density-extreme": {
        "transcript": "pilot/ml/data/transcripts/adv-density-extreme.md",
        "repo_key": "adversarial",
    },
    "adv-offtopic-mix": {
        "transcript": "pilot/ml/data/transcripts/adv-offtopic-mix.md",
        "repo_key": "adversarial",
    },
    "adv-reversal": {
        "transcript": "pilot/ml/data/transcripts/adv-reversal.md",
        "repo_key": "adversarial",
    },
}
