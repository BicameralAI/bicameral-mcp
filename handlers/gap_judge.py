"""Handler for /bicameral_judge_gaps MCP tool (v0.4.16).

Caller-session LLM gap judge. The server builds a structured context
pack — decisions with source excerpts, cross-symbol related decision
ids, phrasing-based gaps, and a 5-category rubric with a judgment
prompt — and returns it to the caller. The caller's Claude session
applies the rubric in its own LLM context, using its own filesystem
tools for the ``infrastructure_gap`` canonical-path crawl.

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


# ── Rubric — the 5 categories picked for wow × safety ────────────────

_CATEGORIES: list[GapRubricCategory] = [
    GapRubricCategory(
        key="missing_acceptance_criteria",
        title="Missing acceptance criteria",
        prompt=(
            "For each decision, ask: does the source_excerpt define a "
            "testable condition for 'done'? If not, list the specific "
            "missing acceptance questions the room still needs to answer. "
            "Quote the source_excerpt VERBATIM when you cite. Never "
            "invent a success criterion the team did not state."
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
            "path holes — failure modes, boundary conditions, error "
            "handling the team deferred or never addressed. Render a "
            "two-column table: Happy path (what's specified) ↔ Missing "
            "sad path (what's deferred). Use only evidence from the "
            "source_excerpt; never invent a failure mode the team did "
            "not hint at."
        ),
        output_shape="happy_sad_table",
        requires_codebase_crawl=False,
    ),
    GapRubricCategory(
        key="infrastructure_gap",
        title="Implied infrastructure not verified",
        prompt=(
            "For each decision, enumerate implied infrastructure: "
            "database, cache, message queue, CDN, env vars, secrets, "
            "CI/CD jobs, deploy targets. For each implied item, use "
            "your Glob and Read tools to check the canonical_paths "
            "listed in this rubric category. Render a checklist:\n"
            "  - Implied X → ✓ found in `<file_path>:<line>` (cite the match)\n"
            "  - Implied Y → ○ missing (not found in any canonical_path)\n"
            "  - Implied Z → ? ambiguous (found partial match, cite it)\n"
            "Never claim a match without citing file:line. Never "
            "fabricate implied infra the decision didn't imply."
        ),
        output_shape="checklist",
        requires_codebase_crawl=True,
        canonical_paths=[
            ".github/workflows/",
            "Dockerfile",
            "docker-compose.yml",
            "terraform/",
            "k8s/",
            ".env.example",
            "infra/",
            "deploy/",
        ],
    ),
    GapRubricCategory(
        key="underspecified_integration",
        title="External systems touched but not discussed",
        prompt=(
            "For each decision, extract the external systems or APIs "
            "it implies touching (name them explicitly from the "
            "source_excerpt — e.g. 'Stripe API', 'Postgres', 'Slack "
            "webhooks'). Then compare against the set of systems "
            "actually discussed in the related decisions' excerpts. "
            "Render a dependency radar:\n"
            "  - System A → ✓ discussed in decision <intent_id>\n"
            "  - System B → ○ touched but never discussed\n"
            "Never invent an integration point the decision didn't "
            "name. An implied integration is OK to surface; a "
            "fabricated one is a bug."
        ),
        output_shape="dependency_radar",
        requires_codebase_crawl=False,
    ),
    GapRubricCategory(
        key="missing_data_requirements",
        title="Data model implications not addressed",
        prompt=(
            "For each decision, ask: does it imply schema changes, "
            "migrations, data retention policies, or PII handling? "
            "If the decision implies any of these but the "
            "source_excerpt and related decisions never address them, "
            "surface as a checklist item:\n"
            "  - Decision implies <schema_change> → ○ not addressed\n"
            "Cite the exact phrase in source_excerpt that implied the "
            "data change. Never fabricate a schema implication the "
            "decision didn't hint at."
        ),
        output_shape="checklist",
        requires_codebase_crawl=False,
    ),
]


_JUDGMENT_PROMPT = (
    "You are the caller-session reasoner for bicameral's v0.4.16 gap "
    "judge. Apply each of the 5 rubric categories below to every "
    "decision in this context pack, in rubric order. For each "
    "category, emit one section using its `output_shape`.\n\n"
    "Rules:\n"
    "1. Surface findings VERBATIM — quote source_excerpt directly, "
    "never paraphrase the rubric prompts, never editorialize.\n"
    "2. Every bullet, row, or checklist item MUST cite either a "
    "source_ref + meeting_date from the payload OR a file:line from "
    "a codebase crawl. An uncited item is a bug.\n"
    "3. If a category produces no findings for this pack, emit "
    "exactly this single line under its header: `✓ no gaps found`.\n"
    "4. For `infrastructure_gap`, use your Glob/Read/Grep tools to "
    "verify each implied item against the category's "
    "`canonical_paths`. Never claim a match without citing file:line.\n"
    "5. Do not reorder categories. Do not add categories not in the "
    "rubric. Do not add hedges like 'as an AI...' or 'it seems that'.\n"
    "6. Start each section with the category `title` as a header."
)


def _build_rubric() -> GapRubric:
    """Build the static rubric. v0.4.16 — 5 categories, fixed order."""
    return GapRubric(version="v0.4.16", categories=list(_CATEGORIES))


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
    # (symbol, file_path) → set of intent_ids
    symbol_to_intents: dict[tuple[str, str], set[str]] = {}
    for m in matches:
        for region in m.code_regions:
            key = (region.symbol, region.file_path)
            symbol_to_intents.setdefault(key, set()).add(m.intent_id)

    context_decisions: list[GapJudgmentContextDecision] = []
    for m in matches:
        related: set[str] = set()
        for region in m.code_regions:
            key = (region.symbol, region.file_path)
            related.update(symbol_to_intents.get(key, set()))
        related.discard(m.intent_id)  # a decision is not related to itself

        context_decisions.append(
            GapJudgmentContextDecision(
                intent_id=m.intent_id,
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
