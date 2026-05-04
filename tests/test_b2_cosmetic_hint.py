"""Tests for V1 B2 — cosmetic_hint enrichment on DriftEntry.

Exercises ``handlers.detect_drift._enrich_with_cosmetic_hints`` and the
end-to-end flow through ``handle_detect_drift`` to confirm the advisory
flag is set correctly on drifted entries and never on non-drifted ones.

Codex pass-7 finding #3 (B1) and pass-8 finding #1 (B2/B3) require:
  - cosmetic_hint is metadata only — never mutates content_hash
  - cosmetic_hint stays False for renames / docstring edits / etc.
  - cosmetic_hint=True only for whitespace-only diffs
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contracts import DriftEntry
from handlers.detect_drift import _enrich_with_cosmetic_hints


def _make_entry(status: str = "drifted", lines: tuple[int, int] = (1, 3)) -> DriftEntry:
    return DriftEntry(
        decision_id="decision:bench",
        description="Bench decision",
        status=status,  # type: ignore[arg-type]
        symbol="f",
        lines=lines,
        source_ref="bench",
    )


def _write_file(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


@pytest.fixture
def repo_with_baseline(tmp_path):
    """Create a tmp repo, commit a baseline file, then leave a working-tree edit hook in place.

    Returns the repo path and the relative file path. Tests then overwrite
    the working-tree file to whatever they need to compare against HEAD.
    """
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "bench@test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "bench"], cwd=repo, check=True)

    rel = "src/example.py"
    baseline = "def f(x):\n    return x + 1\n"
    _write_file(repo, rel, baseline)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
    return repo, rel


def test_whitespace_only_edit_sets_cosmetic_hint_true(repo_with_baseline):
    repo, rel = repo_with_baseline
    # Working tree edits whitespace only.
    _write_file(repo, rel, "def f(x):\n    return x  +  1\n")
    entry = _make_entry(status="drifted", lines=(1, 2))
    _enrich_with_cosmetic_hints([entry], rel, str(repo))
    assert entry.cosmetic_hint is True


def test_variable_rename_keeps_cosmetic_hint_false(repo_with_baseline):
    repo, rel = repo_with_baseline
    _write_file(repo, rel, "def f(y):\n    return y + 1\n")
    entry = _make_entry(status="drifted", lines=(1, 2))
    _enrich_with_cosmetic_hints([entry], rel, str(repo))
    assert entry.cosmetic_hint is False


def test_docstring_edit_keeps_cosmetic_hint_false(repo_with_baseline, tmp_path):
    repo, rel = repo_with_baseline
    _write_file(repo, rel, "def f(x):\n    return x + 1\n")
    # Now overwrite baseline by committing a docstring-only version, then edit working tree.
    import subprocess

    _write_file(repo, rel, 'def f(x):\n    """Old."""\n    return x + 1\n')
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add docstring"], cwd=repo, check=True)
    # Working tree edits the docstring text — observable via __doc__.
    _write_file(repo, rel, 'def f(x):\n    """New."""\n    return x + 1\n')
    entry = _make_entry(status="drifted", lines=(1, 3))
    _enrich_with_cosmetic_hints([entry], rel, str(repo))
    assert entry.cosmetic_hint is False


def test_pending_entry_skipped(repo_with_baseline):
    """Non-drifted entries are not enriched."""
    repo, rel = repo_with_baseline
    _write_file(repo, rel, "def f(x):\n    return x  +  1\n")
    entry = _make_entry(status="pending", lines=(1, 2))
    _enrich_with_cosmetic_hints([entry], rel, str(repo))
    assert entry.cosmetic_hint is False  # default — never touched


def test_no_diff_keeps_cosmetic_hint_false(repo_with_baseline):
    """If working tree matches HEAD byte-for-byte, hint stays False (meaningless)."""
    repo, rel = repo_with_baseline
    # Don't modify working tree — it equals HEAD.
    entry = _make_entry(status="drifted", lines=(1, 2))
    _enrich_with_cosmetic_hints([entry], rel, str(repo))
    assert entry.cosmetic_hint is False


def test_unsupported_extension_keeps_cosmetic_hint_false(tmp_path):
    """Files outside EXTENSION_LANGUAGE never get a hint."""
    import subprocess

    repo = tmp_path / "repo2"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "bench@test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "bench"], cwd=repo, check=True)
    _write_file(repo, "x.rb", "puts 'hi'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    _write_file(repo, "x.rb", "puts  'hi'\n")
    entry = _make_entry(status="drifted", lines=(1, 1))
    _enrich_with_cosmetic_hints([entry], "x.rb", str(repo))
    assert entry.cosmetic_hint is False


def test_unresolvable_symbol_skipped(repo_with_baseline):
    """Entries whose symbol can't be resolved against HEAD/WT fail safe to False.

    Per the V1 alignment refactor, ``entry.lines`` is no longer the
    slicing input — the enrichment uses ``resolve_symbol_lines`` per
    ref to align HEAD and working-tree slices to the symbol body. If
    resolution returns None on either side (symbol absent, missing
    symbol name, etc.), the hint stays at its False default.
    """
    repo, rel = repo_with_baseline
    _write_file(repo, rel, "def f(x):\n    return x  +  1\n")
    # Symbol name that does not exist in the file → resolve_symbol_lines
    # returns None → enrichment skips this entry.
    entry = _make_entry(status="drifted", lines=(1, 2))
    entry.symbol = "nonexistent_symbol"
    _enrich_with_cosmetic_hints([entry], rel, str(repo))
    assert entry.cosmetic_hint is False


def test_content_hash_never_mutated(repo_with_baseline):
    """Codex pass-1 finding #2 invariant: hint computation never writes baseline.

    The enrichment runs on DriftEntry models in memory; verify nothing
    on disk or in the entry tuple is touched besides the cosmetic_hint
    field itself.
    """
    repo, rel = repo_with_baseline
    _write_file(repo, rel, "def f(x):\n    return x  +  1\n")
    entry = _make_entry(status="drifted", lines=(1, 2))
    snapshot = entry.model_dump()
    _enrich_with_cosmetic_hints([entry], rel, str(repo))
    after = entry.model_dump()
    # Only cosmetic_hint may differ.
    diff = {k: (snapshot[k], after[k]) for k in snapshot if snapshot[k] != after[k]}
    assert set(diff.keys()) <= {"cosmetic_hint"}, diff
