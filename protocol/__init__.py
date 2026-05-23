"""Universal ingest/egress/grounding protocol.

Public surface for adapters that talk to the bicameral-daemon. Today this
module lives in-tree alongside the MCP server; in Phase 3 it is extracted to
the public `bicameral-protocol` package so any integration (Linear, Notion,
Slack, future grounders) can implement it without seeing MCP-internal code.

The wire format is JSON-RPC 2.0 over a Unix domain socket. The daemon
publishes the socket path in `~/.bicameral/daemon.json` at startup.
"""

from __future__ import annotations

from .categorization import (
    Category,
    ProtocolMethodNameError,
    get_category,
    get_method,
    grounding_analyze,
    grounding_lookup,
    is_categorized,
    read_tool,
    system_tool,
    write_tool,
)
from .client import ProtocolClient
from .contracts import (
    LOCAL_TENANT_ID,
    PROTOCOL_VERSION,
    AnalyzeRegionRequest,
    AttachRequest,
    AttachResult,
    BatchAnalyzeRequest,
    CodeRegion,
    DeliveryResult,
    DriftResult,
    EgressAdapter,
    ExtractSymbolsRequest,
    GetNeighborsRequest,
    GroundingPort,
    HistoryRequest,
    IngestAdapter,
    IngestRequest,
    IngestResult,
    LinkCommitRequest,
    LinkCommitResult,
    Neighbor,
    NotAttachedError,
    NotificationEvent,
    ProtocolError,
    ProtocolVersionError,
    Symbol,
    UsageSummaryRequest,
    UsageSummaryResult,
    ValidateSymbolsRequest,
)
from .server import ConnectionContext, ProtocolServer

__all__ = [
    "LOCAL_TENANT_ID",
    "PROTOCOL_VERSION",
    "AnalyzeRegionRequest",
    "AttachRequest",
    "AttachResult",
    "BatchAnalyzeRequest",
    "Category",
    "CodeRegion",
    "ConnectionContext",
    "DeliveryResult",
    "DriftResult",
    "EgressAdapter",
    "ExtractSymbolsRequest",
    "GetNeighborsRequest",
    "GroundingPort",
    "HistoryRequest",
    "IngestAdapter",
    "IngestRequest",
    "IngestResult",
    "LinkCommitRequest",
    "LinkCommitResult",
    "Neighbor",
    "NotAttachedError",
    "NotificationEvent",
    "ProtocolClient",
    "ProtocolError",
    "ProtocolMethodNameError",
    "ProtocolServer",
    "ProtocolVersionError",
    "Symbol",
    "UsageSummaryRequest",
    "UsageSummaryResult",
    "ValidateSymbolsRequest",
    "get_category",
    "get_method",
    "grounding_analyze",
    "grounding_lookup",
    "is_categorized",
    "read_tool",
    "system_tool",
    "write_tool",
]
