import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/preact";
import { PulseView } from "./PulseView";
import type { ProjectPulseSummary } from "../types";

const GOLDEN: ProjectPulseSummary = {
  health: {
    decisions_reflected: 12,
    decisions_drifted: 2,
    decisions_pending: 1,
    decisions_ungrounded: 3,
    drifted_regions: 4,
    last_sync: "2026-05-20T10:00:00Z",
  },
  needs_attention: [
    {
      kind: "pending_signoff",
      decision_id: "decision:abc",
      summary: "Adopt hash-based routing",
      signer: "alice@example.com",
    },
  ],
  recently_learned: [
    {
      decision_id: "decision:xyz",
      summary: "Pin every npm dependency",
      source_type: "github",
      source_ref: "PR #501",
      date: "2026-05-19T09:00:00Z",
    },
  ],
  suggested_next_move: "Review 2 drifted decisions",
  is_all_clear: false,
};

const ALL_CLEAR: ProjectPulseSummary = {
  health: {
    decisions_reflected: 8,
    decisions_drifted: 0,
    decisions_pending: 0,
    decisions_ungrounded: 0,
    drifted_regions: 0,
    last_sync: null,
  },
  needs_attention: [],
  recently_learned: [],
  suggested_next_move: "Keep building",
  is_all_clear: true,
};

function stubPulse(body: unknown, ok = true) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok,
      status: ok ? 200 : 500,
      json: async () => body,
    })),
  );
}

describe("PulseView (M2 redesign)", () => {
  beforeEach(() => {
    window.location.hash = "";
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("composes every M2 section in the golden state", async () => {
    stubPulse(GOLDEN);
    render(<PulseView />);

    expect(await screen.findByText("Adopt hash-based routing")).toBeTruthy();
    expect(screen.getByText("Two-Chamber Overview")).toBeTruthy();
    expect(screen.getByText("Suggested Next Move")).toBeTruthy();
    expect(screen.getByText("Needs Attention")).toBeTruthy();
    expect(screen.getByText("Decision Ledger Health")).toBeTruthy();
    expect(screen.getByText("Grounding Pulse")).toBeTruthy();
    expect(screen.getByText("Recently Learned")).toBeTruthy();
    expect(screen.getByText("Dependency Pulse")).toBeTruthy();
    expect(screen.getByText("Team Memory State")).toBeTruthy();

    expect(screen.getByText("Review 2 drifted decisions")).toBeTruthy();
    // Signer email reduced to local-part.
    expect(screen.getByText("alice")).toBeTruthy();
    expect(screen.queryByText("alice@example.com")).toBeNull();
    // source_type + source_ref joined in the activity feed.
    expect(screen.getByText("github · PR #501")).toBeTruthy();
  });

  it("renders the all-clear friendly state", async () => {
    stubPulse(ALL_CLEAR);
    render(<PulseView />);

    expect(
      await screen.findByText(/No drift, no pending signoffs/),
    ).toBeTruthy();
    expect(screen.getByText("Nothing awaiting attention.")).toBeTruthy();
    expect(screen.getByText("No decisions recorded yet.")).toBeTruthy();
  });

  it("renders the steady state cleanly — last_sync null, drifted_regions 0", async () => {
    // The real /pulse endpoint always emits last_sync=null and
    // drifted_regions=0 today. That must look like a clean baseline,
    // not like breakage.
    stubPulse(ALL_CLEAR);
    render(<PulseView />);

    // "never" appears as the honest last-sync value, not an error.
    expect(await screen.findByText("never")).toBeTruthy();
    // No error banner is shown.
    expect(screen.queryByText(/unavailable/)).toBeNull();
    // The Dependency Pulse placeholder is present and labelled forthcoming.
    expect(screen.getAllByText("Not yet available").length).toBeGreaterThan(0);
  });

  it("renders the error state from a {error} payload", async () => {
    stubPulse({ error: "ledger offline" });
    render(<PulseView />);

    expect(
      await screen.findByText(/Project Pulse unavailable: ledger offline/),
    ).toBeTruthy();
  });

  it("renders the error state from a non-2xx response", async () => {
    stubPulse({}, false);
    render(<PulseView />);

    expect(
      await screen.findByText(/Project Pulse unavailable/),
    ).toBeTruthy();
  });

  it("escapes a user-sourced summary (text child, no HTML injection)", async () => {
    stubPulse({
      ...GOLDEN,
      needs_attention: [
        {
          kind: "pending_signoff",
          decision_id: "decision:evil",
          summary: "<img src=x onerror=alert(1)>",
          signer: null,
        },
      ],
    });
    const { container } = render(<PulseView />);

    expect(
      await screen.findByText("<img src=x onerror=alert(1)>"),
    ).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });

  it("escapes a malicious decision_id in the data-decision-id attribute", async () => {
    // decision_id reaches the DOM as a JSX prop (attribute) — Preact
    // escapes attribute values too. The payload must land verbatim in the
    // attribute and spawn no element.
    const evilId = '"><img src=x onerror=alert(1)>';
    stubPulse({
      ...GOLDEN,
      needs_attention: [
        {
          kind: "pending_signoff",
          decision_id: evilId,
          summary: "attribute escape probe",
          signer: null,
        },
      ],
    });
    const { container } = render(<PulseView />);

    await screen.findByText("attribute escape probe");
    const row = container.querySelector("[data-decision-id]");
    expect(row).toBeTruthy();
    // Attribute value is the exact payload string, not parsed as markup.
    expect(row?.getAttribute("data-decision-id")).toBe(evilId);
    expect(container.querySelector("img")).toBeNull();
  });

  it("escapes a malicious date through the fmtDate path", async () => {
    // An unparseable date falls through fmtDate to String(value); it must
    // still render as an escaped text child, never as markup.
    const evilDate = "<svg onload=alert(1)>";
    stubPulse({
      ...GOLDEN,
      recently_learned: [
        {
          decision_id: "decision:date",
          summary: "date escape probe",
          source_type: null,
          source_ref: null,
          date: evilDate,
        },
      ],
    });
    const { container } = render(<PulseView />);

    expect(await screen.findByText(evilDate)).toBeTruthy();
    expect(container.querySelector("svg")).toBeNull();
  });
});
