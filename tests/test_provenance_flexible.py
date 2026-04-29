"""Regression test for issue #72 — binds_to.provenance must preserve nested keys.

Before this fix, ``provenance ON binds_to`` was declared ``TYPE object``
without the ``FLEXIBLE`` modifier. SurrealDB v2 silently strips nested
keys from such fields on insert/update, leaving only the top-level
scalar/array primitives intact.

Concretely, callers attach structured provenance like:

    {"caller_llm": {"model": "gpt-4o", "session": "abc"},
     "search_hint": {"q": "auth flow", "boost": 1.4}}

…and on read-back the ``caller_llm`` and ``search_hint`` *values* came
back as ``{}`` (empty objects) — the keys existed, but the nested data
was gone.

Adding ``FLEXIBLE`` to the field definition tells SurrealDB to accept
arbitrary object shapes without a sub-schema. This test pins the
behaviour by writing a deeply-nested object and asserting every key
survives a round-trip.
"""

from __future__ import annotations

import os

import pytest

from ledger.client import LedgerClient
from ledger.queries import relate_binds_to
from ledger.schema import init_schema

pytestmark = pytest.mark.phase2


@pytest.fixture
async def client() -> LedgerClient:
    """In-memory SurrealDB client with the ledger schema applied."""
    surreal_url = os.getenv("SURREAL_URL", "memory://")
    c = LedgerClient(surreal_url)
    await c.connect()
    await init_schema(c)
    yield c
    await c.close()


async def _create_decision(client: LedgerClient, description: str) -> str:
    rows = await client.query(
        "CREATE decision SET description = $d, status = 'ungrounded' RETURN type::string(id) AS id",
        {"d": description},
    )
    return str(rows[0]["id"])


async def _create_region(client: LedgerClient, file_path: str, symbol_name: str) -> str:
    rows = await client.query(
        "CREATE code_region SET "
        "file_path = $f, symbol_name = $s, start_line = 1, end_line = 10 "
        "RETURN type::string(id) AS id",
        {"f": file_path, "s": symbol_name},
    )
    return str(rows[0]["id"])


async def _read_provenance(client: LedgerClient, decision_id: str) -> dict:
    rows = await client.query(
        f"SELECT provenance FROM binds_to WHERE in = {decision_id} LIMIT 1",
    )
    assert rows, "binds_to edge not found"
    return rows[0]["provenance"]


async def test_nested_provenance_keys_survive_round_trip(client: LedgerClient) -> None:
    """The original failure mode from #72: nested objects roundtrip cleanly."""
    decision_id = await _create_decision(client, "use Argon2 for password hashing")
    region_id = await _create_region(client, "auth/passwords.py", "hash_password")

    nested_provenance = {
        "caller_llm": {
            "model": "gpt-4o",
            "session": "abc-123",
            "params": {"temperature": 0.0, "max_tokens": 8192},
        },
        "search_hint": {
            "q": "argon2 password hashing implementation",
            "boost": 1.4,
            "filters": ["auth", "security"],
        },
        "ingested_at": "2026-04-26T19:00:00Z",
    }

    await relate_binds_to(
        client,
        decision_id=decision_id,
        region_id=region_id,
        confidence=0.92,
        provenance=nested_provenance,
    )

    round_tripped = await _read_provenance(client, decision_id)

    # Top-level keys present (this passed even before the fix).
    assert set(round_tripped.keys()) == {"caller_llm", "search_hint", "ingested_at"}

    # Nested values intact (this is what the fix ensures).
    assert round_tripped["caller_llm"] == {
        "model": "gpt-4o",
        "session": "abc-123",
        "params": {"temperature": 0.0, "max_tokens": 8192},
    }
    assert round_tripped["search_hint"] == {
        "q": "argon2 password hashing implementation",
        "boost": 1.4,
        "filters": ["auth", "security"],
    }
    assert round_tripped["ingested_at"] == "2026-04-26T19:00:00Z"


async def test_empty_provenance_still_works(client: LedgerClient) -> None:
    """Default-empty provenance is the most common path; must not regress."""
    decision_id = await _create_decision(client, "trivial decision")
    region_id = await _create_region(client, "x.py", "f")

    await relate_binds_to(
        client,
        decision_id=decision_id,
        region_id=region_id,
        confidence=0.5,
        provenance=None,  # → defaults to {}
    )

    round_tripped = await _read_provenance(client, decision_id)
    assert round_tripped == {}


async def test_deeply_nested_provenance_round_trips(client: LedgerClient) -> None:
    """Stress test: arrays of objects, objects-in-objects, mixed types."""
    decision_id = await _create_decision(client, "deeply-nested provenance test")
    region_id = await _create_region(client, "deep.py", "deep_fn")

    deep_provenance = {
        "tools_invoked": [
            {"name": "Grep", "args": {"pattern": "foo", "path": "/x"}, "ms": 12},
            {"name": "Read", "args": {"file": "/y.py", "lines": [1, 50]}, "ms": 4},
        ],
        "metadata": {
            "level_1": {
                "level_2": {
                    "level_3": {
                        "level_4": {"value": "needle"},
                    },
                },
            },
        },
    }

    await relate_binds_to(
        client,
        decision_id=decision_id,
        region_id=region_id,
        confidence=0.7,
        provenance=deep_provenance,
    )

    round_tripped = await _read_provenance(client, decision_id)

    # Array of objects
    assert isinstance(round_tripped["tools_invoked"], list)
    assert len(round_tripped["tools_invoked"]) == 2
    assert round_tripped["tools_invoked"][0]["args"]["pattern"] == "foo"
    assert round_tripped["tools_invoked"][1]["ms"] == 4

    # 4-deep object nesting
    deepest = round_tripped["metadata"]["level_1"]["level_2"]["level_3"]["level_4"]
    assert deepest == {"value": "needle"}
