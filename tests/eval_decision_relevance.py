#!/usr/bin/env python3
"""M1 decision-relevance eval — measures ungrounded rate end-to-end.

Ingests each transcript in the fixture registry (TRANSCRIPT_SOURCES) into a
fresh in-memory ledger, records how many decisions ground to real code, and
emits a per-transcript + per-repo JSON report. Used as a CI regression gate
(warn-only initially) and as the ruler for Phase 5 skill-spec A/B.

Usage:
    .venv/bin/python tests/eval_decision_relevance.py \\
        --multi-repo '{"adversarial": "test-results/.repos/medusa"}' \\
        --skill-variant from-skill-md \\
        -o test-results/m1-adversarial.json

Flags:
    --multi-repo      JSON map repo_key -> repo path. Only source_refs whose
                      TRANSCRIPT_SOURCES.repo_key is in this map will run.
    --source-ref      Restrict to a single transcript (debugging).
    --skill-variant   'none'          : ingest fixture decisions directly
                                        (pure grounding-pipeline test).
                      'from-skill-md' : run headless LLM extraction from the
                                        current .claude/skills/bicameral-ingest/
                                        SKILL.md, then ingest the result.
                                        (Phase 4 — not implemented yet.)
    --min-grounded-pct    Regression gate. Exit non-zero if below (aggregate).
    --max-repo-variance   Regression gate. Exit non-zero if repo variance
                          exceeds threshold.
    -o / --output         Write JSON report to this path.
    --verbose             Print per-decision rows.

The fixture is the single source of truth for corpus + oracle. Adding a new
transcript = one entry in TRANSCRIPT_SOURCES. No runner changes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

# Ensure pilot/mcp is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.expected.decisions import ALL_DECISIONS, TRANSCRIPT_SOURCES

# MCP repo root — used to resolve fixture-relative transcript paths.
MCP_ROOT = Path(__file__).resolve().parents[1]


def _load_transcript(rel_path: str) -> str:
    """Resolve a transcript path relative to the MCP repo root."""
    p = (MCP_ROOT / rel_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"transcript not found: {p}")
    return p.read_text(encoding="utf-8")


def _decisions_for_source_ref(source_ref: str) -> list[dict]:
    return [d for d in ALL_DECISIONS if d.get("source_ref") == source_ref]


def _build_payload_from_fixture(source_ref: str) -> dict:
    """Build an ingest payload from the fixture oracle for this source_ref.

    Used by --skill-variant none. Each fixture entry's `description` becomes
    one decision. The runner bypasses the LLM extraction stage and feeds these
    directly into handle_ingest so we're measuring the grounding pipeline
    (code_graph.ground_mappings + ledger) in isolation.
    """
    fixture_decisions = _decisions_for_source_ref(source_ref)
    return {
        "source": "transcript",
        "title": source_ref,
        "decisions": [{"description": d["description"]} for d in fixture_decisions],
    }


def _build_payload_from_skill_md(
    transcript_text: str, source_ref: str
) -> tuple[dict, list[dict]]:
    """Call the headless extraction driver (Step 1 of the current SKILL.md)
    and shape the result as a natural-format ingest payload.

    Returns ``(payload, extracted_decisions)`` so the caller can also compute
    extraction precision/recall against a pregenerated ground-truth fixture.
    """
    # tests/ has no __init__.py; import as a sibling module via the dir on sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _extract_headless import extract_from_current_skill  # type: ignore[import-not-found]

    extracted = extract_from_current_skill(
        transcript_text,
        source_ref=source_ref,
    )
    decisions = [
        {"description": d.get("description", "")}
        for d in (extracted.get("decisions") or [])
        if d.get("description")
    ]
    action_items = [
        # IngestPayload contract requires owner: str — coerce None/missing → ""
        {"text": a.get("text", ""), "owner": a.get("owner") or ""}
        for a in (extracted.get("action_items") or [])
        if a.get("text")
    ]
    payload = {
        "source": "transcript",
        "title": source_ref,
        "decisions": decisions,
        "action_items": action_items,
    }
    return payload, decisions


async def _ingest_one(
    ctx,
    source_ref: str,
    transcript_text: str,
    skill_variant: str,
) -> dict:
    """Run a single transcript through handle_ingest and return per-decision rows."""
    from handlers.ingest import handle_ingest

    # tests/ has no __init__.py; import metrics helper as sibling module.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _extraction_metrics import (  # type: ignore[import-not-found]
        compute_extraction_metrics,
        load_fixture,
    )

    extracted_decisions: list[dict] = []
    if skill_variant == "none":
        payload = _build_payload_from_fixture(source_ref)
    elif skill_variant == "from-skill-md":
        payload, extracted_decisions = _build_payload_from_skill_md(
            transcript_text, source_ref
        )
    else:
        raise ValueError(f"unknown skill-variant: {skill_variant!r}")

    # Tag the payload with the source_ref so per-transcript queries work cleanly.
    payload["title"] = source_ref

    result = await handle_ingest(ctx, payload)
    stats = result.stats

    m2_fixture_size = len(_decisions_for_source_ref(source_ref))

    # Extraction precision/recall vs pregenerated ground truth (only meaningful
    # for --skill-variant from-skill-md; the "none" variant uses the fixture as
    # its input, so comparing it against itself would be tautological).
    if skill_variant == "from-skill-md":
        ground_truth = load_fixture(source_ref)
        extraction_metrics = compute_extraction_metrics(
            extracted_decisions, ground_truth
        )
    else:
        extraction_metrics = {"skipped": True, "reason": "not applicable in this variant"}

    return {
        "source_ref": source_ref,
        "intents_created": stats.intents_created,
        "grounded": stats.grounded,
        "ungrounded": stats.ungrounded,
        "grounded_pct": round(stats.grounded_pct, 4),
        "grounding_deferred": stats.grounding_deferred,
        "ungrounded_decisions": list(result.ungrounded_decisions),
        "m2_fixture_size": m2_fixture_size,
        "extraction_metrics": extraction_metrics,
    }


async def _run_repo(
    repo_key: str,
    repo_path: str,
    source_refs: list[str],
    skill_variant: str,
    surreal_url: str,
    verbose: bool,
) -> dict:
    """Run all transcripts for a single repo under a fresh ctx/ledger."""
    from adapters.ledger import reset_ledger_singleton
    from context import BicameralContext

    os.environ["REPO_PATH"] = str(Path(repo_path).resolve())
    os.environ["SURREAL_URL"] = surreal_url
    os.environ.setdefault("USE_REAL_LEDGER", "1")

    per_transcript: list[dict] = []

    for source_ref in source_refs:
        src = TRANSCRIPT_SOURCES[source_ref]
        transcript_text = _load_transcript(src["transcript"])

        # Fresh ledger per transcript — prevents cross-contamination.
        reset_ledger_singleton()
        ctx = BicameralContext.from_env()
        if hasattr(ctx.ledger, "connect"):
            await ctx.ledger.connect()

        row = await _ingest_one(ctx, source_ref, transcript_text, skill_variant)
        per_transcript.append(row)

        if verbose:
            print(
                f"  [{repo_key}] {source_ref}: "
                f"{row['grounded']}/{row['intents_created']} "
                f"grounded ({row['grounded_pct']:.0%})"
            )

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _extraction_metrics import aggregate_extraction_metrics  # type: ignore[import-not-found]

    totals = {
        "intents_created": sum(r["intents_created"] for r in per_transcript),
        "grounded": sum(r["grounded"] for r in per_transcript),
        "ungrounded": sum(r["ungrounded"] for r in per_transcript),
    }
    totals["grounded_pct"] = round(
        (totals["grounded"] / totals["intents_created"]) if totals["intents_created"] else 0.0,
        4,
    )

    extraction_aggregate = aggregate_extraction_metrics(
        [r["extraction_metrics"] for r in per_transcript]
    )

    return {
        "repo_key": repo_key,
        "repo_path": str(Path(repo_path).resolve()),
        "transcripts": per_transcript,
        "totals": totals,
        "extraction_metrics": extraction_aggregate,
    }


async def run(args) -> tuple[dict, int]:
    repo_map: dict[str, str] = json.loads(args.multi_repo) if args.multi_repo else {}
    if not repo_map:
        print("error: --multi-repo is required", file=sys.stderr)
        return {}, 2

    # Build work plan: group source_refs by repo_key, honoring --source-ref filter.
    plan: dict[str, list[str]] = defaultdict(list)
    skipped_source_refs: list[str] = []

    for source_ref, src in TRANSCRIPT_SOURCES.items():
        if args.source_ref and source_ref != args.source_ref:
            continue
        if src["repo_key"] not in repo_map:
            skipped_source_refs.append(source_ref)
            continue
        plan[src["repo_key"]].append(source_ref)

    if not plan:
        print(
            f"error: no transcripts to run (skipped: {skipped_source_refs})",
            file=sys.stderr,
        )
        return {}, 2

    print(f"M1 decision-relevance eval (skill-variant={args.skill_variant})")
    print(f"  repos: {list(plan.keys())}")
    print(f"  transcripts: {sum(len(v) for v in plan.values())}")
    if skipped_source_refs:
        print(f"  skipped (no repo in --multi-repo): {len(skipped_source_refs)}")
    print()

    repo_reports: list[dict] = []
    for repo_key, source_refs in plan.items():
        report = await _run_repo(
            repo_key=repo_key,
            repo_path=repo_map[repo_key],
            source_refs=source_refs,
            skill_variant=args.skill_variant,
            surreal_url=args.surreal_url,
            verbose=args.verbose,
        )
        repo_reports.append(report)
        t = report["totals"]
        ex = report.get("extraction_metrics", {})
        extraction_summary = ""
        if not ex.get("skipped", True):
            extraction_summary = (
                f" | extraction P={ex['precision']:.2f} R={ex['recall']:.2f} F1={ex['f1']:.2f}"
            )
        print(
            f"  [{repo_key}] {t['grounded']}/{t['intents_created']} "
            f"grounded ({t['grounded_pct']:.0%}){extraction_summary}"
        )

    # Aggregate grounding counts.
    total_intents = sum(r["totals"]["intents_created"] for r in repo_reports)
    total_grounded = sum(r["totals"]["grounded"] for r in repo_reports)
    aggregate_pct = (total_grounded / total_intents) if total_intents else 0.0
    per_repo_pcts = [r["totals"]["grounded_pct"] for r in repo_reports]
    repo_variance = (
        round(max(per_repo_pcts) - min(per_repo_pcts), 4) if len(per_repo_pcts) > 1 else 0.0
    )

    # Aggregate extraction metrics across all scored transcripts (ignoring
    # repo boundaries — precision/recall of the skill is a global property).
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _extraction_metrics import aggregate_extraction_metrics  # type: ignore[import-not-found]
    all_extraction_rows = [
        t["extraction_metrics"]
        for r in repo_reports
        for t in r["transcripts"]
    ]
    aggregate_extraction = aggregate_extraction_metrics(all_extraction_rows)

    combined = {
        "skill_variant": args.skill_variant,
        "repos": {r["repo_key"]: r for r in repo_reports},
        "aggregate": {
            "intents_created": total_intents,
            "grounded": total_grounded,
            "grounded_pct": round(aggregate_pct, 4),
            "repo_variance": repo_variance,
            "per_repo_grounded_pct": {
                r["repo_key"]: r["totals"]["grounded_pct"] for r in repo_reports
            },
            "extraction": aggregate_extraction,
        },
        "skipped_source_refs": skipped_source_refs,
    }

    print()
    print(
        f"  aggregate: {total_grounded}/{total_intents} "
        f"grounded ({aggregate_pct:.0%}, variance={repo_variance:.3f})"
    )
    if not aggregate_extraction.get("skipped", True):
        # Peek at any scored transcript to report which matcher was used
        # (all transcripts in a run use the same matcher per --skill-variant).
        sample_matcher = next(
            (
                t["extraction_metrics"].get("matcher", "?")
                for r in repo_reports
                for t in r["transcripts"]
                if not t["extraction_metrics"].get("skipped", False)
            ),
            "?",
        )
        print(
            f"  extraction ({sample_matcher}): "
            f"P={aggregate_extraction['precision']:.2f} "
            f"R={aggregate_extraction['recall']:.2f} "
            f"F1={aggregate_extraction['f1']:.2f} "
            f"(TP={aggregate_extraction['true_positives']} "
            f"FP={aggregate_extraction['false_positives']} "
            f"FN={aggregate_extraction['false_negatives']}, "
            f"scored={aggregate_extraction['scored_transcripts']}/"
            f"{sum(len(r['transcripts']) for r in repo_reports)})"
        )
    else:
        print(f"  extraction: skipped ({aggregate_extraction.get('reason', '')})")

    # Regression gates.
    exit_code = 0
    if args.min_grounded_pct is not None and aggregate_pct < args.min_grounded_pct:
        print(
            f"\n❌ REGRESSION: grounded_pct {aggregate_pct:.3f} "
            f"< threshold {args.min_grounded_pct:.3f}",
            file=sys.stderr,
        )
        exit_code = 1
    if args.max_repo_variance is not None and repo_variance > args.max_repo_variance:
        print(
            f"\n❌ REGRESSION: repo variance {repo_variance:.3f} "
            f"> threshold {args.max_repo_variance:.3f}",
            file=sys.stderr,
        )
        exit_code = 1
    if exit_code == 0 and args.min_grounded_pct is not None:
        print(
            f"\n✅ PASS: grounded_pct {aggregate_pct:.3f} "
            f"≥ threshold {args.min_grounded_pct:.3f}"
        )

    return combined, exit_code


def main():
    parser = argparse.ArgumentParser(description="M1 decision-relevance evaluation")
    parser.add_argument(
        "--multi-repo",
        type=str,
        required=True,
        help='JSON map repo_key -> path, e.g. \'{"medusa":"test-results/.repos/medusa"}\'',
    )
    parser.add_argument(
        "--source-ref",
        type=str,
        default=None,
        help="Restrict to a single source_ref (debugging).",
    )
    parser.add_argument(
        "--skill-variant",
        type=str,
        default="none",
        choices=["none", "from-skill-md"],
        help="'none' = ingest fixture decisions directly; 'from-skill-md' = Phase 4 LLM extraction.",
    )
    parser.add_argument(
        "--min-grounded-pct",
        type=float,
        default=None,
        help="Regression gate: fail if aggregate grounded_pct below this.",
    )
    parser.add_argument(
        "--max-repo-variance",
        type=float,
        default=None,
        help="Regression gate: fail if repo variance exceeds this.",
    )
    parser.add_argument(
        "--surreal-url",
        type=str,
        default="memory://",
        help="SurrealDB URL (default memory:// for isolated eval).",
    )
    parser.add_argument("--output", "-o", help="Write JSON report to file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Per-decision rows")
    args = parser.parse_args()

    combined, exit_code = asyncio.run(run(args))

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(combined, indent=2))
        print(f"\n  report written to {out}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
