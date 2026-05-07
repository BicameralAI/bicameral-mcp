"""Functionality tests for `release.sbom_emit` (#218 Phase 1).

Locks the CycloneDX-1.5 SBOM emission contract:
- Successful emission returns the output path; the file parses as JSON
  with ``bomFormat == "CycloneDX"`` and ``specVersion == "1.5"``
- Subprocess failure raises (no silent empty-SBOM fallback)
- Subprocess invocation is list-form argv, no shell=True
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from release import sbom_emit


def _stub_run_writes_valid_sbom(output_path: Path):
    def runner(cmd, **_kwargs):
        # Honor subprocess discipline: cmd must be list-form, no shell=True.
        assert isinstance(cmd, list), "must be list-form argv"
        assert "shell" not in _kwargs or not _kwargs["shell"], "shell=True forbidden"
        # Find the -o argument
        idx = cmd.index("-o")
        target = Path(cmd[idx + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "specVersion": "1.5",
                    "components": [{"name": "bicameral-mcp", "version": "0.13.3"}],
                }
            )
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    return runner


def test_emit_sbom_returns_path_to_valid_cyclonedx_15_document(tmp_path: Path) -> None:
    wheel = tmp_path / "bicameral_mcp-0.13.3-py3-none-any.whl"
    wheel.write_bytes(b"PK\x03\x04stub-wheel-content")
    output = tmp_path / "sbom.json"
    with patch.object(subprocess, "run", _stub_run_writes_valid_sbom(output)):
        result = sbom_emit.emit_sbom(wheel, output)
    assert result == output
    parsed = json.loads(output.read_text())
    assert parsed["bomFormat"] == "CycloneDX"
    assert parsed["specVersion"] == "1.5"
    assert len(parsed["components"]) >= 1


def test_emit_sbom_raises_on_subprocess_failure(tmp_path: Path) -> None:
    wheel = tmp_path / "bad.whl"
    wheel.write_bytes(b"")
    output = tmp_path / "sbom.json"

    def fail_runner(cmd, **_kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"bad wheel")

    with patch.object(subprocess, "run", fail_runner):
        with pytest.raises(subprocess.CalledProcessError):
            sbom_emit.emit_sbom(wheel, output)
    # No silent empty-SBOM fallback.
    assert not output.exists()


def test_emit_sbom_rejects_output_with_wrong_spec_version(tmp_path: Path) -> None:
    wheel = tmp_path / "wheel.whl"
    wheel.write_bytes(b"stub")
    output = tmp_path / "sbom.json"

    def wrong_version_runner(cmd, **_kwargs):
        idx = cmd.index("-o")
        Path(cmd[idx + 1]).write_text(json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.4"}))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    with patch.object(subprocess, "run", wrong_version_runner):
        with pytest.raises(sbom_emit.SBOMValidationError):
            sbom_emit.emit_sbom(wheel, output)


def test_emit_sbom_uses_list_form_argv_with_no_shell_true(tmp_path: Path) -> None:
    """Defense-in-depth A03 contract: assertion lives in the stub runner."""
    wheel = tmp_path / "wheel.whl"
    wheel.write_bytes(b"stub")
    output = tmp_path / "sbom.json"
    captured = {}

    def capture(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        Path(output).write_text(
            json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.5", "components": []})
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    with patch.object(subprocess, "run", capture):
        sbom_emit.emit_sbom(wheel, output)
    assert isinstance(captured["cmd"], list)
    assert captured["kwargs"].get("shell") in (None, False)
