import type { ChamberModel } from "./deriveChambers";

interface TwoChamberOverviewProps {
  chambers: ChamberModel;
}

interface StatDef {
  num: number;
  label: string;
  /** Highlight as a drift/attention signal when non-zero. */
  alert?: boolean;
}

function StatCluster({ stats }: { stats: StatDef[] }) {
  return (
    <div class="chamber-stats">
      {stats.map((s) => (
        <div class="chamber-stat" key={s.label}>
          <span
            class={
              "chamber-stat-num" +
              (s.alert && s.num > 0 ? " alert" : "")
            }
          >
            {s.num}
          </span>
          <span class="chamber-stat-label">{s.label}</span>
        </div>
      ))}
    </div>
  );
}

// Two-Chamber Overview — Intent (Decision Ledger axis) vs Execution
// (Grounding axis), side by side.
//
// CRITICAL: each chamber renders a cluster of DISTINCT labelled stats.
// `pending` and `awaiting signoff` are two separate numbers because they
// live on orthogonal axes (status vs signoff.state) — see deriveChambers.ts.
// There is no summed scalar anywhere in this component.
export function TwoChamberOverview({ chambers }: TwoChamberOverviewProps) {
  const { intent, execution } = chambers;
  return (
    <section class="pulse-section">
      <div class="pulse-section-name">Two-Chamber Overview</div>
      <div class="chamber-grid">
        <div class="chamber chamber-intent">
          <div class="chamber-head">
            <span class="chamber-title">Intent</span>
            <span class="chamber-sub">Decision Ledger · PM</span>
          </div>
          <StatCluster
            stats={[
              { num: intent.pending, label: "pending" },
              { num: intent.awaitingSignoff, label: "awaiting signoff" },
              { num: intent.reflected, label: "reflected" },
            ]}
          />
        </div>
        <div class="chamber chamber-execution">
          <div class="chamber-head">
            <span class="chamber-title">Execution</span>
            <span class="chamber-sub">Grounding · Dev</span>
          </div>
          <StatCluster
            stats={[
              { num: execution.drifted, label: "drifted", alert: true },
              { num: execution.ungrounded, label: "ungrounded" },
              {
                num: execution.driftedRegions,
                label: "drifted regions",
                alert: true,
              },
            ]}
          />
        </div>
      </div>
    </section>
  );
}
