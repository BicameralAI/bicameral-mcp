"""Regression guard: e2e harness purges Claude Code per-project memory.

Locks the fix for the PR #181 e2e flake — Flow 5 (PM history+ratify, MCP-
layer) and Flow 3 (commit→sync, agentic) failed because a stale
``~/.claude/projects/<key>/memory/MEMORY.md`` from a prior run let the agent
answer Flow 5's prompt directly from disk instead of invoking
``bicameral.history``. Flow 3's failure cascaded because its ledger snapshot
relies on Flow 5's bicameral call to drain the post-commit JSONL queue via
``EventMaterializer.replay_new_events``.

If this test fails, the harness has stopped purging memory and the same
state-pollution flake will recur on shared CI runners.
"""

from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "e2e"))

from _harness_setup import clean_claude_memory_for_repo  # noqa: E402


def _project_key(repo_path: str) -> str:
    return str(pathlib.Path(repo_path).resolve()).replace("\\", "-").replace("/", "-")


def test_clean_claude_memory_purges_existing_memory_dir(tmp_path, monkeypatch):
    """Helper removes ~/.claude/projects/<key>/memory/ when it exists."""
    fake_home = tmp_path / "home"
    fake_repo = tmp_path / "repo" / "desktop-clone"
    fake_repo.mkdir(parents=True)
    monkeypatch.setattr(pathlib.Path, "home", lambda: fake_home)

    key = _project_key(str(fake_repo))
    memory_dir = fake_home / ".claude" / "projects" / key / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("- some prior-session memory entry")
    (memory_dir / "feedback_x.md").write_text("---\nname: x\n---\nbody")
    assert memory_dir.exists()

    clean_claude_memory_for_repo(str(fake_repo))

    assert not memory_dir.exists(), (
        "memory dir still present after purge — state pollution will recur "
        "(see test docstring for context on PR #181 cascade)"
    )


def test_clean_claude_memory_is_noop_when_dir_absent(tmp_path, monkeypatch):
    """Helper does not raise when there's no memory dir to purge (fresh runner)."""
    fake_home = tmp_path / "home"
    fake_repo = tmp_path / "repo" / "desktop-clone"
    fake_repo.mkdir(parents=True)
    monkeypatch.setattr(pathlib.Path, "home", lambda: fake_home)

    key = _project_key(str(fake_repo))
    memory_dir = fake_home / ".claude" / "projects" / key / "memory"
    assert not memory_dir.exists()

    clean_claude_memory_for_repo(str(fake_repo))  # must not raise

    assert not memory_dir.exists()


def test_clean_claude_memory_does_not_touch_other_project_memory(tmp_path, monkeypatch):
    """Helper purges ONLY the matching project key — other projects' memory
    on the same machine survives (the runner may host other test repos)."""
    fake_home = tmp_path / "home"
    target_repo = tmp_path / "repo" / "desktop-clone"
    other_repo = tmp_path / "repo" / "other-project"
    target_repo.mkdir(parents=True)
    other_repo.mkdir(parents=True)
    monkeypatch.setattr(pathlib.Path, "home", lambda: fake_home)

    target_key = _project_key(str(target_repo))
    other_key = _project_key(str(other_repo))
    target_memory = fake_home / ".claude" / "projects" / target_key / "memory"
    other_memory = fake_home / ".claude" / "projects" / other_key / "memory"
    target_memory.mkdir(parents=True)
    other_memory.mkdir(parents=True)
    (target_memory / "MEMORY.md").write_text("target")
    (other_memory / "MEMORY.md").write_text("other")

    clean_claude_memory_for_repo(str(target_repo))

    assert not target_memory.exists()
    assert other_memory.exists()
    assert (other_memory / "MEMORY.md").read_text() == "other"
