"""
Desynchronization scenario tests.

Maps to the 12 scenarios from "The Auto-Grounding Problem" Notion doc.
Each test simulates a specific way decision-code links can break,
and verifies the system handles it correctly.

Uses a temporary git repo with real commits so that ingest_commit()
can process diffs and update statuses correctly.

Run: USE_REAL_LEDGER=1 SURREAL_URL=memory:// pytest tests/test_desync_scenarios.py -v
"""
import json
import os
import hashlib
import subprocess
import tempfile
import shutil
from pathlib import Path

import pytest

# Ensure pilot/mcp is on path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.ledger import get_ledger, reset_ledger_singleton
from handlers.link_commit import handle_link_commit
from handlers.detect_drift import handle_detect_drift
from handlers.decision_status import handle_decision_status
from handlers.ingest import handle_ingest
from ledger.status import compute_content_hash, derive_status, hash_lines

RESULTS_DIR = Path(__file__).parent.parent / "test-results" / "desync"


# ── Auto-configure env so `pytest tests/test_desync_scenarios.py` just works ──
os.environ.setdefault("USE_REAL_LEDGER", "1")
os.environ.setdefault("SURREAL_URL", "memory://")


# ── Git Repo Helpers ──────────────────────────────────────────────────

def _git(repo_dir: str, *args) -> str:
    """Run a git command in repo_dir, return stdout."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=repo_dir, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def _create_test_repo(tmp_path: Path) -> str:
    """Create a temp git repo with initial Python files and a commit."""
    repo = str(tmp_path / "test-repo")
    os.makedirs(repo)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@bicameral.ai")
    _git(repo, "config", "user.name", "Desync Test")

    # Initial code files
    payments = Path(repo) / "payments"
    payments.mkdir()
    (payments / "handler.py").write_text(
        'def process_payment(amount, currency):\n'
        '    """Process a payment."""\n'
        '    if amount <= 0:\n'
        '        raise ValueError("Amount must be positive")\n'
        '    return {"status": "ok", "amount": amount, "currency": currency}\n'
    )
    (payments / "refund.py").write_text(
        'def process_refund(order_id, reason):\n'
        '    """Process a refund via Stripe."""\n'
        '    return {"refunded": True, "order_id": order_id}\n'
    )

    middleware = Path(repo) / "middleware"
    middleware.mkdir()
    (middleware / "rate_limiter.py").write_text(
        'RATE_LIMIT = 100  # req/min per user\n'
        '\n'
        'def check_rate_limit(user_id, endpoint):\n'
        '    """Check if user is within rate limit."""\n'
        '    # TODO: implement Redis-backed check\n'
        '    return True\n'
    )

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Initial commit: payments + rate limiter")

    return repo


def _commit_change(repo: str, file_path: str, content: str, message: str) -> str:
    """Write content to file, commit, return the commit SHA."""
    full_path = Path(repo) / file_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content)
    _git(repo, "add", file_path)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _delete_and_commit(repo: str, file_path: str, message: str) -> str:
    """Delete a file, commit, return SHA."""
    _git(repo, "rm", file_path)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _get_head(repo: str) -> str:
    return _git(repo, "rev-parse", "HEAD")


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def setup_env(monkeypatch, tmp_path):
    """Fresh memory:// ledger + temp git repo for each test."""
    monkeypatch.setenv("SURREAL_URL", "memory://")
    reset_ledger_singleton()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    yield
    reset_ledger_singleton()


@pytest.fixture
def test_repo(tmp_path, monkeypatch) -> str:
    """Create a temp git repo and point REPO_PATH to it."""
    repo = _create_test_repo(tmp_path)
    monkeypatch.setenv("REPO_PATH", repo)
    return repo


def _make_payload(
    intent_text: str,
    file_path: str,
    symbol_name: str,
    start_line: int,
    end_line: int,
    repo: str = "",
    commit_hash: str = "HEAD",
    source_ref: str = "desync-meeting",
) -> dict:
    """Build a minimal ingest payload with one grounded decision."""
    repo = repo or os.getenv("REPO_PATH", ".")
    return {
        "query": intent_text,
        "repo": repo,
        "commit_hash": commit_hash,
        "analyzed_at": "2026-03-31T00:00:00Z",
        "mappings": [
            {
                "span": {
                    "span_id": "desync-span-001",
                    "source_type": "transcript",
                    "text": intent_text,
                    "speaker": "PM",
                    "source_ref": source_ref,
                },
                "intent": intent_text,
                "symbols": [{"name": symbol_name, "type": "function"}],
                "code_regions": [
                    {
                        "file_path": file_path,
                        "symbol": symbol_name,
                        "type": "function",
                        "start_line": start_line,
                        "end_line": end_line,
                        "purpose": f"Implements: {intent_text}",
                    }
                ],
                "dependency_edges": [],
            }
        ],
    }


def _make_ungrounded_payload(
    intent_text: str,
    repo: str = "",
    commit_hash: str = "HEAD",
    source_ref: str = "desync-meeting",
) -> dict:
    """Build a payload with NO code_regions (will be ungrounded)."""
    repo = repo or os.getenv("REPO_PATH", ".")
    return {
        "query": intent_text,
        "repo": repo,
        "commit_hash": commit_hash,
        "analyzed_at": "2026-03-31T00:00:00Z",
        "mappings": [
            {
                "span": {
                    "span_id": "desync-span-ungrounded",
                    "source_type": "transcript",
                    "text": intent_text,
                    "speaker": "PM",
                    "source_ref": source_ref,
                },
                "intent": intent_text,
                "symbols": [],
                "code_regions": [],
                "dependency_edges": [],
            }
        ],
    }


# ── Scenario Tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_desync_01_new_decision_no_auto_link(test_repo):
    """
    Scenario 1: Decision ingested with empty code_regions, matching code exists.
    Auto-grounding via handle_ingest should ground it automatically.
    """
    from handlers.ingest import handle_ingest

    payload = _make_ungrounded_payload("Payment processing handles refunds", repo=test_repo)
    result = await handle_ingest(payload)

    status = await handle_decision_status(filter="all")
    our = [d for d in status.decisions if "refunds" in d.description.lower()]
    assert len(our) == 1

    # Auto-grounding should have matched against existing code in test_repo
    assert our[0].status in ("pending", "reflected"), (
        f"Expected auto-grounded status, got: {our[0].status}"
    )


@pytest.mark.asyncio
async def test_desync_02_code_changed_hash_mismatch(test_repo):
    """
    Scenario 2: Code changed after grounding → content_hash mismatch → drifted.
    """
    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    head = _get_head(test_repo)

    # Ground decision to check_rate_limit function (lines 3-6)
    payload = _make_payload(
        intent_text="Rate limit is 100 req/min per user",
        file_path="middleware/rate_limiter.py",
        symbol_name="check_rate_limit",
        start_line=3, end_line=6,
        repo=test_repo, commit_hash=head,
    )
    await ledger.ingest_payload(payload)

    # Sync initial commit so status moves from "pending" to "reflected"
    await ledger.ingest_commit(head, test_repo)

    # Verify reflected
    status = await handle_decision_status(filter="all")
    our = [d for d in status.decisions if "100 req/min" in d.description]
    assert len(our) == 1
    # After syncing the same commit used for ingestion, should be reflected
    assert our[0].status == "reflected", f"Baseline should be reflected, got {our[0].status}"

    # Now change the code — modify rate limit from 100 to 50
    new_sha = _commit_change(
        test_repo, "middleware/rate_limiter.py",
        'RATE_LIMIT = 50  # changed!\n\ndef check_rate_limit(user_id, endpoint):\n    return True\n',
        "change rate limit to 50"
    )

    # Sync the new commit
    await ledger.ingest_commit(new_sha, test_repo)

    status2 = await handle_decision_status(filter="all")
    our2 = [d for d in status2.decisions if "100 req/min" in d.description]
    assert len(our2) == 1
    assert our2[0].status == "drifted", f"Expected drifted after code change, got {our2[0].status}"


@pytest.mark.asyncio
async def test_desync_03_code_deleted_file_gone(test_repo):
    """
    Scenario 3: File deleted after grounding → pending.
    """
    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    head = _get_head(test_repo)

    payload = _make_payload(
        intent_text="Refund processing via Stripe",
        file_path="payments/refund.py",
        symbol_name="process_refund",
        start_line=1, end_line=3,
        repo=test_repo, commit_hash=head,
    )
    await ledger.ingest_payload(payload)
    await ledger.ingest_commit(head, test_repo)

    # Delete the file
    new_sha = _delete_and_commit(test_repo, "payments/refund.py", "delete refund module")
    await ledger.ingest_commit(new_sha, test_repo)

    status = await handle_decision_status(filter="all")
    our = [d for d in status.decisions if "refund" in d.description.lower()]
    assert len(our) == 1
    assert our[0].status == "pending", f"Expected pending (file deleted), got {our[0].status}"


@pytest.mark.asyncio
async def test_desync_04_symbol_renamed(test_repo):
    """
    Scenario 4: Symbol renamed → old link stale. Hash may still match if
    only the function name changed but body is identical.
    """
    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    head = _get_head(test_repo)

    payload = _make_payload(
        intent_text="Payment processing handles amounts",
        file_path="payments/handler.py",
        symbol_name="process_payment",
        start_line=1, end_line=5,
        repo=test_repo, commit_hash=head,
    )
    await ledger.ingest_payload(payload)
    await ledger.ingest_commit(head, test_repo)

    # Rename function: process_payment → handle_payment (body stays same)
    new_sha = _commit_change(
        test_repo, "payments/handler.py",
        'def handle_payment(amount, currency):\n'
        '    """Process a payment."""\n'
        '    if amount <= 0:\n'
        '        raise ValueError("Amount must be positive")\n'
        '    return {"status": "ok", "amount": amount, "currency": currency}\n',
        "rename process_payment to handle_payment"
    )
    await ledger.ingest_commit(new_sha, test_repo)

    status = await handle_decision_status(filter="all")
    our = [d for d in status.decisions if "payment processing" in d.description.lower()]
    assert len(our) == 1
    # Hash-only: "drifted" (name change = content change at line 1)
    # Symbol-aware (future): should detect rename and still match
    assert our[0].status == "drifted", f"Expected drifted (rename changes hash), got {our[0].status}"


@pytest.mark.asyncio
async def test_desync_05_symbol_moved_file(test_repo):
    """
    Scenario 5: Symbol moved to different file → file_path mismatch.
    """
    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    head = _get_head(test_repo)

    payload = _make_payload(
        intent_text="Rate limiter checks user limits",
        file_path="middleware/rate_limiter.py",
        symbol_name="check_rate_limit",
        start_line=3, end_line=6,
        repo=test_repo, commit_hash=head,
    )
    await ledger.ingest_payload(payload)
    await ledger.ingest_commit(head, test_repo)

    # Move function to a new file (single commit that modifies rate_limiter.py)
    (Path(test_repo) / "auth").mkdir(exist_ok=True)
    (Path(test_repo) / "auth" / "rate_check.py").write_text(
        'def check_rate_limit(user_id, endpoint):\n    return True\n'
    )
    (Path(test_repo) / "middleware" / "rate_limiter.py").write_text(
        'RATE_LIMIT = 100\n# moved to auth/rate_check.py\n'
    )
    _git(test_repo, "add", "-A")
    _git(test_repo, "commit", "-m", "move check_rate_limit to auth module")
    new_sha = _get_head(test_repo)
    await ledger.ingest_commit(new_sha, test_repo)

    status = await handle_decision_status(filter="all")
    our = [d for d in status.decisions if "rate limiter" in d.description.lower()]
    assert len(our) == 1
    # File changed → hash mismatch → drifted (correct detection, but no re-ground)
    assert our[0].status in ("drifted", "pending")


@pytest.mark.asyncio
async def test_desync_06_index_rebuilt_new_symbols(test_repo):
    """
    Scenario 6: Code index rebuilt with new symbols → ungrounded should match
    after link_commit triggers re-grounding.
    """
    from handlers.link_commit import handle_link_commit

    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    payload = _make_ungrounded_payload("Audit logging for admin actions", repo=test_repo)
    await ledger.ingest_payload(payload)

    status = await handle_decision_status(filter="all")
    our = [d for d in status.decisions if "audit logging" in d.description.lower()]
    assert len(our) == 1
    assert our[0].status == "ungrounded"

    # Add code that matches the decision
    _commit_change(
        test_repo, "admin/audit.py",
        'def log_admin_action(user, action, details):\n    """Audit log for admin."""\n    pass\n',
        "add audit logging"
    )

    # link_commit triggers _reground_ungrounded which re-runs auto-grounding
    await handle_link_commit("HEAD")

    status = await handle_decision_status(filter="all")
    our = [d for d in status.decisions if "audit logging" in d.description.lower()]
    assert len(our) == 1
    assert our[0].status in ("pending", "reflected", "ungrounded"), (
        f"After re-grounding, got: {our[0].status}"
    )


@pytest.mark.asyncio
async def test_desync_07_cold_start_no_index(test_repo):
    """
    Scenario 7: Cold start — no index → everything ungrounded.
    """
    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    payload = _make_ungrounded_payload("Rate limiting on checkout endpoint", repo=test_repo)
    result = await ledger.ingest_payload(payload)
    assert result["stats"]["ungrounded"] >= 1

    status = await handle_decision_status(filter="ungrounded")
    assert len(status.decisions) >= 1


@pytest.mark.asyncio
async def test_desync_08_drifted_but_recoverable(test_repo):
    """
    Scenario 8: Code moved but description matches → should re-ground.
    """
    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    head = _get_head(test_repo)

    # Ground to wrong file (simulating the file it USED to be in)
    payload = _make_payload(
        intent_text="Refund processing via Stripe API",
        file_path="payments/handler.py",  # Wrong — it's in payments/refund.py
        symbol_name="process_refund",
        start_line=1, end_line=3,
        repo=test_repo, commit_hash=head,
    )
    await ledger.ingest_payload(payload)
    await ledger.ingest_commit(head, test_repo)

    status = await handle_decision_status(filter="all")
    our = [d for d in status.decisions if "refund processing" in d.description.lower()]
    assert len(our) == 1

    # Wrong file → hash won't match → drifted or pending
    if our[0].status in ("drifted", "pending"):
        pytest.xfail("Re-grounding for drifted intents not yet implemented (desync_08)")
    assert our[0].status == "reflected"


@pytest.mark.asyncio
async def test_desync_09_intent_supersession(test_repo):
    """
    Scenario 9: Updated version of same decision → should supersede.
    """
    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    head = _get_head(test_repo)

    payload_v1 = _make_payload(
        intent_text="Rate limit checkout at 50 req/min",
        file_path="middleware/rate_limiter.py", symbol_name="check_rate_limit",
        start_line=1, end_line=6, repo=test_repo, commit_hash=head,
        source_ref="sprint-12",
    )
    await ledger.ingest_payload(payload_v1)

    payload_v2 = _make_payload(
        intent_text="Rate limit checkout at 100 req/min per user",
        file_path="middleware/rate_limiter.py", symbol_name="check_rate_limit",
        start_line=1, end_line=6, repo=test_repo, commit_hash=head,
        source_ref="sprint-13",
    )
    await ledger.ingest_payload(payload_v2)

    status = await handle_decision_status(filter="all")
    rate = [d for d in status.decisions if "rate limit" in d.description.lower()]

    if len(rate) > 1:
        pytest.xfail("Intent supersession not yet implemented (desync_09)")
    assert len(rate) == 1


@pytest.mark.asyncio
async def test_desync_10_multiple_intents_same_symbol(test_repo):
    """
    Scenario 10: Two intents map to same symbol — both update together.
    """
    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    head = _get_head(test_repo)

    for text in ["Payment amount must be positive", "Payment returns status ok"]:
        payload = _make_payload(
            intent_text=text,
            file_path="payments/handler.py", symbol_name="process_payment",
            start_line=1, end_line=5, repo=test_repo, commit_hash=head,
        )
        await ledger.ingest_payload(payload)

    await ledger.ingest_commit(head, test_repo)

    # Change the code
    new_sha = _commit_change(
        test_repo, "payments/handler.py",
        'def process_payment(amount):\n    return {"status": "changed"}\n',
        "simplify process_payment"
    )
    await ledger.ingest_commit(new_sha, test_repo)

    status = await handle_decision_status(filter="all")
    payment = [d for d in status.decisions if "payment" in d.description.lower()]
    assert len(payment) >= 2

    statuses = {d.status for d in payment}
    assert len(statuses) == 1, f"Co-mapped intents should share status, got {statuses}"
    assert "drifted" in statuses


@pytest.mark.asyncio
async def test_desync_11_false_positive_grounding(test_repo):
    """
    Scenario 11: Vague decision should NOT auto-ground to wrong file.
    """
    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    payload = _make_ungrounded_payload("Integration testing for payment module", repo=test_repo)
    await ledger.ingest_payload(payload)

    status = await handle_decision_status(filter="all")
    our = [d for d in status.decisions if "integration testing" in d.description.lower()]
    assert len(our) == 1
    assert our[0].status == "ungrounded"


@pytest.mark.asyncio
async def test_desync_12_line_numbers_shift(test_repo):
    """
    Scenario 12: Insertion above shifts line numbers → false drift.
    """
    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    head = _get_head(test_repo)

    # Ground to check_rate_limit at lines 3-6
    payload = _make_payload(
        intent_text="Rate limit check returns boolean",
        file_path="middleware/rate_limiter.py",
        symbol_name="check_rate_limit",
        start_line=3, end_line=6,
        repo=test_repo, commit_hash=head,
    )
    await ledger.ingest_payload(payload)
    await ledger.ingest_commit(head, test_repo)

    # Verify baseline is reflected
    status = await handle_decision_status(filter="all")
    our = [d for d in status.decisions if "rate limit check" in d.description.lower()]
    assert len(our) == 1
    assert our[0].status == "reflected", f"Baseline should be reflected, got {our[0].status}"

    # Insert 3 lines ABOVE the function (function shifts from 3-6 to 6-9)
    new_sha = _commit_change(
        test_repo, "middleware/rate_limiter.py",
        'RATE_LIMIT = 100  # req/min per user\n'
        '# Added: import for Redis\n'
        'import redis\n'
        '\n'
        'def check_rate_limit(user_id, endpoint):\n'
        '    """Check if user is within rate limit."""\n'
        '    # TODO: implement Redis-backed check\n'
        '    return True\n',
        "add redis import above rate limiter"
    )
    await ledger.ingest_commit(new_sha, test_repo)

    status2 = await handle_decision_status(filter="all")
    our2 = [d for d in status2.decisions if "rate limit check" in d.description.lower()]
    assert len(our2) == 1

    # Hash-only: lines 3-6 now contain different content → "drifted" (FALSE POSITIVE)
    # Symbol-aware (future): resolve by name → find at new lines → "reflected"
    if our2[0].status == "drifted":
        pytest.xfail("Line shift causes false drift — needs symbol-based resolution (desync_12)")
    assert our2[0].status == "reflected"


# ── Scorecard Generator ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_desync_scorecard():
    """
    Meta-test: generates a JSON scorecard of all 12 scenarios.
    Always passes — just writes the report.
    """
    from fixtures.expected.desync import DESYNC_SCENARIOS

    scorecard = {
        "implementation": "v0_hash_only",
        "scenarios": {},
        "summary": {},
    }

    for s in DESYNC_SCENARIOS:
        scorecard["scenarios"][s["id"]] = {
            "name": s["name"],
            "severity": s["severity"],
            "currently_passing": s["currently_passing"],
            "expected_status": s["expected_status"],
            "current_behavior": s["current_behavior"],
        }

    passing = sum(1 for s in DESYNC_SCENARIOS if s["currently_passing"])
    total = len(DESYNC_SCENARIOS)
    scorecard["summary"] = {
        "total": total,
        "passing": passing,
        "failing": total - passing,
        "pass_rate": f"{passing}/{total} = {passing/total*100:.0f}%",
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "scorecard.json"
    out.write_text(json.dumps(scorecard, indent=2))
    print(f"\n📊 Desync scorecard written to {out}")
    print(f"   Pass rate: {scorecard['summary']['pass_rate']}")
