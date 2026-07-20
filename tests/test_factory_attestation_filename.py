from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "validate_factory_attestation.py"
SPEC = importlib.util.spec_from_file_location("factory_attestation", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AttestationFilenameTests(unittest.TestCase):
    def test_accepts_legacy_and_per_run_attestation_names(self) -> None:
        commit = "a" * 40
        self.assertTrue(MODULE.valid_attestation_filename(f"{commit}.json", commit))
        self.assertTrue(MODULE.valid_attestation_filename(f"{commit}.release-v0.1.12.json", commit))

    def test_rejects_empty_or_unsafe_run_ids(self) -> None:
        commit = "a" * 40
        self.assertFalse(MODULE.valid_attestation_filename(f"{commit}..json", commit))
        self.assertFalse(
            MODULE.valid_attestation_filename(f"{commit}.release/v0.1.12.json", commit)
        )


if __name__ == "__main__":
    unittest.main()
