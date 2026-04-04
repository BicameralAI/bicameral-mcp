"""Ground truth decision fixtures — derived from real meeting transcripts.

Each entry defines:
  - description: the intent text the system should extract (or find via search)
  - source_ref: which transcript it came from
  - keywords: BM25 search terms that should surface this decision
  - expected_symbols: code symbols this decision should map to
  - expected_file_patterns: substring patterns for expected file paths
  - prd_failure_mode: which PRD failure mode this tests (CONSTRAINT_LOST etc.)
  - adversarial_type: adversarial dimension (negation, temporal, blast_radius, etc.) or None
  - difficulty: easy (direct token overlap), medium (partial overlap), hard (no overlap)

These fixtures define what a correct system must do. Tests that ingest these
transcripts and query back should match these expectations.

Symbol names are verified against cloned repos at HEAD (2026-04-04).
Medusa symbols updated from v1 → v2 equivalents where needed.
"""

from __future__ import annotations

# ── Medusa: Payment Timeout (medusa-payment-timeout.md) ───────────────
MEDUSA_PAYMENT_TIMEOUT = [
    {
        "description": "Add 12-second timeout ceiling on payment provider authorize calls; return requires_more status on timeout",
        "source_ref": "medusa-payment-timeout",
        "keywords": ["payment timeout", "authorize call", "12 second", "requires_more", "checkout timeout"],
        "expected_symbols": [
            "AbstractPaymentProvider",
            "completeCartWorkflow",
            "PaymentProviderService",
        ],
        "expected_file_patterns": ["payment", "checkout", "cart"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "medium",
        "status_at_ingest": "pending",
    },
    {
        "description": "Background sweeper job via JobLoader: void payment sessions stuck in pending state for more than 5 minutes",
        "source_ref": "medusa-payment-timeout",
        "keywords": ["sweeper job", "pending payment session", "void", "5 minutes", "job scheduler"],
        "expected_symbols": [
            "JobLoader",
            "AbstractPaymentProvider",
            "IPaymentModuleService",
        ],
        "expected_file_patterns": ["payment", "job"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "difficulty": "hard",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Emit payment.authorization_timeout event through EventBus when authorize call times out",
        "source_ref": "medusa-payment-timeout",
        "keywords": ["authorization_timeout", "event bus", "emit event", "payment event"],
        "expected_symbols": [
            "IEventBusModuleService",
            "PaymentProviderService",
        ],
        "expected_file_patterns": ["payment", "event"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "difficulty": "medium",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Guard against garbage responses from community payment providers — throw typed error if authorize returns undefined or malformed object",
        "source_ref": "medusa-payment-timeout",
        "keywords": ["validate provider response", "community provider", "undefined response", "typed error", "authorize response"],
        "expected_symbols": [
            "PaymentProviderService",
            "AbstractPaymentProvider",
        ],
        "expected_file_patterns": ["payment"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "hard",
        "status_at_ingest": "pending",
    },
    # ── NEW: mined from medusa-payment-timeout transcript ──
    {
        "description": "On authorize timeout, return requires_more status instead of error so storefront retry and additional-auth redirect flow is reused",
        "source_ref": "medusa-payment-timeout",
        "keywords": ["requires_more", "storefront redirect", "additional auth", "checkout retry", "timeout status"],
        "expected_symbols": [
            "completeCartWorkflow",
            "PaymentProviderService",
        ],
        "expected_file_patterns": ["payment", "cart", "complete"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "hard",
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
            "getResolvedPlugins",
        ],
        "expected_file_patterns": ["plugin", "module", "service"],
        "prd_failure_mode": "CONTEXT_SCATTERED",
        "difficulty": "medium",
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
        "difficulty": "easy",
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
        "prd_failure_mode": "CONSTRAINT_LOST",
        "adversarial_type": "negation",
        "difficulty": "medium",
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
        "difficulty": "medium",
        "status_at_ingest": "pending",
    },
    # ── NEW: mined from medusa-plugin-migration transcript ──
    {
        "description": "Use model.define utility to define data models in v2 — automatically provides CRUD methods on the module service",
        "source_ref": "medusa-plugin-migration",
        "keywords": ["model.define", "CRUD", "data model", "DML", "EntityBuilder"],
        "expected_symbols": [
            "model",
            "AbstractModuleService",
        ],
        "expected_file_patterns": ["dml", "entity-builder", "model"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "difficulty": "medium",
        "status_at_ingest": "pending",
    },
    {
        "description": "Fulfillment plugin directly imports OrderService from core in v1 — in v2 must resolve through Modules registry by name",
        "source_ref": "medusa-plugin-migration",
        "keywords": ["fulfillment plugin", "OrderService", "Modules registry", "resolve by name", "cross-module"],
        "expected_symbols": [
            "OrderService",
            "Modules",
        ],
        "expected_file_patterns": ["order", "module", "fulfillment"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "adversarial_type": "negation",
        "difficulty": "hard",
        "status_at_ingest": "pending",
    },
]

# ── Medusa: Webhook Notifications (medusa-webhook-notifications.md) ───
MEDUSA_WEBHOOKS = [
    {
        "description": "Create webhook notification provider by extending AbstractNotificationProviderService — same base class used by email and SMS providers",
        "source_ref": "medusa-webhook-notifications",
        "keywords": ["webhook provider", "AbstractNotificationProviderService", "notification module", "extend base class"],
        "expected_symbols": [
            "AbstractNotificationProviderService",
        ],
        "expected_file_patterns": ["webhook", "notification"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "difficulty": "medium",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Exponential backoff retry: 30s initial delay, max 4h, 6 retries then dead-letter queue to Redis Streams",
        "source_ref": "medusa-webhook-notifications",
        "keywords": ["exponential backoff", "retry webhook", "dead letter queue", "6 retries", "Redis DLQ"],
        "expected_symbols": [],
        "expected_file_patterns": ["webhook", "retry"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "hard",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Per-endpoint rate limiter: token bucket, max 10 requests/second, overflow queued",
        "source_ref": "medusa-webhook-notifications",
        "keywords": ["rate limit", "token bucket", "10 per second", "webhook rate"],
        "expected_symbols": [],
        "expected_file_patterns": ["webhook", "rate"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "hard",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Include idempotency key (UUID per delivery attempt) in webhook payload so merchants can deduplicate",
        "source_ref": "medusa-webhook-notifications",
        "keywords": ["idempotency key", "webhook deduplication", "UUID delivery", "delivery attempt"],
        "expected_symbols": [],
        "expected_file_patterns": ["webhook"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "adversarial_type": "blast_radius",
        "difficulty": "hard",
        "status_at_ingest": "ungrounded",
    },
    # ── NEW: mined from medusa-webhook-notifications transcript ──
    {
        "description": "Webhook system built as standalone module linked via defineLink — not folded into notification module",
        "source_ref": "medusa-webhook-notifications",
        "keywords": ["standalone module", "defineLink", "module separation", "webhook module"],
        "expected_symbols": [
            "defineLink",
            "AbstractNotificationProviderService",
        ],
        "expected_file_patterns": ["webhook", "link"],
        "prd_failure_mode": "CONTEXT_SCATTERED",
        "difficulty": "hard",
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
            "PluginsManager",
            "CheckoutError",
        ],
        "expected_file_patterns": ["checkout", "plugin", "validation"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "medium",
        "status_at_ingest": "pending",
    },
    {
        "description": "Circuit breaker: 3 consecutive validation endpoint timeouts — skip that plugin for subsequent checkouts; per-app per-event-type tracking in Redis sliding window",
        "source_ref": "saleor-checkout-extensibility",
        "keywords": ["circuit breaker", "validation timeout", "3 consecutive failures", "skip plugin", "sliding window"],
        "expected_symbols": [],
        "expected_file_patterns": ["checkout", "plugin", "circuit"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "adversarial_type": "multi_hop",
        "difficulty": "hard",
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
        "difficulty": "medium",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Plugins receive serialized checkout data, not raw querysets — security boundary to prevent third-party data access",
        "source_ref": "saleor-checkout-extensibility",
        "keywords": ["plugin data access", "serialized data", "security boundary", "not raw queryset"],
        "expected_symbols": [
            "PluginsManager",
        ],
        "expected_file_patterns": ["plugin", "checkout"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "adversarial_type": "negation",
        "difficulty": "hard",
        "status_at_ingest": "pending",
    },
    # ── NEW: mined from saleor-checkout-extensibility transcript ──
    {
        "description": "Add EXTERNAL_VALIDATION_ERROR code to CheckoutError GraphQL type with metadata field for plugin-specific error details",
        "source_ref": "saleor-checkout-extensibility",
        "keywords": ["EXTERNAL_VALIDATION_ERROR", "CheckoutError", "error code", "metadata", "plugin error"],
        "expected_symbols": [
            "CheckoutError",
            "CheckoutErrorCode",
        ],
        "expected_file_patterns": ["checkout", "error", "enums"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "difficulty": "easy",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Use WebhookPlugin bridge pattern to route CHECKOUT_VALIDATE_COMPLETE events to third-party app webhook URLs — same pattern already used for tax calculations",
        "source_ref": "saleor-checkout-extensibility",
        "keywords": ["WebhookPlugin", "CHECKOUT_VALIDATE_COMPLETE", "bridge pattern", "tax webhook", "app webhook"],
        "expected_symbols": [
            "WebhookPlugin",
            "PluginsManager",
        ],
        "expected_file_patterns": ["plugin", "webhook"],
        "prd_failure_mode": "CONTEXT_SCATTERED",
        "difficulty": "hard",
        "status_at_ingest": "ungrounded",
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
        "difficulty": "medium",
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
        "adversarial_type": "temporal",
        "difficulty": "medium",
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
        "difficulty": "medium",
        "status_at_ingest": "pending",
    },
    # ── NEW: mined from saleor-graphql-permissions transcript ──
    {
        "description": "For orderUpdate mutation, resolve channel from the order's existing channel foreign key then check scoped permissions against it",
        "source_ref": "saleor-graphql-permissions",
        "keywords": ["orderUpdate", "channel foreign key", "resolve channel", "scoped permissions"],
        "expected_symbols": [
            "orderUpdate",
            "check_permissions",
        ],
        "expected_file_patterns": ["order", "mutation", "permission"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "medium",
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
        "difficulty": "easy",
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
        "adversarial_type": "temporal",
        "difficulty": "easy",
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
        "difficulty": "easy",
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
        "difficulty": "medium",
        "status_at_ingest": "pending",
    },
    # ── NEW: mined from saleor-order-workflows transcript ──
    {
        "description": "Write management command to find and clean up orphaned Allocation records as safety net alongside the transaction fix",
        "source_ref": "saleor-order-workflows",
        "keywords": ["management command", "orphaned allocations", "cleanup", "Allocation", "safety net"],
        "expected_symbols": [
            "Allocation",
            "Stock",
        ],
        "expected_file_patterns": ["warehouse", "allocation"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "difficulty": "medium",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "available_quantity on Stock is computed as quantity minus allocations — orphaned allocations silently under-report available inventory",
        "source_ref": "saleor-order-workflows",
        "keywords": ["available_quantity", "quantity minus allocations", "computed field", "stock", "inventory"],
        "expected_symbols": [
            "Stock",
            "Allocation",
        ],
        "expected_file_patterns": ["warehouse", "stock", "models"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "hard",
        "status_at_ingest": "pending",
    },
    {
        "description": "Order lifecycle tests must cover unfulfilled to partially returned to returned — existing tests only cover the happy path",
        "source_ref": "saleor-order-workflows",
        "keywords": ["order lifecycle", "partially returned", "returned", "fulfillment status", "test coverage"],
        "expected_symbols": [
            "update_order_status",
            "Fulfillment",
        ],
        "expected_file_patterns": ["order", "fulfillment", "test"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "difficulty": "medium",
        "status_at_ingest": "ungrounded",
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
        "prd_failure_mode": "TRIBAL_KNOWLEDGE",
        "adversarial_type": "blast_radius",
        "difficulty": "easy",
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
        "difficulty": "medium",
        "status_at_ingest": "pending",
    },
    # ── NEW: mined from vendure-channel-pricing transcript ──
    {
        "description": "syncPricesAcrossChannels is ineffective when channels use different currency codes — only syncs same-currency prices",
        "source_ref": "vendure-channel-pricing",
        "keywords": ["syncPricesAcrossChannels", "currency code", "default sync", "cross-channel pricing"],
        "expected_symbols": [
            "syncPricesAcrossChannels",
        ],
        "expected_file_patterns": ["pricing", "variant", "strategy"],
        "prd_failure_mode": "TRIBAL_KNOWLEDGE",
        "difficulty": "easy",
        "status_at_ingest": "pending",
    },
    {
        "description": "Price strategy must iterate per currency within each channel — channels can support multiple currencies like EU channel with EUR and GBP",
        "source_ref": "vendure-channel-pricing",
        "keywords": ["multiple currencies", "currency per channel", "iterate currencies", "onPriceUpdated"],
        "expected_symbols": [
            "ProductVariantPriceUpdateStrategy",
            "onPriceUpdated",
        ],
        "expected_file_patterns": ["pricing", "variant", "strategy"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "medium",
        "status_at_ingest": "pending",
    },
    {
        "description": "Inject TaxRateService into custom price strategy via InjectableStrategy pattern to resolve applicable tax rate per variant tax category per channel zone",
        "source_ref": "vendure-channel-pricing",
        "keywords": ["InjectableStrategy", "TaxRateService", "inject service", "tax zone", "tax category"],
        "expected_symbols": [
            "TaxRateService",
            "InjectableStrategy",
        ],
        "expected_file_patterns": ["tax", "strategy", "injectable"],
        "prd_failure_mode": "TRIBAL_KNOWLEDGE",
        "difficulty": "medium",
        "status_at_ingest": "pending",
    },
    {
        "description": "Admin UI surfaces active exchange rates via a custom field on the Channel entity",
        "source_ref": "vendure-channel-pricing",
        "keywords": ["custom field", "Channel", "exchange rate", "Admin UI"],
        "expected_symbols": [
            "Channel",
            "CustomFieldConfig",
        ],
        "expected_file_patterns": ["channel", "config"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "difficulty": "hard",
        "status_at_ingest": "ungrounded",
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
        "difficulty": "easy",
        "status_at_ingest": "pending",
    },
    {
        "description": "struct type custom field warning: stores as simple-json, no SQL-level querying or indexing on sub-fields — do not use struct if you need to filter on nested values",
        "source_ref": "vendure-custom-fields",
        "keywords": ["struct custom field", "simple-json", "no SQL indexing", "nested field warning"],
        "expected_symbols": [],
        "expected_file_patterns": [],
        "prd_failure_mode": "TRIBAL_KNOWLEDGE",
        "adversarial_type": "negation",
        "difficulty": "hard",
        "status_at_ingest": "ungrounded",
    },
    # ── NEW: mined from vendure-custom-fields transcript ──
    {
        "description": "brandStory localeText custom field on Product stored in translation table — automatically queryable via GraphQL with filter/sort inputs",
        "source_ref": "vendure-custom-fields",
        "keywords": ["brandStory", "localeText", "translation table", "Product custom field", "GraphQL filter"],
        "expected_symbols": [
            "CustomProductFields",
            "CustomFieldConfig",
        ],
        "expected_file_patterns": ["custom-field", "product"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "difficulty": "medium",
        "status_at_ingest": "pending",
    },
    {
        "description": "hazmat boolean custom field on ProductVariant — non-nullable, default false, used to flag variants requiring special shipping",
        "source_ref": "vendure-custom-fields",
        "keywords": ["hazmat", "boolean", "ProductVariant", "custom field", "non-nullable"],
        "expected_symbols": [
            "CustomProductVariantFields",
            "CustomFieldConfig",
        ],
        "expected_file_patterns": ["custom-field", "variant"],
        "prd_failure_mode": "DECISION_UNDOCUMENTED",
        "difficulty": "medium",
        "status_at_ingest": "pending",
    },
    {
        "description": "relation type custom field linking hazmat ProductVariant to ComplianceDocument — singular relation adds foreign key, list relation creates junction table",
        "source_ref": "vendure-custom-fields",
        "keywords": ["relation custom field", "ComplianceDocument", "foreign key", "junction table"],
        "expected_symbols": [
            "CustomProductVariantFields",
            "CustomFieldConfig",
        ],
        "expected_file_patterns": ["custom-field", "variant"],
        "prd_failure_mode": "TRIBAL_KNOWLEDGE",
        "difficulty": "hard",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "TypeScript declaration merging required for custom field type safety — extend CustomProductFields and CustomCustomerFields interfaces from @vendure/core",
        "source_ref": "vendure-custom-fields",
        "keywords": ["declaration merging", "TypeScript", "CustomProductFields", "CustomCustomerFields"],
        "expected_symbols": [
            "CustomProductFields",
            "CustomCustomerFields",
        ],
        "expected_file_patterns": ["types", "custom"],
        "prd_failure_mode": "TRIBAL_KNOWLEDGE",
        "difficulty": "medium",
        "status_at_ingest": "pending",
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
        "difficulty": "easy",
        "status_at_ingest": "pending",
    },
    {
        "description": "Split workers using activeQueues option: dedicated search worker plus general worker so reindex does not block order confirmation emails",
        "source_ref": "vendure-search-reindexing",
        "keywords": ["activeQueues", "split workers", "dedicated search worker", "worker isolation"],
        "expected_symbols": [],
        "expected_file_patterns": ["worker", "config"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "hard",
        "status_at_ingest": "ungrounded",
    },
    {
        "description": "Performance targets: reindex p95 search latency under 200ms (was 800ms during reindex), database CPU under 50% during full reindex",
        "source_ref": "vendure-search-reindexing",
        "keywords": ["search latency 200ms", "database CPU reindex", "p95 latency", "reindex performance"],
        "expected_symbols": [],
        "expected_file_patterns": [],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "hard",
        "status_at_ingest": "ungrounded",
    },
    # ── NEW: mined from vendure-search-reindexing transcript ──
    {
        "description": "Collection filter changes cascade into search index updates — 85 dynamic facet-based collections means updating a facet value triggers re-evaluation of every variant against every collection",
        "source_ref": "vendure-search-reindexing",
        "keywords": ["collection filters", "facet value", "cascade", "re-evaluation", "search index"],
        "expected_symbols": [
            "CollectionService",
            "DefaultSearchPlugin",
        ],
        "expected_file_patterns": ["collection", "search", "plugin"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "adversarial_type": "blast_radius",
        "difficulty": "hard",
        "status_at_ingest": "pending",
    },
    {
        "description": "BullMQJobQueuePlugin replaces SqlJobQueueStrategy to eliminate polling overhead — SqlJobQueueStrategy polls every 200ms per queue generating hundreds of DB queries per second",
        "source_ref": "vendure-search-reindexing",
        "keywords": ["BullMQJobQueuePlugin", "SqlJobQueueStrategy", "polling", "200ms", "push-based"],
        "expected_symbols": [
            "BullMQJobQueuePlugin",
            "SqlJobQueueStrategy",
        ],
        "expected_file_patterns": ["queue", "plugin", "job"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "easy",
        "status_at_ingest": "pending",
    },
    {
        "description": "Full reindex has N+1 query problem — iterates every variant, loads all relations, upserts into search table one by one",
        "source_ref": "vendure-search-reindexing",
        "keywords": ["N+1", "reindex", "variant relations", "upsert", "search table"],
        "expected_symbols": [
            "DefaultSearchPlugin",
        ],
        "expected_file_patterns": ["search", "plugin", "reindex"],
        "prd_failure_mode": "CONSTRAINT_LOST",
        "difficulty": "medium",
        "status_at_ingest": "pending",
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

# By difficulty
BY_DIFFICULTY: dict[str, list[dict]] = {}
for d in ALL_DECISIONS:
    diff = d.get("difficulty", "medium")
    BY_DIFFICULTY.setdefault(diff, []).append(d)
