"""Pytest runner for preflight §C cost/latency baseline (issue #88).

Three deterministic metrics with committed baselines and an asymmetric
regression rule (only flags regressions, not improvements). C4 (LLM-in-
the-loop end-to-end) is deferred.

| Metric | What | Scope |
|---|---|---|
| **C1** | ``bicameral.history()`` payload tokens at N = 10, 100, 1000 features | synthetic ledger, JSON-serialized |
| **C2** | ``bicameral.preflight()`` response size (region-anchored + HITL) | mocked ledger, representative shape |
| **C3** | Handler latency p50 / p95 on ``bicameral.preflight`` | mocked ledger, representative shape |

C2 / C3 use mocked ledger queries so the metric isolates handler-logic +
serialization cost from SurrealDB I/O variance. Real-ledger latency is
its own concern; this baseline tracks the optimization-target code paths
named in #58 (semantic prefilter, lazy/two-pass history, etc. — all of
which mutate the handler logic, not the ledger).

Modes:
- Default: assert current values are within ±20% of the committed baseline,
  with a noise floor (10 tokens / 0.5ms) below which deltas are dismissed
  as measurement variance
- ``BICAMERAL_EVAL_RECORD_BASELINE=1``: write/update ``cost_baseline.jsonl``
  for the current platform; no assertion runs
- No baseline for current platform: skip with re-record instructions
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _baseline_io import (  # noqa: E402  (sibling module)
    BASELINE_PATH,
    BASELINE_VERSION,
    LATENCY_NOISE_FLOOR_MS,
    TOKEN_NOISE_FLOOR,
    current_platform,
    find_baseline,
    is_recording,
    load_baselines,
    now_iso,
    regression_check,
    upsert_baseline,
    write_baselines,
)
from _synthetic_ledger import GENERATOR_VERSION, generate_ledger  # noqa: E402
from _token_count import count_tokens, count_tokens_json  # noqa: E402

_C3_WARMUP = 10
_C3_SAMPLES = 100


def _record_or_assert(
    *,
    metric: str,
    current_values: dict,
    noise_floors: dict,
    extra_key_fields: dict | None = None,
    label: str,
) -> None:
    """Single entry point used by every metric test.

    Recording mode: upsert the row in ``cost_baseline.jsonl``, no assertion.
    Default mode: look up the matching row, assert each value within
    threshold via ``regression_check``. Skip cleanly if no baseline exists
    for the current platform or if the baseline version doesn't match.
    """
    extras = dict(extra_key_fields or {})
    platform_tag = current_platform()

    rows = load_baselines()

    if is_recording():
        new_row = {
            "metric": metric,
            "recorded_on": platform_tag,
            "_baseline_version": BASELINE_VERSION,
            "recorded_at": now_iso(),
            **extras,
            **current_values,
        }
        if metric == "C1":
            new_row["tokenizer"] = "cl100k_base"
            new_row["_generator_version"] = GENERATOR_VERSION
        rows = upsert_baseline(rows, new_row)
        write_baselines(rows)
        return

    baseline = find_baseline(
        rows,
        metric=metric,
        recorded_on=platform_tag,
        n_features=extras.get("n_features"),
    )
    if baseline is None:
        pytest.skip(
            f"{label}: no baseline for platform={platform_tag!r}. "
            f"Re-record with BICAMERAL_EVAL_RECORD_BASELINE=1 and commit {BASELINE_PATH.name}."
        )
    if baseline.get("_baseline_version") != BASELINE_VERSION:
        pytest.skip(
            f"{label}: baseline version mismatch (file={baseline.get('_baseline_version')!r} "
            f"vs code={BASELINE_VERSION!r}). Re-record with BICAMERAL_EVAL_RECORD_BASELINE=1."
        )

    failures: list[str] = []
    for field, current in current_values.items():
        floor = noise_floors[field]
        msg = regression_check(
            field=field,
            current=current,
            baseline=baseline.get(field, 0),
            noise_floor=floor,
        )
        if msg is not None:
            failures.append(msg)
    if failures:
        pytest.fail(f"{label}: " + "; ".join(failures))


# ── Handler isolation ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_handler_environment(monkeypatch, tmp_path):
    """Mirror the isolation in `run_preflight_eval.py` so handler calls are
    deterministic and free of user/env interference."""
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_MUTE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    import handlers.sync_middleware as sm

    monkeypatch.setattr(sm, "ensure_ledger_synced", AsyncMock(return_value=None))
    import handlers.preflight as pf

    monkeypatch.setattr(pf, "_should_show_product_stage", lambda: False)


def _make_region_decision(decision_id: str, description: str, file_path: str, symbol: str) -> dict:
    return {
        "decision_id": decision_id,
        "description": description,
        "status": "reflected",
        "source_type": "transcript",
        "source_ref": "test",
        "source_excerpt": "",
        "meeting_date": "",
        "ingested_at": "2026-04-28",
        "signoff": None,
        "code_region": {
            "file_path": file_path,
            "symbol": symbol,
            "lines": (1, 50),
            "purpose": description,
            "content_hash": "test",
        },
    }


def _make_hitl_row(decision_id: str, description: str, signoff_state: str) -> dict:
    return {
        "decision_id": decision_id,
        "description": description,
        "status": "pending",
        "signoff": {"state": signoff_state},
    }


def _build_realistic_ctx(
    monkeypatch,
    *,
    n_region_matches: int = 10,
    n_collision_pending: int = 2,
    n_context_pending: int = 2,
) -> SimpleNamespace:
    """Mocked BicameralContext with production-realistic data shape.

    Defaults reflect a typical preflight call: caller-supplied ``file_paths``
    that resolve to ~10 region matches, a couple of HITL pending items.
    """
    ledger = MagicMock()
    region_decisions = [
        _make_region_decision(
            decision_id=f"decision:test-{i}",
            description=f"Test decision number {i} — pinned to a representative region",
            file_path=f"src/module_{i % 5}.py",
            symbol=f"function_{i}",
        )
        for i in range(n_region_matches)
    ]
    ledger.get_decisions_for_files = AsyncMock(return_value=region_decisions)
    inner = MagicMock()
    inner._client = MagicMock()
    ledger._inner = inner

    import ledger.queries as lq

    monkeypatch.setattr(
        lq,
        "get_collision_pending_decisions",
        AsyncMock(
            return_value=[
                _make_hitl_row(f"decision:coll-{i}", f"Collision pending {i}", "collision_pending")
                for i in range(n_collision_pending)
            ]
        ),
    )
    monkeypatch.setattr(
        lq,
        "get_context_for_ready_decisions",
        AsyncMock(
            return_value=[
                _make_hitl_row(
                    f"decision:ctx-{i}", f"Context pending ready {i}", "context_pending_ready"
                )
                for i in range(n_context_pending)
            ]
        ),
    )

    return SimpleNamespace(
        ledger=ledger,
        guided_mode=False,
        _sync_state={},
    )


# ── C1: bicameral.history() payload tokens ─────────────────────────────


@pytest.mark.parametrize("n_features", [10, 100, 1000])
def test_c1_history_payload_tokens(n_features, capsys):
    """C1 — token count of a synthetic bicameral.history() payload at scale."""
    ledger = generate_ledger(n_features=n_features)
    tokens = count_tokens_json(ledger)

    with capsys.disabled():
        print(f"  C1 [N={n_features}]: tokens={tokens}")

    _record_or_assert(
        metric="C1",
        current_values={"tokens": tokens},
        noise_floors={"tokens": TOKEN_NOISE_FLOOR},
        extra_key_fields={"n_features": n_features},
        label=f"C1[N={n_features}]",
    )


# ── C2: bicameral.preflight() response size ────────────────────────────


@pytest.mark.asyncio
async def test_c2_preflight_response_size(monkeypatch, capsys):
    """C2 — response token + byte count on representative preflight inputs.

    Single fixed shape: 10 region matches + 2 collision-pending + 2
    context-pending. Response size doesn't scale meaningfully with ledger
    size — it's bounded by ``file_paths`` and HITL state cardinality.
    """
    from handlers.preflight import handle_preflight

    ctx = _build_realistic_ctx(monkeypatch)
    response = await handle_preflight(
        ctx=ctx,
        topic="implement payment idempotency",
        file_paths=["src/module_0.py", "src/module_1.py"],
    )

    response_json = response.model_dump_json()
    response_tokens = count_tokens(response_json)
    response_bytes = len(response_json.encode("utf-8"))

    with capsys.disabled():
        print(f"  C2: tokens={response_tokens}, bytes={response_bytes}")

    assert response.fired is True, "representative load should fire (region + HITL signal present)"

    _record_or_assert(
        metric="C2",
        current_values={"tokens": response_tokens, "bytes": response_bytes},
        noise_floors={"tokens": TOKEN_NOISE_FLOOR, "bytes": TOKEN_NOISE_FLOOR},
        label="C2",
    )


# ── C3: handler latency ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_c3_preflight_handler_latency(monkeypatch, capsys):
    """C3 — p50 / p95 latency on bicameral.preflight, representative load.

    Mocked ledger queries so the metric isolates handler-logic + serialization
    cost. Real SurrealDB latency is a separate baseline (not tracked here).
    Production-realistic shape: ~10 region matches + a couple of HITL items.
    """
    from handlers.preflight import handle_preflight

    ctx = _build_realistic_ctx(monkeypatch)

    async def _one_call():
        # Reset dedup state so each call evaluates the full path, not a
        # recently_checked early-out.
        ctx._sync_state = {}
        return await handle_preflight(
            ctx=ctx,
            topic="implement payment idempotency",
            file_paths=["src/module_0.py", "src/module_1.py"],
        )

    for _ in range(_C3_WARMUP):
        await _one_call()

    timings_ms: list[float] = []
    for _ in range(_C3_SAMPLES):
        t0 = time.perf_counter()
        await _one_call()
        t1 = time.perf_counter()
        timings_ms.append((t1 - t0) * 1000)

    timings_ms.sort()
    p50 = timings_ms[len(timings_ms) // 2]
    p95 = timings_ms[int(len(timings_ms) * 0.95)]

    with capsys.disabled():
        print(f"  C3: p50={p50:.2f}ms, p95={p95:.2f}ms (n={_C3_SAMPLES} after {_C3_WARMUP} warmup)")

    assert p50 > 0, f"p50 should be positive, got {p50}"
    assert p95 >= p50, f"p95 ({p95}) should be ≥ p50 ({p50})"

    _record_or_assert(
        metric="C3",
        current_values={"p50_ms": round(p50, 3), "p95_ms": round(p95, 3)},
        noise_floors={"p50_ms": LATENCY_NOISE_FLOOR_MS, "p95_ms": LATENCY_NOISE_FLOOR_MS},
        label="C3",
    )
