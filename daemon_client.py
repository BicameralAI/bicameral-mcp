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


class DaemonConnectionError(DaemonClientError):
    code = "daemon_unavailable"


class DaemonProtocolError(DaemonClientError):
    code = "daemon_protocol_mismatch"


class DaemonCapabilityError(DaemonClientError):
    code = "daemon_capability_error"


@dataclass(frozen=True)
class DaemonClient:
    base_url: str
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> DaemonClient:
        base_url = (
            os.environ.get("BICAMERAL_DAEMON_URL")
            or os.environ.get("BICAMERAL_BOT_DAEMON_URL")
            or "http://127.0.0.1:37373"
        )
        timeout = float(os.environ.get("BICAMERAL_DAEMON_TIMEOUT", "10"))
        return cls(base_url=base_url.rstrip("/"), timeout_seconds=timeout)

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
            raise DaemonConnectionError(f"daemon HTTP {exc.code} at {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise DaemonConnectionError(
                f"cannot reach bicameral-bot daemon at {url}: {exc}"
            ) from exc
        except TimeoutError as exc:
            raise DaemonConnectionError(
                f"timed out reaching bicameral-bot daemon at {url}"
            ) from exc

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DaemonProtocolError(f"daemon returned invalid JSON at {url}") from exc
        if not isinstance(decoded, dict):
            raise DaemonProtocolError(f"daemon returned non-object JSON at {url}")
        return decoded
