"""Deterministic v0 user-flow replay for the MCP thin client.

This is the no-LLM replacement for the shelved live v0 user-flow gate
(mcp#555 / mcp#628). It drives the public MCP tool surface through the real
``server.call_tool`` path, seams the daemon with an in-process memory fake, and
asserts protocol facts that are stable enough to block CI:

* expected ToolRequest command sequence,
* schema-shaped response envelopes,
* typed failure/currentness states preserved from the daemon, and
* no mutation flags on read/advisory flow steps.

It intentionally does not evaluate live agent tool choice, extraction quality,
provider availability, transcript similarity, or recording/demo output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from tool_request import MCP_TOOL_COMMANDS  # noqa: E402
from version import TOOLREQUEST_PROTOCOL_VERSION  # noqa: E402

MEMORY_WORKSPACE = "memory://issue-108-v0-replay"

EXPECTED_COMMAND_SEQUENCE = (
    "ingest.submit_local",
    "lookup.query",  # coverage guard (#343) probes before full preflight
    "preflight.run",
    "binding.inspect",
    "binding.create",
    "evidence.refresh",
    "history.list",
    "search.query",
)

READ_OR_ADVISORY_COMMANDS = {
    "preflight.run",
    "binding.inspect",
    "evidence.refresh",
    "history.list",
    "search.query",
}

FORBIDDEN_LEGACY_COMMANDS = {
    "link_commit",
    "resolve_compliance",
    "review.resolve_compliance",
}


@dataclass(frozen=True)
class ReplayStep:
    name: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ReplayResult:
    command_sequence: list[str]
    responses: list[dict[str, Any]]
    request_count: int


class MemoryReplayDaemon:
    """Small fake daemon that records ToolRequests and returns typed fixtures."""

    protocol_version = TOOLREQUEST_PROTOCOL_VERSION

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.revision = 0

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": self.protocol_version,
            "supported_commands": list(MCP_TOOL_COMMANDS.values()),
            "storage": {"kind": "memory", "workspace": MEMORY_WORKSPACE},
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        command = tool_request["command"]["name"]
        params = tool_request["command"]["params"]

        if command == "ingest.submit_local":
            self.revision += 1
            return self._response(
                tool_request,
                "ok",
                {
                    "candidate_id": "cand-v0-meeting",
                    "decision_id": "DEC-v0-001",
                    "storage": "memory://",
                    "ledger_revision": self.revision,
                },
            )

        if command == "lookup.query":
            # Coverage guard probe — return matches so guard falls through to
            # full preflight (simulates files that DO have ledger coverage).
            return self._response(
                tool_request,
                "ok",
                {
                    "recall_packet": {
                        "searched_sources": params.get("files", []),
                        "matches": [{"decision_id": "DEC-v0-001", "confidence": 0.9}],
                        "unknown_scope": [],
                    },
                    "mutation": "none",
                    "ledger_revision": self.revision,
                },
            )

        if command == "preflight.run":
            return {
                **self._response(
                    tool_request,
                    "ok",
                    {
                        "relevant_decisions": ["DEC-v0-001"],
                        "mutation": "none",
                        "ledger_revision": self.revision,
                    },
                ),
                "staged": {
                    "capture": {"status": "not_configured"},
                    "projection": {"status": "not_configured"},
                    "lookup": {
                        "status": "completed",
                        "decision_refs": ["DEC-v0-001"],
                        "limitations": ["source-only deterministic replay"],
                    },
                    "enforcement": {"status": "not_configured"},
                    "session_directive": {"mode": "continue"},
                },
            }

        if command == "binding.inspect":
            return self._response(
                tool_request,
                "stale",
                {
                    "decision_or_candidate_id": params["decision_or_candidate_id"],
                    "binding_scope": {"status": "stale"},
                    "mutation": "none",
                    "ledger_revision": self.revision,
                },
            )

        if command == "binding.create":
            self.revision += 1
            effective_ref = params.get("commit_sha", "authoritative-fallback")
            return self._response(
                tool_request,
                "ok",
                {
                    "decision_or_candidate_id": params["decision_or_candidate_id"],
                    "evidence_state": "verified",
                    "verified": True,
                    "bind_effective_ref": effective_ref,
                    "ledger_revision": self.revision,
                },
            )

        if command == "evidence.refresh":
            return self._response(
                tool_request,
                "content_changed",
                {
                    "decision_id": params["decision_id"],
                    "currentness": "content_changed",
                    "signoff_mutated": False,
                    "compliance_mutated": False,
                    "binding_evidence_mutated": False,
                    "ledger_revision": self.revision,
                },
            )

        if command == "history.list":
            return self._response(
                tool_request,
                "ok",
                {
                    "items": [{"decision_id": "DEC-v0-001", "title": "Use deterministic CI"}],
                    "binding_scope": {"status": "unsupported"},
                    "mutation": "none",
                    "ledger_revision": self.revision,
                },
            )

        if command == "search.query":
            return self._response(
                tool_request,
                "not_found",
                {
                    "query": params["query"],
                    "results": [],
                    "binding_scope": {"status": "unsupported"},
                    "mutation": "none",
                    "ledger_revision": self.revision,
                },
            )

        return self._response(
            tool_request,
            "unsupported",
            {"unsupported_command": command, "mutation": "none"},
        )

    @staticmethod
    def _response(
        tool_request: dict[str, Any],
        status: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "request_id": tool_request["request_id"],
            "status": status,
            "result": result,
            "responded_at": "2026-06-24T00:00:00Z",
        }


def replay_steps() -> tuple[ReplayStep, ...]:
    control = {
        "actor_id": "v0-replay-agent",
        "session_id": "issue-108-deterministic-replay",
        "workspace": MEMORY_WORKSPACE,
        "policy_scope": ["ci", "deterministic-replay"],
    }
    return (
        ReplayStep(
            name="record decision from meeting",
            tool_name="bicameral.ingest",
            arguments={
                **control,
                "source_uri": "memory://meeting/issue-108",
                "source_type": "meeting",
                "title": "Use deterministic CI for v0 user-flow validation",
                "description": "Live agent execution is advisory; ToolRequest replay gates PRs.",
                "level": "decision",
            },
        ),
        ReplayStep(
            name="begin coding with preflight",
            tool_name="bicameral.preflight",
            arguments={
                **control,
                "files": ["server.py", "tool_request.py"],
                "symbols": ["call_tool", "build_tool_request"],
                "branch": "feat/628-deterministic-v0-ci",
            },
        ),
        ReplayStep(
            name="inspect current binding state",
            tool_name="bicameral.binding.inspect",
            arguments={**control, "decision_or_candidate_id": "DEC-v0-001"},
        ),
        ReplayStep(
            name="create binding after verified selection",
            tool_name="bicameral.bind",
            arguments={
                **control,
                "decision_or_candidate_id": "DEC-v0-001",
                "bindings": [{"symbol": "build_tool_request", "file": "tool_request.py"}],
                "commit_sha": "abc1234",
            },
        ),
        ReplayStep(
            name="refresh evidence currentness",
            tool_name="bicameral.evidence.refresh",
            arguments={**control, "decision_id": "DEC-v0-001"},
        ),
        ReplayStep(
            name="review tracked state",
            tool_name="bicameral.history",
            arguments={**control, "decision_id": "DEC-v0-001", "include_bindings": True},
        ),
        ReplayStep(
            name="search tracked decisions",
            tool_name="bicameral.search",
            arguments={**control, "query": "deterministic CI", "scope": "decisions"},
        ),
    )


async def run_replay() -> ReplayResult:
    daemon = MemoryReplayDaemon()
    with patch.object(server, "_client", lambda: daemon):
        responses: list[dict[str, Any]] = []
        for step in replay_steps():
            content = await server.call_tool(step.tool_name, dict(step.arguments))
            response = json.loads(content[0].text)
            responses.append(response)
            _assert_response_contract(step, response)

    command_sequence = [request["command"]["name"] for request in daemon.requests]
    result = ReplayResult(
        command_sequence=command_sequence,
        responses=responses,
        request_count=len(daemon.requests),
    )
    assert_replay_contract(result, daemon.requests)
    return result


def assert_replay_contract(result: ReplayResult, requests: list[dict[str, Any]]) -> None:
    if result.command_sequence != list(EXPECTED_COMMAND_SEQUENCE):
        raise AssertionError(
            f"unexpected command sequence: {result.command_sequence}; "
            f"expected {list(EXPECTED_COMMAND_SEQUENCE)}"
        )

    forbidden = FORBIDDEN_LEGACY_COMMANDS.intersection(result.command_sequence)
    if forbidden:
        raise AssertionError(f"legacy authority-shaped commands appeared: {sorted(forbidden)}")

    # Coverage guard (#343) adds one lookup.query before preflight.run.
    expected_count = len(replay_steps()) + 1
    if result.request_count != expected_count:
        raise AssertionError(
            f"expected {expected_count} ToolRequests (steps + coverage guard), "
            f"got {result.request_count}"
        )

    for request in requests:
        command = request["command"]["name"]
        authority = request["authority"]
        if authority["workspace"] != MEMORY_WORKSPACE:
            raise AssertionError(f"{command} did not run against {MEMORY_WORKSPACE}")
        if command in READ_OR_ADVISORY_COMMANDS:
            params = request["command"]["params"]
            leaked_control = {"actor_id", "session_id", "workspace", "policy_scope"} & set(params)
            if leaked_control:
                raise AssertionError(f"{command} leaked control keys into params: {leaked_control}")

    # Coverage guard (#343) inserts lookup.query into command_sequence without a
    # corresponding visible response; filter it for the step-to-response mapping.
    step_commands = [c for c in result.command_sequence if c != "lookup.query"]
    for command, response in zip(step_commands, result.responses, strict=True):
        result_payload = response.get("result", {})
        if command in READ_OR_ADVISORY_COMMANDS:
            if result_payload.get("mutation") not in (None, "none"):
                raise AssertionError(f"{command} reported a mutation: {result_payload}")
        if command == "evidence.refresh":
            for key in (
                "signoff_mutated",
                "compliance_mutated",
                "binding_evidence_mutated",
            ):
                if result_payload.get(key) is not False:
                    raise AssertionError(f"evidence.refresh did not preserve {key}=False")


def _assert_response_contract(step: ReplayStep, response: dict[str, Any]) -> None:
    if not response.get("request_id"):
        raise AssertionError(f"{step.name} did not return request_id")
    if "status" not in response:
        raise AssertionError(f"{step.name} did not return status")
    if step.tool_name == "bicameral.preflight":
        if response.get("session_directive") != {"mode": "continue"}:
            raise AssertionError("preflight did not preserve daemon session directive")
        if response["stages"]["enforcement"]["status"] != "not_configured":
            raise AssertionError("preflight enforcement state was not preserved")
        if response["stages"]["enforcement"]["behavior"] != "none":
            raise AssertionError("preflight enforcement was promoted to blocking")


def _json_summary(result: ReplayResult) -> str:
    return json.dumps(
        {
            "status": "ok",
            "workspace": MEMORY_WORKSPACE,
            "request_count": result.request_count,
            "command_sequence": result.command_sequence,
            "live_llm": False,
            "provider_credentials_required": False,
        },
        indent=2,
        sort_keys=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Run assertions and print summary.")
    args = parser.parse_args(argv)

    os.environ.pop("ANTHROPIC_API_KEY", None)
    result = asyncio.run(run_replay())
    if args.check:
        print(_json_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
