import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/preact";
import { AppShell } from "./AppShell";

// The router is exercised through AppShell so the hash -> active-view
// behavior is verified end to end (sociable: real router, real views).

describe("hash router", () => {
  beforeEach(() => {
    // Stub /pulse so PulseView (the default route) resolves deterministically.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ error: "stubbed" }),
      })),
    );
    window.location.hash = "";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.location.hash = "";
  });

  it("defaults to the Pulse route when the hash is empty", async () => {
    render(<AppShell />);
    expect(window.location.hash).toBe("#/pulse");
    expect(await screen.findByText("Project Pulse")).toBeTruthy();
  });

  it("switches the active view on hashchange", async () => {
    render(<AppShell />);
    await screen.findByText("Project Pulse");

    window.location.hash = "#/settings";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(await screen.findByText("Coming soon")).toBeTruthy();
    // Query the nav link by role — "Settings" also appears as the
    // Placeholder view's heading, so getByText would be ambiguous.
    expect(
      screen.getByRole("link", { name: "Settings" }).className,
    ).toContain("active");

    window.location.hash = "#/ledger";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(
      await screen.findByTitle("Bicameral Decision Ledger"),
    ).toBeTruthy();
  });

  it("falls back to Pulse for an unknown hash", async () => {
    window.location.hash = "#/nonsense";
    render(<AppShell />);
    expect(await screen.findByText("Project Pulse")).toBeTruthy();
  });
});
