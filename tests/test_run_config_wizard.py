"""R4 two-pane editor for run_config_wizard (#368).

The editor must:
  - Read team-identity keys from <repo>/.bicameral/config.yaml
  - Read per-operator keys from ~/.bicameral/projects/<id>/operator.yaml
  - Tag each prompt with [team] or [your machine] so the operator can see
    which file their answer lands in
  - Write back via the same atomic two-file split as the setup wizard

Decision: decision:5nr66wvmapjpt58rrji8.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import setup_wizard  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("BICAMERAL_AUTHORITATIVE_REF", raising=False)
    monkeypatch.delenv("SURREAL_URL", raising=False)
    monkeypatch.delenv("CODE_LOCATOR_SQLITE_DB", raising=False)


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class _CapturingSelect:
    """Stand-in for `questionary.select(...).ask()` that records the prompt
    message + returns a scripted value from the supplied script dict."""

    def __init__(self, script: dict[str, object], captured: list[dict]) -> None:
        self._script = script
        self._captured = captured

    def __call__(self, message, choices=None, default=None, **kwargs):
        self._captured.append(
            {
                "message": message,
                "choices": [c.value for c in (choices or [])],
                "default": getattr(default, "value", default),
            }
        )
        # Lookup by message prefix — first matching key in the script wins.
        for key, value in self._script.items():
            if key in message:
                return _Ask(value)
        # Default: return the prompt's `default` value (its .value attr).
        return _Ask(getattr(default, "value", default))


class _Ask:
    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


def _install_fake_questionary(monkeypatch, script: dict[str, object], captured: list[dict]) -> None:
    """Stub `questionary` so the wizard's interactive prompts return
    scripted values and record what they were asked."""
    import types

    fake = types.ModuleType("questionary")
    fake.select = _CapturingSelect(script, captured)

    class _Choice:
        def __init__(self, label, value=None):
            self.label = label
            self.value = value if value is not None else label

    fake.Choice = _Choice
    monkeypatch.setitem(sys.modules, "questionary", fake)
    monkeypatch.setattr(setup_wizard, "_is_interactive", lambda: True)


def _stub_post_write_install(monkeypatch) -> None:
    """The wizard re-installs skills via subprocess after writing. Stub it
    out so tests don't shell out and parse the subprocess's stdout."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=a, returncode=0, stdout="0", stderr=""),
    )


def test_editor_reads_from_both_files(git_repo: Path, tmp_path: Path, monkeypatch) -> None:
    """When mode lives in config.yaml and guided/channel live in operator.yaml,
    the editor surfaces both — each tagged with its routing prefix."""
    operator_path = tmp_path / "operator.yaml"
    monkeypatch.setattr(
        "ledger_locator.resolve_operator_config_path",
        lambda repo_path=None: operator_path,
    )
    (git_repo / ".bicameral").mkdir()
    (git_repo / ".bicameral" / "config.yaml").write_text(
        "mode: team\nteam:\n  backend: google_drive\n  folder_id: x\n",
        encoding="utf-8",
    )
    operator_path.write_text("guided: true\ntelemetry: false\nchannel: stable\n", encoding="utf-8")

    captured: list[dict] = []
    # Empty script → every prompt returns the displayed default → no edits.
    _install_fake_questionary(monkeypatch, {}, captured)
    _stub_post_write_install(monkeypatch)
    monkeypatch.setattr(setup_wizard, "_detect_repo", lambda hint=None: git_repo)

    rc = setup_wizard.run_config_wizard()
    assert rc == 0

    messages = [entry["message"] for entry in captured]
    # The mode prompt is team-routed.
    assert any("[team]" in m and "Collaboration mode" in m for m in messages), messages
    # The guided prompt is operator-routed.
    assert any("[your machine]" in m and "Interaction intensity" in m for m in messages), messages
    # Defaults reflect what was loaded from each file.
    by_msg = {entry["message"]: entry for entry in captured}
    mode_entry = next(v for k, v in by_msg.items() if "Collaboration mode" in k)
    guided_entry = next(v for k, v in by_msg.items() if "Interaction intensity" in k)
    assert mode_entry["default"] == "team"
    assert guided_entry["default"] is True


def test_editor_writes_to_routed_file(git_repo: Path, tmp_path: Path, monkeypatch) -> None:
    """Simulate edits — mode team→solo (team-side), guided true→false
    (operator-side). Each change must land in the right file with the
    other keys untouched."""
    operator_path = tmp_path / "op-dir" / "operator.yaml"
    monkeypatch.setattr(
        "ledger_locator.resolve_operator_config_path",
        lambda repo_path=None: operator_path,
    )
    (git_repo / ".bicameral").mkdir()
    (git_repo / ".bicameral" / "config.yaml").write_text(
        "mode: team\nteam:\n  backend: google_drive\n  folder_id: x\n",
        encoding="utf-8",
    )
    operator_path.parent.mkdir(parents=True, exist_ok=True)
    operator_path.write_text("guided: true\ntelemetry: false\nchannel: stable\n", encoding="utf-8")

    captured: list[dict] = []
    _install_fake_questionary(
        monkeypatch,
        {
            "Collaboration mode": "solo",
            "Interaction intensity": False,
            # telemetry stays false — keep it routed without changing
        },
        captured,
    )
    _stub_post_write_install(monkeypatch)
    monkeypatch.setattr(setup_wizard, "_detect_repo", lambda hint=None: git_repo)

    rc = setup_wizard.run_config_wizard()
    assert rc == 0

    team_yaml = yaml.safe_load((git_repo / ".bicameral" / "config.yaml").read_text())
    op_yaml = yaml.safe_load(operator_path.read_text())

    # Team file: mode flipped to solo; team-backend block untouched per
    # _write_collaboration_config's behavior (only emits team: when
    # team_backend is passed). For mode=solo the writer drops the team
    # block — that's the correct semantic (solo has no team backend).
    assert team_yaml["mode"] == "solo"
    # Per-operator keys must NOT have leaked into the team file.
    for forbidden in ("guided", "telemetry", "channel", "signer_email_fallback"):
        assert forbidden not in team_yaml

    # Operator file: guided flipped to false; channel + telemetry unchanged.
    assert op_yaml["guided"] is False
    assert op_yaml["telemetry"] is False
    assert op_yaml["channel"] == "stable"
    # Team-identity keys must NOT have leaked into the operator file.
    assert "mode" not in op_yaml
