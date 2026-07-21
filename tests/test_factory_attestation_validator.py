"""Tests for per-PR Factory attestation filenames."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / ".github/scripts/validate_factory_attestation.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("factory_attestation_validator", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_per_run_and_legacy_names_are_accepted(tmp_path: Path, monkeypatch):
    validator = _load_validator()
    commit = "a" * 40
    directory = tmp_path / "factory-attestations"
    directory.mkdir()
    monkeypatch.setattr(validator, "load_json", lambda _path: ({"factory_commit": commit}, []))
    monkeypatch.setattr(validator, "validate", lambda *_args: [])

    assert validator.validate_file(directory / f"{commit}.json", set(), None) == []
    assert (
        validator.validate_file(
            directory / f"{commit}.fix-736-bounded-manual-grant.json", set(), None
        )
        == []
    )


def test_unrelated_filename_is_rejected(tmp_path: Path, monkeypatch):
    validator = _load_validator()
    commit = "b" * 40
    directory = tmp_path / "factory-attestations"
    directory.mkdir()
    monkeypatch.setattr(validator, "load_json", lambda _path: ({"factory_commit": commit}, []))
    monkeypatch.setattr(validator, "validate", lambda *_args: [])

    errors = validator.validate_file(directory / "unrelated.json", set(), None)
    assert len(errors) == 1
    assert "<run-id>.json" in errors[0]
