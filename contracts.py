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
    # v0.4.14: meeting context — the raw passage from the source that
    # produced this decision, plus the meeting date if known. Pulled
    # from source_span.text via the yields reverse edge. Empty when
    # the source_span has no text or no link to this intent.
    source_excerpt: str = ""
    meeting_date: str = ""


class LinkCommitResponse(BaseModel):
    """Returned by /link_commit and embedded in /search_decisions + /detect_drift.

    v0.4.11 (latent drift fix):
      - ``decisions_reflected`` and ``decisions_drifted`` count **distinct
        intent_ids** that flipped this sweep, not (region, intent) pairs.
        A decision with 5 regions all flipping to drifted now reports 1,
        not 5. Matches user mental model.
      - ``sweep_scope`` and ``range_size`` describe what the sweep covered.
        ``head_only`` is the v0.4.10 behavior — only files in the HEAD
        commit's own diff. ``range_diff`` is the v0.4.11 default — files
        changed between ``last_synced_commit`` and HEAD. ``range_truncated``
        means the range exceeded ``MAX_SWEEP_FILES`` (200) so we capped it
        and only swept the first chunk; the remainder will catch up on
        next sync.
    """
    commit_hash: str
    synced: bool                      # False = new work done; True = fast-path
    reason: Literal["new_commit", "already_synced", "no_changes"]
    regions_updated: int = 0
    decisions_reflected: int = 0     # distinct intents that flipped to reflected
    decisions_drifted: int = 0       # distinct intents that flipped to drifted
    undocumented_symbols: list[str] = []
    # v0.4.11: sweep scope provenance.
    sweep_scope: Literal[
        "head_only",       # files in HEAD commit only (first sync, or fallback)
        "range_diff",      # files in last_synced..HEAD range (default after first sync)
        "range_truncated", # range exceeded MAX_SWEEP_FILES; capped
    ] = "head_only"
    range_size: int = 0              # number of files swept in this run


class ActionHint(BaseModel):
    """v0.4.9 (Phase 2): tester-mode directive appended to search/brief
    responses. Blocking hints MUST be addressed by the agent before any
    write operation (file edits, commits, PRs, bicameral_ingest). Skill
    contracts enforce this — the wire protocol itself is advisory.

    Kinds:
      - ``answer_open_questions`` — matched decisions have unresolved
        open questions the agent should resolve with the user first.
      - ``review_drift`` — at least one matched decision is drifted.
        Surface the drifted region before editing anywhere near it.
      - ``resolve_divergence`` — two non-superseded decisions contradict
        on the same symbol. Human resolution required.
      - ``ground_decision`` — a matched decision has no code regions.
        Call ``bicameral_ingest`` on the referenced intent with fresh
        grounding before acting on it.

    Empty when ``ctx.tester_mode`` is False (default), so regular-mode
    responses are byte-identical to v0.4.8.
    """
    kind: Literal[
        "answer_open_questions",
        "review_drift",
        "resolve_divergence",
        "ground_decision",
    ]
    message: str                    # 1-sentence directive, agent-facing
    blocking: bool                  # True = skill contract forbids writes until addressed
    refs: list[str] = []            # kind-specific refs (intent_ids, file paths, question texts)


class SearchDecisionsResponse(BaseModel):
    query: str
    sync_status: LinkCommitResponse  # result of auto-triggered link_commit
    matches: list[DecisionMatch]
    ungrounded_count: int            # matches with no code region
    suggested_review: list[str]      # intent_ids of drifted/pending to review first
    # v0.4.9 (Phase 2): populated only when ctx.tester_mode is True.
    action_hints: list[ActionHint] = []


# ── Tool 3: /detect_drift ────────────────────────────────────────────


class DriftEntry(BaseModel):
    intent_id: str
    description: str
    status: Literal["reflected", "drifted", "pending", "ungrounded"]
    symbol: str
    lines: tuple[int, int]
    drift_evidence: str = ""
    source_ref: str
    # v0.4.14: meeting context tied to this decision (see DecisionMatch).
    source_excerpt: str = ""
    meeting_date: str = ""


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
    grounded: int = 0
    grounded_pct: float = 0.0  # grounded / intents_created, 0.0 when intents_created == 0
    grounding_deferred: int = 0  # index not ready at ingest time — re-ingest after build_index
    cache_hits: int = 0  # decision grounding reuse: skipped BM25 via similar prior intent


class IngestResponse(BaseModel):
    ingested: bool
    repo: str
    query: str
    source_refs: list[str]
    stats: IngestStats
    ungrounded_intents: list[str]
    source_cursor: SourceCursorSummary | None = None
    # v0.4.8: ingest auto-fires bicameral_brief on the derived topic and
    # embeds the full brief response here. None when topic derivation yields
    # nothing usable, or when the chained brief call fails (logged, not
    # raised — ingest must not fail because post-phase brief had a hiccup).
    brief: "BriefResponse | None" = None


# ── Tool 6: /bicameral_brief — pre-meeting one-pager ────────────────


class BriefDecision(BaseModel):
    """One decision surfaced in a brief. Strict subset of DecisionStatusEntry
    to keep the brief response small and scan-friendly."""
    intent_id: str
    description: str
    status: Literal["reflected", "drifted", "pending", "ungrounded"]
    source_type: str = ""
    source_ref: str = ""
    code_regions: list[CodeRegionSummary] = []
    severity_tier: int = 1  # 1=L1, 2=L2, 3=L3 — populated by v0.4.7 severity config
    drift_evidence: str = ""
    # v0.4.14: meeting context tied to this decision (see DecisionMatch).
    source_excerpt: str = ""
    meeting_date: str = ""


class BriefGap(BaseModel):
    """A gap surfaced from the brief — a decision area where acceptance
    criteria or follow-up answers are missing."""
    description: str
    hint: str  # why this is a gap (e.g. "no acceptance criteria", "open question phrasing")
    relevant_source_refs: list[str] = []


class BriefQuestion(BaseModel):
    """A suggested question for the pre-meeting artifact."""
    question: str
    why: str  # rationale for asking this — what gap/drift/divergence motivated it


class BriefDivergence(BaseModel):
    """Two or more non-superseded intents mapping to the same symbol with
    contradictory descriptions. Branch Problem Instance 4 — detected, not
    resolved. The human picks which wins via a later forget/revise call.
    """
    symbol: str
    file_path: str
    conflicting_decisions: list[BriefDecision]  # >= 2 entries
    summary: str  # one-line framing suitable for a PR comment


class BriefResponse(BaseModel):
    """Response envelope for bicameral_brief(topic)."""
    topic: str
    participants: list[str] = []
    as_of: str  # ISO datetime of generation
    ref: str    # git ref at time of generation
    decisions: list[BriefDecision] = []
    drift_candidates: list[BriefDecision] = []  # subset of decisions with status=drifted
    divergences: list[BriefDivergence] = []     # Branch Problem Instance 4 detector output
    gaps: list[BriefGap] = []
    suggested_questions: list[BriefQuestion] = []
    # v0.4.9 (Phase 2): populated only when ctx.tester_mode is True.
    action_hints: list[ActionHint] = []


# ── Tool 7: /bicameral_reset — fail-safe recovery ───────────────────


class ResetReplayEntry(BaseModel):
    """One entry in the reset replay plan — summarizes a source that would
    need to be re-ingested to restore the wiped ledger."""
    source_type: str
    source_scope: str
    last_source_ref: str = ""


class ResetResponse(BaseModel):
    """Response envelope for bicameral_reset(confirm=False|True)."""
    wiped: bool
    ledger_url: str                      # the SURREAL_URL that was / would be wiped
    repo: str                            # repo scope for this wipe
    cursors_before: int                  # how many source_cursor rows existed
    replay_plan: list[ResetReplayEntry] = []
    replay_errors: list[str] = []
    next_action: str                     # human-readable next step for the caller


# ── Tool 9: /bicameral_preflight — proactive context surfacing (v0.4.12) ──


class PreflightResponse(BaseModel):
    """Response envelope for bicameral_preflight(topic).

    The handler runs ``bicameral.search`` (and optionally ``bicameral.brief``)
    against the topic and returns a gated decision: should the agent
    surface this context to the user, or proceed silently?

    Gating logic depends on ``ctx.guided_mode``:

    - **Normal mode** (``guided_mode=False``): less intense. ``fired=True``
      only when search matches contain **actionable signal** — at least one
      drifted match, ungrounded match, divergent pair, or open question.
      Plain matches (all reflected, no drift, no questions) → ``fired=False``.
      Trust contract: surface only when there's something the developer
      actually needs to know.

    - **Guided mode** (``guided_mode=True``): standard. ``fired=True`` when
      search returns any matches at all. Surface even on plain matches —
      the user opted into the loud experience.

    Always-true gates:

    - Topic must validate (≥4 chars, ≥2 non-stopword content tokens, not
      a generic catch-all). Failed validation → ``fired=False`` with
      ``reason="topic_too_generic"``.
    - Per-session dedup: if the same topic was preflight-checked within
      the last 5 minutes of this MCP server session, ``fired=False`` with
      ``reason="recently_checked"``.

    On ``fired=False``, the agent produces NO OUTPUT to the user — that's
    the trust contract. The empty path is silent.
    """
    topic: str                           # the topic that was preflight-checked
    fired: bool                          # True = render output, False = silent skip
    reason: Literal[
        "fired",                         # gates passed, render the response
        "no_matches",                    # search returned nothing
        "no_actionable_signal",          # normal mode + no drift/divergence/etc
        "topic_too_generic",             # topic failed deterministic validation
        "recently_checked",              # per-session dedup hit
        "guided_mode_off",               # ctx.guided_mode is False AND nothing actionable
        "preflight_disabled",            # explicit env override mute
    ]
    guided_mode: bool                    # echo the flag for caller visibility
    # Populated when fired=True. Empty when fired=False.
    decisions: list[BriefDecision] = []
    drift_candidates: list[BriefDecision] = []
    divergences: list[BriefDivergence] = []
    open_questions: list[BriefGap] = []
    action_hints: list[ActionHint] = []
    sources_chained: list[str] = []      # which tools were called: ["search"], ["search", "brief"]


# v0.4.8: resolve the forward reference on IngestResponse.brief (BriefResponse
# is defined further down in the file than IngestResponse).
IngestResponse.model_rebuild()
