import type { PulseHealth } from "../../types";

interface GroundingPulseSectionProps {
  health: PulseHealth;
}

// Grounding Pulse — amendment §6.4. The Grounding Layer signals that the
// backend actually exposes: ungrounded decisions, drifted regions, and
// drifted decisions. IDE/plugin/session source breakdown is not exposed by
// `/pulse`, so it is not rendered here (no fabrication).
//
// Note: `drifted_regions` is currently always 0 — the `/pulse` endpoint
// passes no drift findings. That steady state renders as a clean "0", not
// as breakage.
export function GroundingPulseSection({
  health,
}: GroundingPulseSectionProps) {
  const h = health ?? ({} as PulseHealth);
  const signal = (num: number, label: string, alert = false) => (
    <div class="pulse-stat" key={label}>
      <span class={"pulse-stat-num" + (alert && num > 0 ? " drift" : "")}>
        {num ?? 0}
      </span>
      <span class="pulse-stat-label">{label}</span>
    </div>
  );
  return (
    <section class="pulse-section">
      <div class="pulse-section-name">Grounding Pulse</div>
      <div class="pulse-health">
        {signal(h.decisions_ungrounded, "ungrounded decisions")}
        {signal(h.decisions_drifted, "drifted decisions", true)}
        {signal(h.drifted_regions, "drifted regions", true)}
      </div>
    </section>
  );
}
