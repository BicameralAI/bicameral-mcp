import { ROUTES, type RouteId } from "./router";

// Display labels for the 8 views, in canonical order (design §3).
const NAV_LABELS: Record<RouteId, string> = {
  pulse: "Pulse",
  ledger: "Ledger",
  ratification: "Ratification",
  drift: "Drift",
  sources: "Sources",
  audit: "Audit",
  integrations: "Integrations",
  settings: "Settings",
};

interface SidebarNavProps {
  active: RouteId;
}

export function SidebarNav({ active }: SidebarNavProps) {
  return (
    <nav class="sidebar" aria-label="Primary">
      <div class="sidebar-brand">Bicameral</div>
      <ul class="nav-list">
        {ROUTES.map((id) => (
          <li key={id}>
            <a
              class={"nav-item" + (id === active ? " active" : "")}
              href={`#/${id}`}
              aria-current={id === active ? "page" : undefined}
            >
              {NAV_LABELS[id]}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
