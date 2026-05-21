"""Sociable tests for the dashboard ``GET /pulse`` endpoint (#437 Phase 3).

``DashboardServer._serve_pulse`` is exercised against a real
``SurrealDBLedgerAdapter`` over ``memory://`` — no ``MagicMock`` for the
ledger or the ``ctx``. The endpoint builds a ``ProjectPulseSummary`` via the
real Phase 1 ``build_project_pulse`` and writes its ``to_dict()`` shape as the
HTTP body; these tests assert that wire shape and the fail-soft error path.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dashboard.server import DashboardServer
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate


async def _fresh_adapter(suffix: str) -> tuple[SurrealDBLedgerAdapter, LedgerClient]:
    """Real ``memory://`` ledger adapter — the sociable-test seam from
    ``tests/test_codegenome_continuity_service.py::_fresh_adapter``.
    """
    c = LedgerClient(url="memory://", ns=f"pulse_dash_{suffix}", db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    a = SurrealDBLedgerAdapter(url="memory://")
    a._client = c
    a._connected = True
    return a, c


class _FakeWriter:
    """Minimal ``asyncio.StreamWriter`` stand-in — captures written bytes.

    Solitary is correct here: a ``StreamWriter`` is a transport primitive, not
    a collaborator we ship to the agent; capturing its bytes is the cleanest
    way to assert the exact HTTP response ``_serve_pulse`` produces.
    """

    def __init__(self) -> None:
        self.buffer = b""

    def write(self, data: bytes) -> None:
        self.buffer += data

    async def drain(self) -> None:
        return None


def _parse_http_body(raw: bytes) -> dict:
    """Split an HTTP/1.1 response into headers + JSON-decoded body."""
    _, _, body = raw.partition(b"\r\n\r\n")
    return json.loads(body.decode("utf-8"))


async def _create_decision(client: LedgerClient, **fields: object) -> str:
    """CREATE one decision row with the production schema; return its id."""
    assignments = ", ".join(f"{k} = $f_{k}" for k in fields)
    params = {f"f_{k}": v for k, v in fields.items()}
    rows = await client.query(f"CREATE decision SET {assignments}", params)
    return str(rows[0]["id"])


def _server_for(adapter: SurrealDBLedgerAdapter) -> DashboardServer:
    """A ``DashboardServer`` whose ``_ctx_factory`` yields a real-ledger ctx.

    ``ctx`` is a ``SimpleNamespace`` (not ``MagicMock``) so a future required
    ``ctx`` field fails the test honestly instead of being silently invented.
    """
    server = DashboardServer()
    server._ctx_factory = lambda: SimpleNamespace(ledger=adapter, repo_path=".")
    return server


@pytest.mark.asyncio
async def test_pulse_returns_to_dict_shape_for_populated_ledger() -> None:
    """``GET /pulse`` returns the ``ProjectPulseSummary.to_dict()`` shape."""
    adapter, client = await _fresh_adapter("populated")
    try:
        await _create_decision(
            client,
            description="Adopt JWT refresh tokens",
            source_type="meeting",
            source_ref="Sprint Planning",
            status="reflected",
            canonical_id="dec-jwt",
        )
        # A decision awaiting ratification → one needs_attention item.
        await _create_decision(
            client,
            description="Switch checkout to Stripe",
            source_type="slack",
            source_ref="#payments",
            status="pending",
            signoff={"state": "proposed", "signer": "alice@example.com"},
            canonical_id="dec-stripe",
        )

        server = _server_for(adapter)
        writer = _FakeWriter()
        await server._serve_pulse(writer)  # type: ignore[arg-type]

        body = _parse_http_body(writer.buffer)
        assert b"200 OK" in writer.buffer
        assert b"application/json" in writer.buffer

        # The four ProjectPulseSummary sections + the all-clear flag.
        assert set(body) == {
            "health",
            "needs_attention",
            "recently_learned",
            "suggested_next_move",
            "is_all_clear",
        }
        assert set(body["health"]) == {
            "decisions_reflected",
            "decisions_drifted",
            "decisions_pending",
            "decisions_ungrounded",
            "drifted_regions",
            "last_sync",
        }
        assert body["health"]["decisions_reflected"] == 1
        assert body["health"]["decisions_pending"] == 1

        # The proposed decision surfaces as a needs_attention item.
        assert len(body["needs_attention"]) == 1
        item = body["needs_attention"][0]
        assert set(item) == {"kind", "decision_id", "summary", "signer"}
        assert item["kind"] == "awaiting_ratification"
        assert item["summary"] == "Switch checkout to Stripe"

        # Both decisions are recently-learned items with the LearnedItem shape.
        assert len(body["recently_learned"]) == 2
        learned = body["recently_learned"][0]
        assert set(learned) == {
            "decision_id",
            "summary",
            "source_type",
            "source_ref",
            "date",
        }

        # A pending ratification → not all-clear.
        assert body["is_all_clear"] is False
        assert "ratification" in body["suggested_next_move"].lower()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_pulse_all_clear_for_empty_ledger() -> None:
    """An empty ledger yields the explicit all-clear summary."""
    adapter, client = await _fresh_adapter("empty")
    try:
        server = _server_for(adapter)
        writer = _FakeWriter()
        await server._serve_pulse(writer)  # type: ignore[arg-type]

        body = _parse_http_body(writer.buffer)
        assert body["is_all_clear"] is True
        assert body["needs_attention"] == []
        assert body["recently_learned"] == []
        assert body["health"]["decisions_reflected"] == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_pulse_error_path_returns_error_body() -> None:
    """A failing ``ctx_factory`` surfaces an ``{"error": ...}`` JSON body.

    Mirrors ``_serve_history``'s fail-soft contract: ``/pulse`` must never
    crash the dashboard server — a hard failure becomes an error body so the
    Pulse section can render an inline error without blocking the ledger.
    """

    def _boom() -> SimpleNamespace:
        raise RuntimeError("ledger unavailable")

    server = DashboardServer()
    server._ctx_factory = _boom
    writer = _FakeWriter()
    await server._serve_pulse(writer)  # type: ignore[arg-type]

    body = _parse_http_body(writer.buffer)
    assert b"200 OK" in writer.buffer
    assert set(body) == {"error"}
    assert "ledger unavailable" in body["error"]
