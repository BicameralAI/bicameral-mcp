import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/preact";
import { TwoChamberOverview } from "./TwoChamberOverview";
import type { ChamberModel } from "./deriveChambers";

const MODEL: ChamberModel = {
  intent: { pending: 3, awaitingSignoff: 2, reflected: 40 },
  execution: { drifted: 5, ungrounded: 7, driftedRegions: 2 },
};

describe("TwoChamberOverview", () => {
  it("renders Intent and Execution chambers", () => {
    render(<TwoChamberOverview chambers={MODEL} />);
    expect(screen.getByText("Intent")).toBeTruthy();
    expect(screen.getByText("Execution")).toBeTruthy();
  });

  it("renders pending and awaiting-signoff as DISTINCT numbers, never summed", () => {
    const { container } = render(<TwoChamberOverview chambers={MODEL} />);

    // Both distinct stats are present as their own labelled values.
    expect(screen.getByText("pending")).toBeTruthy();
    expect(screen.getByText("awaiting signoff")).toBeTruthy();

    // Scope to the Intent chamber: pending=3 and awaitingSignoff=2 each
    // appear as their own stat number; the summed scalar 5 must not.
    const intent = container.querySelector(".chamber-intent");
    expect(intent).toBeTruthy();
    const nums = Array.from(
      intent?.querySelectorAll(".chamber-stat-num") ?? [],
    ).map((n) => n.textContent);
    expect(nums).toEqual(["3", "2", "40"]); // pending, awaitingSignoff, reflected
    expect(nums).not.toContain("5"); // no pending + awaitingSignoff sum
  });

  it("renders all six distinct execution + intent stat labels", () => {
    render(<TwoChamberOverview chambers={MODEL} />);
    for (const label of [
      "pending",
      "awaiting signoff",
      "reflected",
      "drifted",
      "ungrounded",
      "drifted regions",
    ]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });
});
