import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/preact";
import { NotAvailable } from "./NotAvailable";
import { DependencyPulseSection } from "./DependencyPulseSection";

describe("NotAvailable", () => {
  it("renders an unmistakable forthcoming-feature card", () => {
    const { container } = render(
      <NotAvailable title="Dependency Pulse" detail="Lands later." />,
    );
    expect(screen.getByText("Dependency Pulse")).toBeTruthy();
    expect(screen.getByText("Not yet available")).toBeTruthy();
    expect(screen.getByText("Lands later.")).toBeTruthy();
    // It is the dedicated placeholder element, not a data section.
    expect(container.querySelector(".pulse-na")).toBeTruthy();
  });
});

describe("DependencyPulseSection", () => {
  it("renders ONLY a not-yet-available card — no fabricated numbers", () => {
    const { container } = render(<DependencyPulseSection />);
    expect(screen.getByText("Dependency Pulse")).toBeTruthy();
    expect(screen.getByText("Not yet available")).toBeTruthy();
    expect(
      screen.getByText("Blast-radius & scope-creep analysis"),
    ).toBeTruthy();
    // No stat numbers are rendered for this section.
    expect(container.querySelector(".pulse-stat")).toBeNull();
    expect(container.querySelector(".chamber-stat")).toBeNull();
  });
});
