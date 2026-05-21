import type { PulseHealth } from "../../types";
import { fmtDate } from "./format";
import { NotAvailable } from "./NotAvailable";

interface TeamMemoryStateProps {
  health: PulseHealth;
}

// Team Memory State — amendment §6.1 / §11.
//
// The amendment's rich freshness ribbon (Drive current/stale/offline/auth,
// local queue depth, shared-actions paused/enabled) has NO backend: the
// `/pulse` endpoint exposes ONLY `health.last_sync`, which is currently
// always null. So this section renders two honest parts:
//
//  1. the real `last_sync` line — "Last confirmed team sync: never" when
//     null (the actual steady state), or the date when populated;
//  2. the rich freshness model as an explicit NotAvailable card — clearly
//     a forthcoming feature, never a fabricated status.
export function TeamMemoryState({ health }: TeamMemoryStateProps) {
  const h = health ?? ({} as PulseHealth);
  const lastSync = h.last_sync ? fmtDate(h.last_sync) : "never";
  return (
    <section class="pulse-section">
      <div class="pulse-section-name">Team Memory State</div>
      <div class="pulse-sync-line">
        Last confirmed team sync: <strong>{lastSync}</strong>
      </div>
      <NotAvailable
        title="Drive freshness ribbon"
        detail={
          "Live Google Drive freshness (current / stale / offline / auth " +
          "needed), local queue depth and shared-actions state arrive in a " +
          "later milestone. The line above reflects the only sync signal " +
          "the Pulse endpoint exposes today."
        }
      />
    </section>
  );
}
