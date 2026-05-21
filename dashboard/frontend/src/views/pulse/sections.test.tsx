import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/preact";
import { LedgerHealthSection } from "./LedgerHealthSection";
import { NeedsAttentionSection } from "./NeedsAttentionSection";
import { RecentlyLearnedSection } from "./RecentlyLearnedSection";
import { GroundingPulseSection } from "./GroundingPulseSection";
import { SuggestedNextMove } from "./SuggestedNextMove";
import { TeamMemoryState } from "./TeamMemoryState";
import type { PulseHealth } from "../../types";

const HEALTH: PulseHealth = {
  decisions_reflected: 12,
  decisions_drifted: 2,
  decisions_pending: 1,
  decisions_ungrounded: 3,
  drifted_regions: 4,
  last_sync: "2026-05-20T10:00:00Z",
};

const STEADY: PulseHealth = {
  decisions_reflected: 0,
  decisions_drifted: 0,
  decisions_pending: 0,
  decisions_ungrounded: 0,
  drifted_regions: 0,
  last_sync: null,
};

describe("LedgerHealthSection", () => {
  it("renders the 4 backend-backed statuses and the honest gap note", () => {
    render(<LedgerHealthSection health={HEALTH} />);
    expect(screen.getByText("Decision Ledger Health")).toBeTruthy();
    expect(screen.getByText("reflected")).toBeTruthy();
    expect(screen.getByText("drifted")).toBeTruthy();
    expect(screen.getByText("pending")).toBeTruthy();
    expect(screen.getByText("ungrounded")).toBeTruthy();
    // Honest note: the 3 missing statuses are not fabricated.
    expect(
      screen.getByText(/Ratified, rejected and superseded counts/),
    ).toBeTruthy();
  });
});

describe("NeedsAttentionSection", () => {
  it("renders rows with the local-part signer", () => {
    render(
      <NeedsAttentionSection
        items={[
          {
            kind: "pending_signoff",
            decision_id: "decision:1",
            summary: "Adopt routing",
            signer: "bob@example.com",
          },
        ]}
      />,
    );
    expect(screen.getByText("Adopt routing")).toBeTruthy();
    expect(screen.getByText("pending signoff")).toBeTruthy();
    expect(screen.getByText("bob")).toBeTruthy();
  });

  it("renders the empty state", () => {
    render(<NeedsAttentionSection items={[]} />);
    expect(screen.getByText("Nothing awaiting attention.")).toBeTruthy();
  });
});

describe("RecentlyLearnedSection", () => {
  it("renders a dated activity feed", () => {
    render(
      <RecentlyLearnedSection
        items={[
          {
            decision_id: "decision:2",
            summary: "Pin deps",
            source_type: "github",
            source_ref: "PR #1",
            date: "2026-05-19T09:00:00Z",
          },
        ]}
      />,
    );
    expect(screen.getByText("Pin deps")).toBeTruthy();
    expect(screen.getByText("github · PR #1")).toBeTruthy();
    expect(screen.getByText("2026-05-19")).toBeTruthy();
  });

  it("renders the empty state", () => {
    render(<RecentlyLearnedSection items={[]} />);
    expect(screen.getByText("No decisions recorded yet.")).toBeTruthy();
  });
});

describe("GroundingPulseSection", () => {
  it("renders the grounding signals", () => {
    render(<GroundingPulseSection health={HEALTH} />);
    expect(screen.getByText("Grounding Pulse")).toBeTruthy();
    expect(screen.getByText("ungrounded decisions")).toBeTruthy();
    expect(screen.getByText("drifted decisions")).toBeTruthy();
    expect(screen.getByText("drifted regions")).toBeTruthy();
  });

  it("renders drifted_regions 0 cleanly as a steady-state baseline", () => {
    const { container } = render(<GroundingPulseSection health={STEADY} />);
    // drifted_regions=0 is the real steady state — a plain "0", no alert.
    const stats = container.querySelectorAll(".pulse-stat-num");
    expect(stats.length).toBe(3);
    stats.forEach((s) => {
      expect(s.textContent).toBe("0");
      expect(s.className).not.toContain("drift");
    });
  });
});

describe("SuggestedNextMove", () => {
  it("renders the suggested move", () => {
    render(<SuggestedNextMove move="Review drift" />);
    expect(screen.getByText("Suggested Next Move")).toBeTruthy();
    expect(screen.getByText("Review drift")).toBeTruthy();
  });
});

describe("TeamMemoryState", () => {
  it("shows 'never' when last_sync is null (the real steady state)", () => {
    render(<TeamMemoryState health={STEADY} />);
    expect(screen.getByText("Team Memory State")).toBeTruthy();
    expect(screen.getByText("never")).toBeTruthy();
    // The rich freshness model is an explicit not-yet-available card.
    expect(screen.getByText("Not yet available")).toBeTruthy();
    expect(screen.getByText("Drive freshness ribbon")).toBeTruthy();
  });

  it("shows the formatted date when last_sync is populated", () => {
    render(<TeamMemoryState health={HEALTH} />);
    expect(screen.getByText("2026-05-20")).toBeTruthy();
  });
});
