// Typed fetch wrappers for the dashboard server endpoints.
import type { PulseResponse } from "./types";

/**
 * Fetch the Project Pulse summary from `GET /pulse`.
 *
 * The endpoint is served by the same localhost dashboard server that hosts
 * this bundle, so a relative path is correct. Both the success shape
 * (ProjectPulseSummary) and the failure shape (`{error}`) are returned as
 * `PulseResponse`; callers narrow with `isPulseError`. A transport-level
 * failure (network, non-2xx) is normalised into the `{error}` shape so the
 * view has a single error path.
 */
export async function fetchPulse(): Promise<PulseResponse> {
  try {
    const res = await fetch("/pulse", {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) {
      return { error: `server returned ${res.status}` };
    }
    return (await res.json()) as PulseResponse;
  } catch (e) {
    return { error: e instanceof Error ? e.message : "request failed" };
  }
}
