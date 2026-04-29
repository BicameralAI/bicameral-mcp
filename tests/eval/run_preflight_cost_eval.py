"""Pytest runner for preflight §C cost/latency baseline (issue #88).

Three deterministic metrics with committed baselines and an asymmetric
regression rule (only flags regressions, not improvements). C4 (LLM-in-
the-loop end-to-end) is deferred.

| Metric | What | Scope |
|---|---|---|
| **C1** | ``bicameral.history()`` payload tokens at N = 10, 100, 1000 features | synthetic ledger dict, JSON-serialized |
| **C2** | ``bicameral.preflight()`` response size at N = 10, 100, 1000 | real ``memory://`` SurrealDB seeded from synthetic generator |
| **C3** | Handler latency p50 / p95 on ``bicameral.preflight`` at N = 10, 100, 1000 | real ``memory://`` SurrealDB seeded from synthetic generator |

C2 + C3 measure against a **real seeded ledger**. The synthetic generator
produces a deterministic `HistoryResponse`-shaped dict; ``_seed_ledger.py``
translates it through ``adapter.ingest_payload`` into the v4 graph
(input_span / decision / code_region nodes + yields / binds_to edges). The
preflight handler then runs against real SurrealDB queries — same code
path the developer hits in production. A regression in the SurrealDB query
plan, the handler's iteration logic, the JSON serialization, or any
combination surfaces as a C2 byte-count or C3 latency change.

Modes:
- Default: assert current values are within ±20% of the committed baseline,
  with a noise floor (10 tokens / 0.5ms) below which deltas are dismissed
  as measurement variance
- ``BICAMERAL_EVAL_RECORD_BASELINE=1``: write/update ``cost_baseline.jsonl``
  for the current platform; no assertion runs
- No baseline for current platform: skip with re-record instructions
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _baseline_io import (  # noqa: E402  (sibling module)
    ANY_PLATFORM,
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
from _seed_ledger import seed_ledger_from_synthetic  # noqa: E402
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
    platform_agnostic: bool = False,
) -> None:
    """Single entry point used by every metric test.

    Recording mode: upsert the row in ``cost_baseline.jsonl``, no assertion.
    Default mode: look up the matching row, assert each value within
    threshold via ``regression_check``. Skip cleanly if no baseline exists
    for the current platform or if the baseline version doesn't match.

    ``platform_agnostic=True`` records / matches with ``recorded_on=any``
    so the baseline applies on every host. Use for metrics that don't
    depend on OS/hardware (token counts, byte counts).
    """
    extras = dict(extra_key_fields or {})
    platform_tag = ANY_PLATFORM if platform_agnostic else current_platform()

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
    """Isolate handler from user/env interference. Notably stubs out
    ``ensure_ledger_synced`` (cost/latency tests don't need real sync —
    that's link_commit's territory) and the product-stage marker."""
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_MUTE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    import handlers.sync_middleware as sm
    monkeypatch.setattr(sm, "ensure_ledger_synced", AsyncMock(return_value=None))
    import handlers.preflight as pf
    monkeypatch.setattr(pf, "_should_show_product_stage", lambda: False)


async def _build_seeded_ctx(n_features: int) -> tuple[SimpleNamespace, dict]:
    """Spin up a fresh ``memory://`` adapter, seed it with N synthetic
    features, return (ctx, synthetic_dict). The synthetic dict is returned
    so callers can pick file_paths that match grounded decisions.

    Each call creates a new adapter instance — tests do not share state.
    """
    from ledger.adapter import SurrealDBLedgerAdapter

    adapter = SurrealDBLedgerAdapter(url="memory://")
    await adapter.connect()

    synthetic = generate_ledger(n_features=n_features, seed=42)
    await seed_ledger_from_synthetic(adapter, synthetic)

    ctx = SimpleNamespace(
        ledger=adapter,
        guided_mode=False,
        _sync_state={},
    )
    return ctx, synthetic


def _pick_grounded_paths(synthetic: dict, count: int = 2) -> list[str]:
    """Return up to ``count`` file_paths drawn from grounded
    (reflected / drifted) decisions in the synthetic ledger.

    Used to guarantee region-anchored matches in C2 / C3 — the preflight
    response should fire so we measure a non-trivial response shape.
    """
    paths: list[str] = []
    for feature in synthetic["features"]:
        for decision in feature["decisions"]:
            if decision["status"] in {"reflected", "drifted"}:
                fulfillments = decision.get("fulfillments") or []
                if fulfillments:
                    paths.append(fulfillments[0]["file_path"])
                    if len(paths) >= count:
                        return paths
    return paths


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
        platform_agnostic=True,  # tiktoken + JSON is deterministic across OSes
    )


# ── C2: bicameral.preflight() response size (real seeded ledger) ──────


@pytest.mark.parametrize("n_features", [10, 100, 1000])
async def test_c2_preflight_response_size(n_features, capsys):
    """C2 — response token + byte count against a real ledger seeded
    with N synthetic features. file_paths picked from the seeded data
    so region-anchored lookup hits at least 2 grounded decisions."""
    from handlers.preflight import handle_preflight

    seed_t0 = time.perf_counter()
    ctx, synthetic = await _build_seeded_ctx(n_features)
    seed_ms = (time.perf_counter() - seed_t0) * 1000

    file_paths = _pick_grounded_paths(synthetic, count=2)

    response = await handle_preflight(
        ctx=ctx,
        topic="implement payment idempotency",
        file_paths=file_paths,
    )
    response_json = response.model_dump_json()
    response_tokens = count_tokens(response_json)
    response_bytes = len(response_json.encode("utf-8"))

    with capsys.disabled():
        print(
            f"  C2 [N={n_features}]: tokens={response_tokens}, bytes={response_bytes}, "
            f"fired={response.fired} (seed={seed_ms:.0f}ms)"
        )

    _record_or_assert(
        metric="C2",
        current_values={"tokens": response_tokens, "bytes": response_bytes},
        noise_floors={"tokens": TOKEN_NOISE_FLOOR, "bytes": TOKEN_NOISE_FLOOR},
        extra_key_fields={"n_features": n_features},
        label=f"C2[N={n_features}]",
        platform_agnostic=True,  # response shape is deterministic given same seed
    )


# ── C3: handler latency (real seeded ledger) ──────────────────────────


@pytest.mark.parametrize("n_features", [10, 100, 1000])
async def test_c3_preflight_handler_latency(n_features, capsys):
    """C3 — p50 / p95 latency on bicameral.preflight against a real
    ledger seeded with N synthetic features. Measures handler-logic +
    real SurrealDB query time + serialization — what the developer
    actually feels.

    Per-platform baseline (latency varies meaningfully across hosts).
    """
    from handlers.preflight import handle_preflight

    seed_t0 = time.perf_counter()
    ctx, synthetic = await _build_seeded_ctx(n_features)
    seed_ms = (time.perf_counter() - seed_t0) * 1000

    file_paths = _pick_grounded_paths(synthetic, count=2)

    async def _one_call():
        # Reset dedup state so each call evaluates the full path, not a
        # recently_checked early-out.
        ctx._sync_state = {}
        return await handle_preflight(
            ctx=ctx,
            topic="implement payment idempotency",
            file_paths=file_paths,
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
        print(
            f"  C3 [N={n_features}]: p50={p50:.2f}ms, p95={p95:.2f}ms "
            f"(seed={seed_ms:.0f}ms, n={_C3_SAMPLES} after {_C3_WARMUP} warmup)"
        )

    assert p50 > 0, f"p50 should be positive, got {p50}"
    assert p95 >= p50, f"p95 ({p95}) should be ≥ p50 ({p50})"

    _record_or_assert(
        metric="C3",
        current_values={"p50_ms": round(p50, 3), "p95_ms": round(p95, 3)},
        noise_floors={"p50_ms": LATENCY_NOISE_FLOOR_MS, "p95_ms": LATENCY_NOISE_FLOOR_MS},
        extra_key_fields={"n_features": n_features},
        label=f"C3[N={n_features}]",
    )
