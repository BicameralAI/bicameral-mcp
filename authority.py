"""AuthorityContext assembly for MCP-originated ToolRequests."""

from __future__ import annotations

import getpass
import os
import socket
from typing import Any

from version import SERVER_VERSION


def build_authority_context(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    actor_id = (
        arguments.get("actor_id")
        or os.environ.get("BICAMERAL_ACTOR_ID")
        or os.environ.get("USER")
        or getpass.getuser()
        or "local-mcp-user"
    )
    session_id = arguments.get("session_id") or os.environ.get("BICAMERAL_MCP_SESSION_ID")
    workspace = (
        arguments.get("workspace")
        or os.environ.get("BICAMERAL_WORKSPACE")
        or os.environ.get("REPO_PATH")
        or os.getcwd()
    )
    policy_scope = arguments.get("policy_scope") or os.environ.get("BICAMERAL_POLICY_SCOPE")
    if isinstance(policy_scope, str):
        policy_scope = [part.strip() for part in policy_scope.split(",") if part.strip()]
    if not policy_scope:
        policy_scope = ["default"]

    return {
        "actor_id": str(actor_id),
        "auth_method": "mcp_session",
        "session_id": str(session_id) if session_id else None,
        "workspace": str(workspace) if workspace else None,
        "policy_scope": policy_scope,
        "audit_metadata": {
            "surface": "mcp",
            "mcp_tool": tool_name,
            "mcp_version": SERVER_VERSION,
            "client_name": os.environ.get("MCP_CLIENT_NAME"),
            "client_version": os.environ.get("MCP_CLIENT_VERSION"),
            "host": socket.gethostname(),
        },
    }
