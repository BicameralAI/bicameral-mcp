"""Internal module — not registered as an MCP tool. Called by handle_ingest and handle_preflight.

Pre-meeting one-pager generator. Accepts a topic (and optional participant
list) and returns a structured response with decisions-in-scope, drift
candidates, divergences (two non-superseded decisions on the same symbol
with contradictory descriptions), gaps, and suggested meeting questions.

Composes over ``handle_search_decisions`` — the retrieval layer is already
good enough; this handler shapes the output for human consumption.

Design anchors (from the v0.4.6 plan):
- Divergent-intent detection is a HEURISTIC-only pass in v0.4.6. No LLM.
  The negation-pair table + divergence-token set catch the common
  contradiction shapes (redis vs local memory, sync vs async, etc.).
  LLM-backed contradiction check is deferred.
- Suggested questions are generated deterministically from the gap set
  and the divergences — no LLM in v0.4.6.
- When ``divergences`` is non-empty, the brief skill surfaces them BEFORE
  the decision list. That ordering contract lives in the SKILL.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from contracts import (
    BriefDecision,
    BriefDivergence,
    BriefGap,
    BriefQuestion,
    BriefResponse,
    CodeRegionSummary,
    DecisionMatch,
    SearchDecisionsResponse,
)
from handlers.action_hints import generate_hints_for_brief
from handlers.link_commit import handle_link_commit
from handlers.search_decisions import handle_search_decisions
from ledger.status import resolve_head

logger = logging.getLogger(__name__)


# ── Divergence detection — cheap heuristics ──────────────────────────


# Pairs of mutually-exclusive terms. If description A contains the left
# and description B contains the right (or vice versa), they're treated
# as contradictory. Extend this list as new contradiction patterns are
# observed in real ledgers.
_NEGATION_PAIRS: list[tuple[str, str]] = [
    ("redis", "local memory"),
    ("redis", "in-memory"),
    ("redis", "in memory"),
    ("oauth", "basic auth"),
    ("jwt", "session cookie"),
    ("jwt", "session cookies"),
    ("synchronous", "async"),
    ("sync", "async"),
    ("block", "allow"),
    ("enable", "disable"),
    ("required", "optional"),
    ("mandatory", "optional"),
    ("reject", "accept"),
    ("whitelist", "blacklist"),
    ("opt-in", "opt-out"),
]

# Tokens that signal a description is comparing alternatives or posing
# an open question. When either description in a symbol-grouped pair
# contains one of these, we flag the pair as potentially divergent.
_DIVERGENCE_TOKENS = {
    " vs ", " vs. ", " or ", "instead of", "rather than",
}


def _descriptions_conflict(descriptions: list[str]) -> bool:
    """Return True when any two descriptions in the list look mutually
    exclusive based on the heuristics above.

    Checks pairwise:
    - Negation-pair split across the two descriptions
    - Divergence-token in either description (signals a vs/or framing)
    """
    lower = [d.lower() for d in descriptions]
    for i, a in enumerate(lower):
        for b in lower[i + 1:]:
            for left, right in _NEGATION_PAIRS:
                if (left in a and right in b) or (left in b and right in a):
                    return True
            if any(tok in a or tok in b for tok in _DIVERGENCE_TOKENS):
                return True
    return False


def _detect_divergences(matches: list[DecisionMatch]) -> list[BriefDivergence]:
    """Group matches by (symbol, file_path) and flag contradiction groups.

    Skips groups of size <2 (nothing to contradict). Uses
    ``_descriptions_conflict`` as the detection rule. Pure function —
    no IO, no LLM.
    """
    by_symbol: dict[tuple[str, str], list[DecisionMatch]] = {}
    for m in matches:
        for region in m.code_regions:
            key = (region.symbol, region.file_path)
            by_symbol.setdefault(key, []).append(m)

    divergences: list[BriefDivergence] = []
    for (symbol, file_path), group in by_symbol.items():
        if len(group) < 2:
            continue
        if not _descriptions_conflict([m.description for m in group]):
            continue
        divergences.append(
            BriefDivergence(
                symbol=symbol,
                file_path=file_path,
                conflicting_decisions=[_to_brief_decision(m) for m in group],
                summary=(
                    f"{len(group)} non-superseded decisions on "
                    f"`{symbol}` ({file_path}) have contradictory descriptions "
                    f"— human resolution required."
                ),
            )
        )
    return divergences


# ── Gap extraction — pure heuristic ──────────────────────────────────


_OPEN_QUESTION_MARKERS = ("?", " tbd", " tbh", " vs ", " vs. ", "open question", "should we", "which one")


def _looks_like_open_question(description: str) -> bool:
    lo = description.lower()
    return any(m in lo for m in _OPEN_QUESTION_MARKERS)


def _extract_gaps(matches: list[DecisionMatch]) -> list[BriefGap]:
    """Identify gap-shaped decisions: open-question phrasing, ungrounded
    decisions that mention acceptance criteria language, or decisions that
    look like they're waiting on an answer.
    """
    gaps: list[BriefGap] = []
    for m in matches:
        if _looks_like_open_question(m.description):
            gaps.append(
                BriefGap(
                    description=m.description,
                    hint="open-question phrasing (vs/or/tbd/?)",
                    relevant_source_refs=[m.source_ref] if m.source_ref else [],
                )
            )
            continue
        if m.status == "ungrounded":
            gaps.append(
                BriefGap(
                    description=m.description,
                    hint="decision recorded but no code grounding — needs implementation or clarification",
                    relevant_source_refs=[m.source_ref] if m.source_ref else [],
                )
            )
    return gaps


# ── Question generation — deterministic, no LLM ──────────────────────


def _generate_questions(
    topic: str,
    matches: list[DecisionMatch],
    drift_candidates: list[DecisionMatch],
    divergences: list[BriefDivergence],
    gaps: list[BriefGap],
    participants: list[str] | None,
) -> list[BriefQuestion]:
    """Build 3-5 meeting-ready questions from the findings.

    Priority order: divergences first (load-bearing), then drift, then
    gaps. No LLM in v0.4.6 — the phrasings are templated.
    """
    questions: list[BriefQuestion] = []

    for div in divergences:
        questions.append(
            BriefQuestion(
                question=(
                    f"Which decision on `{div.symbol}` is authoritative going forward — "
                    f"we have {len(div.conflicting_decisions)} non-superseded decisions "
                    f"that contradict each other?"
                ),
                why="divergence — must resolve before next deploy",
            )
        )

    for m in drift_candidates[:2]:
        questions.append(
            BriefQuestion(
                question=(
                    f"Is the drift in `{m.description[:80]}` intentional, "
                    f"and should we update the decision or revert the code?"
                ),
                why=f"drift candidate — evidence: {m.drift_evidence[:120] if m.drift_evidence else 'hash mismatch'}",
            )
        )

    # Gap questions — prioritize ungrounded gaps (most actionable)
    seen_gap_descriptions: set[str] = set()
    for gap in gaps:
        if gap.description in seen_gap_descriptions:
            continue
        seen_gap_descriptions.add(gap.description)
        if len(questions) >= 5:
            break
        questions.append(
            BriefQuestion(
                question=(
                    f"Can we close the gap on `{gap.description[:80]}`? "
                    f"({gap.hint})"
                ),
                why="gap — no acceptance criteria or open-question phrasing",
            )
        )

    # Backfill: if nothing surfaced, still give the caller a meta-question
    # anchored on the topic.
    if not questions:
        questions.append(
            BriefQuestion(
                question=(
                    f"I didn't find any prior decisions, drift, or gaps for `{topic}`. "
                    f"Is this a greenfield area, or should we widen the search?"
                ),
                why="nothing found — confirm scope before the meeting",
            )
        )

    return questions[:5]


# ── Mapping helpers ──────────────────────────────────────────────────


def _to_brief_decision(m: DecisionMatch) -> BriefDecision:
    return BriefDecision(
        decision_id=m.decision_id,
        description=m.description,
        status=m.status,
        source_type="",
        source_ref=m.source_ref,
        code_regions=[
            CodeRegionSummary(
                file_path=r.file_path,
                symbol=r.symbol,
                lines=r.lines,
                purpose=r.purpose,
            )
            for r in m.code_regions
        ],
        severity_tier=1,
        drift_evidence=m.drift_evidence,
        source_excerpt=m.source_excerpt,
        meeting_date=m.meeting_date,
        product_signoff=m.product_signoff,
    )


# ── Public handler ───────────────────────────────────────────────────


async def handle_brief(
    ctx,
    topic: str,
    participants: list[str] | None = None,
    max_decisions: int = 10,
) -> BriefResponse:
    """Pre-meeting one-pager.

    1. Auto-sync the ledger via link_commit(HEAD)
    2. Search for decisions matching the topic (wraps bicameral_search)
    3. Partition matches into drift candidates, divergences, and gaps
    4. Generate meeting questions from the findings
    5. Return a ``BriefResponse`` with everything the caller needs
    """
    # 1. Auto-sync
    try:
        await handle_link_commit(ctx, "HEAD")
    except Exception as exc:
        logger.warning("[brief] link_commit sync failed: %s", exc)

    # 2. Retrieval
    search_result: SearchDecisionsResponse = await handle_search_decisions(
        ctx,
        query=topic,
        max_results=max_decisions,
        min_confidence=0.3,
    )

    matches = search_result.matches
    drift_candidates_match = [m for m in matches if m.status == "drifted"]

    # 3. Divergence + gap detection
    divergences = _detect_divergences(matches)
    gaps = _extract_gaps(matches)

    # 4. Questions
    questions = _generate_questions(
        topic=topic,
        matches=matches,
        drift_candidates=drift_candidates_match,
        divergences=divergences,
        gaps=gaps,
        participants=participants,
    )

    # 5. Assemble response
    ref = resolve_head(ctx.repo_path) or "HEAD"

    response = BriefResponse(
        topic=topic,
        participants=participants or [],
        as_of=datetime.now(timezone.utc).isoformat(),
        ref=ref,
        decisions=[_to_brief_decision(m) for m in matches],
        drift_candidates=[_to_brief_decision(m) for m in drift_candidates_match],
        divergences=divergences,
        gaps=gaps,
        suggested_questions=questions,
    )
    response.action_hints = generate_hints_for_brief(
        response, guided_mode=getattr(ctx, "guided_mode", False),
    )
    return response
