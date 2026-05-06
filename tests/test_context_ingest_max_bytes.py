"""Functionality tests for ``_read_ingest_max_bytes`` (#216 Phase 1).

Locks the precedence: missing file → default; valid value → returned;
out-of-range → default (fail-closed); malformed yaml → default (fail-soft).
"""

from __future__ import annotations

from pathlib import Path

from context import _DEFAULT_INGEST_MAX_BYTES, _read_ingest_max_bytes


def _write_config(tmp_path: Path, content: str) -> str:
    """Write ``.bicameral/config.yaml`` under ``tmp_path``; return repo path."""
    config_dir = tmp_path / ".bicameral"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(content, encoding="utf-8")
    return str(tmp_path)


def test_read_ingest_max_bytes_defaults_when_config_missing(tmp_path: Path) -> None:
    # No config file at all.
    assert _read_ingest_max_bytes(str(tmp_path)) == _DEFAULT_INGEST_MAX_BYTES
    assert _DEFAULT_INGEST_MAX_BYTES == 1024 * 1024


def test_read_ingest_max_bytes_honors_valid_yaml_value(tmp_path: Path) -> None:
    repo = _write_config(tmp_path, "ingest_max_bytes: 524288\n")
    assert _read_ingest_max_bytes(repo) == 524288


def test_read_ingest_max_bytes_clamps_below_minimum(tmp_path: Path) -> None:
    # 100 bytes is below the 1 KiB minimum — fall back to default.
    repo = _write_config(tmp_path, "ingest_max_bytes: 100\n")
    assert _read_ingest_max_bytes(repo) == _DEFAULT_INGEST_MAX_BYTES


def test_read_ingest_max_bytes_clamps_above_maximum(tmp_path: Path) -> None:
    # 1 GB is well past the 64 MiB maximum — operator-footgun protection.
    repo = _write_config(tmp_path, "ingest_max_bytes: 999999999\n")
    assert _read_ingest_max_bytes(repo) == _DEFAULT_INGEST_MAX_BYTES


def test_read_ingest_max_bytes_falls_back_on_non_integer(tmp_path: Path) -> None:
    repo = _write_config(tmp_path, 'ingest_max_bytes: "not-an-int"\n')
    assert _read_ingest_max_bytes(repo) == _DEFAULT_INGEST_MAX_BYTES
