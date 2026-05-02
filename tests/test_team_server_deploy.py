"""Functionality tests for team_server Phase 1 — deployment artifact validation."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_docker_compose_yaml_validates():
    """Behavior: docker-compose can parse the team-server compose file and
    surfaces the bicameral-team-server service in its config output."""
    if not shutil.which("docker-compose") and not shutil.which("docker"):
        pytest.skip("docker / docker-compose not on PATH")
    compose_path = REPO_ROOT / "deploy" / "team-server.docker-compose.yml"
    assert compose_path.exists(), f"compose file missing: {compose_path}"
    cmd = (
        ["docker-compose", "-f", str(compose_path), "config"]
        if shutil.which("docker-compose")
        else ["docker", "compose", "-f", str(compose_path), "config"]
    )
    # The compose file enforces BICAMERAL_TEAM_SERVER_SECRET_KEY at parse time
    # (using ${VAR:?error} syntax) — fail-loud rather than ship a default.
    # Provide a dummy value here so `config` parses; deployment supplies real.
    import os
    env = {**os.environ, "BICAMERAL_TEAM_SERVER_SECRET_KEY": "dGVzdF9rZXk="}
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    assert result.returncode == 0, f"compose config failed: {result.stderr}"
    assert "bicameral-team-server" in result.stdout
