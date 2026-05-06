"""Functionality tests for ``_read_ingest_rate_limit_burst`` and
``_read_ingest_rate_limit_refill_per_sec`` (#216 Phase 2).

Locks: missing file → default; valid value → returned; out-of-range
or malformed → default (fail-soft / fail-closed-on-config-error).
"""

from __future__ import annotations

from pathlib import Path

from context import (
    _DEFAULT_INGEST_RATE_LIMIT_BURST,
    _DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC,
    _read_ingest_rate_limit_burst,
    _read_ingest_rate_limit_refill_per_sec,
)


def _write_config(tmp_path: Path, content: str) -> str:
    config_dir = tmp_path / ".bicameral"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(content, encoding="utf-8")
    return str(tmp_path)


# ── burst ────────────────────────────────────────────────────────────


def test_read_ingest_rate_limit_burst_defaults_when_config_missing(tmp_path: Path) -> None:
    assert _read_ingest_rate_limit_burst(str(tmp_path)) == _DEFAULT_INGEST_RATE_LIMIT_BURST
    assert _DEFAULT_INGEST_RATE_LIMIT_BURST == 10


def test_read_ingest_rate_limit_burst_honors_valid_yaml_value(tmp_path: Path) -> None:
    repo = _write_config(tmp_path, "ingest_rate_limit_burst: 25\n")
    assert _read_ingest_rate_limit_burst(repo) == 25


def test_read_ingest_rate_limit_burst_clamps_out_of_range(tmp_path: Path) -> None:
    repo_low = _write_config(tmp_path, "ingest_rate_limit_burst: 0\n")
    assert _read_ingest_rate_limit_burst(repo_low) == _DEFAULT_INGEST_RATE_LIMIT_BURST

    # Use a separate tmp dir so the two cases don't share config.
    high_dir = tmp_path / "high"
    high_dir.mkdir()
    repo_high = _write_config(high_dir, "ingest_rate_limit_burst: 99999\n")
    assert _read_ingest_rate_limit_burst(repo_high) == _DEFAULT_INGEST_RATE_LIMIT_BURST


# ── refill ───────────────────────────────────────────────────────────


def test_read_ingest_rate_limit_refill_defaults_when_config_missing(tmp_path: Path) -> None:
    assert (
        _read_ingest_rate_limit_refill_per_sec(str(tmp_path))
        == _DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC
    )
    assert _DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC == 1.0


def test_read_ingest_rate_limit_refill_honors_valid_yaml_value(tmp_path: Path) -> None:
    repo = _write_config(tmp_path, "ingest_rate_limit_refill_per_sec: 0.5\n")
    assert _read_ingest_rate_limit_refill_per_sec(repo) == 0.5


def test_read_ingest_rate_limit_refill_clamps_out_of_range(tmp_path: Path) -> None:
    # 0.0 would lock the bucket forever after first burst — fall back.
    repo_zero = _write_config(tmp_path, "ingest_rate_limit_refill_per_sec: 0.0\n")
    assert (
        _read_ingest_rate_limit_refill_per_sec(repo_zero)
        == _DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC
    )

    high_dir = tmp_path / "high"
    high_dir.mkdir()
    repo_high = _write_config(high_dir, "ingest_rate_limit_refill_per_sec: 1000.0\n")
    assert (
        _read_ingest_rate_limit_refill_per_sec(repo_high)
        == _DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC
    )
