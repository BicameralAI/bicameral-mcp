// Request metric collection (TypeScript runtime).
// Sibling of collect.py — same contract, different runtime.

interface Request {
  path: string;
  auth: { tenantId: string };
}

interface Response {
  status: number;
}

export function collectRequestMetrics(
  request: Request,
  response: Response,
  latencyMs: number,
): void {
  // Emit per-request metrics: latency, status, route, tenant.
  // Fired by the API gateway middleware on every inbound request,
  // after the response is generated. TS sibling of
  // collect.py:collect_request_metrics.
  emitCounter("requests_total", { route: request.path, status: String(response.status) });
  emitHistogram("request_latency_ms", latencyMs, { tenant: request.auth.tenantId });
}

function emitCounter(_name: string, _tags: Record<string, string>): void {
  throw new Error("not implemented");
}

function emitHistogram(
  _name: string,
  _value: number,
  _tags: Record<string, string>,
): void {
  throw new Error("not implemented");
}
