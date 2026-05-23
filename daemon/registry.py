"""Adapter registry — keyed lookup of IngestAdapter / EgressAdapter
instances by ``adapter.name``.

Adapters register themselves on daemon startup; the protocol router uses
this map to dispatch ``ingest.ingest(adapter_name="mcp", ...)`` and
``egress.deliver(channel="slack", ...)`` calls to the right backend.

The registry is *opinionated about uniqueness*: registering two adapters
with the same name raises rather than silently shadowing. Daemons that
need multiple instances of the same adapter type must give them distinct
names (``"linear-eng"`` vs ``"linear-product"``).
"""

from __future__ import annotations

from protocol.contracts import EgressAdapter, IngestAdapter


class AdapterRegistryError(Exception):
    """Raised on duplicate registration or unknown lookup."""


class AdapterRegistry:
    def __init__(self) -> None:
        self._ingest: dict[str, IngestAdapter] = {}
        self._egress: dict[str, EgressAdapter] = {}

    def register_ingest(self, adapter: IngestAdapter) -> None:
        if adapter.name in self._ingest:
            raise AdapterRegistryError(f"ingest adapter '{adapter.name}' already registered")
        self._ingest[adapter.name] = adapter

    def register_egress(self, adapter: EgressAdapter) -> None:
        if adapter.name in self._egress:
            raise AdapterRegistryError(f"egress adapter '{adapter.name}' already registered")
        self._egress[adapter.name] = adapter

    def lookup_ingest(self, name: str) -> IngestAdapter:
        try:
            return self._ingest[name]
        except KeyError as exc:
            raise AdapterRegistryError(f"unknown ingest adapter '{name}'") from exc

    def lookup_egress(self, name: str) -> EgressAdapter:
        try:
            return self._egress[name]
        except KeyError as exc:
            raise AdapterRegistryError(f"unknown egress adapter '{name}'") from exc

    def ingest_names(self) -> list[str]:
        return sorted(self._ingest.keys())

    def egress_names(self) -> list[str]:
        return sorted(self._egress.keys())
