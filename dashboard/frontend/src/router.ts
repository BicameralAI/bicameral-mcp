// Minimal hash-based router. No server SPA-fallback is needed — hash
// routing is fully client-side. Default route is `#/pulse`.
import { useEffect, useState } from "preact/hooks";

export const ROUTES = [
  "pulse",
  "ledger",
  "ratification",
  "drift",
  "sources",
  "audit",
  "integrations",
  "settings",
] as const;

export type RouteId = (typeof ROUTES)[number];

export const DEFAULT_ROUTE: RouteId = "pulse";

function parseHash(hash: string): RouteId {
  const id = hash.replace(/^#\/?/, "").trim().toLowerCase();
  return (ROUTES as readonly string[]).includes(id)
    ? (id as RouteId)
    : DEFAULT_ROUTE;
}

/** Current route derived from `window.location.hash`. */
export function currentRoute(): RouteId {
  return parseHash(window.location.hash);
}

/** Preact hook: re-renders on `hashchange`, returns the active route. */
export function useRoute(): RouteId {
  const [route, setRoute] = useState<RouteId>(currentRoute());

  useEffect(() => {
    const onChange = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onChange);
    // Normalise a missing/empty hash to the default route on first load.
    if (!window.location.hash) {
      window.location.hash = `#/${DEFAULT_ROUTE}`;
    }
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  return route;
}
