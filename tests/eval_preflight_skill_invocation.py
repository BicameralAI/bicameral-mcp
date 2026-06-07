#!/usr/bin/env python3
"""M_skill_preflight Step-0 CLI eval runner — emits aggregate JSON.

Drives ``tests/eval/_skill_invocation_judge.run_invocation_judgment``
over every row in ``tests/eval/preflight_skill_invocation_dataset.jsonl``
(15 rows after #306 Part B). Mirrors ``tests/eval_preflight_m6_recall.py``
shape: writes a structured JSON report that
``tests/eval_preflight_skill_summary.py`` renders to
``$GITHUB_STEP_SUMMARY``. The existing pytest runner
(``tests/eval/run_preflight_skill_invocation_eval.py``) stays for local
dev + assertion-style usage; this script is the CI-friendly equivalent.

Reports the 2x2 confusion matrix:

    | should_invoke | invoked | outcome                            |
    |---------------|---------|------------------------------------|
    | True          | True    | invoked_history_correctly (TP)     |
    | True          | False   | skipped_history_should_have (FN)   |
    | False         | True    | invoked_history_unnecessarily (FP) |
    | False         | False   | proceeded_without_fetch (TN)       |

Plus aggregate recall / precision on the should-invoke axis.

Usage:
    python tests/eval_preflight_skill_invocation.py
        -o test-results/skill-step0.json
        [--gate-mode warn|hard]
        [--min-recall 0.50]
        [--max-fp-rate 0.30]

Cache discipline: hits the same
``tests/eval/fixtures/skill_invocation_judge/`` fixture dir Part B
committed. CI runs cache-hits-only after the dataset + skill SHA
stabilize.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests" / "eval"))

from _gate import gate_exit_code  # noqa: E402
from _skill_invocation_judge import (  # noqa: E402
    DEFAULT_MODEL,
    classify_outcome,
    fixture_exists,
    run_invocation_judgment,
)

DATASET = REPO_ROOT / "tests" / "eval" / "preflight_skill_invocation_dataset.jsonl"


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
        default=0.50,
        help=(
            "Minimum recall on the should-invoke axis (TP / (TP + FN)). "
            "Default 0.50 — set by #306 acceptance: if below this, file a "
            "SKILL.md strengthening followup."
        ),
    )
    parser.add_argument(
        "--max-fp-rate",
        type=float,
        default=0.30,
        help=(
            "Maximum over-fetch rate on the should-skip axis (FP / (FP + TN)). "
            "Default 0.30 — over-fetching is wasted tokens; the skill should "
            "honor the negative controls."
        ),
    )
    parser.add_argument(
        "--catastrophic-recall",
        type=float,
        default=0.25,
        help="Hard floor (#537): should-invoke recall below this hard-fails CI in "
        "any gate mode (a collapsed invocation path, not LLM variance). Default 0.25.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("BICAMERAL_PREFLIGHT_INVOCATION_EVAL_MODEL", DEFAULT_MODEL),
        help="Anthropic model id (default: env or _skill_invocation_judge.DEFAULT_MODEL)",
    )
    args = parser.parse_args()

    rows = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())

    counts: dict[str, int] = {
        "invoked_history_correctly": 0,
        "skipped_history_should_have": 0,
        "invoked_history_unnecessarily": 0,
        "proceeded_without_fetch": 0,
        "skip": 0,
        "error": 0,
    }
    case_rows: list[dict] = []

    for row in rows:
        has_cache = fixture_exists(
            topic=row["topic"], seeded_decisions=row["seeded_decisions"], model=args.model
        )
        if not has_cache and not has_key:
            counts["skip"] += 1
            case_rows.append({"id": row["id"], "outcome": "skip"})
            continue
        try:
            judgment = run_invocation_judgment(
                case_id=row["id"],
                topic=row["topic"],
                seeded_decisions=row["seeded_decisions"],
                model=args.model,
            )
        except Exception as exc:  # noqa: BLE001
            counts["error"] += 1
            case_rows.append(
                {"id": row["id"], "outcome": "error", "error": f"{type(exc).__name__}: {exc}"}
            )
            continue

        outcome = classify_outcome(
            should_invoke=row["should_invoke_history"], invoked=judgment.invoked_history
        )
        counts[outcome] += 1
        case_rows.append(
            {
                "id": row["id"],
                "should_invoke_history": row["should_invoke_history"],
                "invoked_history": judgment.invoked_history,
                "submitted": judgment.submitted,
                "outcome": outcome,
                "reasoning": judgment.reasoning,
                "turns": judgment.turns,
                "tokens_in": judgment.tokens_in,
                "tokens_out": judgment.tokens_out,
            }
        )

    tp = counts["invoked_history_correctly"]
    fn = counts["skipped_history_should_have"]
    fp = counts["invoked_history_unnecessarily"]
    tn = counts["proceeded_without_fetch"]

    recall: float | None = (tp / (tp + fn)) if (tp + fn) > 0 else None  # should-invoke recall
    precision: float | None = (tp / (tp + fp)) if (tp + fp) > 0 else None
    fp_rate: float | None = (fp / (fp + tn)) if (fp + tn) > 0 else None

    breaches: list[str] = []
    if recall is not None and recall < args.min_recall:
        breaches.append(f"recall={recall:.3f} < {args.min_recall}")
    if fp_rate is not None and fp_rate > args.max_fp_rate:
        breaches.append(f"fp_rate={fp_rate:.3f} > {args.max_fp_rate}")

    # Catastrophic floor (#537): should-invoke recall collapse hard-fails any mode.
    catastrophic: list[str] = []
    if recall is not None and recall < args.catastrophic_recall:
        catastrophic.append(f"recall={recall:.3f} < catastrophic floor {args.catastrophic_recall}")

    payload = {
        "step": "step0_invocation",
        "model": args.model,
        "gate_mode": args.gate_mode,
        "min_recall": args.min_recall,
        "max_fp_rate": args.max_fp_rate,
        "catastrophic_recall": args.catastrophic_recall,
        "summary": {
            "total": len(rows),
            "counts": counts,
            "tp": tp,
            "fn": fn,
            "fp": fp,
            "tn": tn,
            "recall": recall,
            "precision": precision,
            "fp_rate": fp_rate,
            "gate_breaches": breaches,
            "catastrophic_breaches": catastrophic,
        },
        "rows": case_rows,
    }

    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return gate_exit_code(
        quality_breaches=breaches,
        catastrophic_breaches=catastrophic,
        gate_mode=args.gate_mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
