import { useEffect, useState } from "preact/hooks";
import { fetchPulse } from "../api";
import {
  isPulseError,
  type ProjectPulseSummary,
  type PulseResponse,
} from "../types";
import { deriveChambers } from "./pulse/deriveChambers";
import { TwoChamberOverview } from "./pulse/TwoChamberOverview";
import { LedgerHealthSection } from "./pulse/LedgerHealthSection";
import { NeedsAttentionSection } from "./pulse/NeedsAttentionSection";
import { RecentlyLearnedSection } from "./pulse/RecentlyLearnedSection";
import { GroundingPulseSection } from "./pulse/GroundingPulseSection";
import { DependencyPulseSection } from "./pulse/DependencyPulseSection";
import { TeamMemoryState } from "./pulse/TeamMemoryState";
import { SuggestedNextMove } from "./pulse/SuggestedNextMove";

// ── PulseView — Dashboard v2 Milestone 2 (visual redesign) ──────────
// This view is now a thin composition of small section components under
// ./pulse/. It owns only the fetch lifecycle and the three top-level
// states (loading / error / success), all carried from M1.
//
// XSS discipline (load-bearing): every user-sourced field reaches the DOM
// as a JSX text child or prop inside the section components — Preact
// auto-escapes both. There is NO dangerouslySetInnerHTML and NO
// string-concatenated DOM anywhere in this view or its sections.

function PulseBody({ summary }: { summary: ProjectPulseSummary }) {
  // The two-chamber model is DERIVED from existing data only. The
  // needs_attention count is passed as its own argument and kept as a
  // distinct stat — never summed into a status count. See deriveChambers.ts.
  const chambers = deriveChambers(
    summary.health,
    (summary.needs_attention ?? []).length,
  );
  return (
    <div class="pulse-card">
      <div class="pulse-title">Project Pulse</div>
      {summary.is_all_clear ? (
        <div class="pulse-allclear">
          Bicameral checked project memory.
          <br />
          No drift, no pending signoffs — memory is current.
        </div>
      ) : null}
      <TwoChamberOverview chambers={chambers} />
      <SuggestedNextMove move={summary.suggested_next_move} />
      <NeedsAttentionSection items={summary.needs_attention} />
      <LedgerHealthSection health={summary.health} />
      <GroundingPulseSection health={summary.health} />
      <RecentlyLearnedSection items={summary.recently_learned} />
      <DependencyPulseSection />
      <TeamMemoryState health={summary.health} />
    </div>
  );
}

export function PulseView() {
  const [state, setState] = useState<
    { status: "loading" } | { status: "done"; data: PulseResponse }
  >({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchPulse().then((data) => {
      if (!cancelled) setState({ status: "done", data });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div class="content-pad">
      {state.status === "loading" ? (
        <div class="pulse-card">
          <div class="pulse-title">Project Pulse</div>
          <div class="pulse-loading">Reading project memory…</div>
        </div>
      ) : isPulseError(state.data) ? (
        <div class="pulse-card">
          <div class="pulse-title">Project Pulse</div>
          <div class="pulse-error">
            Project Pulse unavailable: {state.data.error || "no data"}
          </div>
        </div>
      ) : (
        <PulseBody summary={state.data} />
      )}
    </div>
  );
}
