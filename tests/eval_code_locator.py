#!/usr/bin/env python3
"""
Standalone code locator evaluation — no SurrealDB needed.

Measures retrieval quality of search_code() against ground truth decisions.
Uses rank_eval for TREC-verified metric computation.

Usage:
    cd pilot/mcp
    .venv/bin/python tests/eval_code_locator.py -v
    .venv/bin/python tests/eval_code_locator.py --top-k 5 -o test-results/eval.json
    .venv/bin/python tests/eval_code_locator.py --repo /path/to/repo -v
    .venv/bin/python tests/eval_code_locator.py --channel bm25 -v
    .venv/bin/python tests/eval_code_locator.py --channel all -v
    .venv/bin/python tests/eval_code_locator.py --channel grounding -v
    .venv/bin/python tests/eval_code_locator.py --threshold-sweep -o sweep.json
    .venv/bin/python tests/eval_code_locator.py --min-mrr 0.65 --min-recall 0.75
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Ensure pilot/mcp is on sys.path (covers code_locator, adapters, etc.)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.expected.decisions import ALL_DECISIONS


# ---------------------------------------------------------------------------
# Adapter helpers
# ---------------------------------------------------------------------------

def _get_adapter(repo_path: str):
    os.environ["REPO_PATH"] = repo_path
    os.environ["CODE_LOCATOR_SQLITE_DB"] = str(
        Path(repo_path) / ".bicameral" / "code-graph.db"
    )
    from adapters.code_locator import RealCodeLocatorAdapter

    adapter = RealCodeLocatorAdapter(repo_path=repo_path)
    adapter._ensure_initialized()
    return adapter


# ---------------------------------------------------------------------------
# Retrieval runners
# ---------------------------------------------------------------------------

def _run_rrf(adapter, query: str, top_k: int) -> list[dict]:
    return adapter.search_code(query)[:top_k]


def _run_bm25_only(adapter, query: str, top_k: int) -> list[dict]:
    bm25 = adapter._search_tool.bm25
    config = adapter._search_tool.config
    results = bm25.search(query, num_results=config.max_retrieval_results)
    return [r.model_dump() for r in results[:top_k]]


def _run_graph_only(adapter, query: str, top_k: int) -> list[dict]:
    bm25 = adapter._search_tool.bm25
    bm25_results = bm25.search(query, num_results=3)
    if not bm25_results:
        return []
    seed_names = [r.symbol_name for r in bm25_results if r.symbol_name]
    if not seed_names:
        return []
    validated = adapter.validate_symbols(seed_names[:3])
    symbol_ids = [v["symbol_id"] for v in validated if v.get("symbol_id")]
    if not symbol_ids:
        return []
    neighbors = []
    for sid in symbol_ids[:2]:
        neighbors.extend(adapter.get_neighbors(sid))
    return neighbors[:top_k]


def _run_grounding(adapter, query: str, top_k: int) -> list[dict]:
    """Full auto-grounding pipeline — Stage 1 (BM25->file->symbols) + Stage 2 (fuzzy token).

    Calls the same code path as handle_ingest() to measure end-to-end grounding quality.
    """
    repo = os.environ.get("REPO_PATH", ".")
    from handlers.ingest import _auto_ground_via_search

    mapping = {"intent": query, "code_regions": []}
    resolved, _ = _auto_ground_via_search([mapping], repo)

    regions = resolved[0].get("code_regions", []) if resolved else []
    return [
        {
            "symbol_name": r.get("symbol", ""),
            "file_path": r.get("file_path", ""),
            "score": 1.0 / (rank + 1),
            "line_number": r.get("start_line", 0),
        }
        for rank, r in enumerate(regions[:top_k])
    ]


CHANNEL_RUNNERS = {
    "rrf": _run_rrf,
    "bm25": _run_bm25_only,
    "graph": _run_graph_only,
    "grounding": _run_grounding,
}


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def _is_relevant(hit: dict, expected_symbols: set[str], expected_files: list[str]) -> bool:
    sym = hit.get("symbol_name", "")
    fp = hit.get("file_path", "")
    return sym in expected_symbols or any(pat in fp for pat in expected_files)


def run_eval(
    decisions: list[dict],
    adapter,
    top_k: int,
    channel: str = "rrf",
    query_field: str = "keywords",
    verbose: bool = False,
) -> tuple[list[dict], list[float]]:
    """Run retrieval for all decisions. Returns (per_query_results, latencies)."""
    runner = CHANNEL_RUNNERS.get(channel, _run_rrf)
    per_query: list[dict] = []
    latencies: list[float] = []

    for i, d in enumerate(decisions):
        q_id = f"{d['source_ref']}_{i}"
        expected_symbols = set(d.get("expected_symbols", []))
        expected_files = d.get("expected_file_patterns", [])

        if query_field == "description":
            query = d.get("description", "")
        else:
            keywords = d.get("keywords", [])
            query = keywords[0] if keywords else d.get("description", "")

        if not query:
            per_query.append({"query_id": q_id, "hit": False, "mrr": 0, "retrieved": []})
            continue

        t0 = time.perf_counter()
        try:
            hits = runner(adapter, query, top_k)
        except Exception as e:
            hits = []
            if verbose:
                print(f"  ✗ {d['description'][:60]} — ERROR: {e}")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)

        retrieved_details = []
        first_hit_rank = None
        for rank, hit in enumerate(hits):
            sym = hit.get("symbol_name", "")
            fp = hit.get("file_path", "")
            score = float(hit.get("score", 0))
            retrieved_details.append({"symbol": sym, "file": fp, "score": round(score, 4), "rank": rank + 1})
            if _is_relevant(hit, expected_symbols, expected_files) and first_hit_rank is None:
                first_hit_rank = rank + 1

        mrr = (1.0 / first_hit_rank) if first_hit_rank else 0.0
        found_symbols = {h.get("symbol_name", "") for h in hits}
        coverage = len(expected_symbols & found_symbols) / len(expected_symbols) if expected_symbols else 0

        per_query.append({
            "query_id": q_id,
            "description": d["description"][:80],
            "query": query[:60],
            "source_ref": d["source_ref"],
            "expected": list(expected_symbols),
            "retrieved": retrieved_details[:top_k],
            "hit": first_hit_rank is not None,
            "mrr": round(mrr, 4),
            "first_hit_rank": first_hit_rank,
            "coverage": round(coverage, 4),
            "latency_ms": round(elapsed_ms, 1),
        })

        if verbose:
            status = "✓" if first_hit_rank else "✗"
            print(f"  {status} {d['description'][:60]}")
            print(f"    query: {query[:50]} → MRR={mrr:.2f} cov={coverage:.0%} {elapsed_ms:.0f}ms")
            if not first_hit_rank:
                print(f"    expected: {list(expected_symbols)[:3]}")
                print(f"    got: {[h.get('symbol_name','?') for h in hits[:3]]}")

    return per_query, latencies


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _aggregate_metrics(per_query: list[dict], k: int) -> dict:
    n = len(per_query)
    if n == 0:
        return {}
    mrr = sum(pq["mrr"] for pq in per_query) / n
    hit_rate = sum(1 for pq in per_query if pq["hit"]) / n
    return {f"mrr@{k}": round(mrr, 4), f"hit_rate@{k}": round(hit_rate, 4)}


def _compute_ranx_metrics(per_query: list[dict], decisions: list[dict], k: int) -> dict:
    """Compute metrics via rank_eval if available. Graceful fallback."""
    try:
        from rank_eval import Qrels, Run, evaluate
    except ImportError:
        return {}

    q_ids_qrels, doc_ids_qrels, scores_qrels = [], [], []
    q_ids_run, doc_ids_run, scores_run = [], [], []

    for pq, d in zip(per_query, decisions):
        q_id = pq["query_id"]
        expected = d.get("expected_symbols", [])
        if not expected:
            continue

        q_ids_qrels.append(q_id)
        doc_ids_qrels.append(list(expected))
        scores_qrels.append([1] * len(expected))

        r_docs, r_scores = [], []
        seen = set()
        for r in pq.get("retrieved", []):
            key = r["symbol"] if r["symbol"] else r["file"]
            if key and key not in seen:
                seen.add(key)
                r_docs.append(key)
                r_scores.append(float(r["score"]) if r["score"] else 0.001)
        if not r_docs:
            r_docs = ["__no_result__"]
            r_scores = [0.0]
        q_ids_run.append(q_id)
        doc_ids_run.append(r_docs)
        scores_run.append(r_scores)

    if not q_ids_qrels or not q_ids_run:
        return {}

    qrels = Qrels()
    qrels.add_multi(q_ids_qrels, doc_ids_qrels, scores_qrels)
    run = Run()
    run.add_multi(q_ids_run, doc_ids_run, scores_run)

    metrics = evaluate(qrels, run, [f"mrr@{k}", f"recall@{k}", f"ndcg@{k}", "mrr@1"])
    return {str(m): round(float(v), 4) for m, v in metrics.items()}


def _latency_stats(latencies: list[float]) -> dict:
    if not latencies:
        return {}
    s = sorted(latencies)
    n = len(s)
    return {
        "p50_ms": round(s[int(n * 0.5)], 1),
        "p95_ms": round(s[min(int(n * 0.95), n - 1)], 1),
        "p99_ms": round(s[min(int(n * 0.99), n - 1)], 1),
        "mean_ms": round(sum(s) / n, 1),
    }


def _repo_from_source_ref(source_ref: str) -> str:
    return source_ref.split("-")[0]


def _per_repo_metrics(per_query: list[dict], k: int) -> dict:
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for pq in per_query:
        repo = _repo_from_source_ref(pq.get("source_ref", pq["query_id"]))
        by_repo[repo].append(pq)
    result = {}
    for repo, queries in sorted(by_repo.items()):
        n = len(queries)
        avg_mrr = sum(q["mrr"] for q in queries) / n if n else 0
        hit_rate = sum(1 for q in queries if q["hit"]) / n if n else 0
        result[repo] = {"n_queries": n, f"mrr@{k}": round(avg_mrr, 4), f"hit_rate@{k}": round(hit_rate, 4)}
    return result


# ---------------------------------------------------------------------------
# Threshold sweep
# ---------------------------------------------------------------------------

def _threshold_sweep(decisions: list[dict], adapter, top_k: int, verbose: bool = False) -> dict:
    print("\n🔍 Threshold sweep — running retrieval (top-20), then filtering...")
    per_query, _ = run_eval(decisions, adapter, top_k=20, channel="rrf", verbose=False)

    strategies: dict = {}

    n = len(per_query)
    a_mrr = sum(pq["mrr"] for pq in per_query) / n if n else 0
    a_hits = sum(1 for pq in per_query if pq["hit"])
    strategies["A_no_threshold"] = {
        "description": "No threshold — top-K by RRF rank",
        f"mrr@{top_k}": round(a_mrr, 4),
        "hit_rate": round(a_hits / n, 4) if n else 0,
    }

    b_sweep = []
    for rt in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60]:
        hits, mrr_sum = 0, 0.0
        for pq in per_query:
            retrieved = pq.get("retrieved", [])
            if not retrieved:
                continue
            max_score = max((r["score"] for r in retrieved), default=0)
            if max_score <= 0:
                continue
            filtered = [r for r in retrieved if r["score"] >= max_score * rt][:top_k]
            expected = set(pq.get("expected", []))
            for rank, r in enumerate(filtered):
                if r["symbol"] in expected:
                    hits += 1
                    mrr_sum += 1.0 / (rank + 1)
                    break
        b_sweep.append({"t": rt, f"mrr@{top_k}": round(mrr_sum / n, 4), "hit_rate": round(hits / n, 4)})
    strategies["B_relative"] = {"description": "Relative: score >= max_score * t", "sweep": b_sweep}

    c_sweep = []
    for at in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        hits, mrr_sum = 0, 0.0
        for pq in per_query:
            retrieved = pq.get("retrieved", [])
            filtered = [r for r in retrieved if r["score"] >= at][:top_k]
            expected = set(pq.get("expected", []))
            for rank, r in enumerate(filtered):
                if r["symbol"] in expected:
                    hits += 1
                    mrr_sum += 1.0 / (rank + 1)
                    break
        c_sweep.append({"t": at, f"mrr@{top_k}": round(mrr_sum / n, 4), "hit_rate": round(hits / n, 4)})
    strategies["C_absolute"] = {"description": "Absolute: score >= t", "sweep": c_sweep}

    strategies["per_repo"] = _per_repo_metrics(per_query, top_k)

    if verbose:
        print(f"\n  Strategy A (no threshold): MRR@{top_k}={a_mrr:.3f} Hit={a_hits}/{n}")
        best_b = max(b_sweep, key=lambda x: x[f"mrr@{top_k}"])
        print(f"  Strategy B best: t={best_b['t']} → MRR@{top_k}={best_b[f'mrr@{top_k}']:.3f}")
        best_c = max(c_sweep, key=lambda x: x[f"mrr@{top_k}"])
        print(f"  Strategy C best: t={best_c['t']} → MRR@{top_k}={best_c[f'mrr@{top_k}']:.3f}")

    return strategies


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Code Locator Retrieval Evaluation")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[3]),
                        help="Path to repo (default: bicameral root)")
    parser.add_argument("--filter-repo", help="Only run decisions matching this repo prefix (e.g. 'medusa', 'saleor', 'vendure')")
    parser.add_argument("--multi-repo", help="JSON mapping repo prefix to local path, e.g. '{\"medusa\": \"/path/to/medusa\", ...}'")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--output", "-o", help="Write JSON report to file")
    parser.add_argument("--channel", default="rrf", choices=["rrf", "bm25", "graph", "grounding", "all"])
    parser.add_argument("--query-field", default="keywords", choices=["keywords", "description"])
    parser.add_argument("--threshold-sweep", action="store_true")
    parser.add_argument("--min-mrr", type=float, help="Regression gate: min MRR@K")
    parser.add_argument("--min-recall", type=float, help="Regression gate: min hit rate")
    parser.add_argument("--max-repo-variance", type=float, default=0.15)
    args = parser.parse_args()

    decisions = list(ALL_DECISIONS)
    k = args.top_k

    # Filter decisions to a specific repo prefix
    if args.filter_repo:
        decisions = [d for d in decisions if d["source_ref"].startswith(args.filter_repo)]
        if not decisions:
            print(f"No decisions match filter-repo={args.filter_repo!r}")
            sys.exit(1)

    # Multi-repo mode: run each repo's decisions against its own codebase
    if args.multi_repo:
        repo_map = json.loads(args.multi_repo) if not args.multi_repo.endswith(".json") else json.loads(Path(args.multi_repo).read_text())
        all_pq, all_lat = [], []
        for prefix, repo_path in repo_map.items():
            repo_decisions = [d for d in list(ALL_DECISIONS) if d["source_ref"].startswith(prefix)]
            if not repo_decisions:
                continue
            print(f"\n── Repo: {prefix} ({len(repo_decisions)} decisions) @ {repo_path}")
            adapter = _get_adapter(repo_path)
            pq, lat = run_eval(repo_decisions, adapter, k, channel=args.channel,
                               query_field=args.query_field, verbose=args.verbose)
            all_pq.extend(pq)
            all_lat.extend(lat)

        if not all_pq:
            print("No results from any repo.")
            sys.exit(1)

        agg = _aggregate_metrics(all_pq, k)
        repo_m = _per_repo_metrics(all_pq, k)
        ranx_m = _compute_ranx_metrics(all_pq, [d for p in repo_map for d in ALL_DECISIONS if d["source_ref"].startswith(p)], k)

        print(f"\n{'=' * 50}")
        print(f"  AGGREGATE (all repos)")
        print(f"  MRR@{k}:        {agg.get(f'mrr@{k}', 0):.3f}")
        print(f"  Hit Rate:      {agg.get(f'hit_rate@{k}', 0):.1%}")
        if ranx_m:
            for m, v in ranx_m.items():
                print(f"  ranx {m}: {v:.4f}")
        print(f"  Per-repo:")
        for repo, rm in repo_m.items():
            print(f"    {repo}: MRR@{k}={rm[f'mrr@{k}']:.3f} hit={rm[f'hit_rate@{k}']:.1%} (n={rm['n_queries']})")
        print(f"{'=' * 50}")

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": {"top_k": k, "channel": args.channel, "query_field": args.query_field, "repos": repo_map},
            "ground_truth_count": len(all_pq),
            "metrics": agg,
            "ranx_metrics": ranx_m,
            "per_repo": repo_m,
            "latency": _latency_stats(all_lat),
            "per_query": all_pq,
        }
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(report, indent=2))
            print(f"\n  Report written to {args.output}")
        return

    print(f"📊 Code Locator Evaluation")
    print(f"   Repo:      {args.repo}")
    print(f"   Decisions:  {len(decisions)}")
    print(f"   Top-K:      {k}")
    print(f"   Channel:    {args.channel}")
    print(f"   Query:      {args.query_field}")

    adapter = _get_adapter(args.repo)

    # Threshold sweep mode
    if args.threshold_sweep:
        sweep = _threshold_sweep(decisions, adapter, k, verbose=args.verbose)
        report = {"timestamp": datetime.now(timezone.utc).isoformat(), "strategies": sweep}
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(report, indent=2))
            print(f"\n  Sweep written to {args.output}")
        return

    # Standard eval
    channels_to_run = ["rrf", "bm25", "graph", "grounding"] if args.channel == "all" else [args.channel]
    all_channel_metrics = {}
    primary_pq = None
    primary_lat = None

    for ch in channels_to_run:
        if len(channels_to_run) > 1:
            print(f"\n── Channel: {ch} {'─' * 40}")

        pq, lat = run_eval(decisions, adapter, k, channel=ch, query_field=args.query_field, verbose=args.verbose)
        agg = _aggregate_metrics(pq, k)
        repo_m = _per_repo_metrics(pq, k)
        lat_stats = _latency_stats(lat)
        ranx_m = _compute_ranx_metrics(pq, decisions, k)

        all_channel_metrics[ch] = {**agg, "ranx": ranx_m}
        if primary_pq is None:
            primary_pq, primary_lat = pq, lat

        print(f"\n{'=' * 50}")
        print(f"  Channel:       {ch}")
        print(f"  MRR@{k}:        {agg.get(f'mrr@{k}', 0):.3f}")
        print(f"  Hit Rate:      {agg.get(f'hit_rate@{k}', 0):.1%}")
        if ranx_m:
            for m, v in ranx_m.items():
                print(f"  ranx {m}: {v:.4f}")
        print(f"  Latency p50:   {lat_stats.get('p50_ms', '?')}ms")
        print(f"  Per-repo:")
        for repo, rm in repo_m.items():
            print(f"    {repo}: MRR@{k}={rm[f'mrr@{k}']:.3f} hit={rm[f'hit_rate@{k}']:.1%} (n={rm['n_queries']})")
        print(f"{'=' * 50}")

    # Build report
    repo_m = _per_repo_metrics(primary_pq, k)
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {"top_k": k, "channel": args.channel, "query_field": args.query_field, "repo": args.repo},
        "ground_truth_count": len(decisions),
        "metrics": _aggregate_metrics(primary_pq, k),
        "ranx_metrics": _compute_ranx_metrics(primary_pq, decisions, k),
        "per_repo": repo_m,
        "per_channel": all_channel_metrics if args.channel == "all" else None,
        "latency": _latency_stats(primary_lat),
        "per_query": primary_pq,
    }

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, indent=2))
        print(f"\n  Report written to {args.output}")

    # Regression gate
    exit_code = 0
    if args.min_mrr is not None or args.min_recall is not None:
        agg = _aggregate_metrics(primary_pq, k)
        mrr_val = agg.get(f"mrr@{k}", 0)
        hit_val = agg.get(f"hit_rate@{k}", 0)

        print(f"\n🚦 Regression gate:")
        if args.min_mrr is not None:
            ok = mrr_val >= args.min_mrr
            print(f"  MRR@{k} = {mrr_val:.3f} >= {args.min_mrr} → {'PASS' if ok else 'FAIL'}")
            if not ok:
                exit_code = 1
        if args.min_recall is not None:
            ok = hit_val >= args.min_recall
            print(f"  Hit@{k} = {hit_val:.3f} >= {args.min_recall} → {'PASS' if ok else 'FAIL'}")
            if not ok:
                exit_code = 1

        if repo_m and args.max_repo_variance:
            all_mrr = [rm[f"mrr@{k}"] for rm in repo_m.values()]
            if all_mrr:
                mean_mrr = sum(all_mrr) / len(all_mrr)
                for repo, rm in repo_m.items():
                    diff = abs(rm[f"mrr@{k}"] - mean_mrr)
                    if diff > args.max_repo_variance:
                        print(f"  Repo {repo}: MRR deviates {diff:.3f} from mean {mean_mrr:.3f} → FAIL")
                        exit_code = 1

    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
