"""Handler for /bicameral_judge_gaps MCP tool (v0.4.19 — business-only rubric).

Caller-session LLM gap judge. The server builds a structured context
pack — decisions with source excerpts, cross-symbol related decision
ids, phrasing-based gaps, and a 5-category rubric scoped to **business
requirement gaps** with a judgment prompt — and returns it to the
caller. The caller's Claude session applies the rubric in its own LLM
context using only the source excerpts, not the codebase.

v0.4.19: the rubric was narrowed to surface only business requirement
gaps (product/policy/commitment holes). Engineering gaps — wire
protocols, migration mechanics, Dockerfile content, CI pipelines — are
out of scope and explicitly rejected. The ``infrastructure_gap``
category still exists but is reframed: it no longer asks "does the code
have a Dockerfile?" but "did the team sign off on the business
commitments (cost, vendor lock-in, SLA, compliance surface) this
decision implies?" No codebase crawl is required for any category.

Architectural anchor: the server never calls an LLM, never holds an
API key, preserves the ``no-LLM-in-the-server`` invariant from
``git-for-specs.md``. This handler is a pure data-shape builder.

Attached to ``IngestResponse.judgment_payload`` by the ingest auto-
chain when the brief produced at least one decision. Also callable
standalone via ``bicameral.judge_gaps(topic)``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from contracts import (
    DecisionMatch,
    GapJudgmentContextDecision,
    GapJudgmentPayload,
    GapRubric,
    GapRubricCategory,
    SearchDecisionsResponse,
)
from handlers.brief import _extract_gaps
from handlers.search_decisions import handle_search_decisions

logger = logging.getLogger(__name__)


# ── Rubric — 5 business-requirement categories (v0.4.19) ─────────────
#
# All five surface **business requirement gaps** — product/policy/
# commitment holes a PM, product owner, or founder would need to
# resolve before engineering can proceed with confidence. Engineering
# gaps (wire protocols, migration scripts, Dockerfile content, CI
# pipelines) are out of scope and explicitly rejected in each prompt.
# No codebase crawl is required — the rubric reasons over source
# excerpts only.

_CATEGORIES: list[GapRubricCategory] = [
    GapRubricCategory(
        key="missing_acceptance_criteria",
        title="Missing acceptance criteria",
        prompt=(
            "For each decision, ask: does the source_excerpt define a "
            "testable business outcome for 'done'? A business outcome "
            "is observable by a stakeholder — a user sees X, a metric "
            "moves to Y, a compliance check passes. Implementation "
            "milestones (code lands, tests pass, deploy succeeds) are "
            "NOT acceptance criteria; ignore them. If the decision has "
            "no business-observable success condition, list the "
            "specific acceptance questions the room still needs to "
            "answer. Quote the source_excerpt VERBATIM when you cite. "
            "Never invent a success criterion the team did not state."
        ),
        output_shape="bullet_list",
        requires_codebase_crawl=False,
    ),
    GapRubricCategory(
        key="underdefined_edge_cases",
        title="Happy path specified, sad path deferred",
        prompt=(
            "For each decision, identify the happy path (what IS "
            "specified in the source_excerpt). Then identify the sad "
            "path holes from a **business/product** standpoint — "
            "user-state boundaries (free vs paid, anonymous vs logged-"
            "in, first-time vs returning), policy exceptions (refunds, "
            "overrides, escalations), tier boundaries, lifecycle events "
            "(churn, reactivation, account close). Do NOT surface "
            "technical failure modes (retries, timeouts, network "
            "errors, SMTP failures, race conditions) — those are "
            "engineering concerns and out of scope for this rubric. "
            "Render a two-column table: Happy path (what's specified) ↔ "
            "Missing sad path (business edge case deferred). Use only "
            "evidence from the source_excerpt; never invent an edge "
            "case the team did not hint at."
        ),
        output_shape="happy_sad_table",
        requires_codebase_crawl=False,
    ),
    GapRubricCategory(
        key="infrastructure_gap",
        title="Implied infrastructure commitments not signed off",
        prompt=(
            "For each decision, ask whether the implementation "
            "implicitly commits the business to infrastructure that "
            "the team has not discussed. Business commitments hidden "
            "in infrastructure choices include:\n"
            "  - New SaaS dependency → cost center, procurement, "
            "renewal risk\n"
            "  - Specific cloud vendor / region → vendor lock-in, data "
            "portability\n"
            "  - Data residency jurisdiction → legal / compliance "
            "review\n"
            "  - Implicit SLA (uptime, latency, throughput) → did "
            "product commit to it externally?\n"
            "  - Scale assumption (traffic volume, storage growth, "
            "concurrent users) → did product validate the numbers?\n"
            "Do NOT surface technical implementation gaps (missing "
            "Dockerfile, missing CI job, missing env var) — those are "
            "engineering hygiene and out of scope. Only surface items "
            "where a PM, CFO, or legal reviewer would need to say "
            "'yes' before the decision can ship. Render a checklist:\n"
            "  - Decision implies <business commitment> → ○ not "
            "discussed / no sign-off\n"
            "Quote the source_excerpt phrase that implied the "
            "commitment. Never fabricate a commitment the decision "
            "didn't hint at."
        ),
        output_shape="checklist",
        requires_codebase_crawl=False,
    ),
    GapRubricCategory(
        key="underspecified_integration",
        title="Vendor / provider choices not settled",
        prompt=(
            "For each decision, extract the external providers it "
            "implies a business relationship with — payment processor, "
            "email/SMS provider, analytics, CRM, support platform, "
            "auth provider, etc. Focus on the **business choice** "
            "(which vendor, what contract tier, what data-sharing "
            "scope), NOT the wire protocol / auth scheme / API version "
            "(those are engineering details and out of scope). Compare "
            "against the set of providers explicitly named in the "
            "related decisions' excerpts. Render a dependency radar:\n"
            "  - Provider A → ✓ named in decision <intent_id>\n"
            "  - Provider B → ○ implied but never named (which "
            "vendor?)\n"
            "  - Category C → ○ implied but provider category never "
            "discussed (e.g. decision needs 'an email provider' but "
            "none named)\n"
            "Never invent a provider the decision didn't name or "
            "clearly imply a category for."
        ),
        output_shape="dependency_radar",
        requires_codebase_crawl=False,
    ),
    GapRubricCategory(
        key="missing_data_requirements",
        title="Data policy gaps (PII, retention, consent, audit)",
        prompt=(
            "For each decision, ask whether it implies handling "
            "personal / regulated / sensitive data without a stated "
            "**policy**. Policy gaps include:\n"
            "  - PII / PHI fields collected → classification / consent "
            "documented?\n"
            "  - Retention duration → how long is it kept; what "
            "triggers deletion?\n"
            "  - User consent / opt-in → captured at what moment; "
            "revocable how?\n"
            "  - Audit trail / access logging → who can see what is "
            "logged?\n"
            "  - Cross-border data flow → residency / GDPR / CCPA "
            "review?\n"
            "Do NOT surface schema mechanics (migration scripts, "
            "column types, index choices) — those are engineering and "
            "out of scope. Only surface items a legal, privacy, or "
            "compliance reviewer would flag. Render a checklist:\n"
            "  - Decision implies <policy area> → ○ not addressed\n"
            "Quote the exact phrase in source_excerpt that implied "
            "the data concern. Never fabricate a policy implication "
            "the decision didn't hint at."
        ),
        output_shape="checklist",
        requires_codebase_crawl=False,
    ),
]


_JUDGMENT_PROMPT = (
    "You are the caller-session reasoner for bicameral's v0.4.19 "
    "business-requirement gap judge. Apply each of the 5 rubric "
    "categories below to every decision in this context pack, in "
    "rubric order. For each category, emit one section using its "
    "`output_shape`.\n\n"
    "Scope: this rubric surfaces **business requirement gaps** only — "
    "product / policy / commitment holes a PM, founder, or compliance "
    "reviewer would need to resolve. Engineering gaps (wire protocols, "
    "migration mechanics, Dockerfile content, CI pipelines, retry "
    "logic, race conditions) are out of scope. Each category's prompt "
    "specifies what to reject; follow those rejection rules strictly "
    "— a finding that's technically correct but engineering-focused "
    "is a bug in this rubric.\n\n"
    "Rules:\n"
    "1. Surface findings VERBATIM — quote source_excerpt directly, "
    "never paraphrase the rubric prompts, never editorialize.\n"
    "2. Every bullet, row, or checklist item MUST cite a source_ref + "
    "meeting_date from the payload. No codebase citations — this "
    "rubric does not use filesystem tools. An uncited item is a bug.\n"
    "3. If a category produces no findings for this pack, emit "
    "exactly this single line under its header: `✓ no gaps found`.\n"
    "4. Do not reorder categories. Do not add categories not in the "
    "rubric. Do not add hedges like 'as an AI...' or 'it seems that'.\n"
    "5. Start each section with the category `title` as a header."
)


def _build_rubric() -> GapRubric:
    """Build the static rubric. v0.4.19 — 5 business-requirement
    categories, fixed order, no codebase crawl required."""
    return GapRubric(version="v0.4.19", categories=list(_CATEGORIES))


def _build_context_decisions(
    matches: list[DecisionMatch],
) -> list[GapJudgmentContextDecision]:
    """Convert DecisionMatches into context-pack decisions.

    Groups by (symbol, file_path) to populate ``related_decision_ids`` —
    each decision's entry carries the intent_ids of all *other*
    decisions that share at least one (symbol, file_path) tuple. This
    surfaces cross-decision tension without requiring the caller
    agent to re-query.
    """
    # (symbol, file_path) → set of decision_ids
    symbol_to_decisions: dict[tuple[str, str], set[str]] = {}
    for m in matches:
        for region in m.code_regions:
            key = (region.symbol, region.file_path)
            symbol_to_decisions.setdefault(key, set()).add(m.decision_id)

    context_decisions: list[GapJudgmentContextDecision] = []
    for m in matches:
        related: set[str] = set()
        for region in m.code_regions:
            key = (region.symbol, region.file_path)
            related.update(symbol_to_decisions.get(key, set()))
        related.discard(m.decision_id)

        context_decisions.append(
            GapJudgmentContextDecision(
                decision_id=m.decision_id,
                description=m.description,
                status=m.status,
                source_excerpt=m.source_excerpt,
                source_ref=m.source_ref,
                meeting_date=m.meeting_date,
                related_decision_ids=sorted(related),
            )
        )
    return context_decisions


# ── Public handler ───────────────────────────────────────────────────


async def handle_judge_gaps(
    ctx,
    topic: str,
    max_decisions: int = 10,
) -> GapJudgmentPayload | None:
    """Build the caller-session gap judgment pack for a topic.

    Returns ``None`` on the honest empty path — when no decisions
    match the topic, there is nothing to judge. The caller should
    skip rendering entirely rather than render an empty pack.

    Never calls an LLM. The returned payload contains the rubric
    and the judgment prompt; the caller's Claude session does the
    reasoning in its own LLM context.
    """
    search_result: SearchDecisionsResponse = await handle_search_decisions(
        ctx,
        query=topic,
        max_results=max_decisions,
        min_confidence=0.3,
    )

    if not search_result.matches:
        return None  # honest empty path — nothing to judge

    context_decisions = _build_context_decisions(search_result.matches)
    phrasing_gaps = _extract_gaps(search_result.matches)

    return GapJudgmentPayload(
        topic=topic,
        as_of=datetime.now(timezone.utc).isoformat(),
        decisions=context_decisions,
        phrasing_gaps=phrasing_gaps,
        rubric=_build_rubric(),
        judgment_prompt=_JUDGMENT_PROMPT,
    )
