"""Bicameral MCP thin client.

This package is a transport surface for the local bicameral-bot daemon. It
maps MCP tool calls into canonical ToolRequest envelopes and returns daemon
ToolResponse payloads. It does not own ledger, graph, dashboard, integration,
or governance behavior.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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
    format_recall_expand_scope,
    format_recall_inspect_evidence,
    format_recall_packet,
    format_review_queue_response,
    format_source_link_response,
    format_tool_response,
    format_workspace_bind_remote_conflict,
    format_workspace_bind_response,
    recovery_error_text,
)
from sync_payload_filter import filter_pending_checks
from tool_request import (
    LOCAL_ONLY_TOOLS,
    MCP_TOOL_COMMANDS,
    WORKSPACE_BIND_COMMAND,
    build_tool_request,
    evaluate_remote_evidence,
)
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
    deferred_commands = tuple(capabilities.get("deferred_commands", []))
    endpoint = resolve_daemon_endpoint()
    return CapabilityReport(
        daemon_protocol_version=protocol_version,
        mcp_protocol_version=TOOLREQUEST_PROTOCOL_VERSION,
        supported_commands=supported_commands,
        deferred_commands=deferred_commands,
        daemon_endpoint=endpoint.url,
        workspace_binding_available=bool(capabilities.get("workspace_binding_available", False)),
    )


def _ensure_command_advertised(command_name: str, capability_report: CapabilityReport) -> None:
    if command_name in capability_report.deferred_commands:
        raise DaemonCapabilityError(
            f"daemon reports ToolRequest command as deferred: {command_name}",
            deferred=True,
        )
    if command_name not in capability_report.supported_commands:
        raise DaemonCapabilityError(
            f"daemon does not advertise ToolRequest command: {command_name}"
        )
    # workspace.bind is always protocol-listed, but the daemon only routes it
    # when it holds an operator-local workspace-binding store. Fail closed on
    # capability discovery rather than dispatching a request the daemon would
    # reject with unsupported_capability (bicameral-bot#747).
    if command_name == WORKSPACE_BIND_COMMAND and not capability_report.workspace_binding_available:
        raise DaemonCapabilityError(
            "daemon does not advertise workspace-binding capability "
            "(workspace_binding_available is false); workspace binding is unavailable"
        )


def _filter_tools_by_capability(capability_report: CapabilityReport) -> list[types.Tool]:
    """Exclude tools whose daemon command is deferred or absent.

    ``workspace.bind`` is additionally gated on the daemon's truthful
    ``workspace_binding_available`` capability discovery flag: the bind action
    is only user-visible when the daemon can actually execute it.
    """
    deferred = frozenset(capability_report.deferred_commands)
    supported = frozenset(capability_report.supported_commands)
    result: list[types.Tool] = []
    for tool in SUPPORTED_TOOLS:
        if tool.name in LOCAL_ONLY_TOOLS:
            result.append(tool)
            continue
        command = MCP_TOOL_COMMANDS.get(tool.name)
        if command is None:
            continue
        if command in deferred:
            continue
        if command not in supported:
            continue
        if command == WORKSPACE_BIND_COMMAND and not capability_report.workspace_binding_available:
            continue
        result.append(tool)
    return result


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    capability_report = await _ensure_protocol_compatible(_client())
    return _filter_tools_by_capability(capability_report)


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

    # --- Git-remote evidence guard (mcp#702): fail closed before dispatch when
    # the candidate folder's git remote clearly contradicts the selected
    # project. The remote is evidence only; project_id remains the authority key.
    if name == "bicameral.workspace.bind":
        conflict = _workspace_bind_remote_conflict(arguments)
        if conflict is not None:
            return [conflict]

    command_name = MCP_TOOL_COMMANDS[name]
    try:
        client = _client()
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
        if name == "bicameral.workspace.bind":
            return [format_workspace_bind_response(response)]
        if name == "bicameral.brief":
            return [format_brief_narrative(response)]
        if name == "bicameral.preflight":
            return [format_preflight_response(response)]
        if "recall_packet" in response:
            return [format_recall_packet(response)]
        if name == "bicameral.binding.inspect":
            return [format_source_link_response(response, surface="binding.inspect")]
        if name == "bicameral.lookup":
            return [format_lookup_response(response)]
        if name == "bicameral.context":
            return [format_context_packet_response(response)]
        if name == "bicameral.correction_findings":
            return [format_correction_findings_response(response)]
        if name == "bicameral.review.corpus_proposals":
            return [format_correction_findings_response(response)]
        if name == "bicameral.recall.inspect_evidence":
            return [format_recall_inspect_evidence(response)]
        if name == "bicameral.recall.expand_scope":
            return [format_recall_expand_scope(response)]
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
        if name == "bicameral.history":
            return [format_source_link_response(response, surface="history")]
        if name == "bicameral.search":
            return [format_source_link_response(response, surface="search")]
        return [format_tool_response(response)]
    except DaemonClientError as exc:
        return [
            recovery_error_text(
                exc,
                requested_tool=name,
                requested_command=command_name,
            )
        ]


def _workspace_bind_remote_conflict(
    arguments: dict[str, Any],
) -> types.TextContent | None:
    """Fail closed when the candidate git remote contradicts the project (mcp#702).

    Returns a rendered fail-closed response (and dispatches nothing) when the
    candidate folder's git remote clearly contradicts a supplied registered
    project source ref; otherwise ``None`` so the normal proposal flow proceeds.
    The remote is evidence only — ``project_id`` remains the authority key and
    the daemon still owns validation and materialization.
    """
    source_refs: list[str] = []
    raw_list = arguments.get("project_source_refs")
    if isinstance(raw_list, str):
        source_refs.append(raw_list)
    elif isinstance(raw_list, (list, tuple)):
        source_refs.extend(str(item) for item in raw_list if isinstance(item, str))
    single = arguments.get("project_source_ref")
    if isinstance(single, str):
        source_refs.append(single)
    if not source_refs:
        return None

    evidence = evaluate_remote_evidence(
        candidate_path=arguments.get("candidate_path"),
        project_source_refs=source_refs,
    )
    if evidence.verdict != "contradiction":
        return None
    return format_workspace_bind_remote_conflict(
        project_id=arguments.get("project_id"),
        candidate_repo_ref=evidence.candidate_repo_ref,
        project_source_refs=evidence.project_source_refs,
        reason=evidence.reason,
    )


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
    if name == "bicameral.capture_context":
        return _capture_context_arguments(arguments)
    return arguments


def _capture_context_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Shape local MCP context into daemon-owned ingest.submit_local params.

    The output is Source/SourceSnapshot/EvidenceReference-compatible input for
    the daemon. Code hints are advisory binding_hints only; MCP does not verify
    graph scope, create bindings, resolve compliance, approve signoff, or write
    canonical events.
    """
    correlation_id = arguments.get("correlation_id") or arguments.get("session_id")
    source_uri = arguments.get("source_uri") or f"mcp://session/{correlation_id or 'capture'}"
    source_type = arguments.get("source_type") or arguments.get("source_kind") or "mcp_session"

    snapshot_payload = {
        "kind": "SourceSnapshot",
        "source": {
            "kind": "Source",
            "source_uri": source_uri,
            "source_type": source_type,
            "source_link": arguments.get("source_link"),
        },
        "session_turns": arguments.get("session_turns", []),
        "tool_calls": arguments.get("tool_calls", []),
        "tool_outputs": arguments.get("tool_outputs", []),
        "command_outputs": arguments.get("command_outputs", []),
        "code_hints": arguments.get("code_hints", []),
        "code_region_hints": arguments.get("code_region_hints", []),
        "evidence_references": arguments.get("evidence_references", []),
        "correlation_id": correlation_id,
    }
    snapshot_content = arguments.get("snapshot_content") or json.dumps(
        {key: value for key, value in snapshot_payload.items() if value not in (None, [], {})},
        sort_keys=True,
    )

    metadata = {
        **(arguments.get("metadata") or {}),
        "bot_vocabulary": ["Source", "SourceSnapshot", "EvidenceReference", "SourceKind"],
        "source_kind": source_type,
        "source_link": arguments.get("source_link"),
        "correlation_id": correlation_id,
        "mcp_session_id": arguments.get("session_id"),
        "mcp_capture": {
            "tool": "bicameral.capture_context",
            "has_session_turns": bool(arguments.get("session_turns")),
            "has_tool_calls": bool(arguments.get("tool_calls")),
            "has_tool_outputs": bool(arguments.get("tool_outputs")),
            "has_command_outputs": bool(arguments.get("command_outputs")),
            "has_code_hints": bool(arguments.get("code_hints")),
            "has_code_region_hints": bool(arguments.get("code_region_hints")),
        },
        "evidence_references": arguments.get("evidence_references", []),
        "code_region_hints": arguments.get("code_region_hints", []),
    }

    ingest_args: dict[str, Any] = {
        "source_uri": source_uri,
        "source_type": source_type,
        "label": arguments.get("label"),
        "title": arguments.get("title") or "MCP context capture",
        "description": arguments.get("description")
        or "MCP session/tool/code context submitted as bot-owned source evidence.",
        "snapshot_content": snapshot_content,
        "evidence": _capture_evidence(arguments),
        "binding_hints": _capture_binding_hints(arguments.get("code_hints", [])),
        "rationale": arguments.get("rationale"),
        "metadata": {key: value for key, value in metadata.items() if value not in (None, [], {})},
    }

    return {key: value for key, value in ingest_args.items() if value not in (None, [], {})}


def _capture_evidence(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = list(arguments.get("evidence") or [])
    for output in arguments.get("command_outputs", []):
        excerpt = output.get("excerpt") or output.get("output") or output.get("stdout")
        if excerpt:
            evidence.append({"excerpt": str(excerpt)})
    for output in arguments.get("tool_outputs", []):
        excerpt = output.get("excerpt") or output.get("output") or output.get("content")
        if excerpt:
            evidence.append({"excerpt": str(excerpt)})
    for turn in arguments.get("session_turns", []):
        excerpt = turn.get("excerpt") or turn.get("content") or turn.get("text")
        if excerpt:
            evidence.append({"excerpt": str(excerpt)})
    return evidence


def _capture_binding_hints(code_hints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    binding_hints: list[dict[str, Any]] = []
    for hint in code_hints:
        file = hint.get("file") or hint.get("path")
        if not file:
            continue
        binding_hint = {
            "file": file,
            "range": hint.get("range"),
            "symbol": hint.get("symbol"),
        }
        binding_hints.append(
            {key: value for key, value in binding_hint.items() if value not in (None, [], {})}
        )
    return binding_hints


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
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    # Adapter management and the host-invoked pre-work runner have their own
    # argument grammar; dispatch them before the top-level serve/tools parser.
    if raw_argv and raw_argv[0] == "adapters":
        from preflight_adapters.cli import run_adapters_cli

        return run_adapters_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "prework-run":
        from preflight_adapters.cli import run_prework_cli

        return run_prework_cli(raw_argv[1:])

    parser = argparse.ArgumentParser(
        prog="bicameral-mcp",
        description="Run the Bicameral MCP thin client over stdio.",
    )
    parser.add_argument("--version", action="store_true", help="Print the bicameral-mcp version.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["serve", "tools", "adapters", "prework-run"],
        default="serve",
        help=(
            "'serve' starts the MCP stdio server; 'tools' prints supported tool "
            "names; 'adapters' manages host pre-work adapters; 'prework-run' is "
            "invoked by a host hook."
        ),
    )
    args = parser.parse_args(raw_argv)

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
