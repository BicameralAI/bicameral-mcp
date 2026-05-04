"""Shared test fixtures.

Run everything with: pytest tests/ -v

Phase 1: requires indexed repo (code locator)
Phase 2: requires SurrealDB (memory:// for tests)
Phase 3: full E2E — exercises all handlers with real adapters
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_consent_state(tmp_path_factory):
    """Reroute ~/.bicameral/ to a per-session tmp dir and skip the consent
    notice by default (issue #39).

    Tests that explicitly exercise the consent-notice path unset
    BICAMERAL_SKIP_CONSENT_NOTICE within the test body. Stdlib only — no
    third-party fixture plugin.
    """
    home = tmp_path_factory.mktemp("bicameral_home")
    saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "BICAMERAL_SKIP_CONSENT_NOTICE")}
    os.environ["HOME"] = str(home)
    os.environ["USERPROFILE"] = str(home)
    os.environ["BICAMERAL_SKIP_CONSENT_NOTICE"] = "1"
    try:
        yield home
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def pytest_configure(config):
    config.addinivalue_line("markers", "phase1: requires RealCodeLocatorAdapter")
    config.addinivalue_line("markers", "phase2: requires SurrealDBLedgerAdapter + SurrealDB")
    config.addinivalue_line("markers", "phase3: full E2E — requires both Phase 1 + Phase 2")
    config.addinivalue_line("markers", "alpha_flow: Jacob North Star regression suite — v0.7 gate")
    config.addinivalue_line(
        "markers", "bench: drift benchmark harness (V1 A1) — skipped by default, run with -m bench"
    )


@pytest.fixture(autouse=True)
def _default_authoritative_ref_to_current_branch(monkeypatch):
    """v0.4.6 pollution guard default: treat whatever branch the test
    runner is on as authoritative.

    The branch-name pollution guard in `ingest_commit` refuses baseline
    writes when the current branch != authoritative_ref. Pre-existing
    tests were written before the guard and expect normal write behavior
    regardless of which branch the test runner happens to be on (e.g.
    the bicameral submodule checked out on `chore/bump-v0.4.6`). This
    fixture sets BICAMERAL_AUTHORITATIVE_REF to the current branch so
    those tests keep passing.

    Tests that care about the pollution guard (``test_pollution_bug.py``)
    explicitly ``monkeypatch.delenv("BICAMERAL_AUTHORITATIVE_REF")`` at
    the start of the test, which unsets this default for that test only.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        current_branch = result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        current_branch = ""
    if current_branch and current_branch != "HEAD":
        monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", current_branch)


@pytest.fixture
def repo_path() -> str:
    """Repo root. Defaults to the MCP repo itself for Phase 1+ tests."""
    return os.getenv(
        "REPO_PATH", str(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    )


@pytest.fixture
def surreal_url() -> str:
    return os.getenv("SURREAL_URL", "memory://")


@pytest.fixture
def ctx():
    """Build a BicameralContext from current env (SURREAL_URL, REPO_PATH)."""
    from context import BicameralContext

    return BicameralContext.from_env()


@pytest.fixture
def sample_transcript() -> str:
    return """\
## Architecture Sync — 2026-03-24

**Participants**: Jin, Silong

**Decision 1**: Use BM25 + dependency graph for code search. No vector store.
Rationale: BM25 is fast, deterministic, avoids embedding cost.
Constraints: Must handle repos with 10k+ symbols in < 2s.

**Decision 2**: Decision ledger as internal memory via vocab cache.
Prior intent→code mappings feed back into generation to avoid redundant lookups.

**Decision 3**: Status derived at query time from content-hash comparison.
Rationale: Avoids stale status after commits without full re-index.
"""


@pytest.fixture
def minimal_payload() -> dict:
    """A minimal valid CodeLocatorPayload for ingestion tests."""
    return {
        "query": "test decision for ledger ingestion",
        "repo": "test-repo",
        "commit_hash": "testcommit001",
        "analyzed_at": "2026-03-27T12:00:00Z",
        "mappings": [
            {
                "span": {
                    "span_id": "test-0",
                    "source_type": "transcript",
                    "text": "test decision for ledger ingestion",
                    "speaker": "Jin",
                    "source_ref": "test-meeting-001",
                },
                "intent": "test decision for ledger ingestion",
                "symbols": ["test_function"],
                "code_regions": [
                    {
                        "file_path": "server.py",
                        "symbol": "test_function",
                        "type": "function",
                        "start_line": 1,
                        "end_line": 20,
                        "purpose": "test",
                    }
                ],
                "dependency_edges": [],
            }
        ],
    }
