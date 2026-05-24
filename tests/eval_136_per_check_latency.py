"""M_per_check_latency — wedge measurement for #136 + #431.

Measures cold + warm per-call latency on the three transforms the
content cache wraps (or will wrap):

- ``codegenome.drift_classifier.classify_drift``
- ``codegenome.diff_categorizer.categorize_diff``
- ``governance.engine.evaluate``

For each transform + input category, runs N iterations cold (cache
empty) and N iterations warm (cache pre-populated). Reports p50/p95/p99
in milliseconds plus a warm/cold ratio.

The gate threshold derives from Jin's 2026-05-20 comment on #136:

    Median per-check latency on cosmetic diff target: ≤ 10ms
    (blob-SHA equality, no LLM)

So the gate is: **warm p50 ≤ 10ms** on the cosmetic input category, for
every transform with the cache wired in. Transforms that haven't been
wired yet are measured but exempt from the gate (warn-only).

CLI shape mirrors ``tests/eval_shadow_parity.py``:

    python tests/eval_136_per_check_latency.py
    python tests/eval_136_per_check_latency.py --gate-mode hard
    python tests/eval_136_per_check_latency.py -o test-results/m136.json

Out of scope for this eval:

- End-to-end ``resolve_compliance`` sweep latency — covered by
  ``tests/test_sweep_latency_with_cache.py`` (planned Step 5).
- LLM-judge latency — that's #136's Phase 2 deliverable, not yet scoped.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from codegenome._content_cache import (  # noqa: E402
    ContentCache,
    _reset_default_cache_for_tests,
)

# ── Corpus ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DriftInput:
    """One classify_drift input row."""

    category: str
    old_body: str
    new_body: str
    old_signature_hash: str | None
    new_signature_hash: str | None
    old_neighbors: frozenset[str] | None
    new_neighbors: frozenset[str] | None
    language: str


@dataclass(frozen=True)
class DiffInput:
    """One categorize_diff input row."""

    category: str
    old_body: str
    new_body: str
    language: str


def _cosmetic_drift_input() -> DriftInput:
    """Whitespace/comment-only change — the case the cache must trivially
    short-circuit. Cosmetic ratio = 1.0; classifier verdict = cosmetic."""
    return DriftInput(
        category="cosmetic",
        old_body='def f():\n    """Old docstring."""\n    return 1\n',
        new_body='def f():\n    """New docstring."""\n    return 1\n',
        old_signature_hash="sig_abc",
        new_signature_hash="sig_abc",
        old_neighbors=frozenset({"helper_a", "helper_b"}),
        new_neighbors=frozenset({"helper_a", "helper_b"}),
        language="python",
    )


def _structural_drift_input() -> DriftInput:
    """Logic-bearing change (return value flips, new callee). Classifier
    will compute all four signals, including the relatively expensive
    diff_categorizer step. This is the category that proves the cache
    earns its keep — uncached takes real CPU work."""
    return DriftInput(
        category="structural",
        old_body=("def compute(x):\n    if x > 0:\n        return x * 2\n    return 0\n"),
        new_body=("def compute(x):\n    if x > 0:\n        return helper(x)\n    return 0\n"),
        old_signature_hash="sig_compute_v1",
        new_signature_hash="sig_compute_v1",
        old_neighbors=frozenset({"caller_a"}),
        new_neighbors=frozenset({"caller_a", "helper"}),
        language="python",
    )


def _large_drift_input() -> DriftInput:
    """100+ line function body — representative of real production code.
    The categorize_diff helper runs difflib O(N²) on line count, so
    larger bodies cost real CPU. This is where the cache earns its keep."""
    base_lines = [
        "def long_function(",
        "    param_a: str,",
        "    param_b: int,",
        "    param_c: dict[str, int] | None = None,",
        ") -> tuple[str, int]:",
        '    """A representatively-sized function for benchmarking."""',
    ]
    body_lines = [f"    step_{i} = compute_step({i}, param_a)" for i in range(80)]
    tail_lines = [
        "    result_str = str(step_0)",
        "    result_int = sum(int(s) for s in [step_1, step_2, step_3])",
        "    return result_str, result_int",
    ]
    old_body = "\n".join(base_lines + body_lines + tail_lines) + "\n"
    # Cosmetic-only edit: add one comment line, change a docstring.
    new_lines = base_lines.copy()
    new_lines[5] = '    """A representatively-sized function for benchmarking — updated docs."""'
    new_body = "\n".join(new_lines + ["    # added comment"] + body_lines + tail_lines) + "\n"
    return DriftInput(
        category="large_cosmetic",
        old_body=old_body,
        new_body=new_body,
        old_signature_hash="sig_long_v1",
        new_signature_hash="sig_long_v1",
        old_neighbors=frozenset({"compute_step", "caller_x", "caller_y"}),
        new_neighbors=frozenset({"compute_step", "caller_x", "caller_y"}),
        language="python",
    )


def _unsupported_drift_input() -> DriftInput:
    """Language not in _SUPPORTED_LANGUAGES — classifier short-circuits
    to verdict=uncertain at the top of the function. This category
    measures the baseline overhead of the cache wrapper itself."""
    return DriftInput(
        category="unsupported",
        old_body="(defn f [] (+ 1 2))",
        new_body="(defn f [] (+ 1 3))",
        old_signature_hash=None,
        new_signature_hash=None,
        old_neighbors=None,
        new_neighbors=None,
        language="clojure",
    )


def _cosmetic_diff_input() -> DiffInput:
    return DiffInput(
        category="cosmetic",
        old_body='def f():\n    """Old docstring."""\n    return 1\n',
        new_body='def f():\n    """New docstring."""\n    return 1\n',
        language="python",
    )


def _structural_diff_input() -> DiffInput:
    return DiffInput(
        category="structural",
        old_body=("def compute(x):\n    if x > 0:\n        return x * 2\n    return 0\n"),
        new_body=("def compute(x):\n    if x > 0:\n        return helper(x)\n    return 0\n"),
        language="python",
    )


# ── Measurement ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LatencyStats:
    """Latency distribution for one (transform, category, phase) cell."""

    n: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float

    @classmethod
    def from_samples(cls, samples_seconds: list[float]) -> LatencyStats:
        samples_ms = [s * 1000 for s in samples_seconds]
        samples_ms.sort()
        n = len(samples_ms)
        return cls(
            n=n,
            p50_ms=samples_ms[n // 2],
            p95_ms=samples_ms[min(n - 1, int(n * 0.95))],
            p99_ms=samples_ms[min(n - 1, int(n * 0.99))],
            mean_ms=statistics.mean(samples_ms),
        )


@dataclass(frozen=True)
class TransformResult:
    """All measurements for one transform."""

    name: str
    cache_wired: bool
    cold: dict[str, LatencyStats]
    warm: dict[str, LatencyStats]
    breaches: list[str] = field(default_factory=list)


_GATE_WARM_P50_MS = 10.0  # Jin 2026-05-20 target


def _time_one(fn: Callable[..., Any]) -> float:
    """Single call latency in seconds. Uses perf_counter for ns precision."""
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def _fresh_cache_env() -> tuple[Path, Path]:
    """Create an isolated cache dir + db path; reset the module-level
    default so the next decorator call picks it up. Returns (dir, db)."""
    tmpdir = Path(tempfile.mkdtemp(prefix="m136_eval_"))
    db = tmpdir / "cache.db"
    os.environ["BICAMERAL_CONTENT_CACHE_PATH"] = str(db)
    _reset_default_cache_for_tests()
    return tmpdir, db


def _measure_cold(fn: Callable[..., Any], n: int) -> LatencyStats:
    """N truly cold calls — fresh cache env per sample.

    Each iteration resets the cache before timing, so every sample
    measures a real cold-path latency (compute + cache insert, no
    prior entry). Costs more wall time than warm measurement because of
    the per-iteration tempdir mkdir, but accurate.
    """
    samples: list[float] = []
    for _ in range(n):
        _fresh_cache_env()
        samples.append(_time_one(fn))
    return LatencyStats.from_samples(samples)


def _measure_warm(fn: Callable[..., Any], n: int) -> LatencyStats:
    """N warm calls — one fresh cache env, prime with one call, then
    measure N subsequent calls. Every sample is a cache hit (or a
    cache-wrapper passthrough on un-wired transforms)."""
    _fresh_cache_env()
    fn()  # prime: populates the cache (or runs compute if not wired)
    samples = [_time_one(fn) for _ in range(n)]
    return LatencyStats.from_samples(samples)


# ── Per-transform measurement ──────────────────────────────────────────


def _measure_classify_drift(n: int) -> TransformResult:
    """Cold vs warm latency on classify_drift across three categories.

    'Cold' = cache reset + first call per input; the function runs the
    full structural-classification path.
    'Warm' = same input called N more times; cache returns the stored
    DriftClassification.
    """
    from codegenome.drift_classifier import classify_drift

    # Cache-wired detection: check the inner cached function's marker
    # attribute. The public classify_drift is the normalization wrapper;
    # the @content_cached decoration lives on _classify_drift_cached.
    try:
        from codegenome.drift_classifier import _classify_drift_cached

        cache_wired = hasattr(_classify_drift_cached, "_content_cached_version")
    except ImportError:
        cache_wired = False

    inputs = [
        _cosmetic_drift_input(),
        _structural_drift_input(),
        _large_drift_input(),
        _unsupported_drift_input(),
    ]

    cold: dict[str, LatencyStats] = {}
    warm: dict[str, LatencyStats] = {}

    for inp in inputs:

        def _call(inp=inp) -> Any:
            return classify_drift(
                inp.old_body,
                inp.new_body,
                old_signature_hash=inp.old_signature_hash,
                new_signature_hash=inp.new_signature_hash,
                old_neighbors=inp.old_neighbors,
                new_neighbors=inp.new_neighbors,
                language=inp.language,
            )

        # Cold sampling is expensive (tempdir per iter) — use n/10 samples
        cold[inp.category] = _measure_cold(_call, max(5, n // 10))
        warm[inp.category] = _measure_warm(_call, n)

    breaches: list[str] = []
    if cache_wired:
        for category in ("cosmetic", "structural"):
            if warm[category].p50_ms > _GATE_WARM_P50_MS:
                breaches.append(
                    f"classify_drift[{category}]: warm p50 "
                    f"{warm[category].p50_ms:.2f}ms > gate {_GATE_WARM_P50_MS}ms"
                )

    return TransformResult(
        name="classify_drift",
        cache_wired=cache_wired,
        cold=cold,
        warm=warm,
        breaches=breaches,
    )


def _measure_categorize_diff(n: int) -> TransformResult:
    from codegenome.diff_categorizer import categorize_diff

    cache_wired = hasattr(categorize_diff, "_content_cached_version") or hasattr(
        getattr(categorize_diff, "__wrapped__", None), "_content_cached_version"
    )

    # Re-use the large-body fixture from classify_drift since it's the
    # same shape (old_body, new_body, language). Avoids fixture drift.
    large = _large_drift_input()
    large_diff = DiffInput(
        category="large_cosmetic",
        old_body=large.old_body,
        new_body=large.new_body,
        language=large.language,
    )
    inputs = [_cosmetic_diff_input(), _structural_diff_input(), large_diff]
    cold: dict[str, LatencyStats] = {}
    warm: dict[str, LatencyStats] = {}

    for inp in inputs:

        def _call(inp=inp) -> Any:
            return categorize_diff(inp.old_body, inp.new_body, inp.language)

        cold[inp.category] = _measure_cold(_call, max(5, n // 10))
        warm[inp.category] = _measure_warm(_call, n)

    breaches: list[str] = []
    if cache_wired:
        for category in ("cosmetic", "structural"):
            if warm[category].p50_ms > _GATE_WARM_P50_MS:
                breaches.append(
                    f"categorize_diff[{category}]: warm p50 "
                    f"{warm[category].p50_ms:.2f}ms > gate {_GATE_WARM_P50_MS}ms"
                )

    return TransformResult(
        name="categorize_diff",
        cache_wired=cache_wired,
        cold=cold,
        warm=warm,
        breaches=breaches,
    )


def _measure_governance_evaluate(n: int) -> TransformResult:
    """Light input — governance.evaluate is already cheap (~ms uncached)
    so the cache demonstrates marginal lift, not the headline win."""
    from governance.config import GovernanceConfig
    from governance.contracts import (
        GovernanceFinding,
        GovernanceMetadata,
    )
    from governance.engine import evaluate

    try:
        from governance.engine import _evaluate_cached

        cache_wired = hasattr(_evaluate_cached, "_content_cached_version")
    except ImportError:
        cache_wired = False

    finding = GovernanceFinding(
        finding_id="00000000-0000-0000-0000-000000000001",
        decision_id="decision:eval_bench",
        region_id="region:eval_bench",
        decision_class="implementation_preference",
        risk_class="low",
        escalation_class="warn",
        source="drift",
        semantic_status="likely_drift",
        confidence={"drift_confidence": 0.7, "binding_confidence": 0.9},
        explanation="bench",
        evidence_refs=["score:0.700"],
    )
    metadata = GovernanceMetadata(
        decision_class="implementation_preference",
        risk_class="low",
        escalation_class="warn",
    )
    config = GovernanceConfig()

    cold: dict[str, LatencyStats] = {}
    warm: dict[str, LatencyStats] = {}

    for category in ("typical",):

        def _call() -> Any:
            return evaluate(
                finding=finding,
                metadata=metadata,
                config=config,
                decision_status="ratified",
                bypass_recency_seconds=None,
            )

        cold[category] = _measure_cold(_call, max(5, n // 10))
        warm[category] = _measure_warm(_call, n)

    breaches: list[str] = []
    if cache_wired:
        if warm["typical"].p50_ms > _GATE_WARM_P50_MS:
            breaches.append(
                f"governance.evaluate[typical]: warm p50 "
                f"{warm['typical'].p50_ms:.2f}ms > gate {_GATE_WARM_P50_MS}ms"
            )

    return TransformResult(
        name="governance.evaluate",
        cache_wired=cache_wired,
        cold=cold,
        warm=warm,
        breaches=breaches,
    )


# ── Render ─────────────────────────────────────────────────────────────


def _render_text(results: list[TransformResult], passed: bool) -> str:
    lines = ["=" * 72]
    lines.append("M_per_check_latency (#136 wedge)")
    lines.append("=" * 72)
    for r in results:
        wired_label = "WIRED" if r.cache_wired else "BASELINE (not wired)"
        lines.append(f"\n{r.name}  [{wired_label}]")
        for category in sorted(set(list(r.cold.keys()) + list(r.warm.keys()))):
            cold = r.cold.get(category)
            warm = r.warm.get(category)
            if cold is None or warm is None:
                continue
            ratio = warm.p50_ms / cold.p50_ms if cold.p50_ms > 0 else float("inf")
            lines.append(
                f"  {category:<12} cold p50={cold.p50_ms:7.3f}ms  "
                f"warm p50={warm.p50_ms:7.3f}ms  "
                f"ratio={ratio:6.3f}  (n={cold.n} per phase)"
            )
    lines.append("")
    lines.append(
        f"Gate: warm p50 ≤ {_GATE_WARM_P50_MS}ms on (cosmetic, structural) for wired transforms"
    )
    lines.append(f"Result: {'PASS' if passed else 'FAIL'}")
    return "\n".join(lines)


def _serialize(r: TransformResult) -> dict[str, Any]:
    def cells(d: dict[str, LatencyStats]) -> dict[str, Any]:
        return {
            k: {
                "n": v.n,
                "p50_ms": round(v.p50_ms, 4),
                "p95_ms": round(v.p95_ms, 4),
                "p99_ms": round(v.p99_ms, 4),
                "mean_ms": round(v.mean_ms, 4),
            }
            for k, v in d.items()
        }

    return {
        "name": r.name,
        "cache_wired": r.cache_wired,
        "cold": cells(r.cold),
        "warm": cells(r.warm),
        "breaches": r.breaches,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="M_per_check_latency — wedge measurement (#136)")
    ap.add_argument("--gate-mode", choices=("warn", "hard"), default="warn")
    ap.add_argument("--iterations", "-n", type=int, default=100)
    ap.add_argument("-o", "--output", help="Write JSON results to this path")
    args = ap.parse_args()

    results = [
        _measure_classify_drift(args.iterations),
        _measure_categorize_diff(args.iterations),
        _measure_governance_evaluate(args.iterations),
    ]

    all_breaches: list[str] = []
    for r in results:
        all_breaches.extend(r.breaches)
    passed = not all_breaches

    print(_render_text(results, passed))

    if all_breaches:
        sys.stderr.write("\nBreaches:\n")
        for b in all_breaches:
            sys.stderr.write(f"  - {b}\n")

    if args.output:
        out_payload = {
            "gate_mode": args.gate_mode,
            "gate_warm_p50_ms": _GATE_WARM_P50_MS,
            "passed": passed,
            "breaches": all_breaches,
            "iterations": args.iterations,
            "results": [_serialize(r) for r in results],
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(out_payload, indent=2))
        sys.stderr.write(f"\nWrote JSON: {args.output}\n")

    if args.gate_mode == "hard" and not passed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
