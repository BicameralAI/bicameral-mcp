from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "scripts" / "validate_factory_attestation.py"
)
SPEC = importlib.util.spec_from_file_location("validate_factory_attestation", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)

FACTORY_COMMIT = "96a4d8be8517d97fb73a129117ea79277d3e6e44"


def test_accepts_collision_safe_run_id_filename() -> None:
    assert VALIDATOR.valid_commit_grounded_filename(
        f"{FACTORY_COMMIT}.agent-seed-runtime-conformance.json", FACTORY_COMMIT
    )


def test_accepts_existing_legacy_filename() -> None:
    assert VALIDATOR.valid_commit_grounded_filename(f"{FACTORY_COMMIT}.json", FACTORY_COMMIT)


def test_rejects_missing_or_invalid_run_id() -> None:
    assert not VALIDATOR.valid_commit_grounded_filename(f"{FACTORY_COMMIT}..json", FACTORY_COMMIT)
    assert not VALIDATOR.valid_commit_grounded_filename(
        f"{FACTORY_COMMIT}.bad/run.json", FACTORY_COMMIT
    )
    assert not VALIDATOR.valid_commit_grounded_filename(
        "different-commit.agent-seed-runtime-conformance.json", FACTORY_COMMIT
    )
