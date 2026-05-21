// deriveChambers — pure derivation of the Two-Chamber Overview model.
//
// ── The orthogonal-axes rule (audit MAJOR finding) ──────────────────
// `health.decisions_pending` is a *status* enum value. The `needs_attention`
// count is the number of decisions whose *signoff.state* == "proposed".
// These are TWO INDEPENDENT AXES of the same decision — a single decision
// can be `pending` in status AND awaiting signoff at the same time.
//
// Therefore this function performs NO ADDITION across those axes. It NEVER
// produces a single combined scalar like `pending + awaitingSignoff`.
// Every value below is a distinct, separately-named stat that traces 1:1
// to exactly one real backend field. The TwoChamberOverview renders them
// as separate numbers; the unit tests assert no cross-axis sum exists.

import type { PulseHealth } from "../../types";

/** Intent chamber — the Decision Ledger axis (PM-owned signoff state). */
export interface IntentChamber {
  /** `health.decisions_pending` — status enum. Distinct from awaitingSignoff. */
  pending: number;
  /** count of `needs_attention[]` — signoff.state == "proposed". Distinct from pending. */
  awaitingSignoff: number;
  /** `health.decisions_reflected` — status enum. */
  reflected: number;
}

/** Execution chamber — the Grounding axis (Dev-owned implementation evidence). */
export interface ExecutionChamber {
  /** `health.decisions_drifted`. */
  drifted: number;
  /** `health.decisions_ungrounded`. */
  ungrounded: number;
  /** `health.drifted_regions`. */
  driftedRegions: number;
}

export interface ChamberModel {
  intent: IntentChamber;
  execution: ExecutionChamber;
}

/**
 * Derive the two-chamber model from existing `/pulse` data only.
 *
 * @param health             the `health` block of ProjectPulseSummary.
 * @param needsAttentionCount `needs_attention.length` — the count of
 *                            decisions awaiting signoff. Kept as its OWN
 *                            stat; it is never added to `health.*`.
 */
export function deriveChambers(
  health: PulseHealth,
  needsAttentionCount: number,
): ChamberModel {
  const h = health ?? ({} as PulseHealth);
  return {
    intent: {
      pending: h.decisions_pending ?? 0,
      // ── Distinct axis: signoff.state, NOT summed into `pending`. ──
      awaitingSignoff: needsAttentionCount ?? 0,
      reflected: h.decisions_reflected ?? 0,
    },
    execution: {
      drifted: h.decisions_drifted ?? 0,
      ungrounded: h.decisions_ungrounded ?? 0,
      driftedRegions: h.drifted_regions ?? 0,
    },
  };
}
