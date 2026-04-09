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
    """Initialize code locator adapter for a repo (fresh instance each call)."""
    os.environ["REPO_PATH"] = repo_path
    os.environ["CODE_LOCATOR_SQLITE_DB"] = str(Path(repo_path) / ".bicameral" / "code-graph.db")

    from adapters.code_locator import RealCodeLocatorAdapter
    adapter = RealCodeLocatorAdapter(repo_path=repo_path)
    adapter._ensure_initialized()
    return adapter


def _is_relevant(hit: dict, expected_symbols: set[str], expected_files: list[str]) -> bool:
    """Check if a single hit is relevant (symbol match OR file pattern match)."""
    sym = hit.get("symbol_name", "")
    fp = hit.get("file_path", "")
    return sym in expected_symbols or any(pat in fp for pat in expected_files)


def evaluate(
    adapter,
    decisions: list[dict],
    top_k: int = 3,
    verbose: bool = False,
    use_description: bool = False,
    export_full: bool = False,
) -> dict:
    """Run search_code for each decision, compare against ground truth.

    Args:
        use_description: If True, use the full description as query instead of keywords[0].
                         This matches the live ingest path behavior.
        export_full: If True, include all returned results per decision (not just top-K).
    """
    results = []

    for d in decisions:
        keywords = d.get("keywords", [])
        expected_symbols = set(d.get("expected_symbols", []))
        expected_files = d.get("expected_file_patterns", [])

        if use_description:
            query = d.get("description", "")
        else:
            if not keywords:
                continue
            query = keywords[0]

        if not query:
            continue

        try:
            hits = adapter.search_code(query)
        except Exception as e:
            results.append({
                "description": d["description"][:80],
                "query": query,
                "error": str(e),
                "precision": 0, "recall": 0, "mrr": 0,
            })
            continue

        top_hits = hits[:top_k]
        all_hits = hits  # full result list for analysis
        found_symbols = set()
        found_files = set()
        first_relevant_rank = None

        for rank, hit in enumerate(top_hits):
            sym = hit.get("symbol_name", "")
            fp = hit.get("file_path", "")
            found_symbols.add(sym)
            found_files.add(fp)

            if _is_relevant(hit, expected_symbols, expected_files) and first_relevant_rank is None:
                first_relevant_rank = rank + 1

        # Precision@k: fraction of top_k results that are relevant
        relevant_in_top_k = sum(1 for h in top_hits if _is_relevant(h, expected_symbols, expected_files))
        irrelevant_in_top_k = len(top_hits) - relevant_in_top_k
        precision = relevant_in_top_k / len(top_hits) if top_hits else 0

        # Recall: fraction of expected symbols found in top_k
        matched_symbols = expected_symbols & found_symbols
        recall = len(matched_symbols) / len(expected_symbols) if expected_symbols else 0

        # MRR: 1/rank of first relevant result
        mrr = (1.0 / first_relevant_rank) if first_relevant_rank else 0

        # Check full result list for rank-overflow analysis
        first_relevant_full = None
        for rank, hit in enumerate(all_hits):
            if _is_relevant(hit, expected_symbols, expected_files):
                first_relevant_full = rank + 1
                break

        entry = {
            "description": d["description"][:80],
            "query": query,
            "precision": round(precision, 2),
            "recall": round(recall, 2),
            "mrr": round(mrr, 2),
            "hits": len(top_hits),
            "total_hits": len(all_hits),
            "false_positives_in_top_k": irrelevant_in_top_k,
            "first_relevant_rank_full": first_relevant_full,
            "expected_symbols": list(expected_symbols),
            "expected_file_patterns": expected_files,
            "found_symbols": [h.get("symbol_name", "") for h in top_hits],
            "found_files": [h.get("file_path", "") for h in top_hits],
        }

        if export_full:
            entry["all_results"] = [
                {
                    "rank": i + 1,
                    "file_path": h.get("file_path", ""),
                    "symbol_name": h.get("symbol_name", ""),
                    "score": h.get("score", 0),
                    "method": h.get("method", ""),
                    "line_number": h.get("line_number", 0),
                    "relevant": _is_relevant(h, expected_symbols, expected_files),
                }
                for i, h in enumerate(all_hits)
            ]

        results.append(entry)

        if verbose:
            status = "hit" if mrr > 0 else "MISS"
            print(f"  [{status}] {entry['description']}")
            print(f"    query: {query}")
            print(f"    P@{top_k}={precision:.0%} R={recall:.0%} MRR={mrr:.2f} FP={irrelevant_in_top_k}/{len(top_hits)}")
            if mrr == 0:
                print(f"    expected: {list(expected_symbols)[:3]}")
                print(f"    got files: {[h.get('file_path','?').split('/')[-1] for h in top_hits[:3]]}")
                if first_relevant_full:
                    print(f"    (relevant result exists at rank {first_relevant_full} in full list)")

    # Aggregate
    n = len(results)
    if n == 0:
        return {"error": "No evaluable decisions", "results": []}

    avg_precision = sum(r.get("precision", 0) for r in results) / n
    avg_recall = sum(r.get("recall", 0) for r in results) / n
    avg_mrr = sum(r.get("mrr", 0) for r in results) / n
    hit_rate = sum(1 for r in results if r.get("mrr", 0) > 0) / n
    total_fp = sum(r.get("false_positives_in_top_k", 0) for r in results)
    total_top_k_slots = sum(r.get("hits", 0) for r in results)
    fp_rate = total_fp / total_top_k_slots if total_top_k_slots else 0
    rank_overflow_count = sum(
        1 for r in results
        if r.get("mrr", 0) == 0 and r.get("first_relevant_rank_full") is not None
    )

    return {
        "total_decisions": n,
        "avg_precision_at_k": round(avg_precision, 3),
        "avg_recall": round(avg_recall, 3),
        "mrr_at_k": round(avg_mrr, 3),
        "hit_rate": round(hit_rate, 3),
        "false_positive_rate": round(fp_rate, 3),
        "rank_overflow_count": rank_overflow_count,
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
    parser.add_argument("--use-description", action="store_true",
                        help="Query with full description instead of keywords[0] (matches ingest behavior)")
    parser.add_argument("--export-full", action="store_true",
                        help="Include all results per decision in JSON output (not just top-K)")
    args = parser.parse_args()

    if args.multi_repo:
        repo_map = json.loads(args.multi_repo)
    else:
        repo_map = {"default": args.repo}

    all_reports = {}
    for repo_name, repo_path in repo_map.items():
        # In multi-repo mode, filter decisions to those matching this repo
        if len(repo_map) > 1:
            repo_decisions = [
                d for d in ALL_DECISIONS
                if d.get("source_ref", "").startswith(repo_name)
            ]
        else:
            repo_decisions = ALL_DECISIONS

        print(f"📊 Code Locator Evaluation — {repo_name}")
        print(f"   Repo: {repo_path}")
        print(f"   Decisions: {len(repo_decisions)}")
        print(f"   Top-K: {args.top_k}")
        print()

        adapter = get_adapter(repo_path)
        report = evaluate(
            adapter,
            repo_decisions,
            top_k=args.top_k,
            verbose=args.verbose,
            use_description=args.use_description,
            export_full=args.export_full,
        )
        all_reports[repo_name] = report

        query_mode = "description" if args.use_description else "keywords[0]"
        print(f"\n{'='*50}")
        print(f"  [{repo_name}] (query mode: {query_mode})")
        print(f"  Precision@{args.top_k}:  {report['avg_precision_at_k']:.1%}")
        print(f"  Recall:        {report['avg_recall']:.1%}")
        print(f"  MRR@{args.top_k}:        {report['mrr_at_k']:.3f}")
        print(f"  Hit Rate:      {report['hit_rate']:.1%}")
        print(f"  FP Rate:       {report['false_positive_rate']:.1%}")
        print(f"  Rank Overflow: {report['rank_overflow_count']} (miss in top-{args.top_k}, hit in full list)")
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
