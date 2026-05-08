"""Service-wide rate limiting — protects against abuse, not SLA.

Distinct from tenant_rate_limit.py which enforces the Enterprise
contract. This caps total req/sec across the whole service to keep
the cluster within capacity headroom.
"""


class GlobalRateLimiter:
    """Service-wide ceiling — 50_000 req/sec total across all tenants."""

    GLOBAL_LIMIT_PER_SEC = 50_000

    def __init__(self, store):
        self._store = store

    def check(self, request):
        """Cluster-level cap — not tied to any contract clause.

        Trips long before any tenant could; mostly defensive against
        runaway crawlers and mis-tuned client retry storms. Tenant-scoped
        SLA enforcement lives in tenant_rate_limit.py:TenantCheckoutRateLimiter.
        """
        if self._store.count_in_window("__global__", window_seconds=1) >= self.GLOBAL_LIMIT_PER_SEC:
            raise GlobalRateLimitExceeded
        self._store.increment("__global__")


class GlobalRateLimitExceeded(Exception):
    pass
