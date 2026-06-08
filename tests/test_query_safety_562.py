"""#562 — daemon/projection query-safety guards.

Two layers:

1. **Static anti-regression** (`test_no_unvalidated_record_id_interpolation`):
   AST-scans every module that builds SurrealQL with caller-controlled record
   ids and fails if any function interpolates an id-bearing variable
   (``Name``/``Attribute`` whose name ends in ``id`` — covering ``decision_id``
   and short forms like ``did``/``csid``/``siid`` so a rename cannot evade) into
   an f-string without first routing it through ``_validated_record_id``. Each
   documented exception is in ``_ALLOWLIST`` with a reason.
   Known limitation: only f-string (``ast.JoinedStr``) interpolation is
   inspected — ``.format()``/``%``/concatenation are not (none are used to build
   SurrealQL in the scanned modules today); the behavioral layer + validate-at-
   entry pattern are the runtime backstop.

2. **Behavioral** (sociable, ``memory://``): malicious record-id inputs are
   rejected with ``LedgerError`` through the real query functions; well-formed
   ids are accepted.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from ledger.client import LedgerClient, LedgerError
from ledger.queries import decision_exists, relate_binds_to
from ledger.schema import init_schema, migrate

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Modules that construct SurrealQL touching caller-controlled record ids.
# (Repo-wide audit 2026-06-08 found the id-interpolation surface confined to
# these; cli/_ledger_io_engine.py is an operator-trust import boundary tracked
# as a separate follow-up and is intentionally out of this guard's scope.)
_SCAN = [
    "ledger/queries.py",
    "ledger/adapter.py",
    "handlers/remove_decision.py",
    "handlers/remove_source.py",
    "handlers/resolve_collision.py",
    "handlers/ratify.py",
    "codegenome/continuity_service.py",
    "codegenome/bind_service.py",
]

# (relative_path, function, interpolated-name) that are safe for a documented
# reason other than the shared _validated_record_id choke point.
# Names ending in '_id' that are NOT SurrealDB record ids — session/flow
# correlators that may legitimately appear interpolated (and only ever reach
# SQL as bound parameters, never as a record-id position).
_NOT_RECORD_IDS = {"session_id", "_session_id", "flow_id"}

_ALLOWLIST = {
    # DB-derived (value originates from a ledger row, not the caller):
    ("ledger/queries.py", "lookup_vocab_cache", "top_id"),  # rows[0]["id"] of a vocab_cache hit
    (
        "ledger/adapter.py",
        "ingest_commit",
        "region_id",
    ),  # region.get("region_id"); also via validated update_region_hash
    (
        "codegenome/bind_service.py",
        "_rollback_partial_bind",
        "table_id",
    ),  # subject/identity ids from daemon-side upserts
    # Validated by a function-specific guard, not the shared helper:
    (
        "ledger/queries.py",
        "update_decision_level",
        "decision_id",
    ),  # _DECISION_ID_RE -> ValueError before SQL (test_update_decision_level_query.py)
}


def _is_id_name(name: str) -> bool:
    # ends in 'id' (covers '<x>_id' and short forms did/csid/siid/svid/rid),
    # minus session/flow correlators that are not record ids.
    return name.endswith("id") and name not in _NOT_RECORD_IDS


def _interp_name(node: ast.AST) -> str | None:
    """The interpolated identifier if it is a record-id-bearing Name/Attribute."""
    if isinstance(node, ast.Name) and _is_id_name(node.id):
        return node.id
    if isinstance(node, ast.Attribute) and _is_id_name(node.attr):
        return node.attr
    return None


def _validated_names(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == "_validated_record_id":
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _interpolated_ids(fn: ast.AST) -> set[str]:
    hits: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.JoinedStr):
            for piece in node.values:
                if isinstance(piece, ast.FormattedValue):
                    name = _interp_name(piece.value)
                    if name:
                        hits.add(name)
    return hits


@pytest.mark.parametrize("relpath", _SCAN)
def test_no_unvalidated_record_id_interpolation(relpath: str) -> None:
    tree = ast.parse((_ROOT / relpath).read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        validated = _validated_names(node)
        for name in _interpolated_ids(node):
            if name in validated:
                continue
            if (relpath, node.name, name) in _ALLOWLIST:
                continue
            offenders.append(f"{relpath}::{node.name} interpolates unvalidated {{{name}}}")
    assert not offenders, (
        "Caller-controlled record id interpolated into SurrealQL without "
        "_validated_record_id (route it through the choke point — #562):\n  "
        + "\n  ".join(offenders)
    )


async def _fresh_client(suffix: str) -> LedgerClient:
    client = LedgerClient(url="memory://", ns=f"qs562_{suffix}", db="ledger_test")
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)
    return client


_MALICIOUS = [
    "decision:abc; DROP TABLE decision",  # statement injection
    "decision:abc OR true",  # boolean tamper
    "decision:abc:def",  # second colon
    "not_a_record_id",  # no table prefix
    "../../etc/passwd",  # path-ish
    "",  # empty
]


@pytest.mark.asyncio
async def test_malicious_decision_id_rejected() -> None:
    client = await _fresh_client("mal")
    for bad in _MALICIOUS:
        with pytest.raises(LedgerError):
            await decision_exists(client, bad)


@pytest.mark.asyncio
async def test_wrong_table_prefix_rejected() -> None:
    client = await _fresh_client("tbl")
    with pytest.raises(LedgerError):
        await decision_exists(client, "code_region:abc123")


@pytest.mark.asyncio
async def test_valid_decision_id_accepted() -> None:
    client = await _fresh_client("ok")
    assert await decision_exists(client, "decision:abc123") is False


@pytest.mark.asyncio
async def test_relate_binds_to_rejects_injection_in_either_arg() -> None:
    client = await _fresh_client("rel")
    with pytest.raises(LedgerError):
        await relate_binds_to(client, "decision:ok; DELETE code_region", "code_region:r1")
    with pytest.raises(LedgerError):
        await relate_binds_to(client, "decision:ok", "code_region:r1 OR 1=1")
