"""HTTP client for the local bicameral-bot daemon ToolRequest surface."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class DaemonClientError(RuntimeError):
    code = "daemon_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        # Informational context carried to the recovery payload renderer.
        # None values are dropped so absent context does not leak null fields.
        self.details: dict[str, Any] = {
            key: value for key, value in details.items() if value is not None
        }


class DaemonConnectionError(DaemonClientError):
    code = "daemon_unavailable"


class DaemonProtocolError(DaemonClientError):
    code = "daemon_protocol_mismatch"


class DaemonCapabilityError(DaemonClientError):
    code = "daemon_capability_error"


class CapabilityReport:
    """Structured result from a successful daemon capability handshake.

    Captures the daemon's advertised protocol version and command surface
    so callers can inspect what the tagged daemon actually supports.
    """

    __slots__ = (
        "daemon_protocol_version",
        "mcp_protocol_version",
        "supported_commands",
        "daemon_endpoint",
    )

    def __init__(
        self,
        *,
        daemon_protocol_version: str,
        mcp_protocol_version: str,
        supported_commands: tuple[str, ...],
        daemon_endpoint: str,
    ) -> None:
        self.daemon_protocol_version = daemon_protocol_version
        self.mcp_protocol_version = mcp_protocol_version
        self.supported_commands = supported_commands
        self.daemon_endpoint = daemon_endpoint


DAEMON_URL_ENV_VARS = ("BICAMERAL_DAEMON_URL", "BICAMERAL_BOT_DAEMON_URL")
DEFAULT_DAEMON_URL = "http://127.0.0.1:37373"


@dataclass(frozen=True)
class DaemonEndpoint:
    url: str
    override_env_var: str | None
    override_value: str | None


def resolve_daemon_endpoint() -> DaemonEndpoint:
    """Resolve the daemon URL and report which env override (if any) set it."""
    for env_var in DAEMON_URL_ENV_VARS:
        value = os.environ.get(env_var)
        if value:
            return DaemonEndpoint(
                url=value.rstrip("/"),
                override_env_var=env_var,
                override_value=value,
            )
    return DaemonEndpoint(
        url=DEFAULT_DAEMON_URL,
        override_env_var=None,
        override_value=None,
    )


@dataclass(frozen=True)
class DaemonClient:
    base_url: str
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> DaemonClient:
        endpoint = resolve_daemon_endpoint()
        timeout = float(os.environ.get("BICAMERAL_DAEMON_TIMEOUT", "10"))
        return cls(base_url=endpoint.url, timeout_seconds=timeout)

    async def capabilities(self) -> dict[str, Any]:
        return await self._json_request("GET", "/v2/capabilities")

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        response = await self._json_request("POST", "/v2/tool-requests", tool_request)
        if not isinstance(response, dict):
            raise DaemonProtocolError("daemon returned a non-object ToolResponse")
        if response.get("status") == "error" and response.get("message") == "unsupported_command":
            raise DaemonCapabilityError(str(response))
        return response

    async def _json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import asyncio

        return await asyncio.to_thread(self._json_request_sync, method, path, payload)

    def _json_request_sync(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"content-type": "application/json", "accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise DaemonConnectionError(
                f"daemon HTTP {exc.code} at {url}: {detail}",
                daemon_endpoint=self.base_url,
            ) from exc
        except urllib.error.URLError as exc:
            raise DaemonConnectionError(
                f"cannot reach bicameral-bot daemon at {url}: {exc}",
                daemon_endpoint=self.base_url,
            ) from exc
        except TimeoutError as exc:
            raise DaemonConnectionError(
                f"timed out reaching bicameral-bot daemon at {url}",
                daemon_endpoint=self.base_url,
            ) from exc

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DaemonProtocolError(f"daemon returned invalid JSON at {url}") from exc
        if not isinstance(decoded, dict):
            raise DaemonProtocolError(f"daemon returned non-object JSON at {url}")
        return decoded
