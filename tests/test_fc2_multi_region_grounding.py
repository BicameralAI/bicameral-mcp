"""FC-2 multi-region grounding regression tests (bicameral-mcp v0.4.6).

The FC-2 failure mode: a decision describing a multi-file feature (e.g.
"Google Calendar one-click add events", which spans React components,
hooks, and supabase functions) gets collapsed into a single-file anchor
by BM25 top-1 tiebreak. The wrong file (often a test fixture generator)
wins because it happens to have dense term frequency for the decision's
tokens.

Witnessed in Accountable-App-3.0 (2026-04-14): the "GCal one-click"
decision anchored to ``admin-test-data-generator.ts`` — a test fixture
utility — because it densely references "google calendar" and "event"
while the real implementation files (EventRsvpButton.tsx, use-series.ts,
google-calendar-connect/index.ts) score lower individually.

v0.4.6 fix: ``_ground_single`` now:
  1. Pre-computes fuzzy-validated symbol IDs from the description
  2. Seeds the graph channel in ``search_code()`` with those IDs so the
     RRF fusion layer (code_locator/fusion/rrf.py) activates
  3. Takes up to ``max_symbols`` DISTINCT files from the fused result,
     not just the top-1 BM25 hit
  4. Allocates a fair per-file symbol budget

These tests use lightweight monkeypatching of ``Bm25sClient`` and
``SymbolDB`` so the pipeline wiring is verified without building a
real code_locator index (which takes minutes).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from adapters.code_locator import RealCodeLocatorAdapter


class _FakeSymbolDB:
    """Minimal SymbolDB-shaped test double."""

    def __init__(self, symbols_by_file: dict[str, list[dict]]) -> None:
        self._by_file = symbols_by_file
        self._by_id: dict[int, dict] = {}
        for syms in symbols_by_file.values():
            for s in syms:
                self._by_id[s["id"]] = s

    def lookup_by_file(self, file_path: str):
        return [SimpleNamespace(**s, __getitem__=s.__getitem__, keys=lambda s=s: s.keys()) for s in self._by_file.get(file_path, [])]

    def lookup_by_id(self, sid: int):
        row = self._by_id.get(sid)
        if row is None:
            return None
        return SimpleNamespace(**row, __getitem__=row.__getitem__, keys=lambda r=row: r.keys())

    # Helpers the adapter doesn't use but tests may want
    def all_symbol_ids(self) -> list[int]:
        return sorted(self._by_id)


def _mk_row(sid: int, name: str, file_path: str, start: int = 1, end: int = 10) -> dict:
    return {
        "id": sid,
        "name": name,
        "qualified_name": name,
        "type": "function",
        "file_path": file_path,
        "start_line": start,
        "end_line": end,
    }


class _RowDict(dict):
    """dict subclass that also supports attribute access so lookup_by_file
    can return objects the adapter treats as sqlite3.Row-like."""

    def __getattr__(self, key: str):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


class _FakeDB:
    def __init__(self, symbols_by_file: dict[str, list[dict]]) -> None:
        self._by_file = {
            fp: [_RowDict(s) for s in syms] for fp, syms in symbols_by_file.items()
        }
        self._by_id: dict[int, _RowDict] = {}
        for rows in self._by_file.values():
            for r in rows:
                self._by_id[r["id"]] = r

    def lookup_by_file(self, file_path: str):
        return list(self._by_file.get(file_path, []))

    def lookup_by_id(self, sid: int):
        return self._by_id.get(sid)


def _adapter_with_fakes(
    bm25_hits: list[dict],
    fused_hits: list[dict],
    symbols_by_file: dict[str, list[dict]],
    fuzzy_symbol_ids: list[int],
) -> tuple[RealCodeLocatorAdapter, list[tuple]]:
    """Build a RealCodeLocatorAdapter with every real dependency stubbed.

    Returns (adapter, search_code_call_log) so tests can assert what was
    called and with which args.
    """
    adapter = RealCodeLocatorAdapter(repo_path=".")
    adapter._db = _FakeDB(symbols_by_file)
    adapter._initialized = True  # skip real init

    # Log every search_code invocation so the test can assert graph
    # channel activation.
    call_log: list[tuple] = []

    def _search_code_stub(query: str, symbol_ids=None):
        call_log.append((query, symbol_ids))
        if symbol_ids is not None:
            return list(fused_hits)
        return list(bm25_hits)

    adapter.search_code = _search_code_stub  # type: ignore[method-assign]

    def _validate_stub(candidates, threshold):
        return [{"symbol_id": sid} for sid in fuzzy_symbol_ids]

    adapter._validate_with_threshold = _validate_stub  # type: ignore[method-assign]

    return adapter, call_log


# ── Tests ────────────────────────────────────────────────────────────


def test_ground_single_activates_graph_channel_when_fuzzy_matches():
    """When fuzzy validation returns symbol IDs, ``_ground_single`` must
    re-run ``search_code`` with those IDs as seeds so the graph channel
    in the fusion layer activates.
    """
    bm25_hits = [
        {"file_path": "wrong_test_fixture.ts", "score": 0.9},
        {"file_path": "real_component.tsx", "score": 0.4},
    ]
    fused_hits = [
        # After RRF fusion with graph channel, real_component.tsx wins
        {"file_path": "real_component.tsx", "score": 0.95},
        {"file_path": "wrong_test_fixture.ts", "score": 0.5},
    ]
    symbols = {
        "real_component.tsx": [
            _mk_row(1, "handleClick", "real_component.tsx"),
        ],
        "wrong_test_fixture.ts": [
            _mk_row(2, "generateFakeEvent", "wrong_test_fixture.ts"),
        ],
    }

    adapter, call_log = _adapter_with_fakes(
        bm25_hits=bm25_hits,
        fused_hits=fused_hits,
        symbols_by_file=symbols,
        fuzzy_symbol_ids=[1, 2],
    )

    regions = adapter._ground_single(
        description="google calendar one-click add events",
        db=adapter._db,
        bm25_threshold=0.3,
        fuzzy_threshold=70,
        max_symbols=5,
        hits=bm25_hits,
    )

    # Assert search_code was called TWICE — once for initial BM25, then
    # once more with symbol_ids to activate the graph channel.
    fused_calls = [c for c in call_log if c[1] is not None]
    assert len(fused_calls) >= 1, (
        f"_ground_single did not activate the graph channel. "
        f"search_code calls: {call_log}"
    )
    # The graph seed call must carry the fuzzy-matched symbol IDs
    _, symbol_ids = fused_calls[0]
    assert symbol_ids == [1, 2], f"Expected seed ids [1, 2], got {symbol_ids}"

    # Assert the winning region is from the fused top, not the BM25 top
    assert len(regions) >= 1
    top_file = regions[0]["file_path"]
    assert top_file == "real_component.tsx", (
        f"Expected graph-channel fusion to promote real_component.tsx, "
        f"got {top_file}. Full regions: {regions}"
    )


def test_ground_single_emits_multi_file_regions():
    """For a multi-file feature, ``_ground_single`` must produce regions
    spanning multiple files when the fused ranking includes several
    qualifying files — not collapse to the top-1 file.
    """
    bm25_hits = [
        {"file_path": "ui/EventRsvpButton.tsx", "score": 0.8},
        {"file_path": "hooks/use-series.ts", "score": 0.75},
        {"file_path": "supabase/functions/google-calendar-sync/index.ts", "score": 0.7},
    ]
    fused_hits = bm25_hits  # assume fusion doesn't reshuffle

    symbols = {
        "ui/EventRsvpButton.tsx": [
            _mk_row(1, "EventRsvpButton", "ui/EventRsvpButton.tsx", 1, 20),
            _mk_row(2, "handleClick", "ui/EventRsvpButton.tsx", 21, 40),
        ],
        "hooks/use-series.ts": [
            _mk_row(3, "useSeries", "hooks/use-series.ts", 1, 50),
            _mk_row(4, "rsvpToEvent", "hooks/use-series.ts", 51, 100),
        ],
        "supabase/functions/google-calendar-sync/index.ts": [
            _mk_row(5, "syncCalendarEvents", "supabase/functions/google-calendar-sync/index.ts", 1, 80),
        ],
    }

    adapter, _ = _adapter_with_fakes(
        bm25_hits=bm25_hits,
        fused_hits=fused_hits,
        symbols_by_file=symbols,
        fuzzy_symbol_ids=[1, 3, 5],  # one seed from each file
    )

    regions = adapter._ground_single(
        description="one-click add events to google calendar",
        db=adapter._db,
        bm25_threshold=0.3,
        fuzzy_threshold=70,
        max_symbols=5,
        hits=bm25_hits,
    )

    distinct_files = {r["file_path"] for r in regions}
    assert len(distinct_files) >= 2, (
        f"FC-2 regression: multi-file feature collapsed to {len(distinct_files)} "
        f"file(s). Expected ≥2. Regions: {regions}"
    )
    # All three qualifying files should be represented when max_symbols ≥ 3
    assert len(distinct_files) == 3, (
        f"Expected all 3 qualifying files to surface, got {distinct_files}"
    )


def test_ground_single_respects_max_symbols_cap():
    """When the fused result has many qualifying files, ``_ground_single``
    must cap the total number of regions at ``max_symbols``.
    """
    bm25_hits = [
        {"file_path": f"file_{i}.ts", "score": 0.9 - i * 0.05}
        for i in range(10)
    ]
    symbols = {
        f"file_{i}.ts": [_mk_row(i, f"Symbol{i}", f"file_{i}.ts")]
        for i in range(10)
    }

    adapter, _ = _adapter_with_fakes(
        bm25_hits=bm25_hits,
        fused_hits=bm25_hits,
        symbols_by_file=symbols,
        fuzzy_symbol_ids=list(range(10)),
    )

    regions = adapter._ground_single(
        description="many relevant content tokens here please",
        db=adapter._db,
        bm25_threshold=0.3,
        fuzzy_threshold=70,
        max_symbols=3,
        hits=bm25_hits,
    )

    assert len(regions) <= 3, f"max_symbols cap broken: got {len(regions)} regions"


def test_ground_single_falls_back_to_bm25_when_no_fuzzy_matches():
    """When fuzzy validation returns zero symbol IDs, ``_ground_single``
    must fall back to the precomputed BM25-only hits (no graph seed).
    """
    bm25_hits = [
        {"file_path": "lonely_file.py", "score": 0.9},
    ]
    symbols = {
        "lonely_file.py": [_mk_row(1, "soloSymbol", "lonely_file.py")],
    }

    adapter, call_log = _adapter_with_fakes(
        bm25_hits=bm25_hits,
        fused_hits=[],  # empty — shouldn't be consulted
        symbols_by_file=symbols,
        fuzzy_symbol_ids=[],  # no fuzzy matches → no seed → no fused call
    )

    regions = adapter._ground_single(
        description="xyzzy plugh gnusto",
        db=adapter._db,
        bm25_threshold=0.3,
        fuzzy_threshold=70,
        max_symbols=5,
        hits=bm25_hits,
    )

    # No graph-channel call should have been made (no seeds)
    fused_calls = [c for c in call_log if c[1] is not None]
    assert fused_calls == [], (
        f"With zero fuzzy matches, graph channel should stay unseeded. "
        f"Got calls: {call_log}"
    )
    # Regions still come from the BM25-only hits
    assert len(regions) == 1
    assert regions[0]["file_path"] == "lonely_file.py"
