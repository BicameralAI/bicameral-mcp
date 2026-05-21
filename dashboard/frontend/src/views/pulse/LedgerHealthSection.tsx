import type { PulseHealth } from "../../types";

interface LedgerHealthSectionProps {
  health: PulseHealth;
}

// Decision Ledger Health — the 4 ledger statuses the backend actually
// exposes. Amendment §6.2 lists 7 statuses (proposed, ratified, rejected,
// superseded, ungrounded, reflected, drifted); `build_project_pulse` only
// produces counts for reflected / drifted / pending / ungrounded. The other
// three (ratified, rejected, superseded) have NO backend count — they are
// honestly omitted here rather than fabricated. The footnote states this.
export function LedgerHealthSection({ health }: LedgerHealthSectionProps) {
  const h = health ?? ({} as PulseHealth);
  const stat = (num: number, label: string, alert = false) => (
    <div class="pulse-stat" key={label}>
      <span class={"pulse-stat-num" + (alert && num > 0 ? " drift" : "")}>
        {num ?? 0}
      </span>
      <span class="pulse-stat-label">{label}</span>
    </div>
  );
  return (
    <section class="pulse-section">
      <div class="pulse-section-name">Decision Ledger Health</div>
      <div class="pulse-health">
        {stat(h.decisions_reflected, "reflected")}
        {stat(h.decisions_drifted, "drifted", true)}
        {stat(h.decisions_pending, "pending")}
        {stat(h.decisions_ungrounded, "ungrounded")}
      </div>
      <div class="pulse-footnote">
        Ratified, rejected and superseded counts are not yet surfaced by the
        Pulse endpoint — see the Ledger view for the full status set.
      </div>
    </section>
  );
}
