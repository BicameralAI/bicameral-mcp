"""Regression tests for intentionally tracked governance-boundary files."""

from scripts import validate_governance_boundary as guard


def test_reviewed_bicameral_governance_files_are_allowed():
    roots = guard.all_roots()

    for path in guard.TRACKED_BICAMERAL_ALLOWLIST:
        assert guard.forbidding_root(path, roots) is None

    attestation = ".bicameral/factory-attestations/example.json"
    assert guard.forbidding_root(attestation, roots) is None


def test_unlisted_bicameral_local_state_remains_forbidden():
    roots = guard.all_roots()

    assert guard.forbidding_root(".bicameral/factory-context.local.json", roots) == ".bicameral/"
