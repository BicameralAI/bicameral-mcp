"""Bicameral MCP Server — Bicameral decision ledger + code locator tools.

14 tools:
  bicameral.link_commit       — heartbeat: sync a commit into the decision ledger
  bicameral.ingest            — ingest normalized decision/code evidence and advance source cursors
  bicameral.update            — check for or apply a recommended bicameral-mcp update
  bicameral.reset             — wipe ledger rows scoped to the current repo
  bicameral.preflight         — proactive context surfacing before implementation
  bicameral.judge_gaps        — caller-LLM business-requirement gap judge
  bicameral.resolve_compliance — caller-LLM compliance verdict write-back (v0.5.0 three-way)
  bicameral.ratify            — product sign-off on a decision (double-entry ledger)
  bicameral.history           — read-only ledger dump grouped by feature area
  bicameral.dashboard         — launch live decision dashboard with SSE push updates
  validate_symbols            — fuzzy-match candidate symbol names against the code index
  search_code                 — BM25 + graph search with RRF fusion
  get_neighbors               — 1-hop structural graph traversal around a symbol
  extract_symbols             — tree-sitter symbol extraction from a source file

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
from handlers.bind import handle_bind
from handlers.gap_judge import handle_judge_gaps
from handlers.ingest import handle_ingest
from handlers.link_commit import handle_link_commit
from handlers.preflight import handle_preflight
from handlers.reset import handle_reset
from handlers.ratify import handle_ratify
from handlers.resolve_compliance import handle_resolve_compliance
from handlers.history import handle_history
from handlers.update import get_update_notice, handle_update
from dashboard.server import get_dashboard_server

SERVER_NAME = "bicameral-mcp"


def _resolve_server_version() -> str:
    """Return the version of the code actually running.

    Prefers pyproject.toml (authoritative when running from source) over the
    installed-package metadata, which may be stale when the source tree is
    ahead of the last `pip install`.
    """
    import re
    from pathlib import Path

    here = Path(__file__).parent
    for candidate in (here, here.parent):
        toml = candidate / "pyproject.toml"
        if toml.exists():
            m = re.search(
                r'^version\s*=\s*"([^"]+)"', toml.read_text(), re.MULTILINE
            )
            if m:
                return m.group(1)

    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("bicameral-mcp")
    except Exception:
        return "0.1.0"


SERVER_VERSION = _resolve_server_version()
EXPECTED_TOOL_NAMES = [
    "bicameral.link_commit",
    "bicameral.ingest",
    "bicameral.bind",
    "bicameral.update",
    "bicameral.reset",
    "bicameral.preflight",
    "bicameral.judge_gaps",
    "bicameral.resolve_compliance",
    "bicameral.ratify",
    "bicameral.history",
    "bicameral.dashboard",
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
                "(1) Internal: {query, mappings: [{intent, span: {text, source_type, source_ref, meeting_date}, "
                "symbols, code_regions: [{symbol, file_path, start_line, end_line, type}]}]}. "
                "(2) Natural: {query, source, title, date, participants, "
                "decisions: [{description, id?, title?, status?, participants?}], "
                "action_items: [{action, owner?, due?}], open_questions?: [string]}. "
                "Canonical decision text field is `description` (also accepts `title` as a synonym and `text` as a "
                "v0.4.16+ alias). Canonical action-item text field is `action` (also accepts `text` as an alias). "
                "At least one text field per decision must be non-empty or the decision is silently dropped. "
                "The `query` field drives the post-ingest auto-brief and gap-judge chain — always pass it. "
                "Auto-grounds decisions to code via semantic search over the symbol graph. Ensures the code index is fresh before grounding. "
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
            name="bicameral.bind",
            description=(
                "Write a decision→code_region binding that the caller LLM discovered. "
                "Use this after you've found the correct file, symbol, and line range for "
                "a decision that is pending grounding. The server upserts the code_region, "
                "creates the binds_to edge, transitions the decision from ungrounded→pending, "
                "and returns a PendingComplianceCheck ready for bicameral.resolve_compliance. "
                "Pass start_line/end_line when you have exact lines (e.g. from a Read call) — "
                "omit them to let the server resolve the exact line range automatically. Binding the same "
                "(decision, region) pair twice is idempotent. "
                "Slash alias: /bicameral:bind"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "bindings": {
                        "type": "array",
                        "description": "List of decision→code bindings to write",
                        "items": {
                            "type": "object",
                            "properties": {
                                "decision_id": {"type": "string", "description": "Decision ID from the ledger (e.g. from pending_grounding_decisions)"},
                                "file_path": {"type": "string", "description": "Repo-relative path to the file"},
                                "symbol_name": {"type": "string", "description": "Function/class/method name"},
                                "start_line": {"type": "integer", "description": "1-indexed start line (optional — omit to auto-resolve automatically)"},
                                "end_line": {"type": "integer", "description": "1-indexed end line (optional)"},
                                "purpose": {"type": "string", "description": "Optional one-line description for display"},
                            },
                            "required": ["decision_id", "file_path", "symbol_name"],
                        },
                    },
                },
                "required": ["bindings"],
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
            name="bicameral.reset",
            description=(
                "Fail-safe valve for a polluted ledger. Wipes every row scoped to the current repo "
                "and returns a replay plan listing the source_cursors that existed before the wipe, "
                "so the caller can re-run the original bicameral_ingest calls. "
                "DRY RUN BY DEFAULT — confirm=false returns the wipe plan without touching anything. "
                "Pass confirm=true to actually wipe. Scoped by repo, so multi-repo ledger "
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
        Tool(
            name="bicameral.judge_gaps",
            description=(
                "Caller-session LLM gap judge (v0.4.16). Given a topic, returns a structured "
                "context pack — decisions in scope with source excerpts, cross-symbol related "
                "decision ids, phrasing-based gaps, and a 5-category rubric with a judgment "
                "prompt. The calling agent applies the rubric to the pack IN ITS OWN SESSION, "
                "using its own LLM and filesystem tools for the infrastructure_gap crawl. "
                "The server never calls an LLM, never holds an API key. Returns None (honest "
                "empty path) when no decisions match the topic. Typically fired automatically "
                "by the bicameral-ingest skill after the post-ingest brief; also callable "
                "standalone. Rubric categories: missing_acceptance_criteria, "
                "underdefined_edge_cases, infrastructure_gap, underspecified_integration, "
                "missing_data_requirements. "
                "Slash alias: /bicameral:judge_gaps"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "Topic or feature area to judge gaps on (e.g. 'onboarding email flow'). "
                            "Reuses the same retrieval contract as bicameral.brief."
                        ),
                    },
                    "max_decisions": {
                        "type": "integer",
                        "default": 10,
                        "description": "Maximum decisions to include in the context pack",
                    },
                },
                "required": ["topic"],
            },
        ),
        Tool(
            name="bicameral.resolve_compliance",
            description=(
                "Caller-LLM verification write-back (v0.5.0+). Single tool for every "
                "compliance verdict the caller LLM produces — ingest-time grounding, "
                "drift detection, re-grounding after rename, supersession, divergence. "
                "Receives a batch of verdicts the caller LLM evaluated against the "
                "pending_compliance_checks payload from a prior link_commit / ingest "
                "auto-chain response, and persists them in the compliance_check cache "
                "keyed on (decision_id, region_id, content_hash). Status of affected "
                "decisions is projected holistically at next read from the cache. "
                "Three-way verdict: 'compliant' = code matches decision; 'drifted' = "
                "mismatch; 'not_relevant' = region not related (prunes the binds_to "
                "edge). Idempotent: replaying the same batch is a no-op. Unknown "
                "decision/region IDs are returned as structured rejections (not "
                "exceptions) so the caller can retry the accepted subset. The server "
                "never calls an LLM — every semantic judgment lives in the caller's "
                "session. Slash alias: /bicameral:resolve_compliance"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "phase": {
                        "type": "string",
                        "enum": ["ingest", "drift", "regrounding", "supersession", "divergence"],
                        "description": (
                            "Phase tag the compliance_check rows are written under. "
                            "Match the phase field on the PendingComplianceCheck "
                            "entries you're resolving — group by phase if a single "
                            "response mixes them, and call this tool once per phase."
                        ),
                    },
                    "verdicts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "decision_id": {"type": "string"},
                                "region_id": {"type": "string"},
                                "content_hash": {
                                    "type": "string",
                                    "description": (
                                        "Echo the content_hash from the corresponding "
                                        "PendingComplianceCheck verbatim — this is the "
                                        "cache key the verdict will be stored under."
                                    ),
                                },
                                "verdict": {
                                    "type": "string",
                                    "enum": ["compliant", "drifted", "not_relevant"],
                                    "description": (
                                        "'compliant' = code satisfies the decision; "
                                        "'drifted' = code diverges from the decision; "
                                        "'not_relevant' = this region is unrelated to "
                                        "the decision (prunes the binds_to edge)."
                                    ),
                                },
                                "confidence": {
                                    "type": "string",
                                    "enum": ["high", "medium", "low"],
                                },
                                "explanation": {
                                    "type": "string",
                                    "description": "One-sentence rationale for audit",
                                },
                            },
                            "required": [
                                "decision_id",
                                "region_id",
                                "content_hash",
                                "verdict",
                                "confidence",
                                "explanation",
                            ],
                        },
                    },
                    "commit_hash": {
                        "type": "string",
                        "description": (
                            "Optional provenance — the commit SHA that triggered the "
                            "verification (typically passed for phase='drift')."
                        ),
                    },
                },
                "required": ["phase", "verdicts"],
            },
        ),
        Tool(
            name="bicameral.ratify",
            description=(
                "Product sign-off for a decision (v0.5.0+). One-shot, idempotent. "
                "Sets product_signoff on the decision record — the second entry in the "
                "double-entry ledger (first: code grounding via compliance_check; "
                "second: product owner sign-off via ratify). A decision is only "
                "'reflected' when both entries are present and all bound regions are "
                "compliant. Calling ratify on an already-ratified decision is a no-op "
                "(returns was_new=false) — there is no unratify. The signer field "
                "identifies the human or agent setting the sign-off; the optional note "
                "captures the rationale for audit. "
                "Slash alias: /bicameral:ratify"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "decision_id": {
                        "type": "string",
                        "description": "The decision to ratify (UUIDv5 decision ID from the ledger)",
                    },
                    "signer": {
                        "type": "string",
                        "description": "Identity of the product owner or agent setting the sign-off",
                    },
                    "note": {
                        "type": "string",
                        "default": "",
                        "description": "Optional rationale or context for the sign-off (for audit)",
                    },
                },
                "required": ["decision_id", "signer"],
            },
        ),
        Tool(
            name="bicameral.history",
            description=(
                "Read-only dump of the full decision ledger in a renderable shape. "
                "Returns decisions grouped by feature area with their sources, code grounding, "
                "and current status. Use this to see everything tracked — 'show the decision history', "
                "'list all decisions', 'what's in the ledger', 'show me everything tracked'. "
                "Capped at 50 features; use feature_filter to drill in when truncated=True. "
                "Does NOT fire on implementation, ingest, or drift-specific queries — use "
                "bicameral.preflight or bicameral.ingest for those. "
                "Slash alias: /bicameral:history"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "feature_filter": {
                        "type": "string",
                        "description": "Optional substring match on feature name (case-insensitive)",
                    },
                    "include_superseded": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include superseded decisions in the response",
                    },
                    "as_of": {
                        "type": "string",
                        "description": "Git ref to evaluate against (default: HEAD)",
                    },
                },
            },
        ),
        Tool(
            name="bicameral.dashboard",
            description=(
                "Launch (or return the URL of) the live decision dashboard. "
                "Spins up a local HTTP server inside the MCP process, serves an "
                "interactive single-page view of the full decision ledger, and "
                "pushes live updates via SSE whenever bicameral.ingest or "
                "bicameral.link_commit writes new data. "
                "Subsequent calls return the existing URL immediately — the server "
                "is a singleton and stays running for the session. "
                "Fires on: 'open dashboard', 'show live history', 'launch dashboard'. "
                "Slash alias: /bicameral:dashboard"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "open_browser": {
                        "type": "boolean",
                        "default": True,
                        "description": "When true, instruct the caller to open the URL in a browser",
                    },
                },
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
                "Search the codebase using text search and structural graph traversal. "
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
                "Extract all symbols (functions, classes) from a source file via static parsing. "
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
    import time

    from telemetry import record_event

    ctx = BicameralContext.from_env()
    _t0 = time.monotonic()
    _errored = False
    _diagnostic: dict | None = None

    try:
        if name in ("bicameral.link_commit", "link_commit"):
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
                repo_path=str(ctx.repo_path),
            )
            return [TextContent(type="text", text=json.dumps(data, indent=2))]
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
        elif name in ("bicameral.judge_gaps", "judge_gaps"):
            result = await handle_judge_gaps(
                ctx,
                topic=arguments["topic"],
                max_decisions=arguments.get("max_decisions", 10),
            )
            # Honest empty path — handler returns None when no matches.
            # Emit an empty envelope the agent can detect and skip on.
            if result is None:
                return [TextContent(
                    type="text",
                    text=json.dumps({"judgment_payload": None, "topic": arguments["topic"]}),
                )]
        elif name in ("bicameral.resolve_compliance", "resolve_compliance"):
            result = await handle_resolve_compliance(
                ctx,
                phase=arguments["phase"],
                verdicts=arguments["verdicts"],
                commit_hash=arguments.get("commit_hash"),
            )
        elif name in ("bicameral.ratify", "ratify"):
            result = await handle_ratify(
                ctx,
                decision_id=arguments["decision_id"],
                signer=arguments["signer"],
                note=arguments.get("note", ""),
            )
        elif name in ("bicameral.bind", "bind"):
            result = await handle_bind(
                ctx,
                bindings=arguments.get("bindings", []),
            )
        elif name in ("bicameral.history", "history"):
            result = await handle_history(
                ctx,
                feature_filter=arguments.get("feature_filter"),
                include_superseded=arguments.get("include_superseded", True),
                as_of=arguments.get("as_of"),
            )
            # Inject empty-ledger guidance so the caller-LLM doesn't bypass ingest.
            if result.total_features == 0:
                payload = result.model_dump()
                payload["_guidance"] = (
                    "The decision ledger is empty — no decisions have been ingested yet. "
                    "STOP: do not read the codebase or make code changes yet. "
                    "Instead: (1) call bicameral.ingest with the meeting transcript, "
                    "Slack thread, or document that contains the relevant decisions; "
                    "(2) review the extracted decisions in the ingest response; "
                    "(3) only then use those decisions to guide the implementation."
                )
                update_notice = get_update_notice(SERVER_VERSION)
                if update_notice:
                    payload["_update"] = update_notice
                return [TextContent(type="text", text=json.dumps(payload, indent=2))]
        elif name in ("bicameral.dashboard", "dashboard"):
            from contracts import DashboardResponse
            srv = get_dashboard_server()
            if not srv.running:
                await srv.start(ctx_factory=BicameralContext.from_env)
                status = "started"
            else:
                status = "already_running"
            result = DashboardResponse(url=srv.url, status=status, port=srv.port)
            payload = result.model_dump()
            update_notice = get_update_notice(SERVER_VERSION)
            if update_notice:
                payload["_update"] = update_notice
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]
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

        # After a successful ingest that extracted decisions, remind the caller-LLM
        # to review those decisions before touching any code.
        if name in ("bicameral.ingest", "ingest"):
            stats = payload.get("stats") or {}
            created = stats.get("intents_created", 0)
            if created > 0:
                grounded = stats.get("grounded", 0)
                ungrounded = stats.get("ungrounded", 0)
                _diagnostic = {
                    "grounded_count": grounded,
                    "ungrounded_count": ungrounded,
                    "decisions_created": created,
                }
                payload["_guidance"] = (
                    f"Ingest complete: {created} decision(s) extracted "
                    f"({grounded} grounded to code, {ungrounded} ungrounded). "
                    "STOP: review the 'brief' and 'ungrounded_decisions' fields above "
                    "before making any code changes. Use those decisions — not your own "
                    "analysis — as the implementation spec. "
                    "Call bicameral.history to see the full ledger at any time."
                )

        return [TextContent(type="text", text=json.dumps(payload, indent=2))]

    except Exception:
        _errored = True
        raise
    finally:
        _duration_ms = int((time.monotonic() - _t0) * 1000)
        record_event(name, _duration_ms, _errored, SERVER_VERSION, _diagnostic)


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
    # Start the live dashboard HTTP sidecar in the background.
    # It binds to a free port and stays running for the session.
    dashboard_srv = get_dashboard_server()
    await dashboard_srv.start(ctx_factory=BicameralContext.from_env)

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
    setup_parser.add_argument(
        "--history-path",
        default=None,
        metavar="PATH",
        help="separate directory for .bicameral/ history storage (default: same as repo)",
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
        return run_setup(args.repo_path, args.history_path)

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
