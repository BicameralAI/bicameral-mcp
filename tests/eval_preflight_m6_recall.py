#!/usr/bin/env python3
"""M6 preflight retrieval recall eval — measures whether ``handle_preflight``
surfaces the intended decision given a developer's topic + file_paths (#58).

Built per the wiki's optimization principle ("identify the specific scenario,
then find the optimization that improves efficiency without regressing recall
below an acceptable threshold"): ship the measurement first, get a baseline,
then decide which Phase B optimization direction the data picks.

For each fixture row, the runner:

  1. Seeds a fresh memory:// ledger with the intended decision (via the real
     handle_ingest path so the seeded row has realistic shape — source_type,
     status, signoff, optional binds_to).
  2. Drives ``handle_preflight(topic, file_paths)`` against that ledger.
  3. Classifies outcome:
        surfaced   — intended decision in ``response.decisions``
        missed     — intended decision NOT in response
        error      — runner exception (infra; not an agent miss)
  4. Aggregates per miss-mode + overall.

Three axes (deliberately split for diagnosis, per the plan):
  - Overall recall = surfaced / total
  - Per-miss-mode recall (vocabulary_mismatch / unbound_decision /
                          transitive_relevance) → picks the Phase B direction
  - Fire rate = response.fired == True (secondary diagnostic)

Default gates (provisional):
  - overall recall  ≥ 0.70  (wiki's M6 signal threshold)
  - per-mode recall ≥ 0.50  (no category catastrophically broken)
  - fire rate       ≥ 0.60

Usage:
    .venv/bin/python tests/eval_preflight_m6_recall.py
        --gate-mode warn
        -o test-results/m6-preflight-recall.json

Flags:
    --miss-mode-filter  Run only one category (debug)
    --case-id           Run a single case by id (debug)
    --min-recall        Gate threshold (default 0.70)
    --min-per-mode-recall Gate per category (default 0.50)
    --min-fire-rate     Gate (default 0.60)
    --gate-mode         'warn' (advisory, default) | 'hard' (exit non-zero on miss)
    -o / --output       Write JSON report
    --verbose           Print per-case rows
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests" / "eval"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures" / "preflight_m6"))

from _preflight_m6_seeder import seed_m6_case_into_fresh_ctx  # type: ignore[import-not-found]  # noqa: E402, I001
from dataset import ALL_CASES, GENERATOR_VERSION, M6Case, cases_by_miss_mode  # type: ignore[import-not-found]  # noqa: E402, I001


# ── Outcome classification ──────────────────────────────────────────────


def classify_outcome(case: M6Case, response: Any, intended_decision_id: str) -> str:
    """Map (case, response, seeded_decision_id) → outcome.

    Pure post-hoc classifier. ``intended_decision_id`` is the decision_id
    the seeder produced for this case's intended_description; we look for
    it in ``response.decisions``.
    """
    if response is None:
        return "error"
    decisions = getattr(response, "decisions", None) or []
    surfaced_ids = {getattr(d, "decision_id", "") for d in decisions}
    if intended_decision_id and intended_decision_id in surfaced_ids:
        return "surfaced"
    return "missed"


# ── Per-case row + aggregator ───────────────────────────────────────────


def _per_case_row(
    case: M6Case,
    response: Any,
    intended_decision_id: str,
    outcome: str,
    error_msg: str | None = None,
) -> dict[str, Any]:
    decisions = getattr(response, "decisions", None) or [] if response else []
    sources = getattr(response, "sources_chained", None) or [] if response else []
    fired = bool(getattr(response, "fired", False)) if response else False
    return {
        "case_id": case.case_id,
        "miss_mode": case.miss_mode,
        "topic": case.topic,
        "intended_description": case.intended_description,
        "intended_decision_id": intended_decision_id,
        "intended_file_path": case.intended_file_path,
        "file_paths": list(case.file_paths),
        "decision_status": case.decision_status,
        "outcome": outcome,
        "fired": fired,
        "sources_chained": list(sources),
        "n_decisions_surfaced": len(decisions),
        "surfaced_decision_ids": [getattr(d, "decision_id", "") for d in decisions],
        "error_msg": error_msg or "",
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    by_outcome: dict[str, int] = defaultdict(int)
    by_mode_total: dict[str, int] = defaultdict(int)
    by_mode_surfaced: dict[str, int] = defaultdict(int)
    fired_count = 0

    for r in rows:
        by_outcome[r["outcome"]] += 1
        by_mode_total[r["miss_mode"]] += 1
        if r["outcome"] == "surfaced":
            by_mode_surfaced[r["miss_mode"]] += 1
        if r["fired"]:
            fired_count += 1

    # Use .get() not [] to avoid defaultdict auto-creating zero-count keys
    # that would then leak into the output's `outcomes` dict.
    surfaced = by_outcome.get("surfaced", 0)
    missed = by_outcome.get("missed", 0)
    error = by_outcome.get("error", 0)

    # Recall denominator excludes errors (infra failures, not agent misses).
    evaluable = surfaced + missed
    recall = (surfaced / evaluable) if evaluable > 0 else 0.0
    fire_rate = (fired_count / total) if total > 0 else 0.0

    per_mode: dict[str, dict[str, Any]] = {}
    for mode in sorted(by_mode_total):
        mode_total = by_mode_total[mode]
        mode_surfaced = by_mode_surfaced[mode]
        # Error rows in a mode shouldn't drag its recall — but error counts
        # in the bucket so reviewers see them.
        mode_errors = sum(1 for r in rows if r["miss_mode"] == mode and r["outcome"] == "error")
        mode_evaluable = mode_total - mode_errors
        per_mode[mode] = {
            "total": mode_total,
            "surfaced": mode_surfaced,
            "errors": mode_errors,
            "recall": round((mode_surfaced / mode_evaluable), 4) if mode_evaluable > 0 else 0.0,
        }

    return {
        "total_cases": total,
        "outcomes": dict(by_outcome),
        "recall": round(recall, 4),
        "fire_rate": round(fire_rate, 4),
        "per_miss_mode": per_mode,
        "error_count": error,
    }


# ── Runner ──────────────────────────────────────────────────────────────


async def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    cases: list[M6Case] = list(ALL_CASES)
    if args.miss_mode_filter:
        cases = cases_by_miss_mode(args.miss_mode_filter)
        if not cases:
            print(f"no cases match --miss-mode-filter {args.miss_mode_filter!r}", file=sys.stderr)
            return {}, 2
    if args.case_id:
        cases = [c for c in cases if c.case_id == args.case_id]
        if not cases:
            print(f"no case matches --case-id {args.case_id!r}", file=sys.stderr)
            return {}, 2

    rows: list[dict[str, Any]] = []
    for case in cases:
        try:
            ctx, intended_decision_id, response = await seed_m6_case_into_fresh_ctx(case)
        except Exception as exc:
            print(f"ERROR seeding {case.case_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            rows.append(
                _per_case_row(
                    case,
                    None,
                    "",
                    "error",
                    error_msg=f"seed: {type(exc).__name__}: {exc}",
                )
            )
            continue

        outcome = classify_outcome(case, response, intended_decision_id)
        row = _per_case_row(case, response, intended_decision_id, outcome)
        rows.append(row)
        if args.verbose:
            print(
                f"  {case.case_id:<40}  {outcome:<10}  fired={row['fired']}  "
                f"n_surfaced={row['n_decisions_surfaced']}"
            )

    summary = _aggregate(rows)
    summary["generator_version"] = GENERATOR_VERSION
    summary["gate_mode"] = args.gate_mode

    # Gate enforcement
    breaches: list[str] = []
    if summary["recall"] < args.min_recall:
        breaches.append(f"overall recall {summary['recall']:.3f} < {args.min_recall}")
    for mode, stats in summary["per_miss_mode"].items():
        if stats["recall"] < args.min_per_mode_recall:
            breaches.append(f"{mode} recall {stats['recall']:.3f} < {args.min_per_mode_recall}")
    if summary["fire_rate"] < args.min_fire_rate:
        breaches.append(f"fire_rate {summary['fire_rate']:.3f} < {args.min_fire_rate}")
    summary["gate_breaches"] = breaches

    report = {"summary": summary, "rows": rows}

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.output}")

    print()
    print(f"M6 preflight retrieval recall eval — {summary['total_cases']} cases")
    print(f"  overall recall : {summary['recall']:.3f}  (gate ≥ {args.min_recall})")
    print(f"  fire rate      : {summary['fire_rate']:.3f}  (gate ≥ {args.min_fire_rate})")
    print(f"  errors         : {summary['error_count']}")
    for mode in sorted(summary["per_miss_mode"]):
        stats = summary["per_miss_mode"][mode]
        print(
            f"  {mode:<22} : recall {stats['recall']:.3f}  "
            f"({stats['surfaced']}/{stats['total']} surfaced, {stats['errors']} errors)"
        )
    if breaches:
        print(f"  ⚠ gate breaches: {'; '.join(breaches)}")
    else:
        print("  ✓ all gates pass")

    if breaches and args.gate_mode == "hard":
        return report, 1
    return report, 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    p.add_argument(
        "--miss-mode-filter",
        choices=("vocabulary_mismatch", "unbound_decision", "transitive_relevance"),
    )
    p.add_argument("--case-id", help="run a single case by id (debug)")
    p.add_argument("--min-recall", type=float, default=0.70)
    p.add_argument("--min-per-mode-recall", type=float, default=0.50)
    p.add_argument("--min-fire-rate", type=float, default=0.60)
    p.add_argument("--gate-mode", choices=("warn", "hard"), default="warn")
    p.add_argument("-o", "--output", help="write JSON report to this path")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    _, exit_code = asyncio.run(run(args))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
