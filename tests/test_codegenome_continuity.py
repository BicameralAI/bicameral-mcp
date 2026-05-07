"""Phase 2 unit tests — continuity matcher (deterministic v1)."""

from __future__ import annotations

import pytest

from codegenome.adapter import SubjectIdentity
from codegenome.continuity import (
    ContinuityMatch,
    _jaccard,
    _normalize_name,
    find_continuity_match,
    score_continuity,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_identity(
    *, file_path="src/foo.py", start_line=10, end_line=20, neighbors=("cg:helper_a", "cg:helper_b")
):
    structural = f"{file_path}:{start_line}:{end_line}"
    return SubjectIdentity(
        address=f"cg:{structural}",
        identity_type="deterministic_location_v1",
        structural_signature=structural,
        behavioral_signature=None,
        signature_hash="abcd",
        content_hash="hhh",
        confidence=0.65,
        model_version="deterministic-location-v1",
        neighbors_at_bind=tuple(neighbors) if neighbors is not None else None,
    )


class _Candidate:
    def __init__(self, file_path, start_line, end_line, symbol_name, symbol_kind, neighbors=()):
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.symbol_name = symbol_name
        self.symbol_kind = symbol_kind
        self.neighbors = tuple(neighbors)


class _StubLocator:
    def __init__(self, candidates):
        self._candidates = list(candidates)

    def find_candidates(self, *, symbol_name, symbol_kind, max_candidates):
        return self._candidates[:max_candidates]


# ── _jaccard ────────────────────────────────────────────────────────────────


def test_jaccard_both_empty_returns_zero():
    assert _jaccard((), ()) == 0.0


def test_jaccard_identical_sets_returns_one():
    assert _jaccard(("a", "b", "c"), ("c", "b", "a")) == 1.0


def test_jaccard_disjoint_returns_zero():
    assert _jaccard(("a", "b"), ("c", "d")) == 0.0


def test_jaccard_half_overlap_returns_one_third():
    assert _jaccard(("a", "b"), ("b", "c")) == pytest.approx(1.0 / 3.0)


# ── _normalize_name ─────────────────────────────────────────────────────────


def test_normalize_name_lowercases():
    assert _normalize_name("EnforceLimit") == "enforcelimit"


def test_normalize_name_strips_underscores():
    assert _normalize_name("__private__") == "private"


# ── score_continuity ────────────────────────────────────────────────────────


def test_score_continuity_exact_match_full_signal():
    """Exact name + same kind + identical neighbors → max score; file changed = moved."""
    old = _make_identity(neighbors=("cg:a", "cg:b"))
    cand = _Candidate("src/bar.py", 5, 30, "parse", "function", ("cg:a", "cg:b"))
    score, change_type = score_continuity(
        old, cand, old_symbol_name="parse", old_symbol_kind="function"
    )
    assert score == pytest.approx(1.0)
    assert change_type == "moved"


def test_score_continuity_renamed_in_same_file():
    """Same file, similar name (fuzzy ≥0.80), same kind, full neighbors → renamed."""
    old = _make_identity(file_path="src/foo.py", neighbors=("cg:h",))
    cand = _Candidate("src/foo.py", 12, 25, "enforce_checkout_rate_limit", "function", ("cg:h",))
    score, change_type = score_continuity(
        old,
        cand,
        old_symbol_name="enforce_rate_limit",
        old_symbol_kind="function",
    )
    assert 0.50 <= score < 0.75
    assert change_type == "renamed"


def test_score_continuity_moved_and_renamed():
    old = _make_identity(file_path="src/foo.py", neighbors=("cg:t",))
    cand = _Candidate("src/bar.py", 1, 10, "parse_user_input", "function", ("cg:t",))
    score, change_type = score_continuity(
        old,
        cand,
        old_symbol_name="parse_input",
        old_symbol_kind="function",
    )
    assert change_type == "moved_and_renamed"
    assert score > 0.0


def test_score_continuity_unrelated_returns_low_score():
    old = _make_identity(neighbors=("cg:a",))
    cand = _Candidate("other/x.py", 100, 200, "totally_different", "class", ("cg:z",))
    score, _ = score_continuity(old, cand, old_symbol_name="parse", old_symbol_kind="function")
    assert score < 0.50


def test_score_continuity_kind_mismatch_drops_signal():
    old = _make_identity(neighbors=("cg:a",))
    cand_function = _Candidate("src/foo.py", 10, 20, "parse", "function", ("cg:a",))
    cand_class = _Candidate("src/foo.py", 10, 20, "parse", "class", ("cg:a",))
    score_match, _ = score_continuity(
        old, cand_function, old_symbol_name="parse", old_symbol_kind="function"
    )
    score_mismatch, _ = score_continuity(
        old, cand_class, old_symbol_name="parse", old_symbol_kind="function"
    )
    assert score_match > score_mismatch


def test_score_continuity_neighbors_none_renormalizes_weights():
    """Phase-1+2 row with neighbors_at_bind=None → Jaccard weight drops out."""
    old = _make_identity(neighbors=None)
    cand = _Candidate("src/foo.py", 10, 20, "parse", "function", ("cg:zz",))
    score, _ = score_continuity(old, cand, old_symbol_name="parse", old_symbol_kind="function")
    # With neighbors weight removed: exact=1, fuzzy=1, kind=1 — all weights sum to 0.80,
    # weighted_avg = (1*0.4 + 1*0.2 + 1*0.2) / 0.80 = 1.0
    assert score == pytest.approx(1.0)


# ── find_continuity_match ───────────────────────────────────────────────────


def test_find_continuity_match_returns_best_above_threshold():
    old = _make_identity(neighbors=("cg:a",))
    locator = _StubLocator(
        [
            _Candidate("src/bar.py", 1, 10, "parse", "function", ("cg:a",)),
            _Candidate("src/baz.py", 1, 5, "totally_unrelated", "class", ()),
        ]
    )
    match = find_continuity_match(old, locator, old_symbol_name="parse", old_symbol_kind="function")
    assert match is not None
    assert match.new_file_path == "src/bar.py"
    assert match.confidence >= 0.75


def test_find_continuity_match_returns_none_below_threshold():
    old = _make_identity(neighbors=("cg:a",))
    locator = _StubLocator(
        [
            _Candidate("src/baz.py", 1, 5, "totally_unrelated", "class", ()),
        ]
    )
    match = find_continuity_match(old, locator, old_symbol_name="parse", old_symbol_kind="function")
    assert match is None


def test_find_continuity_match_honors_candidate_cap():
    old = _make_identity(neighbors=("cg:a",))
    bad = [_Candidate(f"src/{i}.py", 1, 5, f"junk_{i}", "class", ()) for i in range(30)]
    perfect = _Candidate("src/match.py", 1, 5, "parse", "function", ("cg:a",))
    locator = _StubLocator(bad + [perfect])
    match = find_continuity_match(
        old,
        locator,
        old_symbol_name="parse",
        old_symbol_kind="function",
        candidate_cap=20,
    )
    # The perfect candidate is at index 30 — beyond the cap. No junk scores ≥ 0.75.
    assert match is None or match.new_file_path != "src/match.py"


def test_find_continuity_match_threshold_at_or_above_0_75():
    old = _make_identity(neighbors=("cg:a",))
    cand = _Candidate("src/bar.py", 1, 5, "parse", "function", ("cg:totally_different",))
    locator = _StubLocator([cand])
    match = find_continuity_match(old, locator, old_symbol_name="parse", old_symbol_kind="function")
    # exact=1, fuzzy=1, kind=1, neighbors=0 → (0.4+0.2+0.2+0)/1.0 = 0.80 ≥ 0.75
    assert match is not None
    assert match.confidence >= 0.75


def test_find_continuity_match_change_type_pure_move():
    old = _make_identity(file_path="src/foo.py", neighbors=("cg:a",))
    locator = _StubLocator(
        [
            _Candidate("src/bar.py", 1, 10, "parse", "function", ("cg:a",)),
        ]
    )
    match = find_continuity_match(old, locator, old_symbol_name="parse", old_symbol_kind="function")
    assert match is not None
    assert match.change_type == "moved"


def test_find_continuity_match_returns_continuity_match_dataclass():
    old = _make_identity(neighbors=("cg:a",))
    locator = _StubLocator(
        [
            _Candidate("src/bar.py", 1, 10, "parse", "function", ("cg:a",)),
        ]
    )
    match = find_continuity_match(old, locator, old_symbol_name="parse", old_symbol_kind="function")
    assert isinstance(match, ContinuityMatch)
    assert match.new_symbol_kind == "function"
