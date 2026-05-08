"""Per-tenant rate limiting for checkout endpoints.

Enterprise SLA: 1000 req/min per tenant. Keyed on tenant_id resolved
from the request's auth claims. Distinct from global_rate_limit.py
which protects the whole service.
"""


class TenantCheckoutRateLimiter:
    """Token bucket rate limiter scoped to (tenant_id, checkout_endpoint)."""

    LIMIT_PER_MIN = 1000

    def __init__(self, store):
        self._store = store

    def check(self, request):
        """Return None if under cap; raise RateLimitExceeded if over.

        Implements the Enterprise SLA commitment: 1000 req/min per tenant
        per checkout endpoint. Tenant id is read from request.auth.tenant_id.
        Used by middleware/checkout_pipeline.py before reaching the
        order handler.
        """
        tenant_id = request.auth.tenant_id
        endpoint = request.path
        key = f"{tenant_id}:{endpoint}"
        if self._store.count_in_window(key, window_seconds=60) >= self.LIMIT_PER_MIN:
            raise RateLimitExceeded(key)
        self._store.increment(key)


class RateLimitExceeded(Exception):
    pass
