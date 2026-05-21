import { SidebarNav } from "./SidebarNav";
import { DashboardHeader } from "./DashboardHeader";
import { useRoute, type RouteId } from "./router";
import { PulseView } from "./views/PulseView";
import { LedgerView } from "./views/LedgerView";
import { Placeholder } from "./views/Placeholder";

// Display names for the placeholder views.
const PLACEHOLDER_NAMES: Partial<Record<RouteId, string>> = {
  ratification: "Ratification",
  drift: "Drift",
  sources: "Sources",
  audit: "Audit",
  integrations: "Integrations",
  settings: "Settings",
};

function renderView(route: RouteId) {
  if (route === "pulse") return <PulseView />;
  if (route === "ledger") return <LedgerView />;
  const name = PLACEHOLDER_NAMES[route];
  return <Placeholder name={name ?? route} />;
}

export function AppShell() {
  const route = useRoute();
  return (
    <div class="app-shell">
      <SidebarNav active={route} />
      <DashboardHeader />
      <main class="content" aria-live="polite">
        {renderView(route)}
      </main>
    </div>
  );
}
