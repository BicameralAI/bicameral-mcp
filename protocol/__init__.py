"""Universal ingest/egress/grounding protocol.

Public surface for adapters that talk to the bicameral-daemon. Today this
module lives in-tree alongside the MCP server; in Phase 3 it is extracted to
the public `bicameral-protocol` package so any integration (Linear, Notion,
Slack, future grounders) can implement it without seeing MCP-internal code.

The wire format is JSON-RPC 2.0 over a Unix domain socket. The daemon
publishes the socket path in `~/.bicameral/daemon.json` at startup.
"""

from __future__ import annotations

from .client import ProtocolClient
from .contracts import (
    PROTOCOL_VERSION,
    AnalyzeRegionRequest,
    BatchAnalyzeRequest,
    CodeRegion,
    DeliveryResult,
    DriftResult,
    EgressAdapter,
    ExtractSymbolsRequest,
    GetNeighborsRequest,
    GroundingPort,
    IngestAdapter,
    IngestRequest,
    IngestResult,
    LinkCommitRequest,
    LinkCommitResult,
    Neighbor,
    NotificationEvent,
    ProtocolError,
    ProtocolVersionError,
    Symbol,
    ValidateSymbolsRequest,
)
from .server import ProtocolServer

__all__ = [
    "PROTOCOL_VERSION",
    "AnalyzeRegionRequest",
    "BatchAnalyzeRequest",
    "CodeRegion",
    "DeliveryResult",
    "DriftResult",
    "EgressAdapter",
    "ExtractSymbolsRequest",
    "GetNeighborsRequest",
    "GroundingPort",
    "IngestAdapter",
    "IngestRequest",
    "IngestResult",
    "LinkCommitRequest",
    "LinkCommitResult",
    "Neighbor",
    "NotificationEvent",
    "ProtocolClient",
    "ProtocolError",
    "ProtocolServer",
    "ProtocolVersionError",
    "Symbol",
    "ValidateSymbolsRequest",
]
