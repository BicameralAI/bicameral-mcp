"""#543 — record-id validation before SurrealQL interpolation."""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.client import LedgerError
from ledger.queries import (
    _validated_record_id,
    decision_exists,
    get_decision_description,
    get_decisions_for_span,
    get_region_descriptor,
    input_span_exists,
    region_exists,
)


class _RecordingClient:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict | None]] = []

    async def query(self, sql: str, params=None):
        self.queries.append((sql, params))
        return []


def test_validated_record_id_rejects_injection_shapes() -> None:
    assert _validated_record_id("decision:abc-123", "decision") == "decision:abc-123"

    for value in (
        "decision:abc; DELETE decision",
        "decision:abc SET status='reflected'",
        "decision:",
        "code_region:abc",
    ):
        with pytest.raises(LedgerError):
            _validated_record_id(value, "decision")


@pytest.mark.parametrize(
    ("func", "bad_id"),
    (
        (decision_exists, "decision:abc; DELETE decision"),
        (get_decision_description, "decision:abc; DELETE decision"),
        (get_decisions_for_span, "input_span:abc; DELETE input_span"),
        (input_span_exists, "input_span:abc; DELETE input_span"),
        (region_exists, "code_region:abc; DELETE code_region"),
        (get_region_descriptor, "code_region:abc; DELETE code_region"),
    ),
)
@pytest.mark.asyncio
async def test_public_helpers_validate_record_ids_before_query(func, bad_id: str) -> None:
    client = _RecordingClient()

    with pytest.raises(LedgerError):
        await func(client, bad_id)

    assert client.queries == []


def test_no_raw_record_id_interpolation_for_caller_supplied_ids() -> None:
    root = Path(__file__).resolve().parents[1]
    sources = [
        root / "ledger" / "queries.py",
        root / "ledger" / "adapter.py",
    ]
    banned = (
        "{decision_id}",
        "{span_id}",
        "{region_id}",
        "{symbol_id}",
        "{old_id}",
        "{new_id}",
    )

    offenders: list[str] = []
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                offenders.append(f"{source.relative_to(root)} contains {token}")

    assert offenders == []
