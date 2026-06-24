"""Deterministic v0 user-flow replay gate (mcp#628)."""

from __future__ import annotations

import pytest

from scripts.sim_issue_108_flows import (
    EXPECTED_COMMAND_SEQUENCE,
    FORBIDDEN_LEGACY_COMMANDS,
    READ_OR_ADVISORY_COMMANDS,
    run_replay,
)


@pytest.mark.asyncio
async def test_issue_108_replay_emits_expected_toolrequest_sequence():
    result = await run_replay()

    assert result.command_sequence == list(EXPECTED_COMMAND_SEQUENCE)
    assert FORBIDDEN_LEGACY_COMMANDS.isdisjoint(result.command_sequence)


@pytest.mark.asyncio
async def test_issue_108_replay_preserves_typed_failures_and_non_mutation():
    result = await run_replay()
    by_command = dict(zip(result.command_sequence, result.responses, strict=True))

    assert by_command["binding.inspect"]["status"] == "stale"
    assert by_command["evidence.refresh"]["status"] == "content_changed"
    assert by_command["search.query"]["status"] == "not_found"

    for command in READ_OR_ADVISORY_COMMANDS:
        payload = by_command[command]["result"]
        assert payload.get("mutation") in (None, "none")

    evidence = by_command["evidence.refresh"]["result"]
    assert evidence["signoff_mutated"] is False
    assert evidence["compliance_mutated"] is False
    assert evidence["binding_evidence_mutated"] is False
