import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/preact";
import { SidebarNav } from "./SidebarNav";

describe("SidebarNav", () => {
  it("renders all 8 nav entries in canonical order", () => {
    render(<SidebarNav active="pulse" />);
    const labels = [
      "Pulse",
      "Ledger",
      "Ratification",
      "Drift",
      "Sources",
      "Audit",
      "Integrations",
      "Settings",
    ];
    const items = screen.getAllByRole("link");
    expect(items).toHaveLength(8);
    items.forEach((el, i) => {
      expect(el.textContent).toBe(labels[i]);
    });
  });

  it("marks the active entry with aria-current and the active class", () => {
    render(<SidebarNav active="drift" />);
    const active = screen.getByText("Drift");
    expect(active.getAttribute("aria-current")).toBe("page");
    expect(active.className).toContain("active");

    const inactive = screen.getByText("Pulse");
    expect(inactive.getAttribute("aria-current")).toBeNull();
    expect(inactive.className).not.toContain("active");
  });

  it("links each entry to its hash route", () => {
    render(<SidebarNav active="pulse" />);
    expect(screen.getByText("Ledger").getAttribute("href")).toBe("#/ledger");
    expect(screen.getByText("Settings").getAttribute("href")).toBe(
      "#/settings",
    );
  });
});
