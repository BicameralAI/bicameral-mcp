"""MCP response contracts — lean, agent-consumable types.

These are the types that cross the MCP boundary. They are NOT the same as the
internal pipeline types in pilot/demo2/contracts.py. Handlers map from internal
types → these before returning to the MCP caller.

Rule: internal types (CodeLocatorPayload, SymbolDecisionResponse, etc.) never
cross the MCP boundary. Only types defined here do.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


# ── Shared sub-types ─────────────────────────────────────────────────


class CodeRegionSummary(BaseModel):
    """Lean code region for MCP responses — no pipeline metadata."""
    file_path: str
    symbol: str
    lines: tuple[int, int]  # (start_line, end_line)
    purpose: str = ""


class SourceCursorSummary(BaseModel):
    repo: str
    source_type: str
    source_scope: str
    cursor: str
    last_source_ref: str = ""
    synced_at: str = ""
    status: str = "ok"
    error: str = ""


# ── Tool 1: /decision_status ─────────────────────────────────────────


class DecisionStatusEntry(BaseModel):
    intent_id: str
    description: str
    status: Literal["reflected", "drifted", "pending", "ungrounded"]
    source_type: str                  # transcript | notion | document | manual
    source_ref: str                   # meeting ID, Notion page ID, etc.
    ingested_at: str                  # ISO datetime
    code_regions: list[CodeRegionSummary]
    drift_evidence: str = ""          # populated when status = "drifted"
    blast_radius: list[str] = []      # symbol names of structural dependents (1-hop)


class DecisionStatusResponse(BaseModel):
    ref: str                          # git ref evaluated against
    as_of: str                        # ISO datetime of evaluation
    summary: dict[str, int]           # {"reflected": N, "drifted": N, ...}
    decisions: list[DecisionStatusEntry]


# ── Tool 2: /search_decisions ────────────────────────────────────────


class DecisionMatch(BaseModel):
    intent_id: str
    description: str                  # the original decision text
    status: Literal["reflected", "drifted", "pending", "ungrounded"]
    confidence: float                 # BM25 match score (0–1)
    source_ref: str
    code_regions: list[CodeRegionSummary]
    drift_evidence: str = ""
    related_constraints: list[str] = []


class LinkCommitResponse(BaseModel):
    """Returned by /link_commit and embedded in /search_decisions + /detect_drift."""
    commit_hash: str
    synced: bool                      # False = new work done; True = fast-path
    reason: Literal["new_commit", "already_synced", "no_changes"]
    regions_updated: int = 0
    decisions_reflected: int = 0     # pending → reflected this run
    decisions_drifted: int = 0       # reflected → drifted this run
    undocumented_symbols: list[str] = []


class SearchDecisionsResponse(BaseModel):
    query: str
    sync_status: LinkCommitResponse  # result of auto-triggered link_commit
    matches: list[DecisionMatch]
    ungrounded_count: int            # matches with no code region
    suggested_review: list[str]      # intent_ids of drifted/pending to review first


# ── Tool 3: /detect_drift ────────────────────────────────────────────


class DriftEntry(BaseModel):
    intent_id: str
    description: str
    status: Literal["reflected", "drifted", "pending", "ungrounded"]
    symbol: str
    lines: tuple[int, int]
    drift_evidence: str = ""
    source_ref: str


class DetectDriftResponse(BaseModel):
    file_path: str
    sync_status: LinkCommitResponse
    source: Literal["working_tree", "HEAD"]
    decisions: list[DriftEntry]
    drifted_count: int
    pending_count: int
    undocumented_symbols: list[str]  # symbols in file with no decision mapping


# ── Tool 4: /link_commit — re-exported here for direct use ───────────
# (LinkCommitResponse defined above alongside SearchDecisionsResponse)


# ── Tool 5: /ingest — INPUT contracts ────────────────────────────────


class IngestSpan(BaseModel):
    """Source excerpt from a meeting, document, or manual input."""
    text: str = ""
    source_type: str = "manual"       # transcript | notion | document | manual
    source_ref: str = ""              # meeting ID, Notion page ID, etc.
    speakers: list[str] = []
    meeting_date: str = ""


class IngestCodeRegion(BaseModel):
    """Pre-resolved code region for a mapping."""
    symbol: str
    file_path: str
    start_line: int = 0
    end_line: int = 0
    type: str = "function"
    purpose: str = ""


class IngestMapping(BaseModel):
    """One decision-to-code mapping in the internal pipeline format."""
    intent: str
    span: IngestSpan = IngestSpan()
    symbols: list[str] = []
    code_regions: list[IngestCodeRegion] = []


class IngestDecision(BaseModel):
    """One decision in the natural LLM-generated format."""
    id: str = ""
    title: str = ""
    description: str = ""
    status: str = ""
    participants: list[str] = []


class IngestActionItem(BaseModel):
    owner: str = "unassigned"
    action: str = ""
    due: str = ""


class IngestPayload(BaseModel):
    """Ingest input — accepts EITHER mappings (internal) or decisions (natural LLM).

    If ``mappings`` is present, it's used directly (internal pipeline format).
    If ``decisions`` is present, they are normalized into mappings automatically.
    """
    # Common fields
    repo: str = ""
    commit_hash: str = ""
    query: str = ""

    # Internal pipeline format
    mappings: list[IngestMapping] = []

    # Natural LLM-generated format (normalized into mappings if present)
    source: str = "manual"
    title: str = ""
    date: str = ""
    participants: list[str] = []
    decisions: list[IngestDecision] = []
    action_items: list[IngestActionItem] = []
    open_questions: list[str] = []


# ── Tool 5: /ingest — RESPONSE contracts ─────────────────────────────


class IngestStats(BaseModel):
    intents_created: int
    symbols_mapped: int
    regions_linked: int
    ungrounded: int
    grounding_deferred: int = 0  # index not ready at ingest time — re-ingest after build_index
    cache_hits: int = 0  # vocab cache: reused prior groundings instead of fresh BM25


class IngestResponse(BaseModel):
    ingested: bool
    repo: str
    query: str
    source_refs: list[str]
    stats: IngestStats
    ungrounded_intents: list[str]
    source_cursor: SourceCursorSummary | None = None
