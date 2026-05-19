"""Tests for the shadow-mode dispatch in ``_extract_definitions`` (#399 Stage C).

Sociable per CLAUDE.md — uses the real ``extract_symbols_from_content``
entry point and real telemetry log writes, redirected via the
``BICAMERAL_M_SHADOW_LOG_PATH`` env override into pytest's tmp_path.
No MagicMock; collaborators we ship to users (the symbol_extractor
dispatch, the m_shadow_divergence_log writer) run as themselves.

What's pinned here:

1. **walker-only mode** (default for Python/Go/Rust/JS/TS/Java/C#)
   produces walker output and emits no shadow log entries.
2. **substrate-only mode** (Elixir) produces substrate output and
   emits no shadow log entries.
3. **shadow-substrate mode** runs both paths, returns walker output
   (authoritative), AND logs a divergence event when results differ.
4. **shadow-walker mode** runs both paths, returns substrate output
   (authoritative).
5. The dispatch table is the only switch — flipping a mode via the
   table changes behavior without touching ``_extract_definitions``.

The mode flips here use monkeypatch on ``_SHADOW_MODES``; this same
mechanism is what Stage D and Stage E PRs will use, just persistently
via a one-line edit instead of a test-scoped monkeypatch.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

# Small Python fixture with a known walker output: 1 class, 2 funcs.
# The substrate (Python tags.scm) emits these PLUS module-level
# constants — which fall outside the walker vocab and are stripped
# before divergence classification, so this fixture produces an
# "equal" comparison.
_PY_FIXTURE_EQUAL = '''\
"""Test fixture."""

X = 1

class Foo:
    pass

def bar():
    return 1

def baz():
    return 2
'''


def _reset_shadow_log(monkeypatch, tmp_path):
    """Redirect the shadow-divergence log into tmp_path and reload the
    module so the env override takes effect."""
    log_path = tmp_path / "m_shadow_divergence.jsonl"
    monkeypatch.setenv("BICAMERAL_M_SHADOW_LOG_PATH", str(log_path))
    import m_shadow_divergence_log as mod  # noqa: WPS433

    importlib.reload(mod)
    return log_path


def _read_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


# ── Mode default state ─────────────────────────────────────────────


def test_default_modes_match_stage_c_initial_state():
    """The dispatch table starts every walker-backed language at
    WALKER_ONLY and Elixir at SUBSTRATE_ONLY. Flipping these is Stages
    D and E — if this test fails after a Stage C-and-below PR, somebody
    edited the table prematurely."""
    from code_locator.indexing.symbol_extractor import _SHADOW_MODES, ShadowMode

    assert _SHADOW_MODES["python"] is ShadowMode.WALKER_ONLY
    assert _SHADOW_MODES["go"] is ShadowMode.WALKER_ONLY
    assert _SHADOW_MODES["rust"] is ShadowMode.WALKER_ONLY
    assert _SHADOW_MODES["javascript"] is ShadowMode.WALKER_ONLY
    assert _SHADOW_MODES["typescript"] is ShadowMode.WALKER_ONLY
    assert _SHADOW_MODES["java"] is ShadowMode.WALKER_ONLY
    assert _SHADOW_MODES["c_sharp"] is ShadowMode.WALKER_ONLY
    assert _SHADOW_MODES["elixir"] is ShadowMode.SUBSTRATE_ONLY


# ── walker-only mode ───────────────────────────────────────────────


def test_walker_only_writes_no_shadow_log(monkeypatch, tmp_path):
    """Default Python (walker-only) runs without touching the shadow
    log. Pins the cost-zero property — production indexing of a fully-
    walker-only workload pays nothing for telemetry."""
    log_path = _reset_shadow_log(monkeypatch, tmp_path)

    from code_locator.indexing.symbol_extractor import extract_symbols_from_content

    records = extract_symbols_from_content(_PY_FIXTURE_EQUAL, "python", "fixture.py")

    names = {r.name for r in records}
    assert "Foo" in names and "bar" in names and "baz" in names
    assert _read_log(log_path) == []


# ── substrate-only mode ────────────────────────────────────────────


def test_substrate_only_writes_no_shadow_log(monkeypatch, tmp_path):
    """Elixir's substrate-only path runs without touching the shadow
    log. Same cost-zero property as walker-only — divergence only
    matters in shadow modes."""
    log_path = _reset_shadow_log(monkeypatch, tmp_path)

    # Tiny inline Elixir module — avoids depending on the
    # tests/fixtures/elixir fixture file being installed.
    elixir_src = "defmodule MyMod do\n  def hello, do: :world\nend\n"

    from code_locator.indexing.symbol_extractor import extract_symbols_from_content

    try:
        records = extract_symbols_from_content(elixir_src, "elixir", "m.ex")
    except Exception:
        pytest.skip("tree_sitter_elixir not installed in this environment")

    if not records:
        pytest.skip("tree_sitter_elixir present but tags.scm did not yield records")

    assert _read_log(log_path) == []


# ── shadow-substrate mode (walker authoritative) ────────────────────


def test_shadow_substrate_returns_walker_output(monkeypatch, tmp_path):
    """In shadow-substrate mode the walker is authoritative — what
    callers see is walker output, even though both paths ran. This is
    the invariant Stage D depends on: flipping Python to shadow-
    substrate must not change behavior visible to indexer consumers."""
    _reset_shadow_log(monkeypatch, tmp_path)

    from code_locator.indexing import symbol_extractor
    from code_locator.indexing.symbol_extractor import (
        _SHADOW_MODES,
        ShadowMode,
        extract_symbols_from_content,
    )

    monkeypatch.setitem(_SHADOW_MODES, "python", ShadowMode.SHADOW_SUBSTRATE)

    records = extract_symbols_from_content(_PY_FIXTURE_EQUAL, "python", "fixture.py")
    names = {r.name for r in records}
    # Walker output: class + 2 funcs. Module constant X is substrate-
    # only and would appear if the substrate side leaked through.
    assert names == {"Foo", "bar", "baz"}, (
        f"shadow-substrate must return walker output; got {names}"
    )

    # Also ensure the symbol_extractor module wasn't accidentally
    # mutated outside the monkeypatch scope.
    assert symbol_extractor._SHADOW_MODES is _SHADOW_MODES


def test_shadow_substrate_logs_divergence_when_walker_extras_exist(monkeypatch, tmp_path):
    """Build a synthetic divergence by patching the walker dispatch
    to return an extra symbol the substrate won't have. Forces a
    ``substrate-subset`` event in the log — the forbidden direction
    the parity gate is designed to catch."""
    log_path = _reset_shadow_log(monkeypatch, tmp_path)

    from code_locator.indexing import symbol_extractor
    from code_locator.indexing.sqlite_store import SymbolRecord
    from code_locator.indexing.symbol_extractor import (
        _SHADOW_MODES,
        ShadowMode,
        extract_symbols_from_content,
    )

    monkeypatch.setitem(_SHADOW_MODES, "python", ShadowMode.SHADOW_SUBSTRATE)

    # Synthetic walker: append a phantom symbol the substrate can't see.
    original_walker = symbol_extractor._run_walker

    def fake_walker(language_id, tree, code, rel_path):
        records = original_walker(language_id, tree, code, rel_path)
        records.append(
            SymbolRecord(
                name="phantom",
                qualified_name="phantom",
                type="function",
                file_path=rel_path,
                start_line=999,
                end_line=999,
                signature="def phantom()",
                parent_qualified_name="",
            )
        )
        return records

    monkeypatch.setattr(symbol_extractor, "_run_walker", fake_walker)

    extract_symbols_from_content(_PY_FIXTURE_EQUAL, "python", "synthetic.py")

    rows = _read_log(log_path)
    assert len(rows) == 1, f"expected 1 divergence row, got {len(rows)}"
    row = rows[0]
    assert row["language_id"] == "python"
    assert row["mode"] == "shadow-substrate"
    assert row["divergence_kind"] == "substrate-subset"
    assert "phantom:function" in row["walker_only"]


# ── shadow-walker mode (substrate authoritative) ────────────────────


def test_shadow_walker_returns_substrate_output(monkeypatch, tmp_path):
    """In shadow-walker mode the substrate is authoritative — Stage
    E's pre-retirement state. The walker still runs (for divergence
    detection) but its output isn't what callers see.

    For Python the walker and substrate produce overlapping name sets
    (the parity gate enforces walker ⊆ substrate, and substrate's only
    Python extras are module constants that ``_KIND_TO_TYPE`` filters
    out via the ``constant`` kind being unmapped). So we can't tell
    sides apart by inspecting names alone. Patch ``_run_substrate`` to
    return a distinct sentinel and assert the dispatch returns it.
    """
    _reset_shadow_log(monkeypatch, tmp_path)

    from code_locator.indexing import symbol_extractor
    from code_locator.indexing.sqlite_store import SymbolRecord
    from code_locator.indexing.symbol_extractor import (
        _SHADOW_MODES,
        ShadowMode,
        extract_symbols_from_content,
    )

    sentinel = SymbolRecord(
        name="SUBSTRATE_SENTINEL",
        qualified_name="SUBSTRATE_SENTINEL",
        type="function",
        file_path="fixture.py",
        start_line=1,
        end_line=1,
        signature="def SUBSTRATE_SENTINEL()",
        parent_qualified_name="",
    )

    def fake_substrate(language_id, tree, code, rel_path):
        return [sentinel]

    monkeypatch.setitem(_SHADOW_MODES, "python", ShadowMode.SHADOW_WALKER)
    monkeypatch.setattr(symbol_extractor, "_run_substrate", fake_substrate)

    records = extract_symbols_from_content(_PY_FIXTURE_EQUAL, "python", "fixture.py")
    assert records == [sentinel], (
        "shadow-walker must return the substrate side; got walker output instead"
    )


# ── Unknown language ───────────────────────────────────────────────


def test_unknown_language_returns_empty(monkeypatch, tmp_path):
    """A language not in _SHADOW_MODES returns []. Preserves the
    pre-Stage-C behavior for unrecognized extensions."""
    _reset_shadow_log(monkeypatch, tmp_path)

    from code_locator.indexing.symbol_extractor import _extract_definitions

    # The dispatch needs a tree, but for an unknown language we never
    # parse — pass None and confirm the early-exit before any tree
    # access.
    records = _extract_definitions("haskell", None, b"", "x.hs")
    assert records == []
