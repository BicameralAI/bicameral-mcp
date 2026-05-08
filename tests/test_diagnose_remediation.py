"""Functional tests for #252 Layer 5 — diagnose remediation recipe.

Pins the `_remediation_recipe` helper's wording AND the
`_compute_suggestions` heuristic firings for every drift_status branch
(drift, unavailable, match, first-write) plus the large-ledger branch's
de-stale-ed wording. Closes the gating-is-observability discipline at
the test layer: clean states emit no remediation, drifty states emit
the recipe, and the recipe wording lives at one source of truth.
"""

from __future__ import annotations

from cli._diagnose_gather import _compute_suggestions, _remediation_recipe


def test_remediation_recipe_returns_export_reset_import_one_liner():
    """Pin the helper's return value: must contain the four load-bearing
    substrings (export command, reset command, import command, policy
    pointer). Locks single source of truth for the recipe wording."""
    recipe = _remediation_recipe()
    assert "ledger-export" in recipe
    assert "reset" in recipe
    assert "ledger-import --from-file" in recipe
    assert "docs/policies/ledger-export.md" in recipe


def test_drift_status_drift_emits_export_import_recipe(monkeypatch):
    """drift_status='drift' branch fires AND emits both remediation paths:
    pin-down-the-writer (`pip install --upgrade surrealdb==<rec>`) AND
    the export → reset → import recipe. Verifies the live recipe
    replaced the vague pre-Layer-4 'back up ledger and reset' tail."""
    # Pin recommended-version fetch so the recommended-version branch doesn't fire.
    monkeypatch.setattr("cli._diagnose_gather._fetch_recommended", lambda: None)
    suggestions = _compute_suggestions(
        {
            "drift_status": "drift",
            "surrealdb_last_write": "1.0.0",
            "surrealdb_running": "2.0.0",
            "bicameral_version": "0.13.8",
        }
    )
    drift_lines = [s for s in suggestions if "Schema-revision drift" in s]
    assert len(drift_lines) == 1
    line = drift_lines[0]
    assert "pip install --upgrade surrealdb==1.0.0" in line
    assert "ledger-export" in line
    assert "reset" in line
    assert "ledger-import --from-file" in line


def test_drift_status_unavailable_emits_acquire_sentinel_recipe(monkeypatch):
    """drift_status='unavailable' branch fires when bicameral_version
    resolves. Emits the recipe + 'predates Layer 2' diagnostic context.
    Verifies the new branch fires."""
    monkeypatch.setattr("cli._diagnose_gather._fetch_recommended", lambda: None)
    suggestions = _compute_suggestions(
        {"drift_status": "unavailable", "bicameral_version": "0.13.8"}
    )
    matches = [s for s in suggestions if "predates Layer 2" in s]
    assert len(matches) == 1
    line = matches[0]
    assert "ledger-export" in line
    assert "reset" in line
    assert "ledger-import --from-file" in line


def test_drift_status_unavailable_skips_when_version_unknown(monkeypatch):
    """drift_status='unavailable' branch skips when bicameral_version is
    'unknown'. Avoids noise on installs without resolvable version
    metadata. Locks the heuristic-skip guard."""
    monkeypatch.setattr("cli._diagnose_gather._fetch_recommended", lambda: None)
    suggestions = _compute_suggestions(
        {"drift_status": "unavailable", "bicameral_version": "unknown"}
    )
    assert not any("predates Layer 2" in s for s in suggestions)


def test_drift_status_match_emits_no_remediation(monkeypatch):
    """drift_status='match' (clean state) emits no remediation recipe.
    Locks the gating-is-observability discipline: clean state is silent."""
    monkeypatch.setattr("cli._diagnose_gather._fetch_recommended", lambda: None)
    suggestions = _compute_suggestions(
        {
            "drift_status": "match",
            "audit_log_channel": "/var/log/bicameral.log",
            "ledger_size_bytes": 1024,
            "schema_version_recorded": 16,
            "schema_version_expected": 16,
            "bicameral_version": "0.13.8",
        }
    )
    assert not any("ledger-export" in s for s in suggestions)
    assert not any("ledger-import" in s for s in suggestions)


def test_drift_status_first_write_emits_no_remediation(monkeypatch):
    """drift_status='first-write' (Layer 2 just connected, no second write
    yet) emits no remediation recipe. 'first-write' is normal post-Layer-2
    boot, not drift."""
    monkeypatch.setattr("cli._diagnose_gather._fetch_recommended", lambda: None)
    suggestions = _compute_suggestions(
        {
            "drift_status": "first-write",
            "audit_log_channel": "/var/log/bicameral.log",
            "ledger_size_bytes": 1024,
            "schema_version_recorded": 16,
            "schema_version_expected": 16,
            "bicameral_version": "0.13.8",
        }
    )
    assert not any("ledger-export" in s for s in suggestions)
    assert not any("ledger-import" in s for s in suggestions)


def test_large_ledger_suggestion_emits_recipe_no_layer4_parenthetical(monkeypatch):
    """large-ledger branch emits the live recipe AND drops the stale
    pre-Layer-4 reference ('consider future ... (Layer 4)'). Locks the
    de-stale-ing of the wording."""
    from cli._diagnose_gather import _LARGE_LEDGER_BYTES

    monkeypatch.setattr("cli._diagnose_gather._fetch_recommended", lambda: None)
    suggestions = _compute_suggestions(
        {
            "drift_status": "match",
            "audit_log_channel": "/var/log/bicameral.log",
            "ledger_size_bytes": _LARGE_LEDGER_BYTES + 1,
            "bicameral_version": "0.13.8",
        }
    )
    matches = [s for s in suggestions if "> 100 MiB" in s]
    assert len(matches) == 1
    line = matches[0]
    assert "ledger-export" in line
    assert "(Layer 4)" not in line
    assert "future" not in line


def test_unavailable_branch_does_not_double_fire_with_drift_branch(monkeypatch):
    """A diagnosis with drift_status='drift' should NOT also trigger the
    'unavailable' branch (the two are mutually exclusive enum values).
    Locks the branch discrimination."""
    monkeypatch.setattr("cli._diagnose_gather._fetch_recommended", lambda: None)
    suggestions = _compute_suggestions(
        {
            "drift_status": "drift",
            "surrealdb_last_write": "1.0.0",
            "surrealdb_running": "2.0.0",
            "bicameral_version": "0.13.8",
        }
    )
    assert not any("predates Layer 2" in s for s in suggestions)
