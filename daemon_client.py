"""HTTP client for the local bicameral-bot daemon ToolRequest surface."""

from __future__ import annotations

import ipaddress
import json
import os
import urllib.error
import urllib.parse
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
        "deferred_commands",
        "daemon_endpoint",
        "workspace_binding_available",
    )

    def __init__(
        self,
        *,
        daemon_protocol_version: str,
        mcp_protocol_version: str,
        supported_commands: tuple[str, ...],
        deferred_commands: tuple[str, ...] = (),
        daemon_endpoint: str,
        workspace_binding_available: bool = False,
    ) -> None:
        self.daemon_protocol_version = daemon_protocol_version
        self.mcp_protocol_version = mcp_protocol_version
        self.supported_commands = supported_commands
        self.deferred_commands = deferred_commands
        self.daemon_endpoint = daemon_endpoint
        # Truthful daemon-level workspace.bind capability discovery
        # (bicameral-bot#747 `CapabilityReport.workspace_binding_available`).
        # This is the daemon signal, never the always-false hosted-bridge
        # projection field of the same name. `workspace.bind` is always listed
        # in supported_commands as a protocol-recognized command, but the
        # daemon can only route it when this flag is true.
        self.workspace_binding_available = workspace_binding_available


DAEMON_URL_ENV_VARS = ("BICAMERAL_DAEMON_URL", "BICAMERAL_BOT_DAEMON_URL")
DEFAULT_DAEMON_URL = "http://127.0.0.1:37373"
DEFAULT_DAEMON_TIMEOUT_SECONDS = 10.0
MIN_DAEMON_TIMEOUT_SECONDS = 0.1
MAX_DAEMON_TIMEOUT_SECONDS = 60.0


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
            url = _validate_daemon_endpoint(value, env_var=env_var)
            return DaemonEndpoint(
                url=url,
                override_env_var=env_var,
                override_value=_redact_url(value),
            )
    return DaemonEndpoint(
        url=DEFAULT_DAEMON_URL,
        override_env_var=None,
        override_value=None,
    )


def resolve_daemon_endpoint_for_display() -> DaemonEndpoint:
    """Resolve daemon endpoint details for recovery payloads without failing.

    ``DaemonClient.from_env`` owns enforcement. Recovery rendering must still
    work when the configured override is invalid, so this helper returns a
    redacted display value instead of raising during error formatting.
    """
    for env_var in DAEMON_URL_ENV_VARS:
        value = os.environ.get(env_var)
        if value:
            display_value = _redact_url(value)
            return DaemonEndpoint(
                url=display_value.rstrip("/"),
                override_env_var=env_var,
                override_value=display_value,
            )
    return DaemonEndpoint(
        url=DEFAULT_DAEMON_URL,
        override_env_var=None,
        override_value=None,
    )


def _validate_daemon_endpoint(value: str, *, env_var: str) -> str:
    raw_value = value.strip()
    parsed = urllib.parse.urlparse(raw_value)
    if parsed.scheme not in {"http", "https"}:
        raise DaemonConnectionError(
            f"{env_var} must use http:// or https:// for the local daemon endpoint",
            daemon_endpoint=_redact_url(raw_value),
        )
    if not parsed.hostname:
        raise DaemonConnectionError(
            f"{env_var} must include a local daemon hostname",
            daemon_endpoint=_redact_url(raw_value),
        )
    if parsed.username or parsed.password:
        raise DaemonConnectionError(
            f"{env_var} must not include credentials in the daemon URL",
            daemon_endpoint=_redact_url(raw_value),
        )
    if parsed.query or parsed.fragment:
        raise DaemonConnectionError(
            f"{env_var} must not include query strings or fragments",
            daemon_endpoint=_redact_url(raw_value),
        )
    if parsed.path not in ("", "/"):
        raise DaemonConnectionError(
            f"{env_var} must point at the daemon root, not a path prefix",
            daemon_endpoint=_redact_url(raw_value),
        )
    if not _is_loopback_host(parsed.hostname):
        raise DaemonConnectionError(
            f"{env_var} must point to a loopback/localhost daemon endpoint",
            daemon_endpoint=_redact_url(raw_value),
        )

    sanitized = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    return sanitized.rstrip("/")


def _is_loopback_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _resolve_timeout_seconds() -> float:
    raw_value = os.environ.get("BICAMERAL_DAEMON_TIMEOUT")
    if raw_value is None:
        return DEFAULT_DAEMON_TIMEOUT_SECONDS
    try:
        timeout = float(raw_value)
    except ValueError as exc:
        raise DaemonConnectionError("BICAMERAL_DAEMON_TIMEOUT must be a number of seconds") from exc
    if not MIN_DAEMON_TIMEOUT_SECONDS <= timeout <= MAX_DAEMON_TIMEOUT_SECONDS:
        raise DaemonConnectionError(
            "BICAMERAL_DAEMON_TIMEOUT must be between "
            f"{MIN_DAEMON_TIMEOUT_SECONDS} and {MAX_DAEMON_TIMEOUT_SECONDS} seconds"
        )
    return timeout


def _redact_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value.strip())
    if not parsed.netloc:
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


@dataclass(frozen=True)
class DaemonClient:
    base_url: str
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> DaemonClient:
        endpoint = resolve_daemon_endpoint()
        timeout = _resolve_timeout_seconds()
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
        display_url = _redact_url(url)
        display_endpoint = _redact_url(self.base_url)
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
                f"daemon HTTP {exc.code} at {display_url}: {detail}",
                daemon_endpoint=display_endpoint,
            ) from exc
        except urllib.error.URLError as exc:
            raise DaemonConnectionError(
                f"cannot reach bicameral-bot daemon at {display_url}: {exc}",
                daemon_endpoint=display_endpoint,
            ) from exc
        except TimeoutError as exc:
            raise DaemonConnectionError(
                f"timed out reaching bicameral-bot daemon at {display_url}",
                daemon_endpoint=display_endpoint,
            ) from exc

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DaemonProtocolError(f"daemon returned invalid JSON at {display_url}") from exc
        if not isinstance(decoded, dict):
            raise DaemonProtocolError(f"daemon returned non-object JSON at {display_url}")
        return decoded
