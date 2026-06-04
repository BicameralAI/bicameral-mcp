"""#544 — CI action pins and hash-locked dependency audit coverage."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_github_workflow_actions_are_sha_pinned() -> None:
    workflow_dir = ROOT / ".github" / "workflows"
    mutable_ref = re.compile(r"uses:\s+[^@\s]+@(?:v\d+|main|master|release/)")

    offenders: list[str] = []
    for workflow in sorted(workflow_dir.glob("*.yml")):
        for line_no, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            if mutable_ref.search(line):
                offenders.append(f"{workflow.relative_to(ROOT)}:{line_no}: {line.strip()}")

    assert offenders == []


def test_dependabot_tracks_github_actions() -> None:
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

    assert 'package-ecosystem: "github-actions"' in config
    assert 'target-branch: "dev"' in config


def test_requirements_locks_are_hash_pinned() -> None:
    lockfiles = [
        ROOT / "requirements.lock",
        ROOT / "requirements-audit.lock",
    ]

    for lockfile in lockfiles:
        text = lockfile.read_text(encoding="utf-8")
        package_lines = [
            line
            for line in text.splitlines()
            if line and not line.startswith((" ", "#")) and "==" in line
        ]

        assert package_lines, f"{lockfile.name} should contain pinned packages"
        assert "--hash=sha256:" in text
        assert all("==" in line and "\\" in line for line in package_lines)


def test_pip_audit_input_pins_audit_tool_version() -> None:
    audit_input = (ROOT / "requirements-audit.in").read_text(encoding="utf-8")

    assert "pip-audit==2.9.0" in audit_input


def test_pip_audit_gate_uses_locked_requirements_without_pip_resolution() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pip-audit.yml").read_text(encoding="utf-8")

    assert "--require-hashes -r requirements-audit.lock" in workflow
    assert "-r requirements.lock" in workflow
    assert "--disable-pip" in workflow


def test_pip_audit_tool_lock_is_hash_pinned() -> None:
    lockfile = ROOT / "requirements-audit.lock"
    text = lockfile.read_text(encoding="utf-8")

    assert "pip-audit==2.9.0" in text
