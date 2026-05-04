"""Pure analysis functions shared across preflight, brief, and gap_judge.

Extracted here so preflight can run divergence/gap detection directly on
search results without routing through handle_brief (which re-runs link_commit
and a duplicate search query).

These are all pure functions — no IO, no LLM, no ledger access.
"""

from __future__ import annotations

from contracts import (
    BriefDecision,
    BriefDivergence,
    BriefGap,
    CodeRegionSummary,
    DecisionMatch,
)

# ── Divergence detection heuristics ─────────────────────────────────

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

_DIVERGENCE_TOKENS = {
    " vs ",
    " vs. ",
    " or ",
    "instead of",
    "rather than",
}


def _descriptions_conflict(descriptions: list[str]) -> bool:
    lower = [d.lower() for d in descriptions]
    for i, a in enumerate(lower):
        for b in lower[i + 1 :]:
            for left, right in _NEGATION_PAIRS:
                if (left in a and right in b) or (left in b and right in a):
                    return True
            if any(tok in a or tok in b for tok in _DIVERGENCE_TOKENS):
                return True
    return False


def _detect_divergences(matches: list[DecisionMatch]) -> list[BriefDivergence]:
    """Group matches by (symbol, file_path) and flag contradiction groups."""
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


# ── Gap extraction heuristic ─────────────────────────────────────────

_OPEN_QUESTION_MARKERS = (
    "?",
    " tbd",
    " tbh",
    " vs ",
    " vs. ",
    "open question",
    "should we",
    "which one",
)


def _looks_like_open_question(description: str) -> bool:
    lo = description.lower()
    return any(m in lo for m in _OPEN_QUESTION_MARKERS)


def _extract_gaps(matches: list[DecisionMatch]) -> list[BriefGap]:
    """Identify open-question phrasing and ungrounded decisions."""
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


# ── Shape conversion ─────────────────────────────────────────────────


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
        signoff=m.signoff,
    )
