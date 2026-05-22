"""Phase 2a: adapter registry uniqueness + lookup semantics."""

from __future__ import annotations

import pytest

from daemon.registry import AdapterRegistry, AdapterRegistryError
from protocol.contracts import (
    ConnectionContext,
    DeliveryResult,
    IngestRequest,
    IngestResult,
    LinkCommitRequest,
    LinkCommitResult,
    NotificationEvent,
)


class _FakeIngest:
    def __init__(self, name: str) -> None:
        self.name = name

    async def ingest(
        self, _req: IngestRequest, _ctx: ConnectionContext
    ) -> IngestResult:
        return IngestResult(status="accepted")

    async def link_commit(
        self, _req: LinkCommitRequest, _ctx: ConnectionContext
    ) -> LinkCommitResult:
        return LinkCommitResult(status="linked")


class _FakeEgress:
    def __init__(self, name: str) -> None:
        self.name = name

    async def deliver(
        self, _event: NotificationEvent, _ctx: ConnectionContext
    ) -> DeliveryResult:
        return DeliveryResult(status="delivered")


def test_register_ingest_then_lookup_returns_same_instance() -> None:
    """Registry returns the registered instance on lookup."""
    registry = AdapterRegistry()
    adapter = _FakeIngest("mcp")
    registry.register_ingest(adapter)
    assert registry.lookup_ingest("mcp") is adapter


def test_duplicate_ingest_name_raises_on_second_register() -> None:
    """Two adapters with same name = error, not silent shadow."""
    registry = AdapterRegistry()
    registry.register_ingest(_FakeIngest("mcp"))
    with pytest.raises(AdapterRegistryError, match="already registered"):
        registry.register_ingest(_FakeIngest("mcp"))


def test_unknown_egress_lookup_raises() -> None:
    """Unknown name is an error — callers cannot silently fall through."""
    registry = AdapterRegistry()
    with pytest.raises(AdapterRegistryError, match="unknown egress adapter"):
        registry.lookup_egress("slack")


def test_ingest_and_egress_namespaces_are_independent() -> None:
    """Same name in both namespaces does not collide."""
    registry = AdapterRegistry()
    ing = _FakeIngest("notion")
    egr = _FakeEgress("notion")
    registry.register_ingest(ing)
    registry.register_egress(egr)
    assert registry.lookup_ingest("notion") is ing
    assert registry.lookup_egress("notion") is egr
    assert registry.ingest_names() == ["notion"]
    assert registry.egress_names() == ["notion"]
