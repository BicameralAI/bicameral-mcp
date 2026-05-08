"""Request and handler metric collection (Python runtime).

Emits to the metrics backend on every inbound request and every
handler invocation. Sibling of collect.ts for the TypeScript runtime.
"""


def collect_request_metrics(request, response, latency_ms):
    """Emit per-request metrics: latency, status, route, tenant.

    Fired by the API gateway middleware on every inbound request, after
    the response is generated. Tagged with tenant_id for SLA tracking.
    Distinct from collect_handler_metrics which is per-handler-invocation
    granularity (one request can fan out to many handlers).
    """
    _emit_counter("requests_total", tags={"route": request.path, "status": response.status})
    _emit_histogram("request_latency_ms", latency_ms, tags={"tenant": request.auth.tenant_id})


def collect_handler_metrics(handler_name, outcome, duration_ms):
    """Emit per-handler-invocation metrics — finer grain than request-level.

    One request → many handler invocations (middleware chain + business
    logic). This captures each handler's own latency + outcome so we can
    spot which handler is responsible for a regression.
    """
    _emit_counter("handler_invocations_total", tags={"handler": handler_name, "outcome": outcome})
    _emit_histogram("handler_duration_ms", duration_ms, tags={"handler": handler_name})


def _emit_counter(name, tags):
    raise NotImplementedError


def _emit_histogram(name, value, tags):
    raise NotImplementedError
