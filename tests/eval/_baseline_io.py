"""Baseline file IO + regression-rule enforcement for cost/latency eval.

Read/write semantics:
- One JSONL file: ``tests/eval/cost_baseline.jsonl``
- Each row keyed on ``(metric, recorded_on)`` plus optional ``n_features`` for C1
- ``recorded_on`` is ``any`` for platform-agnostic metrics (token counts
  via tiktoken + JSON serialization are deterministic across OSes), or
  ``darwin`` / ``linux`` / ``windows`` for platform-specific metrics
  (latency, since wall-clock varies with hardware/scheduler)
- ``_baseline_version`` field on every row; bumping the constant in this
  module invalidates all rows and forces re-record

Modes:
- Default: read the matching row, assert current values are within threshold
- ``BICAMERAL_EVAL_RECORD_BASELINE=1``: write/update the row, no assertion

Regression rule (asymmetric — only flags regressions, not improvements):
1. If ``current <= baseline``: pass (improvement or unchanged)
2. If ``|current - baseline| < noise_floor``: pass (delta in noise)
3. If ``|current - baseline| / baseline > 0.20``: fail (real regression)
4. Else: pass

Noise floors: tokens 10 (deterministic, but tolerate small generator tweaks),
latency 0.5ms (OS scheduler + GC jitter on non-realtime kernels).
"""
from __future__ import annotations

import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path


BASELINE_VERSION = "1"
RELATIVE_THRESHOLD = 0.20
TOKEN_NOISE_FLOOR = 10
LATENCY_NOISE_FLOOR_MS = 0.5

# Platform-agnostic sentinel — used for metrics whose value doesn't depend
# on host OS / hardware (token counts, byte counts via deterministic
# JSON+tiktoken). A row with ``recorded_on=ANY_PLATFORM`` matches every
# host. Latency metrics still record their actual platform.
ANY_PLATFORM = "any"

BASELINE_PATH = Path(__file__).resolve().parent / "cost_baseline.jsonl"


def current_platform() -> str:
    """Return canonical platform tag: ``darwin`` / ``linux`` / ``windows``."""
    sys_name = platform.system().lower()
    if sys_name in {"darwin", "linux", "windows"}:
        return sys_name
    return sys_name  # pragma: no cover — unrecognized platform passes through


def is_recording() -> bool:
    return os.getenv("BICAMERAL_EVAL_RECORD_BASELINE", "").strip().lower() in {"1", "true", "yes"}


def load_baselines(path: Path = BASELINE_PATH) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def write_baselines(rows: list[dict], path: Path = BASELINE_PATH) -> None:
    """Sorted, stable-key JSONL output to keep diffs minimal."""
    def _sort_key(row: dict) -> tuple:
        return (
            row.get("metric", ""),
            row.get("recorded_on", ""),
            row.get("n_features", -1),
        )
    rows_sorted = sorted(rows, key=_sort_key)
    body = "\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows_sorted)
    path.write_text(body + "\n", encoding="utf-8")


def find_baseline(
    rows: list[dict],
    *,
    metric: str,
    recorded_on: str,
    n_features: int | None = None,
) -> dict | None:
    """Find a row matching ``metric`` + ``n_features``, where ``recorded_on``
    matches either the current platform or the ``ANY_PLATFORM`` sentinel.
    Platform-agnostic rows therefore validate on every host without needing
    a separate row per OS."""
    for row in rows:
        if row.get("metric") != metric:
            continue
        row_platform = row.get("recorded_on")
        if row_platform != ANY_PLATFORM and row_platform != recorded_on:
            continue
        if n_features is not None and row.get("n_features") != n_features:
            continue
        return row
    return None


def upsert_baseline(
    rows: list[dict],
    new_row: dict,
    key_fields: tuple[str, ...] = ("metric", "recorded_on", "n_features"),
) -> list[dict]:
    """Replace a matching row in place, or append if not found.

    Match is on the values of ``key_fields`` in ``new_row``. Returns a new
    list (does not mutate ``rows`` in place).
    """
    new_key = tuple(new_row.get(f) for f in key_fields)
    out: list[dict] = []
    replaced = False
    for row in rows:
        existing_key = tuple(row.get(f) for f in key_fields)
        if existing_key == new_key:
            out.append(new_row)
            replaced = True
        else:
            out.append(row)
    if not replaced:
        out.append(new_row)
    return out


def regression_check(
    *,
    field: str,
    current: float,
    baseline: float,
    noise_floor: float,
    relative_threshold: float = RELATIVE_THRESHOLD,
) -> str | None:
    """Asymmetric regression check for one numeric field.

    Returns an error message (string) if regressed beyond threshold, else
    ``None``. Never flags improvements (current <= baseline).
    """
    if current <= baseline:
        return None
    delta = current - baseline
    if delta < noise_floor:
        return None
    if baseline <= 0:
        # No sensible relative comparison — fall back to absolute floor only.
        return (
            f"{field}: regressed past noise floor — "
            f"current {current:g} vs baseline {baseline:g} (Δ {delta:g} ≥ floor {noise_floor:g})"
        )
    relative = delta / baseline
    if relative > relative_threshold:
        return (
            f"{field}: baseline drift exceeded threshold — "
            f"current {current:g} vs baseline {baseline:g} "
            f"(+{relative * 100:.1f}%, Δ {delta:g} above floor {noise_floor:g}). "
            f"Re-record with BICAMERAL_EVAL_RECORD_BASELINE=1 if intentional."
        )
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
