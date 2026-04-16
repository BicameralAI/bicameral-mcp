#!/usr/bin/env python3
"""
Standalone code locator evaluation — no SurrealDB needed.

Measures end-to-end grounding quality of ground_mappings() against ground
truth decisions. Tests the full production pipeline: BM25 search → fuzzy
symbol matching → coverage loop tier broadening → code_region output.

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


def _is_relevant(region: dict, expected_symbols: set[str], expected_files: list[str]) -> bool:
    """Check if a grounded code_region is relevant (symbol match OR file pattern match)."""
    sym = region.get("symbol", "")
    fp = region.get("file_path", "")
    return sym in expected_symbols or any(pat in fp for pat in expected_files)


def evaluate(
    adapter,
    decisions: list[dict],
    top_k: int = 3,
    verbose: bool = False,
    use_description: bool = False,
    export_full: bool = False,
) -> dict:
    """Run ground_mappings for each decision, compare against ground truth.

    Uses the full production grounding pipeline (BM25 + fuzzy symbol matching +
    coverage loop) instead of raw search_code(), so recall measures actual
    symbol-level grounding accuracy.

    Args:
        use_description: If True, use the full description as query instead of keywords[0].
                         This matches the live ingest path behavior.
        export_full: If True, include all returned regions per decision (not just top-K).
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

        # Ground through the full pipeline: BM25 → fuzzy symbol → coverage tiers
        try:
            mapping = {"intent": query}
            resolved, _deferred = adapter.ground_mappings([mapping])
            code_regions = resolved[0].get("code_regions", []) if resolved else []
        except Exception as e:
            results.append({
                "description": d["description"][:80],
                "query": query,
                "error": str(e),
                "precision": 0, "recall": 0, "mrr": 0,
            })
            continue

        top_regions = code_regions[:top_k]
        all_regions = code_regions
        found_symbols = set()
        found_files = set()
        first_relevant_rank = None

        for rank, region in enumerate(top_regions):
            sym = region.get("symbol", "")
            fp = region.get("file_path", "")
            if sym:
                found_symbols.add(sym)
            found_files.add(fp)

            if _is_relevant(region, expected_symbols, expected_files) and first_relevant_rank is None:
                first_relevant_rank = rank + 1

        # Precision@k: fraction of top_k regions that are relevant
        relevant_in_top_k = sum(1 for r in top_regions if _is_relevant(r, expected_symbols, expected_files))
        irrelevant_in_top_k = len(top_regions) - relevant_in_top_k
        precision = relevant_in_top_k / len(top_regions) if top_regions else 0

        # Recall: fraction of expected symbols found in grounded regions
        matched_symbols = expected_symbols & found_symbols
        recall = len(matched_symbols) / len(expected_symbols) if expected_symbols else 0

        # MRR: 1/rank of first relevant region
        mrr = (1.0 / first_relevant_rank) if first_relevant_rank else 0

        # Check full region list for rank-overflow analysis
        first_relevant_full = None
        for rank, region in enumerate(all_regions):
            if _is_relevant(region, expected_symbols, expected_files):
                first_relevant_full = rank + 1
                break

        # Grounding tier (from coverage loop)
        grounding_tier = top_regions[0].get("grounding_tier") if top_regions else None

        # recall@files: fraction of expected_file_patterns covered by any
        # region in code_regions (not just top-K — grounding stores all).
        all_region_files = {r.get("file_path", "") for r in all_regions}
        if expected_files:
            matched_file_patterns = sum(
                1 for pat in expected_files
                if any(pat in fp for fp in all_region_files)
            )
            recall_at_files = matched_file_patterns / len(expected_files)
        else:
            recall_at_files = 0

        # File cardinality: how many distinct files appear in code_regions.
        file_cardinality = len(all_region_files - {""})

        is_multi_region = d.get("multi_region", False)

        entry = {
            "description": d["description"][:80],
            "query": query,
            "precision": round(precision, 2),
            "recall": round(recall, 2),
            "mrr": round(mrr, 2),
            "recall_at_files": round(recall_at_files, 2),
            "file_cardinality": file_cardinality,
            "multi_region": is_multi_region,
            "grounded": bool(code_regions),
            "grounding_tier": grounding_tier,
            "regions": len(top_regions),
            "total_regions": len(all_regions),
            "false_positives_in_top_k": irrelevant_in_top_k,
            "first_relevant_rank_full": first_relevant_full,
            "expected_symbols": list(expected_symbols),
            "expected_file_patterns": expected_files,
            "found_symbols": list(found_symbols),
            "found_files": list(found_files),
        }

        if export_full:
            entry["all_regions"] = [
                {
                    "rank": i + 1,
                    "file_path": r.get("file_path", ""),
                    "symbol": r.get("symbol", ""),
                    "type": r.get("type", ""),
                    "grounding_tier": r.get("grounding_tier"),
                    "relevant": _is_relevant(r, expected_symbols, expected_files),
                }
                for i, r in enumerate(all_regions)
            ]

        results.append(entry)

        if verbose:
            status = "hit" if mrr > 0 else ("grounded" if code_regions else "MISS")
            tier_str = f" tier={grounding_tier}" if grounding_tier is not None else ""
            mr_str = " [multi-region]" if is_multi_region else ""
            print(f"  [{status}{tier_str}]{mr_str} {entry['description']}")
            print(f"    query: {query}")
            print(f"    P@{top_k}={precision:.0%} R={recall:.0%} MRR={mrr:.2f} R@files={recall_at_files:.0%} files={file_cardinality} regions={len(code_regions)}")
            if recall == 0 and expected_symbols:
                print(f"    expected: {list(expected_symbols)[:3]}")
                print(f"    found:    {list(found_symbols)[:3]}")
                if first_relevant_full:
                    print(f"    (relevant region at rank {first_relevant_full} in full list)")
            if is_multi_region and recall_at_files < 1.0:
                missing = [p for p in expected_files if not any(p in fp for fp in all_region_files)]
                print(f"    missing file patterns: {missing}")

    # Aggregate
    n = len(results)
    if n == 0:
        return {"error": "No evaluable decisions", "results": []}

    avg_precision = sum(r.get("precision", 0) for r in results) / n
    avg_recall = sum(r.get("recall", 0) for r in results) / n
    avg_mrr = sum(r.get("mrr", 0) for r in results) / n
    hit_rate = sum(1 for r in results if r.get("mrr", 0) > 0) / n
    grounding_rate = sum(1 for r in results if r.get("grounded")) / n
    total_fp = sum(r.get("false_positives_in_top_k", 0) for r in results)
    total_top_k_slots = sum(r.get("regions", 0) for r in results)
    fp_rate = total_fp / total_top_k_slots if total_top_k_slots else 0
    rank_overflow_count = sum(
        1 for r in results
        if r.get("mrr", 0) == 0 and r.get("first_relevant_rank_full") is not None
    )

    # recall@files (all decisions)
    avg_recall_at_files = sum(r.get("recall_at_files", 0) for r in results) / n

    # recall@files (multi-region only)
    mr_results = [r for r in results if r.get("multi_region")]
    mr_count = len(mr_results)
    mr_recall_at_files = (
        sum(r.get("recall_at_files", 0) for r in mr_results) / mr_count
        if mr_count else 0
    )

    # File-cardinality distribution
    cardinalities = [r.get("file_cardinality", 0) for r in results]
    card_dist = {}
    for c in cardinalities:
        card_dist[c] = card_dist.get(c, 0) + 1

    # Tier distribution
    tier_counts = {}
    for r in results:
        t = r.get("grounding_tier")
        if t is not None:
            tier_counts[t] = tier_counts.get(t, 0) + 1

    return {
        "total_decisions": n,
        "avg_precision_at_k": round(avg_precision, 3),
        "avg_recall": round(avg_recall, 3),
        "mrr_at_k": round(avg_mrr, 3),
        "hit_rate": round(hit_rate, 3),
        "grounding_rate": round(grounding_rate, 3),
        "false_positive_rate": round(fp_rate, 3),
        "rank_overflow_count": rank_overflow_count,
        "avg_recall_at_files": round(avg_recall_at_files, 3),
        "multi_region_count": mr_count,
        "multi_region_recall_at_files": round(mr_recall_at_files, 3),
        "file_cardinality_distribution": dict(sorted(card_dist.items())),
        "tier_distribution": tier_counts,
        "top_k": top_k,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Code Locator E2E Grounding Evaluation")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[3]),
                        help="Path to repo (default: bicameral root)")
    parser.add_argument("--multi-repo", type=str, default=None,
                        help='JSON map of repo_name→path, e.g. \'{"medusa":"/path/to/medusa"}\'')
    parser.add_argument("--top-k", type=int, default=3, help="Top-K for precision/MRR")
    parser.add_argument("--min-mrr", type=float, default=None,
                        help="Minimum MRR threshold — exit non-zero if below (regression gate)")
    parser.add_argument("--min-recall", type=float, default=None,
                        help="Minimum recall threshold — exit non-zero if below (regression gate)")
    parser.add_argument("--max-repo-variance", type=float, default=None,
                        help="Maximum allowed variance in MRR across repos")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print per-decision results")
    parser.add_argument("--output", "-o", help="Write JSON report to file")
    parser.add_argument("--use-description", action="store_true",
                        help="Query with full description instead of keywords[0] (matches ingest behavior)")
    parser.add_argument("--export-full", action="store_true",
                        help="Include all regions per decision in JSON output (not just top-K)")
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

        print(f"  Code Locator E2E Evaluation -- {repo_name}")
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
        tier_dist = report.get("tier_distribution", {})
        tier_str = " ".join(f"T{k}={v}" for k, v in sorted(tier_dist.items()))
        card_dist = report.get("file_cardinality_distribution", {})
        card_str = " ".join(f"{k}f={v}" for k, v in sorted(card_dist.items()))
        mr_count = report.get("multi_region_count", 0)
        mr_raf = report.get("multi_region_recall_at_files", 0)
        print(f"\n{'='*55}")
        print(f"  [{repo_name}] (query mode: {query_mode})")
        print(f"  Precision@{args.top_k}:  {report['avg_precision_at_k']:.1%}")
        print(f"  Recall:        {report['avg_recall']:.1%}")
        print(f"  MRR@{args.top_k}:        {report['mrr_at_k']:.3f}")
        print(f"  Hit Rate:      {report['hit_rate']:.1%}")
        print(f"  Grounding:     {report['grounding_rate']:.1%}")
        print(f"  FP Rate:       {report['false_positive_rate']:.1%}")
        print(f"  Recall@Files:  {report['avg_recall_at_files']:.1%} (all) | {mr_raf:.1%} (multi-region, n={mr_count})")
        print(f"  File Cards:    {card_str or 'none'}")
        print(f"  Tiers:         {tier_str or 'none'}")
        print(f"  Rank Overflow: {report['rank_overflow_count']} (miss in top-{args.top_k}, hit in full list)")
        print(f"  Decisions:     {report['total_decisions']}")
        print(f"{'='*55}\n")

    # Aggregate across repos
    mrr_values = [r["mrr_at_k"] for r in all_reports.values()]
    recall_values = [r["avg_recall"] for r in all_reports.values()]
    avg_mrr = sum(mrr_values) / len(mrr_values) if mrr_values else 0
    avg_recall = sum(recall_values) / len(recall_values) if recall_values else 0

    if len(all_reports) > 1:
        variance = max(mrr_values) - min(mrr_values) if len(mrr_values) > 1 else 0
        print(f"  Aggregate MRR@{args.top_k}: {avg_mrr:.3f}  Recall: {avg_recall:.3f}  (variance: {variance:.3f})")
    else:
        variance = 0

    combined = {
        "repos": {name: r for name, r in all_reports.items()},
        "aggregate_mrr": round(avg_mrr, 3),
        "aggregate_recall": round(avg_recall, 3),
        "repo_variance": round(variance, 3),
    }

    if args.output:
        Path(args.output).write_text(json.dumps(combined, indent=2))
        print(f"\n  Report written to {args.output}")

    # Regression gate
    exit_code = 0
    if args.min_mrr is not None and avg_mrr < args.min_mrr:
        print(f"\n  REGRESSION: MRR {avg_mrr:.3f} < threshold {args.min_mrr:.3f}")
        exit_code = 1
    if args.min_recall is not None and avg_recall < args.min_recall:
        print(f"\n  REGRESSION: Recall {avg_recall:.3f} < threshold {args.min_recall:.3f}")
        exit_code = 1
    if args.max_repo_variance is not None and variance > args.max_repo_variance:
        print(f"\n  REGRESSION: repo variance {variance:.3f} > threshold {args.max_repo_variance:.3f}")
        exit_code = 1

    if exit_code == 0 and (args.min_mrr is not None or args.min_recall is not None):
        print(f"\n  PASS: MRR {avg_mrr:.3f}  Recall {avg_recall:.3f}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
