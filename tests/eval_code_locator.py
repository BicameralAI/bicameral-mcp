#!/usr/bin/env python3
"""
Standalone code locator evaluation — no SurrealDB needed.

Measures retrieval quality of search_code() against ground truth decisions.
Silong: run this after any change to code_locator/ to see if accuracy improves.

Usage:
    cd pilot/mcp
    .venv/bin/python tests/eval_code_locator.py
    .venv/bin/python tests/eval_code_locator.py --repo /path/to/other/repo
    .venv/bin/python tests/eval_code_locator.py --top-k 5 --verbose
"""
import argparse
import json
import os
import sys
from pathlib import Path

# Ensure pilot/mcp is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.expected.decisions import ALL_DECISIONS


def get_adapter(repo_path: str):
    """Initialize code locator adapter for a repo."""
    os.environ.setdefault("REPO_PATH", repo_path)
    os.environ.setdefault("CODE_LOCATOR_SQLITE_DB", str(Path(repo_path) / ".bicameral" / "code-graph.db"))

    from adapters.code_locator import RealCodeLocatorAdapter
    adapter = RealCodeLocatorAdapter()
    adapter._ensure_initialized()
    return adapter


def evaluate(adapter, decisions: list[dict], top_k: int = 3, verbose: bool = False) -> dict:
    """Run search_code for each decision, compare against ground truth."""
    results = []

    for d in decisions:
        keywords = d.get("keywords", [])
        expected_symbols = set(d.get("expected_symbols", []))
        expected_files = d.get("expected_file_patterns", [])

        if not keywords:
            continue

        # Search using the first keyword (primary query)
        query = keywords[0]
        try:
            hits = adapter.search_code(query)
        except Exception as e:
            results.append({
                "description": d["description"][:60],
                "query": query,
                "error": str(e),
                "precision": 0, "recall": 0, "mrr": 0,
            })
            continue

        top_hits = hits[:top_k]
        found_symbols = set()
        found_files = set()
        first_relevant_rank = None

        for rank, hit in enumerate(top_hits):
            sym = hit.get("symbol_name", "")
            fp = hit.get("file_path", "")
            found_symbols.add(sym)
            found_files.add(fp)

            # Check relevance: symbol match OR file pattern match
            is_relevant = (
                sym in expected_symbols
                or any(pat in fp for pat in expected_files)
            )
            if is_relevant and first_relevant_rank is None:
                first_relevant_rank = rank + 1

        # Precision@k: fraction of top_k results that are relevant
        relevant_in_top_k = sum(
            1 for h in top_hits
            if h.get("symbol_name", "") in expected_symbols
            or any(pat in h.get("file_path", "") for pat in expected_files)
        )
        precision = relevant_in_top_k / len(top_hits) if top_hits else 0

        # Recall: fraction of expected symbols found in top_k
        matched_symbols = expected_symbols & found_symbols
        recall = len(matched_symbols) / len(expected_symbols) if expected_symbols else 0

        # MRR: 1/rank of first relevant result
        mrr = (1.0 / first_relevant_rank) if first_relevant_rank else 0

        entry = {
            "description": d["description"][:60],
            "query": query,
            "precision": round(precision, 2),
            "recall": round(recall, 2),
            "mrr": round(mrr, 2),
            "hits": len(top_hits),
            "expected_symbols": list(expected_symbols),
            "found_symbols": [h.get("symbol_name", "") for h in top_hits],
        }
        results.append(entry)

        if verbose:
            status = "✓" if mrr > 0 else "✗"
            print(f"  {status} {entry['description']}")
            print(f"    query: {query} → P@{top_k}={precision:.0%} R={recall:.0%} MRR={mrr:.2f}")
            if mrr == 0:
                print(f"    expected: {list(expected_symbols)[:3]}")
                print(f"    got: {[h.get('symbol_name','?') for h in top_hits[:3]]}")

    # Aggregate
    n = len(results)
    if n == 0:
        return {"error": "No evaluable decisions", "results": []}

    avg_precision = sum(r.get("precision", 0) for r in results) / n
    avg_recall = sum(r.get("recall", 0) for r in results) / n
    avg_mrr = sum(r.get("mrr", 0) for r in results) / n
    hit_rate = sum(1 for r in results if r.get("mrr", 0) > 0) / n

    return {
        "total_decisions": n,
        "avg_precision_at_k": round(avg_precision, 3),
        "avg_recall": round(avg_recall, 3),
        "mrr_at_k": round(avg_mrr, 3),
        "hit_rate": round(hit_rate, 3),
        "top_k": top_k,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Code Locator Retrieval Evaluation")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[3]),
                        help="Path to repo (default: bicameral root)")
    parser.add_argument("--multi-repo", type=str, default=None,
                        help='JSON map of repo_name→path, e.g. \'{"medusa":"/path/to/medusa"}\'')
    parser.add_argument("--top-k", type=int, default=3, help="Top-K for precision/MRR")
    parser.add_argument("--min-mrr", type=float, default=None,
                        help="Minimum MRR threshold — exit non-zero if below (regression gate)")
    parser.add_argument("--max-repo-variance", type=float, default=None,
                        help="Maximum allowed variance in MRR across repos")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print per-decision results")
    parser.add_argument("--output", "-o", help="Write JSON report to file")
    args = parser.parse_args()

    if args.multi_repo:
        repo_map = json.loads(args.multi_repo)
    else:
        repo_map = {"default": args.repo}

    all_reports = {}
    for repo_name, repo_path in repo_map.items():
        print(f"📊 Code Locator Evaluation — {repo_name}")
        print(f"   Repo: {repo_path}")
        print(f"   Decisions: {len(ALL_DECISIONS)}")
        print(f"   Top-K: {args.top_k}")
        print()

        adapter = get_adapter(repo_path)
        report = evaluate(adapter, ALL_DECISIONS, top_k=args.top_k, verbose=args.verbose)
        all_reports[repo_name] = report

        print(f"\n{'='*50}")
        print(f"  [{repo_name}]")
        print(f"  Precision@{args.top_k}:  {report['avg_precision_at_k']:.1%}")
        print(f"  Recall:        {report['avg_recall']:.1%}")
        print(f"  MRR@{args.top_k}:        {report['mrr_at_k']:.3f}")
        print(f"  Hit Rate:      {report['hit_rate']:.1%}")
        print(f"  Decisions:     {report['total_decisions']}")
        print(f"{'='*50}\n")

    # Aggregate across repos
    mrr_values = [r["mrr_at_k"] for r in all_reports.values()]
    avg_mrr = sum(mrr_values) / len(mrr_values) if mrr_values else 0

    if len(all_reports) > 1:
        variance = max(mrr_values) - min(mrr_values) if len(mrr_values) > 1 else 0
        print(f"  Aggregate MRR@{args.top_k}: {avg_mrr:.3f}  (variance: {variance:.3f})")
    else:
        variance = 0

    combined = {
        "repos": {name: r for name, r in all_reports.items()},
        "aggregate_mrr": round(avg_mrr, 3),
        "repo_variance": round(variance, 3),
    }

    if args.output:
        Path(args.output).write_text(json.dumps(combined, indent=2))
        print(f"\n  Report written to {args.output}")

    # Regression gate
    exit_code = 0
    if args.min_mrr is not None and avg_mrr < args.min_mrr:
        print(f"\n❌ REGRESSION: MRR {avg_mrr:.3f} < threshold {args.min_mrr:.3f}")
        exit_code = 1
    if args.max_repo_variance is not None and variance > args.max_repo_variance:
        print(f"\n❌ REGRESSION: repo variance {variance:.3f} > threshold {args.max_repo_variance:.3f}")
        exit_code = 1

    if exit_code == 0 and args.min_mrr is not None:
        print(f"\n✅ PASS: MRR {avg_mrr:.3f} ≥ threshold {args.min_mrr:.3f}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
