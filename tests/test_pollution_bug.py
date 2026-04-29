"""Branch pollution regression tests (v0.4.6, F1 + F1a).

Covers BOTH write sites where v0.4.5 could silently adopt branch state
as the new baseline:

  Bug 1 (F1)  — ``ingest_commit`` via ``handle_link_commit`` on a branch:
                adopted branch content as stored_hash.
  Bug 3 (F1a) — ``ingest_payload`` via ``handle_ingest``: stamped baseline
                hashes from the current HEAD, which is the branch the user
                happens to be sitting on.

Both failure modes are trust-killers: Brian's first-ever adoption experience
on a feature branch would birth a polluted ledger. The fix is authoritative-ref
detection in ``BicameralContext`` + pollution guards in both write sites.

These tests use a tmp git repo with a real main branch and a feature branch
so ``git symbolic-ref`` + ``git rev-parse`` return real SHAs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from adapters.ledger import get_ledger, reset_ledger_singleton
from context import BicameralContext
from handlers.ingest import handle_ingest
from handlers.link_commit import handle_link_commit

# ── Tiny git repo fixture with main + feature branch ─────────────────


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )
    return result.stdout.strip()


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(body).lstrip("\n"))


@pytest.fixture
def branched_repo(tmp_path: Path) -> Path:
    """Create a git repo on ``main`` with one file, then branch to
    ``feat/v2`` and edit the file so branch HEAD ≠ main HEAD.
    Returns the repo root.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")

    _write(
        repo / "pricing.py",
        """
        def calculate_discount(total):
            if total >= 100:
                return total * 0.10
            return 0
        """,
    )
    _git(repo, "add", ".")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init on main")

    # Simulate an origin remote pointing at this repo so
    # detect_authoritative_ref finds origin/HEAD → main.
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    # Branch off and edit
    _git(repo, "checkout", "-q", "-b", "feat/v2")
    _write(
        repo / "pricing.py",
        """
        def calculate_discount(total):
            if total >= 500:
                return total * 0.25
            return 0
        """,
    )
    _git(repo, "add", ".")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "raise discount threshold")

    return repo


# ── Helpers ──────────────────────────────────────────────────────────


def _payload(repo: Path) -> dict:
    """Build an ingest payload for calculate_discount on main's line range."""
    return {
        "query": "discount threshold rule",
        "repo": str(repo),
        "analyzed_at": "2026-04-14T00:00:00Z",
        "mappings": [
            {
                "span": {
                    "span_id": "p-0",
                    "source_type": "transcript",
                    "text": "Apply 10% discount on orders over $100",
                    "source_ref": "sprint14",
                },
                "intent": "Apply 10% discount on orders over $100",
                "symbols": ["calculate_discount"],
                "code_regions": [
                    {
                        "file_path": "pricing.py",
                        "symbol": "calculate_discount",
                        "type": "function",
                        "start_line": 1,
                        "end_line": 4,
                        "purpose": "discount",
                    }
                ],
                "dependency_edges": [],
            }
        ],
    }


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_on_branch_stamps_main_baseline(
    monkeypatch,
    branched_repo,
    surreal_url,
):
    """Bug 3 (F1a) — ``handle_ingest`` from a feature branch must stamp
    baseline hashes against the authoritative ref (main), not the branch.

    Scenario:
      - Repo has main with discount threshold 100
      - Feature branch has threshold 500
      - User is checked out on the feature branch
      - User runs bicameral_ingest

    Expected: the stamped content_hash matches what's on main (threshold 100),
    NOT what's on the branch (threshold 500). After the user switches back
    to main, bicameral_status should report `reflected`, not `drifted`.
    """
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    monkeypatch.setenv("REPO_PATH", str(branched_repo))
    monkeypatch.delenv("BICAMERAL_AUTHORITATIVE_REF", raising=False)
    reset_ledger_singleton()

    # We're on feat/v2 (the branched_repo fixture leaves us there)
    assert _git(branched_repo, "rev-parse", "--abbrev-ref", "HEAD") == "feat/v2"

    ctx = BicameralContext.from_env()
    # Sanity: context knows main is authoritative and HEAD ≠ main
    assert ctx.authoritative_ref == "main"
    assert ctx.authoritative_sha != ""
    assert ctx.head_sha != ctx.authoritative_sha

    await handle_ingest(ctx, _payload(branched_repo))

    # Query the ledger directly for the stamped content_hash
    ledger = get_ledger()
    client = ledger._client
    rows = await client.query("SELECT content_hash FROM code_region WHERE file_path = 'pricing.py'")
    assert len(rows) >= 1, "code_region not created"
    stamped_hash = rows[0].get("content_hash", "")
    assert stamped_hash, "content_hash is empty — pollution guard failed upstream"

    # Compute what main's content hash SHOULD be
    from ledger.status import compute_content_hash

    main_hash = compute_content_hash(
        "pricing.py",
        1,
        4,
        str(branched_repo),
        ref=ctx.authoritative_sha,
    )
    branch_hash = compute_content_hash(
        "pricing.py",
        1,
        4,
        str(branched_repo),
        ref="HEAD",
    )

    assert main_hash != branch_hash, "test setup broken: branch and main have the same hash"
    assert stamped_hash == main_hash, (
        f"ingest stamped branch hash {stamped_hash[:16]} instead of "
        f"main hash {main_hash[:16]}. Pollution bug 3 regressed."
    )

    reset_ledger_singleton()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_link_commit_on_branch_runs_read_only(
    monkeypatch,
    branched_repo,
    surreal_url,
):
    """Bug 1 (F1) — ``handle_link_commit`` on a branch must not update
    stored baseline hashes. Drift is computed for reporting, but the
    region's content_hash is left alone so switching back to main
    doesn't flip every decision to drifted.
    """
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    monkeypatch.delenv("BICAMERAL_AUTHORITATIVE_REF", raising=False)
    reset_ledger_singleton()

    # First: ingest on main so the region starts with main's hash baseline.
    _git(branched_repo, "checkout", "-q", "main")
    monkeypatch.setenv("REPO_PATH", str(branched_repo))
    ctx_main = BicameralContext.from_env()
    assert ctx_main.head_sha == ctx_main.authoritative_sha

    await handle_ingest(ctx_main, _payload(branched_repo))

    ledger = get_ledger()
    client = ledger._client
    rows_before = await client.query(
        "SELECT content_hash FROM code_region WHERE file_path = 'pricing.py'"
    )
    hash_before = rows_before[0].get("content_hash", "")
    assert hash_before, "initial hash stamping failed"

    # Now: checkout the branch, call handle_link_commit.
    _git(branched_repo, "checkout", "-q", "feat/v2")
    # Rebuild ctx to pick up the new HEAD
    ctx_branch = BicameralContext.from_env()
    assert ctx_branch.head_sha != ctx_branch.authoritative_sha, (
        "test setup: branch HEAD should differ from main"
    )

    await handle_link_commit(ctx_branch, commit_hash="HEAD")

    # Hash must be unchanged — pollution guard prevented the write
    rows_after = await client.query(
        "SELECT content_hash FROM code_region WHERE file_path = 'pricing.py'"
    )
    hash_after = rows_after[0].get("content_hash", "")
    assert hash_after == hash_before, (
        f"link_commit on branch mutated the baseline hash: "
        f"before={hash_before[:16]} after={hash_after[:16]}. "
        f"Pollution bug 1 regressed."
    )

    reset_ledger_singleton()
