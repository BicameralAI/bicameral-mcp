"""Phase 1 regression tests — MCP-native code locator tools.

Tests the 3 code locator tool methods (validate_symbols, get_neighbors,
extract_symbols) against the real indexed repo.

Run: pytest tests/ -v  (needs REPO_PATH set to an indexed repo)

Contract: code_locator/models.py (ValidatedSymbol, NeighborInfo)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adapters.code_locator import get_code_locator


# ── Real adapter tests (Phase 1 — require indexed repo) ─────────────


@pytest.mark.phase1
def test_real_adapter_instantiates(monkeypatch, repo_path):
    monkeypatch.setenv("USE_REAL_CODE_LOCATOR", "1")
    monkeypatch.setenv("REPO_PATH", repo_path)
    adapter = get_code_locator()
    assert adapter is not None


@pytest.mark.phase1
def test_validate_symbols_returns_matches(monkeypatch, repo_path):
    """validate_symbols must find matches for known symbol names."""
    monkeypatch.setenv("USE_REAL_CODE_LOCATOR", "1")
    monkeypatch.setenv("REPO_PATH", repo_path)

    adapter = get_code_locator()
    results = adapter.validate_symbols(["SymbolDB", "get_code_locator"])
    assert len(results) >= 1, "Expected at least one match for known symbols"
    for r in results:
        assert r["matched_symbol"], "matched_symbol must be non-empty"
        assert 0 <= r["match_score"] <= 100
        assert isinstance(r["symbol_id"], int)


@pytest.mark.phase1
def test_get_neighbors_returns_valid_edges(monkeypatch, repo_path):
    """get_neighbors must return valid edge types and directions."""
    monkeypatch.setenv("USE_REAL_CODE_LOCATOR", "1")
    monkeypatch.setenv("REPO_PATH", repo_path)

    adapter = get_code_locator()
    validated = adapter.validate_symbols(["SymbolDB"])
    if not validated:
        pytest.skip("No symbols matched — index may be empty")

    symbol_id = validated[0]["symbol_id"]
    neighbors = adapter.get_neighbors(symbol_id)
    assert isinstance(neighbors, list)

    repo = Path(repo_path)
    for n in neighbors:
        assert n["symbol_name"], "symbol_name must be non-empty"
        if n["file_path"]:
            assert (repo / n["file_path"]).exists(), (
                f"neighbor file_path={n['file_path']!r} does not exist"
            )
        assert n["edge_type"] in ("contains", "imports", "invokes", "inherits")
        assert n["direction"] in ("forward", "backward")


# ── extract_symbols ──────────────────────────────────────────────────

@pytest.mark.phase1
@pytest.mark.asyncio
async def test_extract_symbols_from_known_file(monkeypatch, repo_path):
    """extract_symbols must return at least one symbol from a known file."""
    monkeypatch.setenv("USE_REAL_CODE_LOCATOR", "1")
    monkeypatch.setenv("REPO_PATH", repo_path)

    adapter = get_code_locator()
    target = Path(repo_path) / "pilot" / "mcp" / "contracts.py"
    if not target.exists():
        pytest.skip(f"Test file {target} not found")

    symbols = await adapter.extract_symbols(str(target))
    assert len(symbols) > 0, f"No symbols extracted from {target}"

    for sym in symbols:
        assert sym.get("name"), "Symbol must have a non-empty name"
        assert sym.get("type") in ("function", "class", "module", "file"), (
            f"Unexpected type: {sym.get('type')!r}"
        )
        start = sym.get("start_line")
        end = sym.get("end_line")
        assert isinstance(start, int) and isinstance(end, int)
        assert 1 <= start <= end, f"start_line={start}, end_line={end} invalid"
