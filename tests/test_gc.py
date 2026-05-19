"""Tests for `bicameral-mcp gc` (#368 Phase 4).

Lists / deletes orphan project dirs under ~/.bicameral/projects/.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from cli import gc  # noqa: E402


def _seed(state_root: Path, project_id: str, origin_target: Path | None | str) -> Path:
    """Create `<state_root>/<project_id>/origin.txt` pointing at the
    given target (Path → real path, str → literal string for unreadable
    cases, None → omit origin.txt entirely)."""
    project_dir = state_root / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    origin = project_dir / "origin.txt"
    if origin_target is None:
        return project_dir
    text = str(origin_target) if isinstance(origin_target, Path) else origin_target
    origin.write_text(text, encoding="utf-8")
    return project_dir


def test_lists_orphans_and_keeps_live_projects(tmp_path: Path, capsys) -> None:
    state_root = tmp_path / "projects"
    live_target = tmp_path / "live-repo" / ".git"
    live_target.mkdir(parents=True)
    _seed(state_root, "live-id-1234567890", live_target)
    _seed(state_root, "orphan-id-aaaa1234", tmp_path / "vanished" / ".git")

    rc = gc.main(["--state-root", str(state_root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "orphan-id-aaaa1234" in out
    assert "orphan" in out
    assert "live-id-1234567890" in out
    assert "live" in out


def test_delete_prompts_per_item_and_removes_confirmed_ones(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "projects"
    a = _seed(state_root, "orphan-aaaa1234567890", tmp_path / "gone-a" / ".git")
    b = _seed(state_root, "orphan-bbbb1234567890", tmp_path / "gone-b" / ".git")

    responses = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda *a, **kw: next(responses))

    rc = gc.main(["--delete", "--state-root", str(state_root)])
    assert rc == 0
    assert not a.exists()
    assert b.exists()


def test_delete_with_yes_flag_skips_prompts(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "projects"
    a = _seed(state_root, "orphan-aaaa1234567890", tmp_path / "gone-a" / ".git")
    b = _seed(state_root, "orphan-bbbb1234567890", tmp_path / "gone-b" / ".git")

    def _explode(*a, **kw):
        raise AssertionError("prompt should be skipped under --yes")

    monkeypatch.setattr("builtins.input", _explode)
    rc = gc.main(["--delete", "--yes", "--state-root", str(state_root)])
    assert rc == 0
    assert not a.exists()
    assert not b.exists()


def test_skips_unreadable_origin_txt_with_warn(tmp_path: Path, capsys) -> None:
    state_root = tmp_path / "projects"
    # origin.txt missing entirely
    _seed(state_root, "no-origin-abcdef12", None)
    # origin.txt is empty
    pid = _seed(state_root, "empty-origin-12345", "")

    rc = gc.main(["--state-root", str(state_root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "unreadable" in out
    # Default (list-only) does NOT delete.
    assert pid.exists()


def test_empty_state_root_lists_cleanly(tmp_path: Path, capsys) -> None:
    state_root = tmp_path / "projects-empty"
    rc = gc.main(["--state-root", str(state_root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no projects" in out.lower()
