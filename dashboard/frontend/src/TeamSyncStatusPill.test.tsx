import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/preact";
import { TeamSyncStatusPill } from "./TeamSyncStatusPill";

describe("TeamSyncStatusPill", () => {
  it("renders the Solo state with a visible text label", () => {
    render(<TeamSyncStatusPill />);
    // The label is real text, not color-only — required for accessibility.
    expect(screen.getByText("Solo")).toBeTruthy();
  });

  it("exposes the state via an accessible role and label", () => {
    render(<TeamSyncStatusPill />);
    const pill = screen.getByRole("status");
    expect(pill.getAttribute("aria-label")).toContain("Solo");
  });
});
