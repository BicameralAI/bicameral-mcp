"""Mode-aware proxy from MCP-side handlers to the bicameral daemon.

This module is the bridge between the MCP server process and the daemon
process. It resolves the configured mode (local UDS vs hosted HTTPS) by
reading config files in priority order, opens the connection lazily on
first RPC call, and exposes typed methods that handlers invoke.

Phase 2c-4 — load-bearing first-call-site PR. Only the local-mode UDS
path is implemented; the hosted-mode branch raises ``NotImplementedError``
to make the mode seam visible without committing to Phase 5 wiring.

Connection lifecycle: lazy connect on first call, hold for the lifetime
of the proxy (typically = lifetime of the MCP process), one retry on
detected disconnect (daemon restarted mid-session). No exponential
backoff — the second failure raises ``DaemonUnreachableError``.

Error message points users at ``bicameral-mcp setup`` (the wizard, which
walks them through local/hosted choice + daemon-or-auth bootstrap) and
``bicameral-mcp daemon start`` (explicit escape hatch for advanced users).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from daemon.process import DaemonDescriptor
from daemon.supervisor import default_descriptor_path, default_state_dir
from protocol.client import ProtocolClient
from protocol.contracts import LOCAL_TENANT_ID, ProtocolError

# Hosted-mode marker. Phase 5 fills in the body; for now its presence
# triggers a ``NotImplementedError`` from the proxy so users who shouldn't
# be in hosted mode get a clear "wait for Phase 5" signal instead of a
# silent fall-through to local mode.
DEFAULT_AUTH_PATH = default_state_dir() / "auth.json"


class DaemonUnreachableError(RuntimeError):
    """Raised when the proxy can't open a connection to any daemon.

    The message includes the actionable next steps (``bicameral-mcp setup``
    or ``bicameral-mcp daemon start``) so agents and humans get one-glance
    recovery instructions.
    """


_UNREACHABLE_HINT = (
    "Run `bicameral-mcp setup` to configure (local or hosted mode), "
    "or `bicameral-mcp daemon start` if you've already set up local mode."
)


class DaemonProxy:
    """Lazy-connect typed RPC client bound to a ``BicameralContext``.

    Construct cheaply (no I/O). The first call to a typed method opens the
    underlying ``ProtocolClient``, runs the version handshake and
    ``system.attach``, then memoizes the client for subsequent calls.

    Thread-safety: the internal lock guards the connection-establishment
    race when two concurrent handlers fire on the same proxy. Once the
    client is set, calls bypass the lock.
    """

    def __init__(
        self,
        *,
        descriptor_path: Path | None = None,
        auth_path: Path | None = None,
        tenant_id: str = LOCAL_TENANT_ID,
        user_id: str | None = None,
    ) -> None:
        self._descriptor_path = descriptor_path or default_descriptor_path()
        self._auth_path = auth_path or DEFAULT_AUTH_PATH
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._client: ProtocolClient | None = None
        self._lock = asyncio.Lock()

    async def _ensure_connected(self) -> ProtocolClient:
        """Open the connection on first call; reuse thereafter.

        Mode resolution order:
          1. ``auth.json`` present → raise NotImplementedError (Phase 5 stub)
          2. ``daemon.json`` present + socket connectable → use UDS
          3. Neither path resolves → raise DaemonUnreachableError
        """
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is not None:
                return self._client

            if self._auth_path.exists():
                # Hosted-mode marker present — Phase 5 fills this in. Raising
                # rather than silently falling back keeps the mode seam loud.
                raise NotImplementedError(
                    f"hosted mode found in {self._auth_path} but not yet wired — "
                    "see Phase 5 of the daemon-as-process arc plan"
                )

            descriptor = DaemonDescriptor.load(self._descriptor_path)
            if descriptor is None:
                raise DaemonUnreachableError(
                    f"can't reach the bicameral daemon: no descriptor at "
                    f"{self._descriptor_path}. {_UNREACHABLE_HINT}"
                )

            client = ProtocolClient(
                socket_path=descriptor.socket_path,
                tenant_id=self._tenant_id,
                user_id=self._user_id,
            )
            try:
                await client.connect()
            except (OSError, ProtocolError) as exc:
                # Descriptor exists but the daemon process is dead, the
                # socket isn't bound, or the path is otherwise invalid
                # (e.g. too long for AF_UNIX). All map to the same
                # actionable error: tell the user the daemon isn't
                # reachable. ``OSError`` covers ``ConnectionRefusedError``,
                # ``FileNotFoundError``, ``ENAMETOOLONG``, etc.
                raise DaemonUnreachableError(
                    f"daemon descriptor at {self._descriptor_path} but socket "
                    f"unreachable ({type(exc).__name__}: {exc}). {_UNREACHABLE_HINT}"
                ) from exc
            self._client = client
            return client

    async def _call_with_retry(self, method: str, params: dict[str, Any]) -> Any:
        """Call ``method`` with one reconnect on detected disconnect.

        If the existing connection has gone half-closed (daemon restarted
        between calls), the failing write raises ``ProtocolError`` *or*
        ``ConnectionResetError`` / ``BrokenPipeError`` depending on which
        asyncio layer notices first. We treat all three as the same
        "disconnect — retry once" signal, clear the cached client,
        re-resolve the connection, and retry exactly once. A second
        failure surfaces as ``DaemonUnreachableError``.
        """
        # Errors that mean "the previously-good connection is now dead" —
        # all three can surface from asyncio depending on whether the
        # daemon died mid-write, mid-read, or before the next call.
        _disconnect_errors = (ProtocolError, ConnectionResetError, BrokenPipeError)

        client = await self._ensure_connected()
        try:
            return await client._call(method, params)
        except _disconnect_errors as exc:
            del exc  # silence unused — kept for clarity of the failure mode
            # Drop the stale client. The retry path goes through
            # ``_ensure_connected`` which re-reads the descriptor and
            # re-opens the socket. If THAT fails it raises
            # DaemonUnreachableError, which is the right honest answer.
            self._client = None
            client = await self._ensure_connected()
            try:
                return await client._call(method, params)
            except _disconnect_errors as exc2:
                raise DaemonUnreachableError(
                    f"daemon RPC failed twice ({method}): {exc2}. {_UNREACHABLE_HINT}"
                ) from exc2

    async def close(self) -> None:
        """Best-effort cleanup. Safe to call multiple times."""
        async with self._lock:
            if self._client is not None:
                try:
                    await self._client.close()
                except Exception:  # noqa: BLE001 — close must never raise
                    pass
                self._client = None

    # ── Typed RPC methods (one per migrated handler) ────────────────────

    async def history(
        self,
        *,
        repo_id: str,
        ref: str = "HEAD",
        feature_filter: str | None = None,
        include_superseded: bool = True,
        include_pruned: bool = False,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        """Invoke ``read.history`` on the daemon and return the raw payload.

        The MCP-side ``handle_history`` facade is responsible for wrapping
        this dict back into a ``HistoryResponse`` model (and for adding any
        MCP-specific decoration like ``_guidance`` / ``_update`` notices).
        """
        return await self._call_with_retry(
            "read.history",
            {
                "repo_id": repo_id,
                "ref": ref,
                "feature_filter": feature_filter,
                "include_superseded": include_superseded,
                "include_pruned": include_pruned,
                "as_of": as_of,
            },
        )

    async def feedback(
        self,
        *,
        server_version: str,
        skill: str = "",
        trying_to: str = "",
        attempted: str = "",
        stuck_on: str = "",
    ) -> dict[str, Any]:
        """Invoke ``write.feedback`` on the daemon and return the raw payload.

        The MCP-side ``handle_feedback`` facade is responsible for wrapping
        this dict back into a ``FeedbackResult`` model.
        """
        return await self._call_with_retry(
            "write.feedback",
            {
                "server_version": server_version,
                "skill": skill,
                "trying_to": trying_to,
                "attempted": attempted,
                "stuck_on": stuck_on,
            },
        )

    async def skill_begin(
        self,
        *,
        session_id: str,
        skill_name: str,
    ) -> dict[str, Any]:
        """Invoke ``write.skill_begin`` on the daemon and return the raw payload.

        The MCP-side ``handle_skill_begin`` facade is responsible for wrapping
        this dict back into a ``SkillBeginResult`` model.
        """
        return await self._call_with_retry(
            "write.skill_begin",
            {
                "session_id": session_id,
                "skill_name": skill_name,
            },
        )

    async def skill_end(
        self,
        *,
        session_id: str,
        skill_name: str,
        server_version: str,
        errored: bool = False,
        error_class: str | None = None,
        diagnostic: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke ``write.skill_end`` on the daemon and return the raw payload.

        The MCP-side ``handle_skill_end`` facade is responsible for wrapping
        this dict back into a ``SkillEndResult`` model.
        """
        return await self._call_with_retry(
            "write.skill_end",
            {
                "session_id": session_id,
                "skill_name": skill_name,
                "server_version": server_version,
                "errored": errored,
                "error_class": error_class,
                "diagnostic": diagnostic,
            },
        )

    async def usage_summary(
        self,
        *,
        repo_id: str,
        days: int = 7,
    ) -> dict[str, Any]:
        """Invoke ``read.usage_summary`` on the daemon and return the raw payload.

        The MCP-side ``handle_usage_summary`` facade is responsible for
        round-tripping this dict through ``UsageSummaryResult.model_validate``
        to enforce the wire shape before returning to the agent.
        """
        return await self._call_with_retry(
            "read.usage_summary",
            {
                "repo_id": repo_id,
                "days": days,
            },
        )

    # ── Grounding RPC methods (Phase 2c-7a) ────────────────────────────

    async def validate_symbols(
        self,
        *,
        repo_id: str,
        ref: str = "HEAD",
        candidates: list[str],
    ) -> list[dict[str, Any]]:
        """Invoke ``grounding.lookup.validate_symbols`` on the daemon.

        Returns only the candidates that resolve to a known symbol in the
        code-locator index. Each entry is a Symbol-shaped dict with keys
        ``name``, ``file``, ``start_line``, ``end_line``.
        """
        return await self._call_with_retry(
            "grounding.lookup.validate_symbols",
            {
                "repo_id": repo_id,
                "ref": ref,
                "candidates": candidates,
            },
        )

    async def extract_symbols(
        self,
        *,
        repo_id: str,
        ref: str = "HEAD",
        file_path: str,
    ) -> list[dict[str, Any]]:
        """Invoke ``grounding.lookup.extract_symbols`` on the daemon.

        Returns all symbols extracted from ``file_path`` at the given ref
        via tree-sitter. Each entry is a Symbol-shaped dict.
        """
        return await self._call_with_retry(
            "grounding.lookup.extract_symbols",
            {
                "repo_id": repo_id,
                "ref": ref,
                "file_path": file_path,
            },
        )

    async def get_neighbors(
        self,
        *,
        repo_id: str,
        ref: str = "HEAD",
        symbol_id: int,
    ) -> list[dict[str, Any]]:
        """Invoke ``grounding.lookup.get_neighbors`` on the daemon.

        Returns call/import graph neighbors of ``symbol_id``. Each entry
        is a Neighbor-shaped dict with keys ``symbol_id``, ``name``, ``relation``.
        """
        return await self._call_with_retry(
            "grounding.lookup.get_neighbors",
            {
                "repo_id": repo_id,
                "ref": ref,
                "symbol_id": symbol_id,
            },
        )

    async def analyze_region(
        self,
        *,
        repo_id: str,
        ref: str = "HEAD",
        region: dict[str, Any],
        source_context: str = "",
    ) -> dict[str, Any]:
        """Invoke ``grounding.analyze.region`` on the daemon.

        Analyzes a single code region for drift against the stored baseline.
        ``region`` must be a CodeRegion-shaped dict with keys ``file``,
        ``symbol``, ``start_line``, ``end_line``, and optionally
        ``stored_hash``. Returns a DriftResult-shaped dict with keys
        ``status``, ``content_hash``, ``confidence``, ``explanation``.
        """
        return await self._call_with_retry(
            "grounding.analyze.region",
            {
                "repo_id": repo_id,
                "ref": ref,
                "region": region,
                "source_context": source_context,
            },
        )

    async def batch_analyze_regions(
        self,
        *,
        repo_id: str,
        ref: str = "HEAD",
        regions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Invoke ``grounding.analyze.batch`` on the daemon.

        Batch analysis of multiple code regions. Each region must be a
        CodeRegion-shaped dict. Results preserve input order. Each result is
        a DriftResult-shaped dict.
        """
        return await self._call_with_retry(
            "grounding.analyze.batch",
            {
                "repo_id": repo_id,
                "ref": ref,
                "regions": regions,
            },
        )

    async def ingest(
        self,
        *,
        adapter_name: str,
        payload: str,
        source_id: str,
        source_ref: str,
        mode: str = "active",
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Invoke ``write.ingest`` on the daemon and return the raw payload.

        The MCP-side ``handle_ingest`` facade is responsible for wrapping
        this dict back into an ``IngestResult`` model (and for building the
        ``IngestResponse`` returned to the agent).
        """
        return await self._call_with_retry(
            "write.ingest",
            {
                "adapter_name": adapter_name,
                "payload": payload,
                "source_id": source_id,
                "source_ref": source_ref,
                "mode": mode,
                "repo_id": repo_id,
            },
        )

    async def link_commit(
        self,
        *,
        repo_id: str,
        commit_sha: str,
        ref: str = "HEAD",
    ) -> dict[str, Any]:
        """Invoke ``write.link_commit`` on the daemon and return the raw payload.

        The MCP-side ``handle_link_commit`` facade is responsible for wrapping
        this dict back into a ``LinkCommitResult`` model (and for building the
        ``LinkCommitResponse`` returned to the agent).
        """
        return await self._call_with_retry(
            "write.link_commit",
            {
                "repo_id": repo_id,
                "commit_sha": commit_sha,
                "ref": ref,
            },
        )
