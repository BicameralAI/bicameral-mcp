"""R4 git-native onboarding detection (#368) — `run_setup` reads HEAD's
.bicameral/config.yaml via `git show` instead of probing the filesystem.

Decisions:
  - decision:ew9rgegdlblexsraesss — git show HEAD:.bicameral/config.yaml
  - decision:ogdfx014sqgc6fi6ky1a — reuse _resolve_authoritative_ref for
    the divergence guard
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import setup_wizard  # noqa: E402


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit_config(repo: Path, body: str, message: str = "init") -> None:
    bdir = repo / ".bicameral"
    bdir.mkdir(exist_ok=True)
    (bdir / "config.yaml").write_text(body, encoding="utf-8")
    _git(["add", ".bicameral/config.yaml"], repo)
    _git(["commit", "-m", message], repo)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Test repos must resolve their own default branch; outer env shouldn't leak."""
    monkeypatch.delenv("BICAMERAL_AUTHORITATIVE_REF", raising=False)
    monkeypatch.delenv("SURREAL_URL", raising=False)
    monkeypatch.delenv("CODE_LOCATOR_SQLITE_DB", raising=False)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    _git(["init", "-q", "-b", "main"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _git(["config", "user.name", "Test User"], tmp_path)
    return tmp_path


def test_read_committed_config_returns_parsed_yaml(git_repo: Path) -> None:
    _commit_config(
        git_repo,
        "mode: team\nteam:\n  backend: google_drive\n  folder_id: abc123\n",
    )
    parsed = setup_wizard._read_committed_config(git_repo, "HEAD")
    assert parsed is not None
    assert parsed["mode"] == "team"
    assert parsed["team"]["backend"] == "google_drive"
    assert parsed["team"]["folder_id"] == "abc123"


def test_read_committed_config_returns_none_when_unstaged(git_repo: Path) -> None:
    # Repo with one unrelated commit so HEAD exists but config.yaml isn't tracked.
    (git_repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(["add", "README.md"], git_repo)
    _git(["commit", "-m", "seed"], git_repo)
    assert setup_wizard._read_committed_config(git_repo, "HEAD") is None


def test_read_committed_config_returns_none_for_malformed_yaml(git_repo: Path) -> None:
    _commit_config(git_repo, "::: not yaml :::\n  garbage [", message="bad")
    assert setup_wizard._read_committed_config(git_repo, "HEAD") is None


def test_onboarding_skips_team_prompts_when_head_has_team_config(
    git_repo: Path, monkeypatch
) -> None:
    """run_setup auto-joins when HEAD's config.yaml declares team mode +
    backend block — _select_collaboration_mode and _select_team_backend are
    never invoked."""
    _commit_config(
        git_repo,
        "mode: team\nteam:\n  backend: google_drive\n  folder_id: abc123\n",
    )

    def _explode(*a, **kw):  # would only fire if the prompt path is taken
        raise AssertionError("interactive collaboration-mode prompt should be skipped")

    monkeypatch.setattr(setup_wizard, "_select_collaboration_mode", _explode)
    monkeypatch.setattr(setup_wizard, "_select_team_backend", _explode)
    # Other unrelated prompts → safe defaults
    monkeypatch.setattr(setup_wizard, "_select_guided_mode", lambda: False)
    monkeypatch.setattr(setup_wizard, "_select_telemetry", lambda: False)
    monkeypatch.setattr(setup_wizard, "_select_agents", lambda: ["claude"])
    monkeypatch.setattr(setup_wizard, "_install_for_agent", lambda *a, **kw: None)
    monkeypatch.setattr(setup_wizard, "_install_skills", lambda *a, **kw: 0)
    monkeypatch.setattr(setup_wizard, "_install_claude_hooks", lambda *a, **kw: False)
    monkeypatch.setattr(setup_wizard, "_install_user_permissions_allowlist", lambda *a, **kw: False)

    rc = setup_wizard.run_setup(repo_hint=str(git_repo))
    assert rc == 0


def test_onboarding_skips_solo_prompts_when_head_has_solo_config(
    git_repo: Path, monkeypatch
) -> None:
    """Symmetric: HEAD's config.yaml says `mode: solo` → skip the mode prompt."""
    _commit_config(git_repo, "mode: solo\n")

    def _explode(*a, **kw):
        raise AssertionError("interactive collaboration-mode prompt should be skipped")

    monkeypatch.setattr(setup_wizard, "_select_collaboration_mode", _explode)
    monkeypatch.setattr(setup_wizard, "_select_guided_mode", lambda: False)
    monkeypatch.setattr(setup_wizard, "_select_telemetry", lambda: False)
    monkeypatch.setattr(setup_wizard, "_select_agents", lambda: ["claude"])
    monkeypatch.setattr(setup_wizard, "_install_for_agent", lambda *a, **kw: None)
    monkeypatch.setattr(setup_wizard, "_install_skills", lambda *a, **kw: 0)
    monkeypatch.setattr(setup_wizard, "_install_claude_hooks", lambda *a, **kw: False)
    monkeypatch.setattr(setup_wizard, "_install_user_permissions_allowlist", lambda *a, **kw: False)

    rc = setup_wizard.run_setup(repo_hint=str(git_repo))
    assert rc == 0


def test_divergence_guard_warns_on_branch_without_config(
    git_repo: Path, monkeypatch, capsys
) -> None:
    """Default branch has the config, feature branch doesn't — prompt
    asks the operator before falling through to fresh setup, and the
    default-branch name is named in the warning."""
    _commit_config(git_repo, "mode: team\nteam:\n  backend: google_drive\n  folder_id: x\n")
    _git(["checkout", "-q", "-b", "feature-x"], git_repo)
    (git_repo / ".bicameral" / "config.yaml").unlink()
    _git(["commit", "-am", "drop config"], git_repo)

    seen = {"prompted": False}

    def fake_yes_no(message: str, default: bool = False) -> bool:
        seen["prompted"] = True
        seen["message"] = message
        return False  # decline → wizard aborts with merge-first hint

    monkeypatch.setattr(setup_wizard, "_prompt_yes_no", fake_yes_no)
    monkeypatch.setattr(setup_wizard, "_select_agents", lambda: ["claude"])

    rc = setup_wizard.run_setup(repo_hint=str(git_repo))
    assert rc == 1
    assert seen["prompted"]
    out = capsys.readouterr().out
    assert "main" in out  # default branch named
    assert ".bicameral/config.yaml" in out
