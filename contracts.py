"""MCP response contracts — v4 (v0.5.0 decision-tier refactor).

These are the types that cross the MCP boundary. Handlers map from internal
types → these before returning to the MCP caller.

v0.5.0 changes:
  - intent_id → decision_id everywhere (clean break, no aliases)
  - ComplianceVerdict.compliant:bool → verdict:Literal["compliant","drifted","not_relevant"]
  - PendingComplianceCheck.decision_id (was intent_id)
  - IngestDecision gains signoff field
  - New: RatifyResponse
  - ResolveComplianceRejection reason "unknown_intent_id" → "unknown_decision_id"
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Skill telemetry diagnostic models ────────────────────────────────
# One model per skill. extra="forbid" means the handler can detect and
# echo back any field names the LLM sent that don't belong here.


class IngestDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions_ingested: int = 0
    g2_candidates_evaluated: int = 0
    g2_dropped_hard_exclude: int = 0
    g2_dropped_l3: int = 0
    g2_dropped_gate1: int = 0
    g2_dropped_gate2: int = 0
    g2_dropped_implied: int = 0
    g2_parked_context_pending: int = 0
    g2_proposed_count: int = 0
    g2_l1_count: int = 0
    g2_l2_count: int = 0
    g2_user_overrode: int = 0
    g3_decisions_grounded: int = 0
    g3_decisions_ungrounded: int = 0
    g6_compliance_checks_received: int = 0
    g6_verdicts_compliant: int = 0
    g6_verdicts_drifted: int = 0
    g6_verdicts_not_relevant: int = 0
    g6_verdicts_cosmetic_autopass: int = 0


class PreflightDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    g9_history_features_count: int = 0
    g9_features_in_scope: int = 0
    g9_decisions_in_scope: int = 0
    g9_preflight_fired: bool = False
    g10_findings_drift_total: int = 0
    g10_findings_drift_cosmetic_autopass: int = 0
    g10_findings_drift_ask: int = 0
    g10_questions_surfaced: int = 0
    g10_user_overrode: int = 0
    g11_corrections_turns_scanned: int = 0
    g11_corrections_prefilter_retained: int = 0
    g11_corrections_classified_ask: int = 0
    g11_corrections_classified_mechanical: int = 0
    g11_corrections_classified_not: int = 0
    g11_corrections_dedup_removed: int = 0
    g11_user_overrode: int = 0


# Registry: skill_name → diagnostic model class
SKILL_DIAGNOSTIC_MODELS: dict[str, type[BaseModel]] = {
    "bicameral-ingest": IngestDiagnostic,
    "bicameral-preflight": PreflightDiagnostic,
}


# ── Shared sub-types ─────────────────────────────────────────────────


class SyncMetrics(BaseModel):
    """V1 A3 instrumentation — wall-clock timings for sync + write-barrier.

    Populated by sync_middleware.ensure_ledger_synced (sync_catchup_ms) and
    sync_middleware.repo_write_barrier (barrier_held_ms). Either field may
    be ``None`` if that path did not run in the handler — e.g. ledger was
    already synced, or the handler did not take the write barrier.
    """

    sync_catchup_ms: float | None = None
    barrier_held_ms: float | None = None


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
    decision_id: str
    description: str
    status: Literal["reflected", "drifted", "pending", "ungrounded"]
    signoff_state: str | None = (
        None  # proposed | ratified | rejected | collision_pending | context_pending | superseded
    )
    source_type: str  # transcript | notion | document | manual | implementation_choice
    source_ref: str  # meeting ID, Notion page ID, etc.
    ingested_at: str  # ISO datetime
    code_regions: list[CodeRegionSummary]
    drift_evidence: str = ""  # populated when status = "drifted"
    blast_radius: list[str] = []  # symbol names of structural dependents (1-hop)
    source_excerpt: str = ""
    meeting_date: str = ""
    speakers: list[str] = []
    signoff: dict | None = None


class DecisionStatusResponse(BaseModel):
    ref: str  # git ref evaluated against
    as_of: str  # ISO datetime of evaluation
    summary: dict[str, int]  # {"reflected": N, "drifted": N, ...}
    decisions: list[DecisionStatusEntry]


# ── Tool 2: /search_decisions ────────────────────────────────────────


class DecisionMatch(BaseModel):
    decision_id: str
    description: str  # the original decision text
    status: Literal["reflected", "drifted", "pending", "ungrounded"]
    signoff_state: str | None = (
        None  # proposed | ratified | rejected | collision_pending | context_pending | superseded
    )
    confidence: float  # BM25 match score (0–1)
    source_ref: str
    code_regions: list[CodeRegionSummary]
    drift_evidence: str = ""
    related_constraints: list[str] = []
    source_excerpt: str = ""
    meeting_date: str = ""
    signoff: dict | None = None


class PreClassificationHint(BaseModel):
    """Phase 4 (#61) — server-computed structural-drift evidence attached
    to ``PendingComplianceCheck`` when the auto-classifier scored the
    change in the uncertain band [0.30, 0.80).

    The caller LLM may use this as a hint when reasoning about whether
    a code change is genuinely semantic. The caller's verdict always
    wins; this is advisory.
    """

    verdict: Literal["cosmetic", "semantic", "uncertain"]
    confidence: float  # weighted score in [0, 1]
    signals: dict[str, float] = {}  # per-signal contribution
    evidence_refs: list[str] = []  # free-form audit refs


class ComplianceVerdict(BaseModel):
    """One caller-LLM judgment to write back to the compliance cache.

    v0.5.0: verdict replaces compliant:bool with a three-way enum:
      - "compliant"    — code implements the decision correctly
      - "drifted"      — code has drifted from the decision
      - "not_relevant" — retrieval made a mistake; this region is not about
                         this decision. Server will prune the binds_to edge
                         and record compliance_check with pruned=true.

    Phase 4 (#61) — additive optional fields:
      semantic_status: caller's claim about whether this is a cosmetic
                       change (``semantically_preserved``) or a real
                       semantic change (``semantic_change``). Persisted
                       to ``compliance_check.semantic_status`` for the
                       audit trail. ``None`` means "no claim".
      evidence_refs:   free-form audit-trail strings (e.g.
                       ``["signature:1.00", "neighbors:0.97"]``).
    """

    decision_id: str
    region_id: str
    content_hash: str  # echoed from PendingComplianceCheck.content_hash
    verdict: Literal["compliant", "drifted", "not_relevant"]
    confidence: Literal["high", "medium", "low"]
    explanation: str  # one-sentence rationale for audit trail
    phase_metadata: dict = {}
    semantic_status: Literal["semantically_preserved", "semantic_change"] | None = None
    evidence_refs: list[str] = []


class ResolveComplianceRejection(BaseModel):
    """Structured rejection for a verdict that failed input validation."""

    decision_id: str
    region_id: str
    reason: Literal[
        "unknown_decision_id",
        "unknown_region_id",
        "invalid_content_hash",
    ]
    detail: str = ""


class ResolveComplianceAccepted(BaseModel):
    decision_id: str
    region_id: str
    phase: str
    verdict: Literal["compliant", "drifted", "not_relevant"]
    # Phase 4 (#61) additive: echoes the caller's semantic_status claim
    # (or None if the caller didn't provide one).
    semantic_status: Literal["semantically_preserved", "semantic_change"] | None = None


class ResolveComplianceResponse(BaseModel):
    """Response envelope for bicameral.resolve_compliance.

    v0.5.0: accepted entries carry the three-way verdict. not_relevant
    verdicts cause server-side binds_to edge pruning (audit row kept with
    pruned=true). Holistic status is projected via project_decision_status
    after all verdicts in the batch are written.
    """

    phase: Literal["ingest", "drift", "regrounding", "supersession", "divergence"]
    accepted: list[ResolveComplianceAccepted] = []
    rejected: list[ResolveComplianceRejection] = []


class PendingComplianceCheck(BaseModel):
    """One verification job batched for the caller LLM to resolve.

    v0.5.0: decision_id replaces intent_id.

    Phase 4 (#61) additive: ``pre_classification`` carries the
    auto-classifier's structural evidence when the score landed in the
    uncertain band [0.30, 0.80). The caller LLM may use this as a hint
    when reasoning about cosmetic vs semantic; the caller's verdict
    always wins. ``None`` for clearly-semantic pendings (score ≤ 0.30)
    and when ``codegenome.enhance_drift`` is disabled.
    """

    phase: Literal["ingest", "drift", "regrounding"]
    decision_id: str
    region_id: str
    decision_description: str
    file_path: str
    symbol: str
    content_hash: str  # key the verdict must be written against
    code_body: str = ""  # extracted via tree-sitter, capped
    old_code_body: str | None = None  # drift-phase only
    pre_classification: PreClassificationHint | None = None  # Phase 4 (#61)


class ContinuityResolution(BaseModel):
    """Phase 3 (#60) — outcome of the continuity matcher per drifted region.

    Emitted in ``LinkCommitResponse.continuity_resolutions`` when
    ``codegenome.enhance_drift`` is enabled. ``identity_moved`` /
    ``identity_renamed`` indicate auto-resolution (the binding was
    redirected); ``needs_review`` indicates a 0.50-0.75 confidence
    candidate the caller LLM should evaluate.
    """

    decision_id: str
    old_code_region_id: str
    new_code_region_id: str | None = None
    semantic_status: Literal["identity_moved", "identity_renamed", "needs_review"]
    confidence: float = Field(ge=0.0, le=1.0)  # PR #73: bound to probability range
    old_location: CodeRegionSummary
    new_location: CodeRegionSummary | None = None
    rationale: str


class LinkCommitResponse(BaseModel):
    """Returned by /link_commit and embedded in /search_decisions + /detect_drift."""

    commit_hash: str
    synced: bool
    reason: Literal["new_commit", "already_synced", "no_changes"]
    regions_updated: int = 0
    decisions_reflected: int = 0
    decisions_drifted: int = 0
    undocumented_symbols: list[str] = []
    sweep_scope: Literal[
        "head_only",
        "range_diff",
        "range_truncated",
        "branch_delta",
    ] = "head_only"
    range_size: int = 0
    pending_compliance_checks: list[PendingComplianceCheck] = []
    pending_grounding_checks: list[dict] = []
    verification_instruction: str = ""
    flow_id: str = ""
    ephemeral: bool = False
    # Phase 3 (#60) additive: continuity-matcher resolutions per drifted
    # region. Empty when ``codegenome.enhance_drift`` is disabled or no
    # drifted region produces a continuity match.
    continuity_resolutions: list[ContinuityResolution] = []
    # Phase 4 (#61) additive: count of drifted regions auto-resolved as
    # cosmetic (verdict='compliant', semantic_status='semantically_preserved')
    # by the structural classifier. Stripped from
    # ``pending_compliance_checks`` before the response is sent. Zero
    # when ``codegenome.enhance_drift`` is disabled.
    auto_resolved_count: int = 0
    # #65 — preflight telemetry plumb-through. When the caller passed a
    # preflight_id (from a prior bicameral.preflight call), the response
    # echoes it so downstream telemetry rows can be attributed.
    preflight_id: str | None = None


class ActionHint(BaseModel):
    """Tester-mode directive appended to search/brief responses."""

    kind: Literal[
        "answer_open_questions",
        "review_drift",
        "resolve_divergence",
        "ground_decision",
    ]
    message: str
    blocking: bool
    refs: list[str] = []


class SearchDecisionsResponse(BaseModel):
    query: str
    sync_status: LinkCommitResponse
    matches: list[DecisionMatch]
    ungrounded_count: int
    suggested_review: list[str]  # decision_ids of drifted/pending to review first
    action_hints: list[ActionHint] = []
    sync_metrics: SyncMetrics | None = None  # V1 A3 — catch-up / barrier wall times


# ── Tool 3: /detect_drift ────────────────────────────────────────────


class DriftEntry(BaseModel):
    decision_id: str
    description: str
    status: Literal["reflected", "drifted", "pending", "ungrounded"]
    signoff_state: str | None = (
        None  # proposed | ratified | rejected | collision_pending | context_pending | superseded
    )
    symbol: str
    lines: tuple[int, int]
    drift_evidence: str = ""
    source_ref: str
    source_excerpt: str = ""
    meeting_date: str = ""
    # V1 B2 — advisory metadata for the eventual V2 caller-LLM verdict prompt.
    # True only for drifted entries whose HEAD-vs-working-tree byte diff is
    # provably semantics-preserving per ledger.ast_diff.is_cosmetic_change.
    # NEVER gates drift surfacing or status; pure metadata.
    cosmetic_hint: bool = False


class DetectDriftResponse(BaseModel):
    file_path: str
    sync_status: LinkCommitResponse
    source: Literal["working_tree", "HEAD"]
    decisions: list[DriftEntry]
    drifted_count: int
    pending_count: int
    undocumented_symbols: list[str]


# ── Tool 11: /scan_branch ────────────────────────────────────────────


class ScanBranchResponse(BaseModel):
    """Multi-file drift audit across every file changed between base_ref and head_ref.

    Decisions are deduped by decision_id across the full set of changed files.
    """

    base_ref: str
    head_ref: str
    sweep_scope: Literal["head_only", "range_diff", "range_truncated", "branch_delta"]
    range_size: int = 0
    source: Literal["working_tree", "HEAD"]
    decisions: list[DriftEntry] = []
    files_changed: list[str] = []
    drifted_count: int = 0
    pending_count: int = 0
    ungrounded_count: int = 0
    reflected_count: int = 0
    proposal_count: int = 0
    undocumented_symbols: list[str] = []
    action_hints: list[ActionHint] = []


# ── /doctor ──────────────────────────────────────────────────────────


class DoctorLedgerSummary(BaseModel):
    total: int = 0
    drifted: int = 0
    pending: int = 0
    ungrounded: int = 0
    reflected: int = 0
    proposal: int = 0
    stale_proposal: int = 0


class DoctorResponse(BaseModel):
    scope: Literal["file", "branch", "empty"]
    file_scan: DetectDriftResponse | None = None
    branch_scan: ScanBranchResponse | None = None
    ledger_summary: DoctorLedgerSummary | None = None
    action_hints: list[ActionHint] = []


# ── Tool 5: /ingest — INPUT contracts ────────────────────────────────


class IngestSpan(BaseModel):
    """Source excerpt from a meeting, document, or manual input."""

    text: str = ""
    source_type: str = (
        "manual"  # transcript | notion | document | manual | agent_session | implementation_choice
    )
    source_ref: str = ""
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
    signoff: dict | None = None
    feature_group: str | None = None
    decision_level: str | None = None  # L1 | L2 | L3
    parent_decision_id: str | None = None


class IngestDecision(BaseModel):
    """One decision in the natural LLM-generated format.

    v0.5.0: adds signoff. For ingest-time payloads (transcript,
    notion, document, slack) the caller typically sets this at ingest time
    (PM was in the room). For implementation_choice payloads it must be
    None — the handler rejects a non-None signoff on impl-time entries.

    source_excerpt is required (non-empty) per v0.5.0 contract:
    decisions are extracted from source, not inferred. Empty excerpts
    are rejected with a clear error.
    """

    id: str = ""
    title: str = ""
    description: str = ""
    text: str = ""  # tolerant alias for description
    status: str = ""
    participants: list[str] = []
    source_excerpt: str = ""
    signoff: dict | None = None
    feature_group: str | None = None


class IngestActionItem(BaseModel):
    owner: str = "unassigned"
    action: str = ""
    text: str = ""  # tolerant alias for action
    due: str = ""


class IngestPayload(BaseModel):
    """Ingest input — accepts EITHER mappings (internal) or decisions (natural LLM)."""

    repo: str = ""
    commit_hash: str = ""
    query: str = ""
    mappings: list[IngestMapping] = []
    source: str = "manual"  # transcript | notion | slack | document | manual | agent_session | implementation_choice
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
    grounded_pct: float = 0.0
    grounding_deferred: int = 0
    cache_hits: int = 0


class ContextForCandidate(BaseModel):
    """A context_pending decision that the new ingest span may answer.

    Returned in IngestResponse.context_for_candidates when BM25 search finds
    a decision with signoff.state='context_pending' that overlaps with the
    ingested span. Human confirms or rejects via bicameral.resolve_collision.
    """

    span_id: str  # input_span record ID (e.g. 'input_span:abc123')
    decision_id: str
    decision_description: str
    overlap_score: float = 0.0  # rank-position score; raw BM25 score is always 0 in v2 embedded


class CreatedDecision(BaseModel):
    """One decision created during an ingest call.

    Returned in IngestResponse.created_decisions so the caller-LLM can
    cross-reference against bicameral.history without fuzzy text matching.
    """

    decision_id: str
    description: str
    decision_level: str | None = None  # L1 | L2 | L3


class IngestResponse(BaseModel):
    ingested: bool
    repo: str
    query: str
    source_refs: list[str]
    stats: IngestStats
    created_decisions: list[CreatedDecision] = []
    pending_grounding_decisions: list[dict] = []
    context_for_candidates: list[ContextForCandidate] = []
    source_cursor: SourceCursorSummary | None = None
    judgment_payload: GapJudgmentPayload | None = None  # kept for backward compat
    judgment_payloads: list[GapJudgmentPayload] = []  # one per feature_group topic
    sync_status: LinkCommitResponse | None = None


class BriefDecision(BaseModel):
    decision_id: str
    description: str
    status: Literal["reflected", "drifted", "pending", "ungrounded"]
    signoff_state: str | None = (
        None  # proposed | ratified | rejected | collision_pending | context_pending | superseded
    )
    source_type: str = ""
    source_ref: str = ""
    code_regions: list[CodeRegionSummary] = []
    severity_tier: int = 1
    drift_evidence: str = ""
    source_excerpt: str = ""
    meeting_date: str = ""
    signoff: dict | None = None
    decision_level: str | None = None  # L1 | L2 | L3 — CodeGenome claim/identity split
    parent_decision_id: str | None = None  # L2 → L1 parent link for evidence inheritance


class BriefGap(BaseModel):
    description: str
    hint: str
    relevant_source_refs: list[str] = []


class BriefDivergence(BaseModel):
    symbol: str
    file_path: str
    conflicting_decisions: list[BriefDecision]
    summary: str


# ── Tool 7: /bicameral_reset ─────────────────────────────────────────


class ResetReplayEntry(BaseModel):
    source_type: str
    source_scope: str
    last_source_ref: str = ""


class ResetResponse(BaseModel):
    wiped: bool
    wipe_mode: str = "ledger"
    ledger_url: str
    bicameral_dir: str = ""
    repo: str
    cursors_before: int
    replay_plan: list[ResetReplayEntry] = []
    replay_errors: list[str] = []
    next_action: str


# ── Tool 9: /bicameral_preflight ─────────────────────────────────────


class PreflightResponse(BaseModel):
    topic: str
    fired: bool
    reason: Literal[
        "fired",
        "no_matches",
        "no_actionable_signal",
        "topic_too_generic",
        "recently_checked",
        "guided_mode_off",
        "preflight_disabled",
    ]
    guided_mode: bool
    decisions: list[BriefDecision] = []
    drift_candidates: list[BriefDecision] = []
    divergences: list[BriefDivergence] = []
    open_questions: list[BriefGap] = []
    action_hints: list[ActionHint] = []
    sources_chained: list[str] = []
    # v0.8.0 HITL annotations (topic-independent, ledger health)
    unresolved_collisions: list[BriefDecision] = []  # collision_pending from prior sessions
    context_pending_ready: list[BriefDecision] = []  # context_pending with ≥1 confirmed context_for
    sync_metrics: SyncMetrics | None = None  # V1 A3 — catch-up wall times
    product_stage: str | None = None  # shown once per device; wait-time expectation-setting
    # #65 — opaque per-call id for the preflight telemetry capture loop.
    # None when telemetry is disabled (BICAMERAL_PREFLIGHT_TELEMETRY != 1).
    preflight_id: str | None = None


# ── Tool 10: /bicameral_judge_gaps ───────────────────────────────────


class GapRubricCategory(BaseModel):
    key: Literal[
        "missing_acceptance_criteria",
        "underdefined_edge_cases",
        "infrastructure_gap",
        "underspecified_integration",
        "missing_data_requirements",
    ]
    title: str
    prompt: str
    output_shape: Literal[
        "bullet_list",
        "happy_sad_table",
        "absence_matrix",
        "dependency_radar",
        "checklist",
    ]
    requires_codebase_crawl: bool = False
    canonical_paths: list[str] = []


class GapRubric(BaseModel):
    version: str = "v0.4.19"
    categories: list[GapRubricCategory]


class GapJudgmentContextDecision(BaseModel):
    decision_id: str
    description: str
    status: Literal["reflected", "drifted", "pending", "ungrounded"]
    source_excerpt: str = ""
    source_ref: str = ""
    meeting_date: str = ""
    related_decision_ids: list[str] = []


class GapJudgmentPayload(BaseModel):
    topic: str
    as_of: str
    decisions: list[GapJudgmentContextDecision] = []
    phrasing_gaps: list[BriefGap] = []
    rubric: GapRubric
    judgment_prompt: str


# ── New in v0.5.0: /bicameral.ratify ─────────────────────────────────


class RatifyResponse(BaseModel):
    """Response envelope for bicameral.ratify.

    Idempotent: calling ratify on an already-signed-off decision returns
    was_new=False and leaves the existing signoff record untouched.
    """

    decision_id: str
    was_new: bool  # True if this call set the signoff; False if already set
    signoff: dict
    projected_status: Literal["reflected", "drifted", "pending", "ungrounded"]
    # #65 — preflight telemetry plumb-through.
    preflight_id: str | None = None


# ── Tool: bicameral.resolve_collision ────────────────────────────────────────


class ResolveCollisionResponse(BaseModel):
    """Response envelope for bicameral.resolve_collision.

    Dual-mode:
      - collision: new_id + old_id + action ('supersede'|'keep_both')
      - context_for: span_id + decision_id + confirmed (bool)
    """

    mode: Literal["collision", "context_for"]
    action_taken: str
    new_decision_id: str = ""  # collision mode
    old_decision_id: str = ""  # collision mode
    span_id: str = ""  # context_for mode
    decision_id: str = ""  # context_for mode
    edge_written: bool = False
    new_status: str = ""  # projected status of new decision after action
    old_status: str = ""  # projected status of old decision (supersede only)


# ── Tool: bicameral.history ──────────────────────────────────────────────────


class HistorySource(BaseModel):
    """One input span that originated or updated a decision."""

    source_ref: str  # e.g. "sprint-14-planning"
    source_type: Literal["transcript", "slack", "document", "agent_session", "manual"]
    date: str  # ISO date
    speaker: str | None = None
    quote: str  # verbatim excerpt from source_span.text


class HistoryFulfillment(BaseModel):
    """Code grounding for a decision."""

    file_path: str
    symbol: str | None = None
    start_line: int
    end_line: int
    git_url: str | None = None
    grounded_at_ref: str = ""  # git ref when first grounded
    baseline_hash: str | None = None
    current_hash: str | None = None


class HistoryDecision(BaseModel):
    """Balance-sheet view of one decision: commitment + fulfillment + balance."""

    id: str  # decision_id
    summary: str  # canonical decision text
    featureId: str
    status: Literal["reflected", "drifted", "pending", "ungrounded"]
    signoff_state: str | None = (
        None  # proposed | ratified | rejected | collision_pending | context_pending | superseded
    )
    sources: list[HistorySource] = []  # 1+ input spans; empty for AI-discovered
    fulfillments: list[HistoryFulfillment] = []  # all bound code regions
    drift_evidence: str | None = None  # human-readable delta when drifted
    signoff: dict | None = None  # ratification record: state, signer, ratified_at
    decision_level: str | None = None  # L1 | L2 | L3 — for balance-sheet display
    parent_decision_id: str | None = None
    ephemeral: bool = False  # True when current status was determined by a feature-branch commit not yet in authoritative ref


class HistoryFeature(BaseModel):
    """A feature group containing related decisions."""

    id: str  # feature group id (slugified name)
    name: str  # canonical feature_group noun phrase
    decisions: list[HistoryDecision]


class HistoryResponse(BaseModel):
    features: list[HistoryFeature]
    truncated: bool = False
    total_features: int = 0
    as_of: str = ""  # git ref evaluated against
    sync_metrics: SyncMetrics | None = None  # V1 A3 — catch-up wall times


# ── Tool 13: bicameral.dashboard ─────────────────────────────────────


class DashboardResponse(BaseModel):
    """Response from bicameral.dashboard."""

    url: str  # http://localhost:{port}
    status: Literal["started", "already_running"]
    port: int


# ── Tool: bicameral.bind ─────────────────────────────────────────────


class BindResult(BaseModel):
    """Result for one binding in a bicameral.bind call."""

    decision_id: str
    region_id: str
    content_hash: str
    pending_compliance_check: PendingComplianceCheck | None = None
    error: str | None = None


class BindResponse(BaseModel):
    """Response envelope for bicameral.bind."""

    bindings: list[BindResult]
    sync_metrics: SyncMetrics | None = None  # V1 A3 — write-barrier hold time
    # #65 — preflight telemetry plumb-through.
    preflight_id: str | None = None


# ── Session-start banner ─────────────────────────────────────────────


class SessionStartBanner(BaseModel):
    """Open-decision summary shown once per session at session start."""

    drifted_count: int = 0
    ungrounded_count: int = 0
    proposal_count: int = 0
    stale_proposal_count: int = 0
    items: list[dict] = []
    message: str = ""
    truncated: bool = False


# Forward references
IngestResponse.model_rebuild()
ResolveCollisionResponse.model_rebuild()
