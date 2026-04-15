"""Bicameral MCP Server — Bicameral decision ledger + code locator tools.

9 tools:
  bicameral.status       — surface implementation status of tracked decisions
  bicameral.search       — pre-flight: find past decisions relevant to a query
  bicameral.drift        — code review: surface decisions touched by a file
  bicameral.link_commit  — heartbeat: sync a commit into the decision ledger
  bicameral.ingest       — ingest normalized decision/code evidence and advance source cursors
  validate_symbols       — fuzzy-match candidate symbol names against the code index
  search_code            — BM25 + graph search with RRF fusion
  get_neighbors          — 1-hop structural graph traversal around a symbol
  extract_symbols        — tree-sitter symbol extraction from a source file

Run with: bicameral-mcp (or python server.py) for stdio transport.

Env vars:
  REPO_PATH=.                — path to the repo being analyzed
  SURREAL_URL=surrealkv://~/.bicameral/ledger.db — SurrealDB URL (use memory:// for tests)
  CODE_LOCATOR_SQLITE_DB     — optional override for the local code index DB
"""

from __future__ import annotations

import asyncio
import sys
from argparse import ArgumentParser

import mcp.server.stdio
from mcp.server import Server
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.types import TextContent, Tool

from context import BicameralContext
from handlers.brief import handle_brief
from handlers.decision_status import handle_decision_status
from handlers.detect_drift import handle_detect_drift
from handlers.ingest import handle_ingest
from handlers.link_commit import handle_link_commit
from handlers.preflight import handle_preflight
from handlers.reset import handle_reset
from handlers.search_decisions import handle_search_decisions
from handlers.update import get_update_notice, handle_update

SERVER_NAME = "bicameral-mcp"
try:
    from importlib.metadata import version as _pkg_version
    SERVER_VERSION = _pkg_version("bicameral-mcp")
except Exception:
    SERVER_VERSION = "0.1.0"
EXPECTED_TOOL_NAMES = [
    "bicameral.status",
    "bicameral.search",
    "bicameral.drift",
    "bicameral.link_commit",
    "bicameral.ingest",
    "bicameral.update",
    "validate_symbols",
    "search_code",
    "get_neighbors",
    "extract_symbols",
]

server = Server(SERVER_NAME)


def _notification_options() -> NotificationOptions:
    return NotificationOptions()


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="bicameral.status",
            description=(
                "Surface implementation status of all tracked decisions for the repo. "
                "Shows which decisions are reflected in code, drifted, pending, or ungrounded. "
                "Auto-syncs the ledger to HEAD before returning status. Slash alias: /bicameral:status"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "enum": ["all", "drifted", "pending", "reflected", "ungrounded"],
                        "default": "all",
                        "description": "Filter decisions by status",
                    },
                    "since": {
                        "type": "string",
                        "description": "ISO date — only decisions ingested after this date",
                    },
                    "ref": {
                        "type": "string",
                        "default": "HEAD",
                        "description": "Git ref to evaluate against",
                    },
                },
            },
        ),
        Tool(
            name="bicameral.search",
            description=(
                "Pre-flight for implementation planning. Given a feature or task description, "
                "surface past decisions in the same area with their implementation status. "
                "Auto-syncs the ledger to HEAD before searching. "
                "Use this before writing code to check for prior constraints and decisions. "
                "Slash alias: /bicameral:search"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description — e.g. 'add retry with backoff'",
                    },
                    "max_results": {
                        "type": "integer",
                        "default": 10,
                    },
                    "min_confidence": {
                        "type": "number",
                        "default": 0.5,
                        "description": "Minimum BM25 confidence score (0–1)",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="bicameral.drift",
            description=(
                "Code review check. Given a file path, surface all decisions that touch "
                "symbols in that file — highlighting any that diverge from current content. "
                "Use before committing (use_working_tree=true) or during PR review (false). "
                "Slash alias: /bicameral:drift"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "File path relative to repo root",
                    },
                    "use_working_tree": {
                        "type": "boolean",
                        "default": True,
                        "description": "True = compare against disk (pre-commit), False = compare against HEAD",
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="bicameral.link_commit",
            description=(
                "Sync a commit into the decision ledger. Updates implemented_by/touches edges, "
                "recomputes content hashes, re-evaluates drift for affected decisions. "
                "Idempotent — calling twice for the same commit is a no-op. "
                "Slash alias: /bicameral:link-commit"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "commit_hash": {
                        "type": "string",
                        "default": "HEAD",
                        "description": "Git commit hash or ref to sync (default: HEAD)",
                    },
                },
            },
        ),
        Tool(
            name="bicameral.ingest",
            description=(
                "Ingest decisions into the ledger. Accepts two payload formats: "
                "(1) Internal: {mappings: [{intent, span, symbols, code_regions}]} "
                "(2) Natural: {decisions: [{title, description}], action_items: [...], open_questions: [...]} "
                "Auto-grounds decisions to code via BM25. Ensures code graph freshness before grounding. "
                "Slash alias: /bicameral:ingest"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "payload": {
                        "type": "object",
                        "description": "Normalized ingest payload matching the internal code-locator handoff shape",
                    },
                    "source_scope": {
                        "type": "string",
                        "default": "default",
                        "description": "Source stream identifier, e.g. Slack channel or Notion database",
                    },
                    "cursor": {
                        "type": "string",
                        "description": "Optional upstream checkpoint (timestamp, event id, updated_at)",
                    },
                },
                "required": ["payload"],
            },
        ),
        Tool(
            name="bicameral.update",
            description=(
                "Check for or apply a recommended bicameral-mcp update. "
                "action='check' returns current and recommended versions. "
                "action='apply' installs the recommended version via pip and prompts a server restart."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["check", "apply"],
                        "description": "'check' to see if an update is available, 'apply' to install it",
                    },
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="bicameral.brief",
            description=(
                "Pre-meeting one-pager generator. Given a topic (and optional participant list), "
                "returns relevant decisions with status, drift candidates, divergences (contradictory "
                "decisions on the same symbol), open gaps, and 3-5 suggested meeting questions. "
                "Use this before any 1:1, standup, or product review to depersonalize hard "
                "conversations by letting bicameral cite prior decisions for you. "
                "Slash alias: /bicameral:brief"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Topic or feature area to brief on (e.g. 'google calendar integration')",
                    },
                    "participants": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of meeting participants — surfaces decisions they were involved in",
                    },
                    "max_decisions": {
                        "type": "integer",
                        "default": 10,
                        "description": "Maximum decisions to include in the brief",
                    },
                },
                "required": ["topic"],
            },
        ),
        Tool(
            name="bicameral.reset",
            description=(
                "Fail-safe valve for a polluted ledger. Wipes every row scoped to the current repo "
                "and returns a replay plan listing the source_cursors that existed before the wipe, "
                "so the caller can re-run the original bicameral_ingest calls. "
                "DRY RUN BY DEFAULT — confirm=false returns the wipe plan without touching anything. "
                "Pass confirm=true to actually wipe. Scoped by repo, so multi-repo SurrealDB "
                "instances stay isolated. "
                "Slash alias: /bicameral:reset"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "default": False,
                        "description": "MUST be true to actually wipe. Default false returns a dry-run plan only.",
                    },
                    "replay": {
                        "type": "boolean",
                        "default": True,
                        "description": "When true, include the replay plan alongside the wipe summary",
                    },
                },
            },
        ),
        Tool(
            name="bicameral.preflight",
            description=(
                "Proactive context surfacing — call BEFORE implementing, building, modifying, "
                "refactoring, or adding any code that touches a tracked feature area. Returns "
                "prior decisions, drifted regions, divergent decision pairs, and unresolved open "
                "questions linked to the topic, gated by the user's guided_mode setting. "
                "In normal mode, fires only when there's actionable signal (drift, ungrounded, "
                "divergence, open question). In guided mode, fires on any matches. "
                "When fired=false, the agent MUST produce no output and proceed silently — "
                "that's the trust contract. When fired=true, render the surfaced context with "
                "a '(bicameral surfaced)' attribution before continuing with the implementation. "
                "Slash alias: /bicameral:preflight"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "1-line topic capturing the feature area the user is about to "
                            "implement. Extract from the user's prompt — e.g. 'Stripe webhook "
                            "payment_intent succeeded' or 'rate limiting middleware sliding window'. "
                            "Must be ≥4 chars and contain ≥2 non-stopword content tokens, otherwise "
                            "the handler returns fired=false."
                        ),
                    },
                    "participants": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of teammates the user mentioned — used by the chained brief call",
                    },
                },
                "required": ["topic"],
            },
        ),
        # ── Code locator tools (MCP-native) ──────────────────────────
        Tool(
            name="validate_symbols",
            description=(
                "Check if candidate symbol names exist in the codebase index. "
                "Returns fuzzy-matched symbols with confidence scores and symbol IDs. "
                "Use this first to verify symbol hypotheses before searching."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "candidates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Symbol name hypotheses to validate (e.g. ['CheckoutController', 'processOrder'])",
                    },
                },
                "required": ["candidates"],
            },
        ),
        Tool(
            name="search_code",
            description=(
                "Search the codebase using BM25 text search and structural graph traversal. "
                "Returns ranked code locations with file paths, line numbers, and scores. "
                "Optionally provide symbol_ids from validate_symbols to activate "
                "graph-based retrieval for better results."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text search query (e.g. 'checkout rate limit middleware')",
                    },
                    "symbol_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Symbol IDs from validate_symbols to use as graph traversal seeds",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_neighbors",
            description=(
                "Explore structural neighbors of a symbol via 1-hop graph traversal. "
                "Returns callers, callees, imports, and inheritance relationships. "
                "Use this to understand the context around a promising symbol."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol_id": {
                        "type": "integer",
                        "description": "Symbol ID from validate_symbols results",
                    },
                },
                "required": ["symbol_id"],
            },
        ),
        Tool(
            name="extract_symbols",
            description=(
                "Extract all symbols (functions, classes) from a source file via tree-sitter. "
                "Returns symbol names, types, and line ranges. No index required."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute or repo-relative path to the source file",
                    },
                },
                "required": ["file_path"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    import json

    ctx = BicameralContext.from_env()

    if name in ("bicameral.status", "decision_status"):
        result = await handle_decision_status(
            ctx,
            filter=arguments.get("filter", "all"),
            since=arguments.get("since"),
            ref=arguments.get("ref", "HEAD"),
        )
    elif name in ("bicameral.search", "search_decisions"):
        result = await handle_search_decisions(
            ctx,
            query=arguments["query"],
            max_results=arguments.get("max_results", 10),
            min_confidence=arguments.get("min_confidence", 0.5),
        )
    elif name in ("bicameral.drift", "detect_drift"):
        result = await handle_detect_drift(
            ctx,
            file_path=arguments["file_path"],
            use_working_tree=arguments.get("use_working_tree", True),
        )
    elif name in ("bicameral.link_commit", "link_commit"):
        result = await handle_link_commit(
            ctx,
            commit_hash=arguments.get("commit_hash", "HEAD"),
        )
    elif name in ("bicameral.ingest", "ingest"):
        result = await handle_ingest(
            ctx,
            payload=arguments["payload"],
            source_scope=arguments.get("source_scope", "default"),
            cursor=arguments.get("cursor", ""),
        )
    elif name == "bicameral.update":
        data = await handle_update(
            action=arguments["action"],
            current_version=SERVER_VERSION,
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]
    elif name in ("bicameral.brief", "brief"):
        result = await handle_brief(
            ctx,
            topic=arguments["topic"],
            participants=arguments.get("participants") or None,
            max_decisions=arguments.get("max_decisions", 10),
        )
    elif name in ("bicameral.reset", "reset"):
        result = await handle_reset(
            ctx,
            confirm=arguments.get("confirm", False),
            replay=arguments.get("replay", True),
        )
    elif name in ("bicameral.preflight", "preflight"):
        result = await handle_preflight(
            ctx,
            topic=arguments["topic"],
            participants=arguments.get("participants") or None,
        )
    # ── Code locator tools ────────────────────────────────────────
    elif name == "validate_symbols":
        data = await asyncio.to_thread(ctx.code_graph.validate_symbols, arguments["candidates"])
        return [TextContent(type="text", text=json.dumps(data, indent=2))]
    elif name == "search_code":
        data = await asyncio.to_thread(
            ctx.code_graph.search_code,
            arguments["query"],
            arguments.get("symbol_ids"),
        )
        return [TextContent(type="text", text=json.dumps(data, indent=2))]
    elif name == "get_neighbors":
        data = await asyncio.to_thread(ctx.code_graph.get_neighbors, arguments["symbol_id"])
        return [TextContent(type="text", text=json.dumps(data, indent=2))]
    elif name == "extract_symbols":
        data = await ctx.code_graph.extract_symbols(arguments["file_path"])
        return [TextContent(type="text", text=json.dumps(data, indent=2))]
    else:
        raise ValueError(f"Unknown tool: {name}")

    # Inject update notice into all bicameral ledger tool responses
    payload = result.model_dump()
    update_notice = get_update_notice(SERVER_VERSION)
    if update_notice:
        payload["_update"] = update_notice
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


async def run_smoke_test() -> dict[str, object]:
    """Validate package wiring without opening a long-lived stdio session."""
    from adapters.code_locator import get_code_locator
    from adapters.ledger import get_ledger

    tools = await list_tools()
    tool_names = [tool.name for tool in tools]
    if tool_names != EXPECTED_TOOL_NAMES:
        raise RuntimeError(
            f"Unexpected MCP tool registry: {tool_names!r} != {EXPECTED_TOOL_NAMES!r}"
        )

    code_locator = get_code_locator()
    ledger = get_ledger()
    if "Mock" not in type(code_locator).__name__:
        raise RuntimeError(
            f"Default code locator smoke path expected mock adapter, got {type(code_locator).__name__}"
        )
    if "Mock" not in type(ledger).__name__:
        raise RuntimeError(
            f"Default ledger smoke path expected mock adapter, got {type(ledger).__name__}"
        )

    server.get_capabilities(
        notification_options=_notification_options(),
        experimental_capabilities={},
    )
    return {
        "server_name": SERVER_NAME,
        "server_version": SERVER_VERSION,
        "tool_names": tool_names,
    }


async def serve_stdio() -> None:
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
    parser = ArgumentParser(description="Bicameral MCP server")
    subparsers = parser.add_subparsers(dest="command")

    # setup subcommand
    setup_parser = subparsers.add_parser(
        "setup",
        help="interactive setup — configure MCP client to use this server",
    )
    setup_parser.add_argument(
        "repo_path",
        nargs="?",
        default=None,
        help="path to the repo to analyze (auto-detected if omitted)",
    )

    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="validate package wiring and print the registered MCP tools, then exit",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {SERVER_VERSION}",
    )
    args = parser.parse_args(argv)

    if args.command == "setup":
        from setup_wizard import run_setup
        return run_setup(args.repo_path)

    if args.smoke_test:
        result = asyncio.run(run_smoke_test())
        print(f"{result['server_name']} {result['server_version']} smoke test passed")
        for tool_name in result["tool_names"]:
            print(tool_name)
        return 0

    asyncio.run(serve_stdio())
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main(sys.argv[1:]))
