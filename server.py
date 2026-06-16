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

from authority import build_authority_context
from daemon_client import (
    DaemonClient,
    DaemonClientError,
    DaemonProtocolError,
)
from prompts import get_prompt_result, list_prompt_definitions
from responses import (
    error_text,
    format_preflight_response,
    format_tool_response,
    recovery_error_text,
)
from tool_request import MCP_TOOL_COMMANDS, build_tool_request
from tool_schemas import SUPPORTED_TOOLS
from version import SERVER_NAME, SERVER_VERSION, TOOLREQUEST_PROTOCOL_VERSION

server = Server(SERVER_NAME)


def _notification_options() -> NotificationOptions:
    return NotificationOptions()


def _client() -> DaemonClient:
    return DaemonClient.from_env()


async def _ensure_protocol_compatible(client: DaemonClient) -> None:
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


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    await _ensure_protocol_compatible(_client())
    return list(SUPPORTED_TOOLS)


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
    arguments = arguments or {}
    if name not in MCP_TOOL_COMMANDS:
        return [error_text("unsupported_tool", f"Unsupported Bicameral MCP tool: {name}")]

    command_name = MCP_TOOL_COMMANDS[name]
    client = _client()
    try:
        await _ensure_protocol_compatible(client)
        tool_request = build_tool_request(
            command_name=command_name,
            params=arguments,
            authority=build_authority_context(name, arguments),
        )
        response = await client.send_tool_request(tool_request)
        if name == "bicameral.preflight":
            return [format_preflight_response(response)]
        return [format_tool_response(response)]
    except DaemonClientError as exc:
        return [
            recovery_error_text(
                exc,
                requested_tool=name,
                requested_command=command_name,
            )
        ]


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
