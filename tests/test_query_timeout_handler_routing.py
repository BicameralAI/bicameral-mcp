"""#224 handler-routing tests — pin which call sites take the drift
budget vs. the default read budget.

Static-grep shape against handler source rather than behavioral
spin-up: the cost of forcing a real slow query to verify class
selection is high relative to the static-guarantee value. This file
catches the regression class "annotation was removed during a
refactor" cheaply.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HANDLERS_DIR = _REPO_ROOT / "handlers"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Drift-class sites (declared in plan-224 Phase B) ─────────────────


def test_history_enriched_fetch_uses_drift_class() -> None:
    """`_fetch_all_decisions_enriched` runs a single graph-traversal
    query across every decision row — the only handler-level call
    site declared as drift-class in Phase B."""
    src = _read(_HANDLERS_DIR / "history.py")
    # The annotation must appear after the enriched SELECT body.
    enriched_idx = src.find("<-yields<-input_span")
    assert enriched_idx >= 0, "enriched SELECT missing — refactor?"
    # The drift-class annotation appears somewhere after this point
    # and before the next def.
    next_def = src.find("\nasync def ", enriched_idx)
    if next_def < 0:
        next_def = len(src)
    enriched_block = src[enriched_idx:next_def]
    assert 'timeout_class="drift"' in enriched_block, (
        'history.py enriched-fetch call no longer carries timeout_class="drift" — #224 regression'
    )


# ── Read-class sites (default; must NOT carry drift annotation) ─────


_READ_CLASS_HANDLERS = [
    "ratify.py",
    "remove_decision.py",
    "remove_source.py",
    "reset.py",
    "resolve_collision.py",
    "usage_summary.py",
    "decision_status.py",
    "search_decisions.py",
    "ingest.py",
]


@pytest.mark.parametrize("handler_name", _READ_CLASS_HANDLERS)
def test_read_class_handlers_do_not_carry_drift_annotation(handler_name: str) -> None:
    """Phase B keeps read-class handlers on the 5s default. If any of
    them sprout a `timeout_class="drift"` annotation, the plan needs
    to be revisited (either narrow scope, or update this test)."""
    path = _HANDLERS_DIR / handler_name
    if not path.exists():
        pytest.skip(f"handler {handler_name} not present in this checkout")
    src = _read(path)
    assert 'timeout_class="drift"' not in src, (
        f"{handler_name} acquired a drift annotation outside the "
        "Phase B scope — update plan-224 and this test if intentional"
    )


# ── Audit-tier guard: only the declared site uses drift across all handlers ─


def test_exactly_one_handler_call_site_uses_drift_class() -> None:
    """Sanity check: the entire `handlers/` directory should carry
    exactly one `timeout_class="drift"` annotation today (history's
    enriched fetch). Drives reviewers to update plan-224 + this
    test together when adding new drift sites."""
    drift_uses = []
    for path in _HANDLERS_DIR.glob("*.py"):
        for i, line in enumerate(_read(path).splitlines(), start=1):
            if 'timeout_class="drift"' in line:
                drift_uses.append((path.name, i))
    assert len(drift_uses) == 1, f"expected exactly one handler drift annotation, got {drift_uses}"
    assert drift_uses[0][0] == "history.py", drift_uses


# ── governance-gates.yaml: #224 entry present ─────────────────────────


def test_governance_gates_contains_query_timeout_entry() -> None:
    """Per Discipline #2 of plan-224, the deterministic gate must be
    declared in `governance-gates.yaml` so the skill-governance lint
    sees the backing-gate link."""
    gates_yaml = _REPO_ROOT / "governance-gates.yaml"
    src = _read(gates_yaml)
    assert "ledger/client.py::LedgerClient._run_with_timeout::asyncio.wait_for" in src, (
        "#224 gate entry missing from governance-gates.yaml"
    )
    # And the instruction pattern is present so the lint can match.
    assert "queries time out" in src


# ── Module-level signature pin: timeout_class is a typed kwarg ────────


def test_ledger_client_query_exposes_timeout_class_kwarg() -> None:
    """If the kwarg name changes, all handler annotations break
    silently. Pin the signature shape here so a rename caught
    during refactor surfaces as a single failing test."""
    src = _read(_REPO_ROOT / "ledger" / "client.py")
    # The Literal type must be present in the signature.
    pattern = re.compile(
        r'async def query\(.*?timeout_class:\s*Literal\["read",\s*"drift"\]',
        re.DOTALL,
    )
    assert pattern.search(src), (
        'LedgerClient.query signature no longer exposes timeout_class: Literal["read", "drift"]'
    )
    pattern_exec = re.compile(
        r'async def execute\(.*?timeout_class:\s*Literal\["read",\s*"drift"\]',
        re.DOTALL,
    )
    assert pattern_exec.search(src), "LedgerClient.execute signature lost its timeout_class kwarg"
