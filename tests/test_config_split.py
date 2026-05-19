"""R4 config split (#368) — `_write_collaboration_config` writes two files.

Team-identity keys (`mode`, `team.backend`/`team.folder_id`/`team.remote_root`)
land in `<repo>/.bicameral/config.yaml` (committed). Per-operator keys
(`telemetry`, `channel`, `guided`, `signer_email_fallback`,
`render_source_attribution`, `team.role`) land in
`~/.bicameral/projects/<id>/operator.yaml` (private).

Decision: decision:5nr66wvmapjpt58rrji8.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import context  # noqa: E402
import setup_wizard  # noqa: E402


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Bare git init so the locator can resolve a project id."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_team_identity_keys_persist_to_config_yaml(git_repo: Path, tmp_path: Path) -> None:
    operator_path = tmp_path / "operator.yaml"
    setup_wizard._write_collaboration_config(
        data_path=git_repo,
        mode="team",
        guided=False,
        telemetry=False,
        team_backend={"backend": "google_drive", "folder_id": "abc123", "role": "founding_member"},
        channel="stable",
        operator_path=operator_path,
    )
    cfg = _read_yaml(git_repo / ".bicameral" / "config.yaml")
    assert cfg["mode"] == "team"
    assert cfg["team"]["backend"] == "google_drive"
    assert cfg["team"]["folder_id"] == "abc123"
    # Per-operator keys must NOT appear in the committed file.
    assert "telemetry" not in cfg
    assert "channel" not in cfg
    assert "guided" not in cfg
    assert "signer_email_fallback" not in cfg
    assert "render_source_attribution" not in cfg
    # team.role is per-operator → not in config.yaml's team block.
    assert "role" not in cfg["team"]


def test_operator_keys_persist_to_operator_yaml(git_repo: Path, tmp_path: Path) -> None:
    operator_path = tmp_path / "operator.yaml"
    setup_wizard._write_collaboration_config(
        data_path=git_repo,
        mode="team",
        guided=True,
        telemetry=True,
        team_backend={"backend": "google_drive", "folder_id": "abc123", "role": "founding_member"},
        channel="stable",
        operator_path=operator_path,
    )
    op = _read_yaml(operator_path)
    assert op["guided"] is True
    assert op["telemetry"] is True
    assert op["channel"] == "stable"
    assert op["signer_email_fallback"] == "local-part-only"
    assert op["render_source_attribution"] == "redacted"
    assert op["team"]["role"] == "founding_member"
    # team-identity keys must NOT appear in operator.yaml.
    assert "mode" not in op
    assert "backend" not in op.get("team", {})
    assert "folder_id" not in op.get("team", {})


def test_atomic_two_file_write_failure_unlinks_both_temps(
    git_repo: Path, tmp_path: Path, monkeypatch
) -> None:
    operator_path = tmp_path / "op-dir" / "operator.yaml"
    config_path = git_repo / ".bicameral" / "config.yaml"

    real_replace = Path.replace
    call_count = {"n": 0}

    def flaky_replace(self: Path, target):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated rename failure on second file")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    with pytest.raises(OSError):
        setup_wizard._write_collaboration_config(
            data_path=git_repo,
            mode="solo",
            telemetry=False,
            channel="stable",
            operator_path=operator_path,
        )

    # Neither destination file should exist (operator was rolled back).
    assert not operator_path.exists()
    assert not config_path.exists()

    # No leftover .tmp artifacts in either parent.
    leftovers = list(operator_path.parent.glob("*.tmp")) + list(config_path.parent.glob("*.tmp"))
    assert leftovers == [], f"leftover temp files: {leftovers}"


def test_routing_table_covers_every_written_key() -> None:
    """Every key produced by `_write_collaboration_config` must appear in the
    routing table. Catches drift where a new key is added to the writer
    but not the routing table.
    """
    written_keys = {
        "mode",
        "guided",
        "telemetry",
        "channel",
        "signer_email_fallback",
        "render_source_attribution",
        "team.backend",
        "team.folder_id",
        "team.remote_root",
        "team.role",
    }
    missing = written_keys - context._CONFIG_KEY_ROUTING.keys()
    assert missing == set(), f"keys missing from _CONFIG_KEY_ROUTING: {missing}"


def test_context_reads_route_per_key(git_repo: Path, tmp_path: Path, monkeypatch) -> None:
    """A team key set in config.yaml and an operator key set in operator.yaml
    are both reachable through the context.py readers — proving each
    reader consults the file owned by its key.
    """
    # Stub the locator's operator-path resolver so reads route to a fixture
    # path instead of real ~/.bicameral/projects/.
    operator_path = tmp_path / "operator.yaml"
    monkeypatch.setattr(
        "ledger_locator.resolve_operator_config_path",
        lambda repo_path=None: operator_path,
    )

    (git_repo / ".bicameral").mkdir()
    (git_repo / ".bicameral" / "config.yaml").write_text(
        "mode: team\ningest_max_bytes: 524288\n", encoding="utf-8"
    )
    operator_path.write_text("guided: true\n", encoding="utf-8")

    assert context._read_guided_mode(str(git_repo)) is True  # from operator.yaml
    assert context._read_ingest_max_bytes(str(git_repo)) == 524288  # from config.yaml
