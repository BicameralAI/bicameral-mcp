"""Drift benchmark harness — V1 task A1.

Measures wall-clock latency for the three read-path handlers most relevant
to drift workflows:

  - handle_search_decisions
  - handle_detect_drift
  - handle_link_commit (the catch-up path used by every read handler)

Marked @pytest.mark.bench so normal `pytest tests/` runs skip it.
Run explicitly:

    pytest tests/bench_drift.py -v -m bench -s

Output: test-results/bench/drift_baseline.json with per-handler
p50/p95/max wall-clock numbers, plus a stdout summary table.

This is a *baseline* harness. V1 acceptance does not enforce hard latency
thresholds — only that the numbers are reproducible and documented.
The numbers feed the V2 design budget (PLAN.md:83 targets:
search_decisions < 2s, detect_drift < 1s on 100+ decisions).
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

import pytest

from adapters.code_locator import get_code_locator
from adapters.ledger import reset_ledger_singleton
from context import BicameralContext
from handlers.detect_drift import handle_detect_drift
from handlers.ingest import handle_ingest
from handlers.link_commit import handle_link_commit
from handlers.search_decisions import handle_search_decisions

RESULTS_DIR = Path(__file__).parent.parent / "test-results" / "bench"

# Tunables — keep modest so CI doesn't blow up
N_DECISIONS = 100
N_FILES_TARGET = 25
SEARCH_QUERIES = [
    "ledger ingestion",
    "drift detection",
    "code region",
    "symbol resolution",
    "compliance check",
    "tombstone",
    "BM25 search",
    "tree-sitter",
    "session banner",
    "graph walk",
]
SEARCH_ITERATIONS_PER_QUERY = 5
DRIFT_ITERATIONS_PER_FILE = 3
LINK_COMMIT_ITERATIONS = 5


@pytest.fixture
def bench_env(monkeypatch, tmp_path):
    """Fresh in-memory ledger + REPO_PATH pointing at the actual repo."""
    monkeypatch.setenv("SURREAL_URL", "memory://")
    monkeypatch.setenv("REPO_PATH", str(Path(__file__).resolve().parents[1]))
    reset_ledger_singleton()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    yield
    reset_ledger_singleton()


@pytest.fixture
def bench_ctx(bench_env):
    return BicameralContext.from_env()


def _percentiles(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0, "n": 0}
    s = sorted(samples)
    return {
        "p50": statistics.median(s),
        "p95": s[max(0, int(0.95 * len(s)) - 1)],
        "max": s[-1],
        "mean": statistics.fmean(s),
        "n": len(s),
    }


async def _collect_real_symbols(adapter, repo_path: Path, n_files_target: int) -> list[dict]:
    """Walk a curated set of repo files and extract their symbols via tree-sitter.

    Uses ``adapter.extract_symbols`` (which goes straight to tree-sitter and
    does not require the BM25/SQLite index to be built). Picks 25+ files from
    the handlers/ledger/code_locator subtrees so the bench mirrors real
    workload shape without bench setup needing to call
    ``code_locator index <repo_path>`` first.
    """
    seed_dirs = [
        repo_path / "handlers",
        repo_path / "ledger",
        repo_path / "code_locator",
        repo_path / "adapters",
    ]
    files: list[Path] = []
    for d in seed_dirs:
        if d.exists():
            files.extend(
                sorted(p for p in d.rglob("*.py") if p.is_file() and "__pycache__" not in p.parts)
            )

    collected: list[dict] = []
    seen_pairs: set[str] = set()
    for fp in files:
        if len({c["file_path"] for c in collected}) >= n_files_target and len(collected) >= 80:
            break
        try:
            records = await adapter.extract_symbols(str(fp))
        except Exception:
            continue
        rel = str(fp.relative_to(repo_path))
        for rec in records[:6]:  # cap per-file to keep distribution flat
            sym = rec.get("symbol_name") or rec.get("name") or ""
            line = rec.get("start_line") or rec.get("line_number") or 1
            if not sym:
                continue
            key = f"{rel}::{sym}"
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            collected.append(
                {
                    "file_path": rel,
                    "symbol_name": sym,
                    "line_number": line,
                }
            )
    return collected


def _build_payload(symbols: list[dict], batch_idx: int, batch_size: int) -> dict:
    """Build one ingest payload covering `batch_size` decisions.

    Each mapping pairs a synthetic intent with a real symbol so the
    ledger can ground it via search_code at ingest time.
    """
    mappings = []
    for i in range(batch_size):
        sym = symbols[(batch_idx * batch_size + i) % len(symbols)]
        mappings.append(
            {
                "span": {
                    "span_id": f"bench-{batch_idx}-{i}",
                    "source_type": "transcript",
                    "text": f"Bench decision {batch_idx}-{i} about {sym['symbol_name']}",
                    "speaker": "bench",
                    "source_ref": f"bench-meeting-{batch_idx}",
                },
                "intent": f"Bench decision {batch_idx}-{i}: maintain {sym['symbol_name']} in {sym['file_path']}",
                "symbols": [sym["symbol_name"]],
                "code_regions": [
                    {
                        "file_path": sym["file_path"],
                        "symbol": sym["symbol_name"],
                        "type": "function",
                        "start_line": sym["line_number"],
                        "end_line": sym["line_number"] + 20,
                        "purpose": f"bench batch {batch_idx} item {i}",
                    }
                ],
                "dependency_edges": [],
            }
        )
    return {
        "query": f"bench batch {batch_idx}",
        "repo": ".",
        "commit_hash": f"bench-{batch_idx}",
        "analyzed_at": "2026-04-24T00:00:00Z",
        "mappings": mappings,
    }


@pytest.mark.bench
def test_drift_baseline(bench_ctx):
    """Baseline-measurement run for V1 A1.

    Seeds N_DECISIONS decisions, syncs, then times the three handlers.
    Writes JSON artifact + prints stdout summary.
    """
    asyncio.run(_run_bench(bench_ctx))


async def _run_bench(ctx) -> None:
    adapter = get_code_locator()

    # --- Setup: collect real symbols, ingest 100 decisions in batches of 10 ---
    symbols = await _collect_real_symbols(
        adapter, Path(ctx.repo_path), n_files_target=N_FILES_TARGET
    )
    assert len(symbols) >= 25, f"Only got {len(symbols)} symbols; need >= 25 for realistic bench"

    batch_size = 10
    n_batches = N_DECISIONS // batch_size
    print(
        f"\n[bench] Ingesting {N_DECISIONS} decisions across {len(symbols)} unique symbols ({n_batches} batches of {batch_size})"
    )

    setup_start = time.perf_counter()
    for b in range(n_batches):
        payload = _build_payload(symbols, batch_idx=b, batch_size=batch_size)
        await handle_ingest(ctx, payload)
    setup_elapsed = time.perf_counter() - setup_start
    print(f"[bench] Setup ingest done in {setup_elapsed:.2f}s")

    # Initial baseline sync (link_commit HEAD) — also serves as warm-up
    warm_start = time.perf_counter()
    await handle_link_commit(ctx, "HEAD")
    print(f"[bench] Warm-up link_commit(HEAD) in {time.perf_counter() - warm_start:.3f}s")

    # --- Measure: link_commit (already-synced fast path) ---
    link_commit_samples = []
    for _ in range(LINK_COMMIT_ITERATIONS):
        t0 = time.perf_counter()
        await handle_link_commit(ctx, "HEAD")
        link_commit_samples.append(time.perf_counter() - t0)

    # --- Measure: search_decisions across N queries × M iterations ---
    search_samples = []
    for q in SEARCH_QUERIES:
        for _ in range(SEARCH_ITERATIONS_PER_QUERY):
            t0 = time.perf_counter()
            await handle_search_decisions(ctx, q, max_results=10)
            search_samples.append(time.perf_counter() - t0)

    # --- Measure: detect_drift across the touched files × M iterations ---
    file_paths = sorted({s["file_path"] for s in symbols})
    drift_samples = []
    for fp in file_paths:
        for _ in range(DRIFT_ITERATIONS_PER_FILE):
            t0 = time.perf_counter()
            await handle_detect_drift(ctx, fp)
            drift_samples.append(time.perf_counter() - t0)

    # --- Aggregate + write artifact ---
    report = {
        "config": {
            "n_decisions": N_DECISIONS,
            "n_symbols": len(symbols),
            "n_files": len(file_paths),
            "n_search_queries": len(SEARCH_QUERIES),
            "search_iterations_per_query": SEARCH_ITERATIONS_PER_QUERY,
            "drift_iterations_per_file": DRIFT_ITERATIONS_PER_FILE,
            "link_commit_iterations": LINK_COMMIT_ITERATIONS,
        },
        "setup": {
            "ingest_total_seconds": round(setup_elapsed, 4),
            "ingest_per_decision_seconds": round(setup_elapsed / N_DECISIONS, 4),
        },
        "handlers": {
            "search_decisions": _percentiles(search_samples),
            "detect_drift": _percentiles(drift_samples),
            "link_commit_warm": _percentiles(link_commit_samples),
        },
    }

    out_path = RESULTS_DIR / "drift_baseline.json"
    out_path.write_text(json.dumps(report, indent=2))

    # Stdout summary
    print("\n" + "=" * 68)
    print("DRIFT BENCHMARK BASELINE — V1 A1")
    print("=" * 68)
    print(f"Setup: {N_DECISIONS} decisions, {len(symbols)} symbols, {len(file_paths)} files")
    print(
        f"Setup ingest: {setup_elapsed:.2f}s total ({setup_elapsed / N_DECISIONS * 1000:.1f}ms / decision)"
    )
    print()
    print(f"{'handler':<25} {'p50 (ms)':>10} {'p95 (ms)':>10} {'max (ms)':>10} {'n':>5}")
    print("-" * 68)
    for name, p in report["handlers"].items():
        print(
            f"{name:<25} {p['p50'] * 1000:>10.1f} {p['p95'] * 1000:>10.1f} {p['max'] * 1000:>10.1f} {p['n']:>5}"
        )
    print("=" * 68)
    print(f"Artifact: {out_path}")
