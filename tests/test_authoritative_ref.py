"""Authoritative-ref detection regression tests (v0.4.6).

Covers the three fallback tiers:
  1. BICAMERAL_AUTHORITATIVE_REF env var (explicit override)
  2. git symbolic-ref refs/remotes/origin/HEAD (remote default)
  3. fallback to "main"
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_locator_runtime import detect_authoritative_ref, resolve_ref_sha


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )
    return result.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("# test\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    return tmp_path


def test_detect_env_var_override_wins(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "trunk")
    assert detect_authoritative_ref(str(tmp_path)) == "trunk"


def test_detect_falls_back_to_main_when_no_remote(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    monkeypatch.delenv("BICAMERAL_AUTHORITATIVE_REF", raising=False)
    # No origin remote configured → symbolic-ref fails → fallback to "main"
    assert detect_authoritative_ref(str(tmp_path)) == "main"


def test_detect_reads_origin_head_when_configured(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    # Simulate a clone that knows origin/HEAD points at main
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(tmp_path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    monkeypatch.delenv("BICAMERAL_AUTHORITATIVE_REF", raising=False)
    assert detect_authoritative_ref(str(tmp_path)) == "main"


def test_resolve_ref_sha_resolves_local_branch(tmp_path):
    _init_repo(tmp_path)
    expected = _git(tmp_path, "rev-parse", "HEAD")
    assert resolve_ref_sha(str(tmp_path), "main") == expected


def test_resolve_ref_sha_empty_for_missing_ref(tmp_path):
    _init_repo(tmp_path)
    assert resolve_ref_sha(str(tmp_path), "branch-that-does-not-exist") == ""
