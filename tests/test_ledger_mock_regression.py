"""Regression-counter test for #357 sub-task 1 (Phase C).

Guards against new "solitary trap" rows in `ledger/queries.py` — that is,
new SurrealQL-bearing functions whose only direct tests are `AsyncMock`
or `MagicMock` against the function instead of running it against a real
memory:// SurrealDB. This is the #309-class risk: a mocked test makes
the SurrealQL look exercised when it isn't, and parse errors or contract
drifts ship to production undetected.

The test caps the trap count at the post-Phase-B baseline. If a PR adds
a new trap (or accidentally re-mocks something that's currently exercised
sociably), this fails CI with a list of the newly-added trap function
names.

When a backfill PR converts a trap to a sociable test, **decrement
EXPECTED_TRAP_CAP**. The cap is a one-way ratchet by convention — going
up should never happen, and going down is the goal.

See `docs/ledger-sociable-test-audit.md` for the full coverage breakdown
and `scripts/audit_sociable_coverage.py` for the audit logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audit_sociable_coverage import compute_audit  # noqa: E402

# Baseline trap count immediately after #357 Phase B landed. Decrement
# when a backfill PR fixes a trap row. NEVER increment. If you find
# yourself wanting to increment, you are about to ship #309-class risk —
# write a sociable test instead.
EXPECTED_TRAP_CAP = 4


def test_no_new_ledger_query_mock_traps():
    audit = compute_audit()
    trap_count = audit["trap_count"]
    trap_names = sorted(r["name"] for r in audit["traps"])

    assert trap_count <= EXPECTED_TRAP_CAP, (
        f"NEW SOLITARY-TRAP REGRESSION in `ledger/queries.py`.\n\n"
        f"Current trap count: {trap_count} (cap: {EXPECTED_TRAP_CAP})\n"
        f"Current trap functions ({trap_count}):\n"
        + "\n".join(f"  - {n}" for n in trap_names)
        + "\n\n"
        "A 'solitary trap' is a SurrealQL function whose only direct "
        "tests use AsyncMock/MagicMock — the #309-class risk this gate "
        "prevents. Run `python3 scripts/audit_sociable_coverage.py` to "
        "see the full report.\n\n"
        "Fix one of these ways:\n"
        "  1. Backfill a sociable test against memory:// SurrealDB. "
        "     Pattern: tests/test_codegenome_continuity_service.py::_fresh_adapter.\n"
        "  2. If you genuinely added a new function that needs a narrow-seam "
        "     mock per CLAUDE.md, document the seam in the test docstring."
    )


def test_trap_count_constant_matches_audit():
    """If the cap is now strictly greater than the actual trap count, a
    backfill PR has reduced traps but didn't decrement the cap. Decrement
    EXPECTED_TRAP_CAP so the gate keeps ratcheting down.
    """
    audit = compute_audit()
    if audit["trap_count"] < EXPECTED_TRAP_CAP:
        pytest.fail(
            f"Trap count dropped to {audit['trap_count']} but "
            f"EXPECTED_TRAP_CAP is still {EXPECTED_TRAP_CAP}. Decrement "
            f"the constant in tests/test_ledger_mock_regression.py to "
            f"lock in the improvement."
        )


def test_audit_runs_clean():
    """Sanity: the audit script itself doesn't crash and returns the
    expected shape. If a future refactor breaks compute_audit(), the
    regression tests above silently start passing — this catches that.
    """
    audit = compute_audit()
    required_keys = {
        "rows",
        "sql_rows",
        "direct",
        "traps",
        "indirect",
        "uncovered",
        "direct_count",
        "trap_count",
        "indirect_count",
        "uncovered_count",
    }
    missing = required_keys - audit.keys()
    assert not missing, f"compute_audit() missing keys: {missing}"
    assert audit["direct_count"] + audit["trap_count"] + audit["indirect_count"] + audit[
        "uncovered_count"
    ] == len(audit["sql_rows"]), "compute_audit() row categorization is non-partitioning"
