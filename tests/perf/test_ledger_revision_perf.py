"""File-backed SurrealKV perf gate for `get_ledger_revision` (#357 sub-task 2).

The pre-#357 perf claim ("~0.4ms p95 at any ledger size", `ledger/queries.py:1145`)
was measured on `memory://` — a CPU-cache benchmark, not a storage benchmark.
This test runs against a real on-disk SurrealKV instance at four ledger sizes
and asserts the constant-time-at-scale claim under real I/O.

Threshold rationale: local file-backed measurements on a developer MacBook
land around p95=0.15-0.20ms at all four N values. CI runners are typically
2-5x slower for I/O-bound work, so an absolute threshold of 5ms catches
order-of-magnitude regressions (the original v18 ORDER BY scan was 8ms p50)
while leaving room for noise. The threshold tightens to 1-2ms in a follow-up
PR once 3-5 CI runs land green numbers — that's how perf gates ratchet
without flaking on first deployment.

Marked with `perf` so it doesn't run by default. CI runs it via
`.github/workflows/perf-gate.yml` with `pytest -m perf`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from ledger.queries import get_ledger_revision

# Absolute threshold — catches order-of-magnitude regressions. Tighten in a
# follow-up after the gate has 3-5 green CI runs to learn the actual
# CI-runner baseline. Until then, 5ms gives plenty of room while still
# trapping the 8ms ORDER BY regression that originally motivated the v19
# counter mechanism.
P95_THRESHOLD_MS = 5.0

# Number of warm-up iterations (discarded) before timed samples are taken.
WARMUP_ITERATIONS = 5

# Number of timed samples per N — 100 gives a usable p95 (sample 95).
TIMED_SAMPLES = 100

# Where to drop the structured perf results for CI artifact upload.
RESULTS_DIR = Path(os.environ.get("PERF_RESULTS_DIR", "perf-results"))


async def _seed_decisions(client, n: int) -> None:
    """CREATE N decision rows with unique canonical_ids (the index requires uniqueness)."""
    for i in range(n):
        await client.query(
            "CREATE decision SET description=$d, source_type='perf', source_ref='r', "
            "status='ungrounded', canonical_id=$c",
            {"d": f"perf-{i}", "c": f"perf-{i}"},
        )


def _percentile(sorted_samples: list[float], q: float) -> float:
    """Linear-interpolation percentile (q in [0,1]). Standard textbook def."""
    if not sorted_samples:
        return 0.0
    pos = q * (len(sorted_samples) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_samples) - 1)
    frac = pos - lo
    return sorted_samples[lo] * (1 - frac) + sorted_samples[hi] * frac


@pytest.mark.perf
@pytest.mark.asyncio
@pytest.mark.parametrize("n_decisions", [100, 500, 1000, 5000])
async def test_get_ledger_revision_p95_under_threshold(surrealkv_client, n_decisions):
    """Seed N decisions, time WARMUP+TIMED_SAMPLES revision lookups, assert
    p95 stays under the absolute SLO threshold. The v19 counter mechanism
    reads a single row from `bicameral_meta` — it must remain O(1) wrt N.
    Any regression to ORDER-BY-shaped behaviour will scale with N and trip
    the gate first at N=5000 where the regression is most visible.
    """
    await _seed_decisions(surrealkv_client, n_decisions)

    for _ in range(WARMUP_ITERATIONS):
        await get_ledger_revision(surrealkv_client)

    samples_ms: list[float] = []
    for _ in range(TIMED_SAMPLES):
        t0 = time.perf_counter()
        rev = await get_ledger_revision(surrealkv_client)
        samples_ms.append((time.perf_counter() - t0) * 1000.0)
        assert rev, "revision must be non-empty for a populated ledger"

    samples_ms.sort()
    p50 = _percentile(samples_ms, 0.50)
    p95 = _percentile(samples_ms, 0.95)
    p99 = _percentile(samples_ms, 0.99)
    mean = sum(samples_ms) / len(samples_ms)

    result = {
        "metric": "get_ledger_revision",
        "backend": "surrealkv",
        "n_decisions": n_decisions,
        "warmup_iterations": WARMUP_ITERATIONS,
        "timed_samples": TIMED_SAMPLES,
        "p50_ms": round(p50, 4),
        "p95_ms": round(p95, 4),
        "p99_ms": round(p99, 4),
        "mean_ms": round(mean, 4),
        "max_ms": round(samples_ms[-1], 4),
        "p95_threshold_ms": P95_THRESHOLD_MS,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"get_ledger_revision_n{n_decisions}.json").write_text(json.dumps(result, indent=2))

    assert p95 < P95_THRESHOLD_MS, (
        f"get_ledger_revision p95 regression at N={n_decisions} on file-backed SurrealKV.\n"
        f"  p50={p50:.3f}ms  p95={p95:.3f}ms  p99={p99:.3f}ms  threshold={P95_THRESHOLD_MS}ms\n"
        f"The v19 counter mechanism is meant to be O(1) wrt N. A p95 over the threshold "
        f"suggests the read scales with ledger size — likely a regression to ORDER-BY-shaped "
        f"behaviour (the v18 query this counter replaced was ~8ms p50 at N=1000).\n"
        f"See ledger/queries.py::get_ledger_revision for the design history."
    )
