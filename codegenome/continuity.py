"""Continuity matcher (deterministic v1) for CodeGenome Phase 3.

Pure-function module. Given a stored ``SubjectIdentity`` (the bind-time
fingerprint), the original symbol's name + kind, and a ``code_locator``
that supplies post-rebase candidates, determine whether the original
symbol moved/renamed/both. No I/O, no LLM, no embeddings — just
structural signals weighted per the issue spec:

    symbol_name_exact      0.40
    symbol_name_fuzzy      0.20  (rapidfuzz ratio >= 0.80)
    symbol_kind            0.20
    call_graph_neighbor    0.20  (Jaccard of 1-hop neighbors)

Threshold: confidence >= 0.75 → auto-resolve as identity_moved/renamed;
0.50 <= confidence < 0.75 → ``needs_review``; < 0.50 → no match.

Symbol name + kind are passed explicitly because ``SubjectIdentity`` is
a location-only fingerprint (deterministic_location_v1). The continuity
service supplies them from the drifted ``code_region`` row.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

from .adapter import SubjectIdentity
from .confidence import weighted_average

ChangeType = Literal["moved", "renamed", "moved_and_renamed"]

_WEIGHTS = {
    "exact_name": 0.40,
    "fuzzy_name": 0.20,
    "kind": 0.20,
    "neighbors": 0.20,
}
_FUZZY_THRESHOLD = 0.80
_DEFAULT_CAP = 20
_DEFAULT_MATCH_THRESHOLD = 0.75


@dataclass(frozen=True)
class ContinuityMatch:
    new_file_path: str
    new_start_line: int
    new_end_line: int
    new_symbol_name: str
    new_symbol_kind: str
    confidence: float
    change_type: ChangeType


def _normalize_name(s: str) -> str:
    return (s or "").strip("_").lower()


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a or ()), set(b or ())
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _name_signals(old_name: str, cand_name: str, *, fuzzy_threshold: float) -> dict[str, float]:
    norm_old = _normalize_name(old_name)
    norm_cand = _normalize_name(cand_name)
    exact = 1.0 if (norm_old and norm_old == norm_cand) else 0.0
    raw_fuzz = fuzz.ratio(norm_old, norm_cand) / 100.0 if (norm_old and norm_cand) else 0.0
    fuzzy = 1.0 if raw_fuzz >= fuzzy_threshold else 0.0
    return {"exact_name": exact, "fuzzy_name": fuzzy}


def _change_type_for(old_file: str, cand_file: str, name_signals: dict[str, float]) -> ChangeType:
    moved = old_file != cand_file
    renamed = name_signals["exact_name"] == 0.0 and name_signals["fuzzy_name"] == 1.0
    if moved and renamed:
        return "moved_and_renamed"
    if renamed:
        return "renamed"
    return "moved"


def score_continuity(
    old_identity: SubjectIdentity,
    candidate,
    *,
    old_symbol_name: str,
    old_symbol_kind: str,
    fuzzy_threshold: float = _FUZZY_THRESHOLD,
) -> tuple[float, ChangeType]:
    """Pure scoring function. Returns ``(confidence, change_type)``."""
    name_sigs = _name_signals(
        old_symbol_name,
        candidate.symbol_name or "",
        fuzzy_threshold=fuzzy_threshold,
    )
    kind_sig = 1.0 if old_symbol_kind == (candidate.symbol_kind or "") else 0.0
    weights = dict(_WEIGHTS)
    signals: dict[str, float] = {**name_sigs, "kind": kind_sig}
    if old_identity.neighbors_at_bind is None:
        # Pre-v12 row: drop the Jaccard signal entirely; remaining weights
        # renormalize via weighted_average's total-weight handling.
        del weights["neighbors"]
    else:
        cand_neighbors = getattr(candidate, "neighbors", ()) or ()
        signals["neighbors"] = _jaccard(old_identity.neighbors_at_bind, cand_neighbors)
    confidence = weighted_average(signals, weights)
    old_file = (old_identity.structural_signature or "").rsplit(":", 2)[0]
    return confidence, _change_type_for(old_file, candidate.file_path, name_sigs)


def find_continuity_match(
    identity: SubjectIdentity,
    code_locator,
    *,
    old_symbol_name: str,
    old_symbol_kind: str,
    candidate_cap: int = _DEFAULT_CAP,
    threshold: float = _DEFAULT_MATCH_THRESHOLD,
    fuzzy_threshold: float = _FUZZY_THRESHOLD,
) -> ContinuityMatch | None:
    """Score top-N candidates from the locator; return best ``>= threshold`` or ``None``."""
    candidates = code_locator.find_candidates(
        symbol_name=old_symbol_name,
        symbol_kind=old_symbol_kind,
        max_candidates=candidate_cap,
    )
    best: tuple[float, ChangeType, object] | None = None
    for cand in candidates[:candidate_cap]:
        score, change_type = score_continuity(
            identity,
            cand,
            old_symbol_name=old_symbol_name,
            old_symbol_kind=old_symbol_kind,
            fuzzy_threshold=fuzzy_threshold,
        )
        if best is None or score > best[0]:
            best = (score, change_type, cand)
    if best is None or best[0] < threshold:
        return None
    score, change_type, cand = best
    return ContinuityMatch(
        new_file_path=cand.file_path,
        new_start_line=cand.start_line,
        new_end_line=cand.end_line,
        new_symbol_name=cand.symbol_name or "",
        new_symbol_kind=cand.symbol_kind or "",
        confidence=score,
        change_type=change_type,
    )
