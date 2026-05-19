#!/usr/bin/env python3
"""M_skill_preflight Step-1 CLI eval runner — emits aggregate JSON.

Drives ``tests/eval/_skill_judge.judge_relevance`` over every row in
``tests/eval/preflight_skill_dataset.jsonl`` (25 rows after PR #396).
Mirrors ``tests/eval_preflight_m6_recall.py`` shape: writes a structured
JSON report that ``tests/eval_preflight_skill_summary.py`` renders to
``$GITHUB_STEP_SUMMARY``. The existing pytest runner
(``tests/eval/run_preflight_skill_eval.py``) stays for local dev +
assertion-style usage; this script is the CI-friendly equivalent.

Usage:
    python tests/eval_preflight_skill_step1.py
        -o test-results/skill-step1.json
        [--gate-mode warn|hard]
        [--min-recall 0.70]

Cache discipline: hits the same ``tests/eval/fixtures/skill_judge/``
fixture dir Part A committed. CI runs cache-hits-only after the dataset
+ skill SHA stabilize.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests" / "eval"))

from _skill_judge import DEFAULT_MODEL, fixture_exists, judge_relevance  # noqa: E402

DATASET = REPO_ROOT / "tests" / "eval" / "preflight_skill_dataset.jsonl"


def _axis_prefix(row_id: str) -> str:
    """Map a row id like ``M1_synonym_throttling`` → ``M1``.

    Three axes plus direct-match anchors per the Part A spec:
    M1 = vocab_mismatch, M4 = ungrounded, FF1 = false_fire, D = direct_match.
    """
    return row_id.split("_", 1)[0]


def _classify(row: dict, judgment: dict[str, Any]) -> str:
    """Returns ``hit`` if the LLM's chosen feature groups satisfy the row's
    expectations, else ``miss``. ``error`` is reserved for harness
    exceptions caught at the call site."""
    chosen = set(judgment.get("relevant_features") or [])
    expect_rel = set(row["expect_relevant"])
    expect_irrel = set(row.get("expect_strict_irrelevant") or [])
    if expect_rel - chosen:
        return "miss"
    if chosen & expect_irrel:
        return "miss"
    return "hit"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path, help="Write JSON report")
    parser.add_argument(
        "--gate-mode",
        choices=("warn", "hard"),
        default="warn",
        help="warn (default) = advisory; hard = exit non-zero on gate breach",
    )
    parser.add_argument(
        "--min-recall",
        type=float,
        default=0.70,
        help="Per-axis recall gate (default 0.70 per the wiki M6 signal threshold)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("BICAMERAL_PREFLIGHT_EVAL_MODEL", DEFAULT_MODEL),
        help="Anthropic model id (default: env BICAMERAL_PREFLIGHT_EVAL_MODEL or _skill_judge.DEFAULT_MODEL)",
    )
    args = parser.parse_args()

    rows = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())

    per_axis: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "hit": 0, "miss": 0, "skip": 0, "error": 0}
    )
    case_rows: list[dict] = []

    for row in rows:
        prefix = _axis_prefix(row["id"])
        per_axis[prefix]["total"] += 1

        has_cache = fixture_exists(topic=row["topic"], ledger=row["ledger"], model=args.model)
        if not has_cache and not has_key:
            per_axis[prefix]["skip"] += 1
            case_rows.append({"id": row["id"], "axis": prefix, "outcome": "skip"})
            continue
        try:
            judgment = judge_relevance(topic=row["topic"], ledger=row["ledger"], model=args.model)
        except Exception as exc:  # noqa: BLE001
            per_axis[prefix]["error"] += 1
            case_rows.append(
                {
                    "id": row["id"],
                    "axis": prefix,
                    "outcome": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        outcome = _classify(row, judgment)
        per_axis[prefix][outcome] += 1
        case_rows.append(
            {
                "id": row["id"],
                "axis": prefix,
                "outcome": outcome,
                "chosen": sorted(judgment.get("relevant_features") or []),
                "expected_relevant": sorted(row["expect_relevant"]),
                "expected_strict_irrelevant": sorted(row.get("expect_strict_irrelevant") or []),
            }
        )

    total = len(rows)
    hits = sum(a["hit"] for a in per_axis.values())
    misses = sum(a["miss"] for a in per_axis.values())
    skips = sum(a["skip"] for a in per_axis.values())
    errors = sum(a["error"] for a in per_axis.values())
    scored = hits + misses
    overall_recall: float | None = (hits / scored) if scored > 0 else None

    per_axis_summary: dict[str, dict[str, Any]] = {}
    breaches: list[str] = []
    for axis, counts in per_axis.items():
        scored_axis = counts["hit"] + counts["miss"]
        recall: float | None = (counts["hit"] / scored_axis) if scored_axis > 0 else None
        per_axis_summary[axis] = {**counts, "recall": recall}
        if recall is not None and recall < args.min_recall:
            breaches.append(f"axis={axis} recall={recall:.3f} < {args.min_recall}")

    payload = {
        "step": "step1_relevance",
        "model": args.model,
        "gate_mode": args.gate_mode,
        "min_recall": args.min_recall,
        "summary": {
            "total": total,
            "hits": hits,
            "misses": misses,
            "skips": skips,
            "errors": errors,
            "recall": overall_recall,
            "per_axis": per_axis_summary,
            "gate_breaches": breaches,
        },
        "rows": case_rows,
    }

    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.gate_mode == "hard" and breaches:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
