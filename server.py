"""Bicameral MCP thin client.

This package is a transport surface for the local bicameral-bot daemon. It
maps MCP tool calls into canonical ToolRequest envelopes and returns daemon
ToolResponse payloads. It does not own ledger, graph, dashboard, integration,
or governance behavior.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import mcp.server.stdio
from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.models import InitializationOptions

from approval_gate import ApprovalGate, scope_from_params
from authority import build_authority_context
from brief_renderer import format_brief_narrative
from coverage_guard import check_coverage
from daemon_client import (
    CapabilityReport,
    DaemonCapabilityError,
    DaemonClient,
    DaemonClientError,
    DaemonProtocolError,
    resolve_daemon_endpoint,
)
from erasure_gate import (
    ErasureGate,
)
from erasure_gate import (
    scope_from_params as erasure_scope_from_params,
)
from governance_surface import (
    format_governance_inbox,
    format_governance_inspect,
    format_governance_resolve,
)
from prompts import get_prompt_result, list_prompt_definitions
from responses import (
    error_text,
    format_context_packet_response,
    format_correction_findings_response,
    format_correction_response,
    format_lookup_response,
    format_preflight_no_fire,
    format_preflight_response,
    format_recall_packet,
    format_review_queue_response,
    format_tool_response,
    recovery_error_text,
)
from sync_payload_filter import filter_pending_checks
from tool_request import MCP_TOOL_COMMANDS, build_tool_request
from tool_schemas import SUPPORTED_TOOLS
from version import SERVER_NAME, SERVER_VERSION, TOOLREQUEST_PROTOCOL_VERSION

server = Server(SERVER_NAME)

_approval_gate = ApprovalGate()
_erasure_gate = ErasureGate()


def _notification_options() -> NotificationOptions:
    return NotificationOptions()


def _client() -> DaemonClient:
    return DaemonClient.from_env()


async def _ensure_protocol_compatible(client: DaemonClient) -> CapabilityReport:
    capabilities = await client.capabilities()
    protocol_version = capabilities.get("toolrequest_protocol_version") or capabilities.get(
        "protocol_version"
    )
    if protocol_version != TOOLREQUEST_PROTOCOL_VERSION:
        raise DaemonProtocolError(
            "unsupported ToolRequest protocol version: "
            f"daemon={protocol_version!r}, mcp={TOOLREQUEST_PROTOCOL_VERSION!r}",
            daemon_protocol_version=protocol_version,
            mcp_protocol_version=TOOLREQUEST_PROTOCOL_VERSION,
        )
    supported_commands = tuple(capabilities.get("supported_commands", []))
    endpoint = resolve_daemon_endpoint()
    return CapabilityReport(
        daemon_protocol_version=protocol_version,
        mcp_protocol_version=TOOLREQUEST_PROTOCOL_VERSION,
        supported_commands=supported_commands,
        daemon_endpoint=endpoint.url,
    )


def _ensure_command_advertised(command_name: str, capability_report: CapabilityReport) -> None:
    if command_name not in capability_report.supported_commands:
        raise DaemonCapabilityError(
            f"daemon does not advertise ToolRequest command: {command_name}"
        )


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    await _ensure_protocol_compatible(_client())
    return list(SUPPORTED_TOOLS)


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
    arguments = arguments or {}

    # --- Local-only approval tools ---
    if name == "bicameral.request_correction.approve":
        return _handle_approve(arguments)
    if name == "bicameral.privacy.erase_subject.approve":
        return _handle_erasure_approve(arguments)

    if name not in MCP_TOOL_COMMANDS:
        return [error_text("unsupported_tool", f"Unsupported Bicameral MCP tool: {name}")]

    # --- Approval gate for request_correction ---
    if name == "bicameral.request_correction":
        gate_result = _enforce_approval_gate(arguments)
        if gate_result is not None:
            return gate_result

    # --- Approval gate for erasure (GDPR Art.17, fail-closed) ---
    if name == "bicameral.privacy.erase_subject":
        gate_result = _enforce_erasure_gate(arguments)
        if gate_result is not None:
            return gate_result

    command_name = MCP_TOOL_COMMANDS[name]
    client = _client()
    try:
        capability_report = await _ensure_protocol_compatible(client)
        _ensure_command_advertised(command_name, capability_report)

        # --- Coverage guard: fast-path elimination for un-ingested files ---
        if name == "bicameral.preflight":
            files = arguments.get("files")
            if files:
                no_coverage = await check_coverage(
                    client=client,
                    files=files,
                    supported_commands=capability_report.supported_commands,
                    arguments=arguments,
                )
                if no_coverage:
                    from uuid import uuid4

                    return [format_preflight_no_fire(files=files, request_id=str(uuid4()))]

        command_arguments = _command_arguments_for_tool(name, arguments)
        tool_request = build_tool_request(
            command_name=command_name,
            params=command_arguments,
            authority=build_authority_context(name, arguments),
        )
        response = await client.send_tool_request(tool_request)
        caller_file_paths = arguments.get("files")
        filter_pending_checks(response, caller_file_paths)
        if name == "bicameral.brief":
            return [format_brief_narrative(response)]
        if name == "bicameral.preflight":
            return [format_preflight_response(response)]
        if name == "bicameral.lookup":
            return [format_lookup_response(response)]
        if name == "bicameral.context":
            return [format_context_packet_response(response)]
        if name == "bicameral.correction_findings":
            return [format_correction_findings_response(response)]
        if name == "bicameral.review.corpus_proposals":
            return [format_correction_findings_response(response)]
        if name == "bicameral.review.candidates":
            return [format_review_queue_response(response, item_key="decision_candidates")]
        if name in {
            "bicameral.review.promote_candidate",
            "bicameral.review.request_corpus_change",
        }:
            return [format_review_queue_response(response, item_key="review_result")]
        if name == "bicameral.request_correction":
            return [format_correction_response(response)]
        if name == "bicameral.review.contradictions":
            return [format_governance_inbox(response)]
        if name == "bicameral.review.triage_contradiction":
            return [format_governance_resolve(response)]
        if name == "bicameral.governance.inbox":
            return [format_governance_inbox(response)]
        if name == "bicameral.governance.inspect":
            return [format_governance_inspect(response)]
        if name == "bicameral.governance.resolve":
            return [format_governance_resolve(response)]
        if "recall_packet" in response:
            return [format_recall_packet(response)]
        return [format_tool_response(response)]
    except DaemonClientError as exc:
        return [
            recovery_error_text(
                exc,
                requested_tool=name,
                requested_command=command_name,
            )
        ]


def _command_arguments_for_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Apply MCP-tool defaults before command-param allowlisting.

    Defaults are transport ergonomics only; the daemon still owns query
    semantics, review authority, and canonical state transitions.
    """
    if name == "bicameral.review.candidates":
        return {"query": "", "scope": "candidates", **arguments}
    if name == "bicameral.review.corpus_proposals":
        return {
            "scope": "correction_capture",
            "include_correction_findings": True,
            **arguments,
        }
    return arguments


def _handle_approve(arguments: dict[str, Any]) -> list[types.TextContent]:
    """Grant a single-use scoped approval for request_correction."""
    import json

    try:
        scope = scope_from_params(arguments)
    except ValueError as exc:
        return [error_text("approval_scope_invalid", str(exc))]

    key = _approval_gate.grant(scope)
    payload = {
        "status": "approved",
        "scope": scope.description(),
        "approval_key": key,
        "message": (
            "Single-use approval granted. Call bicameral.request_correction "
            "with matching scope parameters to submit."
        ),
    }
    return [types.TextContent(type="text", text=json.dumps(payload))]


def _enforce_approval_gate(
    arguments: dict[str, Any],
) -> list[types.TextContent] | None:
    """Check and consume approval. Returns error content if rejected, None if approved."""
    import json

    try:
        scope = scope_from_params(arguments)
    except ValueError as exc:
        return [error_text("approval_scope_invalid", str(exc))]

    if not _approval_gate.consume(scope):
        payload = {
            "status": "error",
            "error_code": "approval_required",
            "message": (
                "Correction submission rejected: no matching single-use approval found. "
                "Call bicameral.request_correction.approve with the same scope first."
            ),
            "requested_scope": scope.description(),
        }
        return [types.TextContent(type="text", text=json.dumps(payload))]
    return None


def _handle_erasure_approve(arguments: dict[str, Any]) -> list[types.TextContent]:
    """Grant a single-use scoped approval for privacy.erase_subject."""
    import json

    try:
        scope = erasure_scope_from_params(arguments)
    except ValueError as exc:
        return [error_text("erasure_scope_invalid", str(exc))]

    key = _erasure_gate.grant(scope)
    payload = {
        "status": "approved",
        "scope": scope.description(),
        "approval_key": key,
        "message": (
            "Single-use erasure approval granted. Call "
            "bicameral.privacy.erase_subject with matching "
            "subject_id to execute erasure."
        ),
    }
    return [types.TextContent(type="text", text=json.dumps(payload))]


def _enforce_erasure_gate(
    arguments: dict[str, Any],
) -> list[types.TextContent] | None:
    """Check and consume erasure approval. Returns error content if rejected."""
    import json

    try:
        scope = erasure_scope_from_params(arguments)
    except ValueError as exc:
        return [error_text("erasure_scope_invalid", str(exc))]

    if not _erasure_gate.consume(scope):
        payload = {
            "status": "error",
            "error_code": "erasure_approval_required",
            "message": (
                "Erasure rejected: no matching single-use approval found. "
                "Call bicameral.privacy.erase_subject.approve with the "
                "same subject_id first. (GDPR Art.17 fail-closed gate)"
            ),
            "requested_scope": scope.description(),
        }
        return [types.TextContent(type="text", text=json.dumps(payload))]
    return None


@server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    await _ensure_protocol_compatible(_client())
    return list_prompt_definitions()


@server.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
    await _ensure_protocol_compatible(_client())
    return get_prompt_result(name, arguments or {})


async def run_stdio() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version=SERVER_VERSION,
                capabilities=server.get_capabilities(
                    notification_options=_notification_options(),
                    experimental_capabilities={},
                ),
            ),
        )


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bicameral-mcp",
        description="Run the Bicameral MCP thin client over stdio.",
    )
    parser.add_argument("--version", action="store_true", help="Print the bicameral-mcp version.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["serve", "tools"],
        default="serve",
        help="'serve' starts the MCP stdio server; 'tools' prints supported tool names.",
    )
    args = parser.parse_args(argv)

    if args.version:
        print(SERVER_VERSION)
        return 0

    if args.command == "tools":
        for tool in SUPPORTED_TOOLS:
            print(tool.name)
        return 0

    asyncio.run(run_stdio())
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
