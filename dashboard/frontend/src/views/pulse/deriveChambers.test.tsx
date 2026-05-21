import { describe, expect, it } from "vitest";
import { deriveChambers } from "./deriveChambers";
import type { PulseHealth } from "../../types";

const HEALTH: PulseHealth = {
  decisions_reflected: 40,
  decisions_drifted: 5,
  decisions_pending: 3,
  decisions_ungrounded: 7,
  drifted_regions: 2,
  last_sync: null,
};

describe("deriveChambers", () => {
  it("returns distinct named stats traceable 1:1 to backend fields", () => {
    const { intent, execution } = deriveChambers(HEALTH, 2);

    expect(intent.pending).toBe(3); // health.decisions_pending
    expect(intent.awaitingSignoff).toBe(2); // needs_attention count
    expect(intent.reflected).toBe(40); // health.decisions_reflected

    expect(execution.drifted).toBe(5); // health.decisions_drifted
    expect(execution.ungrounded).toBe(7); // health.decisions_ungrounded
    expect(execution.driftedRegions).toBe(2); // health.drifted_regions
  });

  it("performs NO cross-axis sum — pending and awaitingSignoff stay separate", () => {
    // status (decisions_pending) and signoff.state (needs_attention count)
    // are ORTHOGONAL axes; a decision can be on both. The MAJOR audit
    // finding: deriveChambers must never produce pending + awaitingSignoff.
    const { intent } = deriveChambers(HEALTH, 2);

    // The two stats remain their own values.
    expect(intent.pending).toBe(3);
    expect(intent.awaitingSignoff).toBe(2);

    // Their sum (5) must NOT appear as any value in the chamber model.
    const summed = intent.pending + intent.awaitingSignoff;
    expect(summed).toBe(5);
    const allValues = Object.values(intent);
    expect(allValues).not.toContain(summed);

    // And no chamber field equals that combined scalar.
    expect(intent.pending).not.toBe(summed);
    expect(intent.awaitingSignoff).not.toBe(summed);
    expect(intent.reflected).not.toBe(summed);
  });

  it("keeps stats distinct even when the two axes hold equal values", () => {
    // If decisions_pending == needs_attention count, they must still be
    // two separate fields — not collapsed, not doubled.
    const { intent } = deriveChambers(
      { ...HEALTH, decisions_pending: 4 },
      4,
    );
    expect(intent.pending).toBe(4);
    expect(intent.awaitingSignoff).toBe(4);
    // Not summed to 8.
    expect(Object.values(intent)).not.toContain(8);
  });

  it("coerces missing/zero inputs to 0 without inventing data", () => {
    const empty = {} as PulseHealth;
    const { intent, execution } = deriveChambers(empty, 0);
    expect(intent).toEqual({ pending: 0, awaitingSignoff: 0, reflected: 0 });
    expect(execution).toEqual({
      drifted: 0,
      ungrounded: 0,
      driftedRegions: 0,
    });
  });
});
