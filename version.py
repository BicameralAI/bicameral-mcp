"""Version and protocol constants for the Bicameral MCP thin client."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

SERVER_NAME = "bicameral-mcp"
TOOLREQUEST_PROTOCOL_VERSION = "v2"


def resolve_server_version() -> str:
    here = Path(__file__).resolve().parent
    for candidate in (here / "pyproject.toml", here.parent / "pyproject.toml"):
        if candidate.exists():
            match = re.search(r'^version\s*=\s*"([^"]+)"', candidate.read_text(), re.MULTILINE)
            if match:
                return match.group(1)
    try:
        return version("bicameral-mcp")
    except PackageNotFoundError:
        return "0.0.0+local"


SERVER_VERSION = resolve_server_version()
