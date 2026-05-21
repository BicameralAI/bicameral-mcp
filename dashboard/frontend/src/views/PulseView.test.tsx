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
    last_sync: "2026-05-21T08:00:00Z",
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

describe("PulseView", () => {
  beforeEach(() => {
    window.location.hash = "";
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the golden state — four sections with data", async () => {
    stubPulse(GOLDEN);
    render(<PulseView />);

    expect(await screen.findByText("Adopt hash-based routing")).toBeTruthy();
    expect(screen.getByText("Health")).toBeTruthy();
    expect(screen.getByText("Needs Attention")).toBeTruthy();
    expect(screen.getByText("Recently Learned")).toBeTruthy();
    expect(screen.getByText("Suggested Next Move")).toBeTruthy();
    expect(screen.getByText("Review 2 drifted decisions")).toBeTruthy();
    // Signer email is reduced to the local-part.
    expect(screen.getByText("alice")).toBeTruthy();
    expect(screen.queryByText("alice@example.com")).toBeNull();
    // source_type + source_ref joined.
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

  it("escapes user-sourced summaries (no raw HTML injection)", async () => {
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

    // The payload is rendered as text — the string is present verbatim and
    // no <img> element was created from it.
    expect(
      await screen.findByText("<img src=x onerror=alert(1)>"),
    ).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });
});
