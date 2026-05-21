// TypeScript types for the `GET /pulse` response (ProjectPulseSummary).
// Mirrors the #437 Project Pulse contract the legacy dashboard renders.

export interface PulseHealth {
  decisions_reflected: number;
  decisions_drifted: number;
  decisions_pending: number;
  decisions_ungrounded: number;
  drifted_regions: number;
  last_sync: string | null;
}

export interface NeedsAttentionItem {
  kind: string;
  decision_id: string;
  summary: string;
  signer: string | null;
}

export interface LearnedItem {
  decision_id: string;
  summary: string;
  source_type: string | null;
  source_ref: string | null;
  date: string | null;
}

export interface ProjectPulseSummary {
  health: PulseHealth;
  needs_attention: NeedsAttentionItem[];
  recently_learned: LearnedItem[];
  suggested_next_move: string;
  is_all_clear: boolean;
}

// The `/pulse` endpoint returns `{error}` on the failure path.
export interface PulseError {
  error: string;
}

export type PulseResponse = ProjectPulseSummary | PulseError;

export function isPulseError(r: PulseResponse): r is PulseError {
  return (r as PulseError).error !== undefined;
}
