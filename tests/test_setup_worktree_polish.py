"""v0.14.7 — worktree-polish wizard surface tests (#368 stopgap).

Pure unit tests on the new ``setup_wizard`` helpers introduced by the
v0.14.7 worktree-polish patch:

- ``_detect_linked_worktree`` — returns the resolved gitdir when ``.git``
  is a file pointer (linked worktree or submodule), None otherwise.
- ``_probe_origin_head`` — bare branch name from ``refs/remotes/origin/HEAD``
  or None when unset.
- ``_resolve_authoritative_ref`` — env override > origin/HEAD probe >
  interactive prompt > silent "main" fallback. Returns
  ``(branch, needs_env_override)`` so the wizard only writes
  ``BICAMERAL_AUTHORITATIVE_REF`` when load-bearing.

The wider v0.15.0 Ledger Locator (#368) supersedes most of this surface;
these tests guard the stopgap until that lands.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import setup_wizard
from setup_wizard import (
    _build_config,
    _detect_linked_worktree,
    _probe_origin_head,
    _resolve_authoritative_ref,
)


def _make_plain_repo(root: Path) -> Path:
    git_dir = root / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    return root


# ── _detect_linked_worktree ─────────────────────────────────────────────


def test_detect_linked_worktree_returns_none_for_plain_repo(tmp_path: Path) -> None:
    repo = _make_plain_repo(tmp_path)
    assert _detect_linked_worktree(repo) is None


def test_detect_linked_worktree_returns_none_when_no_git_root(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "nowhere"
    not_a_repo.mkdir()
    assert _detect_linked_worktree(not_a_repo) is None


def test_detect_linked_worktree_returns_gitdir_for_submodule(tmp_path: Path) -> None:
    """Submodule = ``.git`` file with relative ``gitdir:`` pointer.
    Same shape as a linked worktree."""
    superrepo = tmp_path / "super"
    real_gitdir = superrepo / ".git" / "modules" / "sub"
    real_gitdir.mkdir(parents=True)
    (real_gitdir / "HEAD").write_text("ref: refs/heads/main\n")

    submodule = superrepo / "sub"
    submodule.mkdir()
    (submodule / ".git").write_text("gitdir: ../.git/modules/sub\n")

    resolved = _detect_linked_worktree(submodule)
    assert resolved is not None
    assert resolved.resolve() == real_gitdir.resolve()


def test_detect_linked_worktree_returns_gitdir_for_worktree(tmp_path: Path) -> None:
    """`git worktree add` layout: ``.git`` is a file with absolute
    ``gitdir: <main>/.git/worktrees/<name>``. Hooks live there."""
    main_repo = tmp_path / "main_repo"
    main_gitdir = main_repo / ".git"
    main_gitdir.mkdir(parents=True)
    (main_gitdir / "HEAD").write_text("ref: refs/heads/main\n")

    wt_gitdir = main_gitdir / "worktrees" / "feature-a"
    wt_gitdir.mkdir(parents=True)

    wt = tmp_path / "feature_a"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_gitdir}\n")

    resolved = _detect_linked_worktree(wt)
    assert resolved is not None
    assert resolved.resolve() == wt_gitdir.resolve()


# ── _probe_origin_head ──────────────────────────────────────────────────


def test_probe_origin_head_returns_branch_name(monkeypatch, tmp_path: Path) -> None:
    """Probe returns the bare branch name when origin/HEAD is set."""

    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="refs/remotes/origin/trunk\n", stderr=""
        )

    monkeypatch.setattr(setup_wizard.subprocess, "run", _fake_run)
    assert _probe_origin_head(tmp_path) == "trunk"


def test_probe_origin_head_returns_none_when_unset(monkeypatch, tmp_path: Path) -> None:
    """Non-zero exit → no remote default; returns None."""

    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="fatal\n")

    monkeypatch.setattr(setup_wizard.subprocess, "run", _fake_run)
    assert _probe_origin_head(tmp_path) is None


def test_probe_origin_head_returns_none_on_exception(monkeypatch, tmp_path: Path) -> None:
    """Subprocess errors degrade to None (the runtime falls back to ``main``)."""

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("git missing")

    monkeypatch.setattr(setup_wizard.subprocess, "run", _boom)
    assert _probe_origin_head(tmp_path) is None


# ── _resolve_authoritative_ref ──────────────────────────────────────────


def test_resolve_auth_ref_honours_explicit_env(monkeypatch, tmp_path: Path) -> None:
    """``BICAMERAL_AUTHORITATIVE_REF`` set → use it; no override write needed."""
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "develop")
    branch, needs_override = _resolve_authoritative_ref(tmp_path)
    assert branch == "develop"
    assert needs_override is False


def test_resolve_auth_ref_uses_probe_when_env_unset(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BICAMERAL_AUTHORITATIVE_REF", raising=False)
    monkeypatch.setattr(setup_wizard, "_probe_origin_head", lambda _: "master")
    branch, needs_override = _resolve_authoritative_ref(tmp_path)
    assert branch == "master"
    assert needs_override is False


def test_resolve_auth_ref_silent_main_fallback_when_noninteractive(
    monkeypatch, tmp_path: Path
) -> None:
    """No env, no probe, non-interactive → silent ``main`` default,
    no env override written."""
    monkeypatch.delenv("BICAMERAL_AUTHORITATIVE_REF", raising=False)
    monkeypatch.setattr(setup_wizard, "_probe_origin_head", lambda _: None)
    monkeypatch.setattr(setup_wizard, "_is_interactive", lambda: False)
    branch, needs_override = _resolve_authoritative_ref(tmp_path)
    assert branch == "main"
    assert needs_override is False


def test_resolve_auth_ref_prompt_default_main_no_override(monkeypatch, tmp_path: Path) -> None:
    """Interactive prompt, user accepts ``main`` default → no env override
    (runtime already falls back to main)."""
    monkeypatch.delenv("BICAMERAL_AUTHORITATIVE_REF", raising=False)
    monkeypatch.setattr(setup_wizard, "_probe_origin_head", lambda _: None)
    monkeypatch.setattr(setup_wizard, "_is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "")
    branch, needs_override = _resolve_authoritative_ref(tmp_path)
    assert branch == "main"
    assert needs_override is False


def test_resolve_auth_ref_prompt_non_main_writes_override(monkeypatch, tmp_path: Path) -> None:
    """Interactive prompt, user gives ``trunk`` → write env override
    (runtime would otherwise wrongly default to ``main``)."""
    monkeypatch.delenv("BICAMERAL_AUTHORITATIVE_REF", raising=False)
    monkeypatch.setattr(setup_wizard, "_probe_origin_head", lambda _: None)
    monkeypatch.setattr(setup_wizard, "_is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "trunk")
    branch, needs_override = _resolve_authoritative_ref(tmp_path)
    assert branch == "trunk"
    assert needs_override is True


# ── _build_config integration ────────────────────────────────────────────


def test_build_config_omits_auth_ref_env_by_default(tmp_path: Path) -> None:
    """No ``BICAMERAL_AUTHORITATIVE_REF`` env key when authoritative_ref=None.
    The runtime auto-detector handles the common case."""
    config = _build_config(tmp_path)
    assert "BICAMERAL_AUTHORITATIVE_REF" not in config["env"]


def test_build_config_writes_auth_ref_env_when_pinned(tmp_path: Path) -> None:
    config = _build_config(tmp_path, authoritative_ref="trunk")
    assert config["env"].get("BICAMERAL_AUTHORITATIVE_REF") == "trunk"


def test_build_config_skips_empty_auth_ref(tmp_path: Path) -> None:
    """Empty string is treated the same as None — no env key written."""
    config = _build_config(tmp_path, authoritative_ref="")
    assert "BICAMERAL_AUTHORITATIVE_REF" not in config["env"]
