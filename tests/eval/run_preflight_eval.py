"""Pytest runner for preflight failure-mode dataset (phase 1 — deterministic).

Each row in `preflight_dataset.jsonl` describes a deterministic handler-layer
scenario from `docs/preflight-failure-scenarios.md`. This runner:

- Loads all rows
- Builds a mocked context per row (or per call, for multi-call dedup tests)
- Calls `handle_preflight` and asserts the response matches `expect`
- Marks rows with non-null `xfail` as expected failures with strict mode —
  when an underlying fix lands and the test starts passing, strict-xfail
  flips it to a failure so the catalog row gets re-statused

Skill-layer scenarios (M1–M4, FF1, FF3 in the catalog) are deferred to
phase 2 (LLM-in-the-loop) and are not included here.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


DATASET = Path(__file__).parent / "preflight_dataset.jsonl"
CATALOG = Path(__file__).parent.parent.parent / "docs" / "preflight-failure-scenarios.md"

REQUIRED_KEYS = {"id", "layer", "axis", "catalog_status", "title"}
ALLOWED_AXES = {"miss", "false_fire", "correct"}
ALLOWED_LAYERS = {"handler", "skill", "meta"}


def _load_rows() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]


def _validate_row(row: dict) -> None:
    missing = REQUIRED_KEYS - row.keys()
    if missing:
        raise AssertionError(f"row {row.get('id')!r} missing keys: {missing}")
    if row["axis"] not in ALLOWED_AXES:
        raise AssertionError(f"row {row['id']}: axis {row['axis']!r} not in {ALLOWED_AXES}")
    if row["layer"] not in ALLOWED_LAYERS:
        raise AssertionError(f"row {row['id']}: layer {row['layer']!r} not in {ALLOWED_LAYERS}")
    if "calls" in row:
        if "expect_final" not in row:
            raise AssertionError(f"row {row['id']}: multi-call rows must define expect_final")
    else:
        if "input" not in row or "expect" not in row:
            raise AssertionError(f"row {row['id']}: single-call rows must define input and expect")


def _make_decision_dict(d: dict) -> dict:
    """Format expected by `ledger.get_decisions_for_files`."""
    return {
        "decision_id": d["decision_id"],
        "description": d["description"],
        "status": d.get("status", "reflected"),
        "source_type": "transcript",
        "source_ref": "test",
        "source_excerpt": "",
        "meeting_date": "",
        "ingested_at": "2026-04-27",
        "signoff": d.get("signoff"),
        "code_region": {
            "file_path": d.get("file_path", "test.py"),
            "symbol": d.get("symbol", "test_symbol"),
            "lines": (1, 10),
            "purpose": d["description"],
            "content_hash": "test",
        },
    }


def _make_hitl_row(d: dict) -> dict:
    """Format expected by `get_collision_pending_decisions` /
    `get_context_for_ready_decisions`."""
    return {
        "decision_id": d["decision_id"],
        "description": d["description"],
        "status": d.get("status", "pending"),
        "signoff": d.get("signoff", {}),
    }


def _make_ctx(*, guided_mode: bool, sync_state: dict) -> SimpleNamespace:
    ledger = MagicMock()
    ledger.get_decisions_for_files = AsyncMock(return_value=[])
    inner = MagicMock()
    inner._client = MagicMock()
    ledger._inner = inner
    return SimpleNamespace(
        ledger=ledger,
        guided_mode=guided_mode,
        _sync_state=sync_state,
    )


def _apply_setup(monkeypatch, setup: dict, ctx: SimpleNamespace) -> None:
    region_decisions = setup.get("region_decisions") or []
    ctx.ledger.get_decisions_for_files = AsyncMock(
        return_value=[_make_decision_dict(d) for d in region_decisions]
    )

    import ledger.queries as lq
    monkeypatch.setattr(
        lq,
        "get_collision_pending_decisions",
        AsyncMock(return_value=[_make_hitl_row(d) for d in setup.get("collision_pending", [])]),
    )
    monkeypatch.setattr(
        lq,
        "get_context_for_ready_decisions",
        AsyncMock(return_value=[_make_hitl_row(d) for d in setup.get("context_pending_ready", [])]),
    )


@pytest.fixture(autouse=True)
def _isolate_handler_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_MUTE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    import handlers.sync_middleware as sm
    monkeypatch.setattr(sm, "ensure_ledger_synced", AsyncMock(return_value=None))
    import handlers.preflight as pf
    monkeypatch.setattr(pf, "_should_show_product_stage", lambda: False)


def _assert_expect(response, expect: dict) -> None:
    assert response.fired == expect["fired"], (
        f"fired: expected {expect['fired']}, got {response.fired} (reason={response.reason})"
    )
    if "reason" in expect:
        assert response.reason == expect["reason"], (
            f"reason: expected {expect['reason']!r}, got {response.reason!r}"
        )
    if "decisions_count" in expect:
        actual = len(response.decisions or [])
        assert actual == expect["decisions_count"], (
            f"decisions_count: expected {expect['decisions_count']}, got {actual}"
        )
    if "collision_pending_count" in expect:
        actual = len(response.unresolved_collisions or [])
        assert actual == expect["collision_pending_count"], (
            f"collision_pending_count: expected {expect['collision_pending_count']}, got {actual}"
        )
    if "context_pending_ready_count" in expect:
        actual = len(response.context_pending_ready or [])
        assert actual == expect["context_pending_ready_count"], (
            f"context_pending_ready_count: expected {expect['context_pending_ready_count']}, got {actual}"
        )


def _params() -> list:
    rows = _load_rows()
    out = []
    for row in rows:
        _validate_row(row)
        marks = []
        if row.get("xfail"):
            marks.append(pytest.mark.xfail(reason=row["xfail"], strict=True))
        out.append(pytest.param(row, id=row["id"], marks=marks))
    return out


@pytest.mark.parametrize("row", _params())
def test_preflight_failure_mode(row, monkeypatch):
    from handlers.preflight import handle_preflight

    if "calls" in row:
        sync_state: dict = {}
        last_response = None
        for call in row["calls"]:
            ctx = _make_ctx(
                guided_mode=call.get("setup", {}).get("guided_mode", False),
                sync_state=sync_state,
            )
            _apply_setup(monkeypatch, call.get("setup", {}), ctx)
            last_response = asyncio.run(
                handle_preflight(
                    ctx=ctx,
                    topic=call["input"]["topic"],
                    file_paths=call["input"].get("file_paths"),
                )
            )
        _assert_expect(last_response, row["expect_final"])
    else:
        ctx = _make_ctx(
            guided_mode=row["setup"].get("guided_mode", False),
            sync_state={},
        )
        _apply_setup(monkeypatch, row["setup"], ctx)
        response = asyncio.run(
            handle_preflight(
                ctx=ctx,
                topic=row["input"]["topic"],
                file_paths=row["input"].get("file_paths"),
            )
        )
        _assert_expect(response, row["expect"])


def test_dataset_schema_valid():
    """Each row in the dataset has the required shape."""
    for row in _load_rows():
        _validate_row(row)


def test_catalog_dataset_consistency():
    """Every catalog row that is testable in phase 1 (handler/meta layer
    rows whose status is open/acknowledged/intentional) has a dataset
    entry whose `id` starts with the catalog ID. Skill-layer rows are
    expected to be absent (phase 2)."""
    if not CATALOG.exists():
        pytest.skip("catalog file not present in this checkout")

    catalog_text = CATALOG.read_text()
    table_id_pattern = re.compile(r"\|\s*\*\*([MF]+\d+)\*\*\s*\|\s*(handler|skill|meta)\s*\|", re.M)
    catalog_rows = {m.group(1): m.group(2) for m in table_id_pattern.finditer(catalog_text)}
    handler_meta_ids = {cid for cid, layer in catalog_rows.items() if layer in {"handler", "meta"}}

    deferred_meta_ids = {"M8", "M9"}
    expected_phase1_ids = handler_meta_ids - deferred_meta_ids

    dataset_ids = {row["id"] for row in _load_rows()}
    dataset_id_prefixes = {re.split(r"[_a-z]", rid)[0] for rid in dataset_ids}

    missing = expected_phase1_ids - dataset_id_prefixes
    assert not missing, (
        f"catalog ↔ dataset drift: catalog has handler/meta rows {sorted(missing)} "
        f"with no dataset coverage. Add them to preflight_dataset.jsonl or mark "
        f"them deferred in this consistency check."
    )
