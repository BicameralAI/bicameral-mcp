"""Phase 2B (#368): ledger/adapter.py:_default_db_url delegates to the Ledger Locator.

The adapter's default URL used to hard-code `~/.bicameral/ledger.db`. After
delegation, it resolves through `ledger_locator.resolve_ledger_url()` so
project-scoped paths and the SURREAL_URL env override are honored
uniformly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_locator_env(monkeypatch):
    monkeypatch.delenv("SURREAL_URL", raising=False)
    monkeypatch.delenv("CODE_LOCATOR_SQLITE_DB", raising=False)
    monkeypatch.delenv("BICAMERAL_LOCATOR_ALLOW_COLLISION", raising=False)


def test_default_url_comes_from_locator(git_repo: Path, monkeypatch) -> None:
    """Without SURREAL_URL set, the adapter's default URL matches the locator.

    Asserts on the locator's output rather than a literal path so the test
    survives layout changes in the locator.
    """
    import ledger_locator
    from ledger.adapter import _default_db_url

    monkeypatch.chdir(git_repo)

    locator_url = ledger_locator.resolve_ledger_url(repo_path=git_repo)
    adapter_url = _default_db_url()

    assert adapter_url == locator_url
    assert adapter_url.startswith("surrealkv://")
    assert "/.bicameral/projects/" in adapter_url


def test_default_url_honors_surreal_env_override(git_repo: Path, monkeypatch) -> None:
    """SURREAL_URL env-var wins over the project-scoped default.

    The adapter must respect the same override the locator does — they go
    through the same code path now.
    """
    from ledger.adapter import _default_db_url

    monkeypatch.chdir(git_repo)
    monkeypatch.setenv("SURREAL_URL", "memory://")

    assert _default_db_url() == "memory://"


def test_default_url_no_legacy_home_literal(git_repo: Path, monkeypatch) -> None:
    """Regression guard: the legacy `~/.bicameral/ledger.db` literal is gone.

    The adapter's default must land under `~/.bicameral/projects/<id>/`,
    never the un-project-scoped `~/.bicameral/ledger.db` of v0.15.x.
    """
    from ledger.adapter import _default_db_url

    monkeypatch.chdir(git_repo)
    url = _default_db_url()

    # Must NOT be the legacy un-project-scoped path.
    assert not url.endswith("/.bicameral/ledger.db")
    # MUST be under the project-scoped projects/ dir.
    assert "/.bicameral/projects/" in url
