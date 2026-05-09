"""Phase 3 tests: setup wizard Create/Join/LocalFolder branches (#277).

Wizard helpers under test live in setup_wizard.py:
  * _select_team_backend(repo_path) → dict
  * _create_shared_ledger_drive(repo_path) → dict
  * _join_shared_ledger_drive(repo_path) → dict
  * _select_local_folder_backend() → dict
  * _extract_folder_id(raw) → str
  * _write_collaboration_config(...) — extended to persist team backend dict

All Drive interactions are stubbed via unittest.mock.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _read_yaml(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ── _extract_folder_id ───────────────────────────────────────────────────


def test_extract_folder_id_accepts_raw_id():
    from setup_wizard import _extract_folder_id

    assert (
        _extract_folder_id("1AbCdEfGhIjKl_mNoPqRsTuVwXyZ-abcd")
        == "1AbCdEfGhIjKl_mNoPqRsTuVwXyZ-abcd"
    )


def test_extract_folder_id_accepts_full_url():
    from setup_wizard import _extract_folder_id

    url = "https://drive.google.com/drive/folders/1AbCdEfGhIjKl_mNoPqRsTuVwXyZ-abcd?usp=sharing"
    assert _extract_folder_id(url) == "1AbCdEfGhIjKl_mNoPqRsTuVwXyZ-abcd"


def test_extract_folder_id_strips_whitespace():
    from setup_wizard import _extract_folder_id

    assert _extract_folder_id("  abc123  \n") == "abc123"


# ── Create branch ───────────────────────────────────────────────────────


def test_create_branch_persists_founding_member_role(tmp_path: Path, monkeypatch):
    """End-to-end: Create flow writes founding_member role + folder_id to config."""
    from setup_wizard import _create_shared_ledger_drive, _write_collaboration_config

    monkeypatch.setattr("setup_wizard._prompt_yes_no", lambda *a, **kw: True)

    fake_adapter = MagicMock()
    fake_adapter.create_folder.return_value = "abc123"
    with patch("events.backends.google_drive.GoogleDriveAdapter", return_value=fake_adapter):
        team_cfg = _create_shared_ledger_drive(repo_path=tmp_path)

    assert team_cfg == {
        "backend": "google_drive",
        "folder_id": "abc123",
        "role": "founding_member",
    }
    fake_adapter._credentials.assert_called_once()
    fake_adapter.create_folder.assert_called_once()

    _write_collaboration_config(
        data_path=tmp_path,
        mode="team",
        guided=False,
        telemetry=False,
        team_backend=team_cfg,
    )
    cfg = _read_yaml(tmp_path / ".bicameral" / "config.yaml")
    assert cfg["mode"] == "team"
    assert cfg["team"] == {
        "backend": "google_drive",
        "folder_id": "abc123",
        "role": "founding_member",
    }


# ── Join branch ─────────────────────────────────────────────────────────


def test_join_branch_extracts_folder_id_from_url(tmp_path: Path, monkeypatch):
    from setup_wizard import _join_shared_ledger_drive, _write_collaboration_config

    monkeypatch.setattr(
        "setup_wizard._prompt_text_or_exit",
        lambda *a, **kw: "https://drive.google.com/drive/folders/xyz789?usp=sharing",
    )
    monkeypatch.setattr("setup_wizard._prompt_yes_no", lambda *a, **kw: True)

    fake_adapter = MagicMock()
    with patch("events.backends.google_drive.GoogleDriveAdapter", return_value=fake_adapter):
        team_cfg = _join_shared_ledger_drive(repo_path=tmp_path)

    assert team_cfg["folder_id"] == "xyz789"
    assert team_cfg["role"] == "member"
    assert team_cfg["backend"] == "google_drive"
    fake_adapter.verify_access.assert_called_once()

    _write_collaboration_config(
        data_path=tmp_path,
        mode="team",
        guided=False,
        telemetry=False,
        team_backend=team_cfg,
    )
    cfg = _read_yaml(tmp_path / ".bicameral" / "config.yaml")
    assert cfg["team"]["folder_id"] == "xyz789"


def test_join_branch_verifies_access_before_persist(tmp_path: Path, monkeypatch):
    """If verify_access raises, the wizard must SystemExit and write nothing."""
    from events.backends.google_drive import FolderNotFoundError
    from setup_wizard import _join_shared_ledger_drive

    monkeypatch.setattr("setup_wizard._prompt_text_or_exit", lambda *a, **kw: "missing-id")
    monkeypatch.setattr("setup_wizard._prompt_yes_no", lambda *a, **kw: True)

    fake_adapter = MagicMock()
    fake_adapter.verify_access.side_effect = FolderNotFoundError("missing-id not found")

    with patch("events.backends.google_drive.GoogleDriveAdapter", return_value=fake_adapter):
        with pytest.raises(SystemExit):
            _join_shared_ledger_drive(repo_path=tmp_path)

    # No config.yaml should exist after a failed Join.
    assert not (tmp_path / ".bicameral" / "config.yaml").exists()


def test_join_branch_aborts_on_identity_decline(tmp_path: Path, monkeypatch):
    """If the operator says No to the identity confirmation, abort cleanly."""
    from setup_wizard import _join_shared_ledger_drive

    monkeypatch.setattr("setup_wizard._prompt_text_or_exit", lambda *a, **kw: "good-id")
    monkeypatch.setattr("setup_wizard._prompt_yes_no", lambda *a, **kw: False)

    fake_adapter = MagicMock()
    with patch("events.backends.google_drive.GoogleDriveAdapter", return_value=fake_adapter):
        with pytest.raises(SystemExit):
            _join_shared_ledger_drive(repo_path=tmp_path)

    assert not (tmp_path / ".bicameral" / "config.yaml").exists()


def test_create_aborts_when_security_disclosure_declined(tmp_path: Path, monkeypatch):
    """Operator declining the post-disclosure consent must abort cleanly."""
    from setup_wizard import _create_shared_ledger_drive

    monkeypatch.setattr("setup_wizard._prompt_yes_no", lambda *a, **kw: False)

    fake_adapter = MagicMock()
    with patch("events.backends.google_drive.GoogleDriveAdapter", return_value=fake_adapter):
        with pytest.raises(SystemExit, match="Aborted"):
            _create_shared_ledger_drive(repo_path=tmp_path)
    fake_adapter._credentials.assert_not_called()
    fake_adapter.create_folder.assert_not_called()


def test_security_disclosure_mentions_what_bicameral_can_see(capsys, monkeypatch):
    """The disclosure must surface the OAuth-app-owner visibility honestly,
    distinguish CLI-on-your-machine from Bicameral-the-company, and name
    the trust dependency explicitly."""
    from setup_wizard import _print_drive_security_disclosure

    _print_drive_security_disclosure()
    out = capsys.readouterr().out
    # File-content claim: company does NOT receive copies.
    assert "does NOT receive copies" in out
    # Scope claim: refers to CLI-on-your-machine, not the company.
    assert "Bicameral CLI on your machine" in out
    # Visibility surface: aggregate API counts + consent records, not contents.
    assert "Aggregate API request counts" in out
    # Trust dependency named explicitly.
    assert "trust dependency" in out.lower()
    assert "drive.file" in out
    assert "google-drive-token.json" in out


# ── LocalFolder branch ──────────────────────────────────────────────────


def test_local_folder_branch_persists_remote_root(tmp_path: Path, monkeypatch):
    from setup_wizard import _select_local_folder_backend, _write_collaboration_config

    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr("setup_wizard._prompt_text_or_exit", lambda *a, **kw: str(shared))

    team_cfg = _select_local_folder_backend()
    assert team_cfg == {
        "backend": "local_folder",
        "remote_root": str(shared),
        "role": "member",
    }

    _write_collaboration_config(
        data_path=tmp_path,
        mode="team",
        guided=False,
        telemetry=False,
        team_backend=team_cfg,
    )
    cfg = _read_yaml(tmp_path / ".bicameral" / "config.yaml")
    assert cfg["team"] == {
        "backend": "local_folder",
        "remote_root": str(shared),
        "role": "member",
    }


def test_local_folder_branch_rejects_unwritable_path(tmp_path: Path, monkeypatch):
    from setup_wizard import _select_local_folder_backend

    monkeypatch.setattr("setup_wizard._prompt_text_or_exit", lambda *a, **kw: "/")
    with pytest.raises(SystemExit, match="not writable"):
        _select_local_folder_backend()
