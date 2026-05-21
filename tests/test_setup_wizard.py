"""Behavioral tests for setup_wizard helpers.

Covers the installer fixes from issue #177:
- _install_skills warns loudly when source is missing (no silent return)
- _install_skills copies all skill folders on the happy path
- _detect_runner returns the bicameral-mcp script when present;
  raises RunnerNotFoundError when no runner is on PATH (no broken
  `python -m bicameral_mcp` fallback)
- run_setup output does not contain the stale `-m bicameral_mcp` runner-note text
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import setup_wizard  # noqa: E402


def test_install_skills_warns_when_source_missing(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    fake_wizard_dir = tmp_path / "fake-pkg"
    fake_wizard_dir.mkdir()
    fake_module_path = fake_wizard_dir / "setup_wizard.py"
    fake_module_path.write_text("")
    with patch.object(setup_wizard, "__file__", str(fake_module_path)):
        count = setup_wizard._install_skills(repo)
    assert count == 0
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "skill source" in captured.out
    assert not (repo / ".claude" / "skills").exists()


def test_install_skills_copies_all_skill_dirs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    src = tmp_path / "pkg"
    (src / "skills" / "alpha").mkdir(parents=True)
    (src / "skills" / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\n")
    (src / "skills" / "beta").mkdir(parents=True)
    (src / "skills" / "beta" / "SKILL.md").write_text("---\nname: beta\n---\n")
    fake_module_path = src / "setup_wizard.py"
    fake_module_path.write_text("")
    with patch.object(setup_wizard, "__file__", str(fake_module_path)):
        count = setup_wizard._install_skills(repo)
    assert count == 2
    assert (repo / ".claude" / "skills" / "alpha" / "SKILL.md").exists()
    assert (repo / ".claude" / "skills" / "beta" / "SKILL.md").exists()


def test_detect_runner_uses_bicameral_mcp_script_when_present():
    def which(name):
        return "/usr/local/bin/bicameral-mcp" if name == "bicameral-mcp" else None

    with patch.object(setup_wizard.shutil, "which", side_effect=which):
        cmd, args = setup_wizard._detect_runner()
    assert cmd == "bicameral-mcp"
    assert args == []


def test_detect_runner_raises_when_no_runner_available():
    with patch.object(setup_wizard.shutil, "which", return_value=None):
        with pytest.raises(setup_wizard.RunnerNotFoundError):
            setup_wizard._detect_runner()


def test_session_end_command_invokes_queue_writer():
    """Regression guard (post #156): the SessionEnd hook is now a path-style
    Python invocation of the transcript-queue writer, not a `claude -p`
    spawn of the capture-corrections slash command. The prior shape
    couldn't see the parent transcript; the queue-write defers correction
    surfacing to the next session.

    Replaces the prior issue-#177 hyphen-vs-colon slash-command guard,
    which is moot now that the hook does not invoke a slash command at
    all."""
    cmd = setup_wizard._BICAMERAL_SESSION_END_COMMAND
    # Post #200 Phase 1: command is rendered per sys.platform — POSIX uses
    # forward slashes, Windows uses backslashes. Substring check the path
    # in a separator-agnostic way.
    assert "session_end_queue_writer.py" in cmd
    assert "scripts" in cmd and "hooks" in cmd
    assert "/bicameral-capture-corrections" not in cmd
    assert "claude -p" not in cmd


def test_install_user_permissions_allowlist_writes_user_level_only(tmp_path):
    """The allowlist must land in ~/.claude/settings.json — never in a
    project-level path. v0 productization §1: no commit pollution."""
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / ".claude").mkdir()
    project_settings = project / ".claude" / "settings.json"
    project_settings.write_text("{}\n")

    with patch.object(setup_wizard.Path, "home", staticmethod(lambda: home)):
        with patch.object(setup_wizard, "_is_interactive", return_value=False):
            wrote = setup_wizard._install_user_permissions_allowlist()

    assert wrote is True
    user_settings = home / ".claude" / "settings.json"
    assert user_settings.exists()
    # Project file untouched.
    assert project_settings.read_text() == "{}\n"

    payload = json.loads(user_settings.read_text())
    allow = payload["permissions"]["allow"]
    deny = payload["permissions"]["deny"]
    assert "mcp__bicameral__bicameral_preflight" in allow
    assert "mcp__bicameral__bicameral_reset" in deny
    assert "mcp__bicameral__bicameral_reset" not in allow


def test_install_user_permissions_allowlist_does_not_approve_bash(tmp_path):
    """Only bicameral MCP tools get pre-approved. Bash/Edit/Write/Read
    never enter the allow-list — shell calls keep their permission
    prompt. This is the load-bearing UX claim of the wizard step."""
    home = tmp_path / "home"
    home.mkdir()

    with patch.object(setup_wizard.Path, "home", staticmethod(lambda: home)):
        with patch.object(setup_wizard, "_is_interactive", return_value=False):
            setup_wizard._install_user_permissions_allowlist()

    payload = json.loads((home / ".claude" / "settings.json").read_text())
    allow = payload["permissions"]["allow"]
    forbidden = {"Bash", "Edit", "Write", "Read", "Grep", "Glob", "NotebookEdit"}
    assert not (forbidden & set(allow)), (
        f"wizard auto-approved a non-bicameral tool: {forbidden & set(allow)}"
    )
    for entry in allow:
        assert entry.startswith("mcp__bicameral__"), (
            f"allow-list contains non-bicameral entry: {entry!r}"
        )


def test_install_user_permissions_allowlist_is_idempotent(tmp_path):
    """Re-running the wizard does not duplicate entries or wipe other
    user-set permissions."""
    home = tmp_path / "home"
    home.mkdir()
    settings_dir = home / ".claude"
    settings_dir.mkdir()
    settings_path = settings_dir / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": ["Read(./docs/**)"],  # user's existing entry
                    "deny": [],
                },
                "theme": "dark",
            }
        )
    )

    with patch.object(setup_wizard.Path, "home", staticmethod(lambda: home)):
        with patch.object(setup_wizard, "_is_interactive", return_value=False):
            first = setup_wizard._install_user_permissions_allowlist()
            second = setup_wizard._install_user_permissions_allowlist()

    assert first is True
    assert second is False  # nothing new to write second time

    payload = json.loads(settings_path.read_text())
    allow = payload["permissions"]["allow"]
    # User's pre-existing entry preserved.
    assert "Read(./docs/**)" in allow
    # Unrelated keys preserved.
    assert payload["theme"] == "dark"
    # No duplicates.
    assert len(allow) == len(set(allow))


def test_install_user_permissions_allowlist_declined_writes_nothing(tmp_path, monkeypatch):
    """If the user answers 'no' at the consent prompt, the wizard must
    not write to settings.json — the consent moment is the load-bearing
    contract."""
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setattr(setup_wizard, "_is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "n")

    with patch.object(setup_wizard.Path, "home", staticmethod(lambda: home)):
        wrote = setup_wizard._install_user_permissions_allowlist()

    assert wrote is False
    assert not (home / ".claude" / "settings.json").exists()


def test_install_user_permissions_allowlist_excludes_extract_symbols():
    """Regression guard: extract_symbols was retired as an MCP tool;
    the wizard must not pre-approve a tool that no longer exists."""
    assert "mcp__bicameral__extract_symbols" not in setup_wizard._BICAMERAL_ALLOW_TOOLS


def test_detect_runner_does_not_return_broken_module_fallback():
    """Regression guard for issue #177: the previous `python -m bicameral_mcp`
    fallback produced a non-functional MCP config because no `bicameral_mcp`
    package exists. The fix raises instead. This test fails if anyone
    re-introduces a non-script runner."""
    with patch.object(setup_wizard.shutil, "which", return_value=None):
        try:
            cmd, args = setup_wizard._detect_runner()
        except setup_wizard.RunnerNotFoundError:
            return
        # If we got here, _detect_runner returned without raising — that's the bug.
        pytest.fail(
            f"_detect_runner returned ({cmd!r}, {args!r}) instead of raising; "
            "broken `python -m bicameral_mcp` fallback may have been re-introduced"
        )


def _write_manifest_only(share_dir: Path, manifest_filename: str) -> Path:
    """Write a fake manifest with NO `.sigstore` bundle companion —
    simulates the packaging state where the wheel ships the manifest but
    the release-side sigstore signing step did not run (#292)."""
    share_dir.mkdir(parents=True, exist_ok=True)
    manifest = share_dir / manifest_filename
    manifest.write_text("manifest_version = 1\n", encoding="utf-8")
    assert not (share_dir / f"{manifest_filename}.sigstore").exists()
    return manifest


def _write_manifest_with_placeholder_bundle(
    share_dir: Path, manifest_filename: str
) -> tuple[Path, Path]:
    """Write a manifest + a ZERO-BYTE `.sigstore` placeholder — simulates
    the local-dev build where the manifest build hook writes an empty
    placeholder so hatch `shared-data` resolves (#292). The helpers must
    treat the empty bundle as absent."""
    share_dir.mkdir(parents=True, exist_ok=True)
    manifest = share_dir / manifest_filename
    manifest.write_text("manifest_version = 1\n", encoding="utf-8")
    bundle = share_dir / f"{manifest_filename}.sigstore"
    bundle.write_bytes(b"")
    return manifest, bundle


def _write_full_pair(share_dir: Path, manifest_filename: str) -> tuple[Path, Path]:
    """Write both artifacts (manifest + non-empty `.sigstore` bundle) —
    simulates the fully-signed wheel produced once the release pipeline
    emits a sigstore bundle. Should cause the helpers to return the
    `(manifest, bundle)` pair (#292)."""
    share_dir.mkdir(parents=True, exist_ok=True)
    manifest = share_dir / manifest_filename
    manifest.write_text("manifest_version = 1\n", encoding="utf-8")
    bundle = share_dir / f"{manifest_filename}.sigstore"
    bundle.write_bytes(b'{"mediaType": "application/vnd.dev.sigstore.bundle+json"}')
    return manifest, bundle


def test_bundled_hooks_manifest_paths_returns_none_when_bundle_missing(tmp_path, monkeypatch):
    """The helper must return None when the wheel ships the .json
    manifest without the `.sigstore` bundle companion — verification
    defers rather than aborting setup on a missing-bundle path (#292)."""
    fake_prefix = tmp_path / "venv"
    share_dir = fake_prefix / "share" / "bicameral-mcp"
    _write_manifest_only(share_dir, "hooks-manifest.json")

    monkeypatch.setattr(setup_wizard.sys, "prefix", str(fake_prefix))
    fake_init = tmp_path / "fake-pkg" / "setup_wizard.py"
    monkeypatch.setattr(setup_wizard, "__file__", str(fake_init))

    assert setup_wizard._bundled_manifest_paths() is None


def test_bundled_skills_manifest_paths_returns_none_when_bundle_missing(tmp_path, monkeypatch):
    """Skills-surface mirror of the missing-`.sigstore` case (#292)."""
    fake_prefix = tmp_path / "venv"
    share_dir = fake_prefix / "share" / "bicameral-mcp"
    _write_manifest_only(share_dir, "skills-manifest.toml")

    monkeypatch.setattr(setup_wizard.sys, "prefix", str(fake_prefix))
    fake_init = tmp_path / "fake-pkg" / "setup_wizard.py"
    monkeypatch.setattr(setup_wizard, "__file__", str(fake_init))

    assert setup_wizard._bundled_skills_manifest_paths() is None


def test_bundled_hooks_manifest_paths_returns_none_when_bundle_zero_byte(tmp_path, monkeypatch):
    """A zero-byte `.sigstore` is the local-dev placeholder the build hook
    writes so hatch `shared-data` resolves; it is never signed and never
    verifies, so the helper treats it as absent → None (#292)."""
    fake_prefix = tmp_path / "venv"
    share_dir = fake_prefix / "share" / "bicameral-mcp"
    _write_manifest_with_placeholder_bundle(share_dir, "hooks-manifest.json")

    monkeypatch.setattr(setup_wizard.sys, "prefix", str(fake_prefix))
    fake_init = tmp_path / "fake-pkg" / "setup_wizard.py"
    monkeypatch.setattr(setup_wizard, "__file__", str(fake_init))

    assert setup_wizard._bundled_manifest_paths() is None


def test_bundled_skills_manifest_paths_returns_none_when_bundle_zero_byte(tmp_path, monkeypatch):
    """Skills-surface mirror of the zero-byte-placeholder case (#292)."""
    fake_prefix = tmp_path / "venv"
    share_dir = fake_prefix / "share" / "bicameral-mcp"
    _write_manifest_with_placeholder_bundle(share_dir, "skills-manifest.toml")

    monkeypatch.setattr(setup_wizard.sys, "prefix", str(fake_prefix))
    fake_init = tmp_path / "fake-pkg" / "setup_wizard.py"
    monkeypatch.setattr(setup_wizard, "__file__", str(fake_init))

    assert setup_wizard._bundled_skills_manifest_paths() is None


def test_bundled_hooks_manifest_paths_returns_pair_when_all_present(tmp_path, monkeypatch):
    """Once the release pipeline emits a non-empty `.sigstore` bundle
    alongside the manifest, the helper returns the `(manifest, bundle)`
    pair — verifier re-engages with no code change at install time."""
    fake_prefix = tmp_path / "venv"
    share_dir = fake_prefix / "share" / "bicameral-mcp"
    manifest, bundle = _write_full_pair(share_dir, "hooks-manifest.json")

    monkeypatch.setattr(setup_wizard.sys, "prefix", str(fake_prefix))
    fake_init = tmp_path / "fake-pkg" / "setup_wizard.py"
    monkeypatch.setattr(setup_wizard, "__file__", str(fake_init))

    result = setup_wizard._bundled_manifest_paths()
    assert result == (manifest, bundle)


def test_bundled_skills_manifest_paths_returns_pair_when_all_present(tmp_path, monkeypatch):
    """Skills-surface mirror of the manifest+bundle-present case (#292)."""
    fake_prefix = tmp_path / "venv"
    share_dir = fake_prefix / "share" / "bicameral-mcp"
    manifest, bundle = _write_full_pair(share_dir, "skills-manifest.toml")

    monkeypatch.setattr(setup_wizard.sys, "prefix", str(fake_prefix))
    fake_init = tmp_path / "fake-pkg" / "setup_wizard.py"
    monkeypatch.setattr(setup_wizard, "__file__", str(fake_init))

    result = setup_wizard._bundled_skills_manifest_paths()
    assert result == (manifest, bundle)
