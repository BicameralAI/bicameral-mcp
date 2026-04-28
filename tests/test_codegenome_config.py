"""Phase 1 unit tests — codegenome.config feature flag loading."""

from __future__ import annotations

import pytest

from codegenome.config import CodeGenomeConfig


_ALL_FLAGS = (
    "BICAMERAL_CODEGENOME_ENABLED",
    "BICAMERAL_CODEGENOME_WRITE_IDENTITY_RECORDS",
    "BICAMERAL_CODEGENOME_ENHANCE_DRIFT",
    "BICAMERAL_CODEGENOME_ENHANCE_SEARCH",
    "BICAMERAL_CODEGENOME_EXPOSE_EVIDENCE_PACKETS",
    "BICAMERAL_CODEGENOME_CHAMBER_EVALUATIONS",
    "BICAMERAL_CODEGENOME_BENCHMARK_MODE",
)


@pytest.fixture(autouse=True)
def _clear_flags(monkeypatch):
    for name in _ALL_FLAGS:
        monkeypatch.delenv(name, raising=False)
    yield


def test_default_is_all_off():
    cfg = CodeGenomeConfig.from_env()
    assert cfg.enabled is False
    assert cfg.write_identity_records is False
    assert cfg.enhance_drift is False
    assert cfg.enhance_search is False
    assert cfg.expose_evidence_packets is False
    assert cfg.chamber_evaluations is False
    assert cfg.benchmark_mode is False


def test_explicit_construction_defaults():
    cfg = CodeGenomeConfig()
    assert cfg.enabled is False
    assert cfg.write_identity_records is False


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "on"])
def test_truthy_values_enable_flag(monkeypatch, value):
    monkeypatch.setenv("BICAMERAL_CODEGENOME_ENABLED", value)
    assert CodeGenomeConfig.from_env().enabled is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  ", "garbage"])
def test_falsy_or_unknown_values_keep_flag_off(monkeypatch, value):
    monkeypatch.setenv("BICAMERAL_CODEGENOME_ENABLED", value)
    assert CodeGenomeConfig.from_env().enabled is False


def test_each_flag_loads_from_its_own_env_var(monkeypatch):
    monkeypatch.setenv("BICAMERAL_CODEGENOME_WRITE_IDENTITY_RECORDS", "1")
    cfg = CodeGenomeConfig.from_env()
    assert cfg.write_identity_records is True
    assert cfg.enabled is False
    assert cfg.enhance_drift is False
    assert cfg.benchmark_mode is False


def test_identity_writes_active_requires_both_flags():
    assert CodeGenomeConfig().identity_writes_active() is False
    assert CodeGenomeConfig(enabled=True).identity_writes_active() is False
    assert CodeGenomeConfig(write_identity_records=True).identity_writes_active() is False
    assert CodeGenomeConfig(
        enabled=True, write_identity_records=True,
    ).identity_writes_active() is True
