"""Pytest runner for preflight failure-mode dataset (phase 1 — deterministic).

Each row in `preflight_dataset.jsonl` describes a deterministic handler-layer
scenario from `docs/preflight-failure-scenarios.md`. This runner:

- Loads all rows
- Builds a SimpleNamespace ctx with a REAL memory:// SurrealDB adapter
  per row (or per call, for multi-call dedup tests) seeded via
  `tests.eval._ledger_seed`
- Calls `handle_preflight` and asserts the response matches `expect`
- Marks rows with non-null `xfail` as expected failures with strict mode —
  when an underlying fix lands and the test starts passing, strict-xfail
  flips it to a failure so the catalog row gets re-statused

Skill-layer scenarios (M1–M4, FF1, FF3 in the catalog) are deferred to
phase 2 (LLM-in-the-loop) and are not included here.

History — #357 Phase B (this file): the prior version monkeypatched
`ledger.queries.get_ledger_revision` with an AsyncMock. That AsyncMock
made every Phase 4 + Phase 5 test pass against #309's coalesce parse
error — production silently bypassed dedup for the entire window between
merge and #311. With a real adapter in the loop, that class of failure
is no longer expressible: every SurrealQL call in the handler executes
against memory:// for real.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.eval._preflight_eval_seed import (
    apply_setup_to_ledger,
    make_real_ledger,
    reset_for_next_call,
)

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


async def _build_ctx(
    *,
    guided_mode: bool,
    sync_state: dict,
    suffix: str,
) -> tuple[SimpleNamespace, "object", "object"]:
    """Build a SimpleNamespace ctx backed by a real memory:// ledger.

    Returns (ctx, adapter, client) — caller owns the lifecycle. The
    adapter and client references are returned so the test fixture can
    keep them in scope (the SurrealDB connection is per-client).
    """
    adapter, client = await make_real_ledger(suffix)
    ctx = SimpleNamespace(
        ledger=adapter,
        guided_mode=guided_mode,
        _sync_state=sync_state,
    )
    return ctx, adapter, client


def _attach_graph_neighbors(ctx: SimpleNamespace, graph_neighbors: dict) -> None:
    """M6 graph-expansion stub. Not a ledger mock — this is a deterministic
    code-graph injection for the 1-hop expansion path tested by M6. Real
    production code reads from a code-graph index; the test scenarios
    supply a hand-curated topology to make the test deterministic.
    """
    if not graph_neighbors:
        return

    class _DatasetCodeGraph:
        def expand_file_paths_via_graph(
            self, file_paths: list[str], hops: int = 1
        ) -> tuple[list[str], list[str]]:
            expanded: list[str] = []
            added: list[str] = []
            seen: set[str] = set()
            for fp in file_paths or []:
                if fp and fp not in seen:
                    seen.add(fp)
                    expanded.append(fp)
            for fp in file_paths or []:
                for n in graph_neighbors.get(fp, []):
                    if n and n not in seen:
                        seen.add(n)
                        expanded.append(n)
                        added.append(n)
            return expanded, added

    ctx.code_graph = _DatasetCodeGraph()


@pytest.fixture(autouse=True)
def _isolate_handler_environment(monkeypatch, tmp_path):
    """Two narrow seams permitted by CLAUDE.md sociable-testing rules.

    `ensure_ledger_synced` (handlers/sync_middleware.py) auto-runs
    `link_commit` against the working tree on every preflight call. Inside
    the eval harness there is no real git tree to sync against — the
    ledger is a per-test in-memory instance — so the auto-sync would
    either crash or no-op noisily. We seam it off here. CLAUDE.md's
    explicit example of an allowed narrow seam: "patching handle_link_commit
    when testing the *caller's* cache logic (not link_commit itself)."
    Same shape — we're testing preflight, not sync middleware.

    `_should_show_product_stage` is a session-level UX flag; off-by-default
    for tests so the response shape is deterministic.
    """
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


async def _run_row_async(row: dict):
    """Async core for a single dataset row. Returns the response to assert.

    Owns the ledger lifecycle: a single adapter/client persists across
    all calls in a multi-call row so the `bicameral_meta.decision_revision`
    counter advances naturally between calls (the M7a/b/c invariant). The
    `ctx._sync_state` dict also persists so the dedup cache survives across
    calls within a row.
    """
    from handlers.preflight import handle_preflight

    suffix = row["id"].replace(":", "_").replace("-", "_")

    if "calls" in row:
        sync_state: dict = {}
        ctx, adapter, client = await _build_ctx(
            guided_mode=row["calls"][0].get("setup", {}).get("guided_mode", False),
            sync_state=sync_state,
            suffix=suffix,
        )
        try:
            last_response = None
            for i, call in enumerate(row["calls"]):
                setup = call.get("setup", {})
                if i > 0:
                    await reset_for_next_call(client)
                _attach_graph_neighbors(ctx, setup.get("graph_neighbors") or {})
                ctx.guided_mode = setup.get("guided_mode", False)
                await apply_setup_to_ledger(client, setup)
                last_response = await handle_preflight(
                    ctx=ctx,
                    topic=call["input"]["topic"],
                    file_paths=call["input"].get("file_paths"),
                )
            return last_response, row["expect_final"]
        finally:
            await client.close()
    else:
        ctx, adapter, client = await _build_ctx(
            guided_mode=row["setup"].get("guided_mode", False),
            sync_state={},
            suffix=suffix,
        )
        try:
            _attach_graph_neighbors(ctx, row["setup"].get("graph_neighbors") or {})
            await apply_setup_to_ledger(client, row["setup"])
            response = await handle_preflight(
                ctx=ctx,
                topic=row["input"]["topic"],
                file_paths=row["input"].get("file_paths"),
            )
            return response, row["expect"]
        finally:
            await client.close()


@pytest.mark.parametrize("row", _params())
def test_preflight_failure_mode(row):
    response, expect = asyncio.run(_run_row_async(row))
    _assert_expect(response, expect)


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
