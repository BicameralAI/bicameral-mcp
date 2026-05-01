"""
v0 user flow e2e — Claude Code CLI session orchestrator.

Drives a real Claude Code CLI session per flow (5 sessions total), with
bicameral-mcp registered as the only MCP server, and asserts on the
stream-json transcript that the right MCP tools were called with the
right shapes.

Each flow:
  1. Reads ``prompts/flow-N-*.md`` (natural-language user prompt)
  2. Invokes ``claude -p <prompt> --mcp-config bicameral.mcp.json
       --strict-mcp-config --output-format stream-json --add-dir <desktop_clone>``
  3. Streams stdout to ``test-results/e2e/flow-N.ndjson``
  4. Walks the transcript for tool_use blocks under ``mcp__bicameral__*``
  5. Asserts per-flow invariants and prints PASS/FAIL

The point: this exercises the full skill + MCP layer the way a user
experiences it. The handler-replay sim at ``scripts/sim_issue_108_flows.py``
remains useful for fast dev iteration on handler logic.

Required env:
  CLAUDE_CODE_OAUTH_TOKEN  Claude Code CLI auth (set by GitHub Actions
                           ``production`` environment in CI).
  DESKTOP_REPO_PATH        Path to a local clone of github.com/desktop/desktop.

CI: see .github/workflows/v0-user-flow-e2e.yml.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable

E2E_ROOT = pathlib.Path(__file__).resolve().parent
PROMPTS_DIR = E2E_ROOT / "prompts"
MCP_CONFIG_TEMPLATE = E2E_ROOT / "bicameral.mcp.json"
RESULTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "test-results" / "e2e"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

DESKTOP_REPO_PATH = os.environ.get("DESKTOP_REPO_PATH", "").strip()
if not DESKTOP_REPO_PATH:
    sys.stderr.write(
        "ERROR: DESKTOP_REPO_PATH env var not set.\n"
        "CI sets this automatically; locally:\n"
        "  git clone --depth=1 https://github.com/desktop/desktop /tmp/desktop-clone\n"
        "  DESKTOP_REPO_PATH=/tmp/desktop-clone python tests/e2e/run_e2e_flows.py\n"
    )
    sys.exit(2)

if not shutil.which("claude"):
    sys.stderr.write(
        "ERROR: 'claude' CLI not found on PATH.\n"
        "Install via: npm install -g @anthropic-ai/claude-code\n"
    )
    sys.exit(2)

if not shutil.which("bicameral-mcp"):
    sys.stderr.write(
        "ERROR: 'bicameral-mcp' command not found on PATH.\n"
        "Install via: pip install -e .\n"
    )
    sys.exit(2)


def _materialize_mcp_config() -> pathlib.Path:
    """Read the MCP config template, substitute env-var placeholders, write
    a runtime copy. The template uses ``${DESKTOP_REPO_PATH}`` so it works
    locally (any clone path) and in CI (the workflow's clone path).

    Claude Code's MCP spawn behaviour for env replacement vs merge is
    implementation-defined; passing REPO_PATH explicitly via the config
    avoids that ambiguity.
    """
    raw = MCP_CONFIG_TEMPLATE.read_text(encoding="utf-8")
    materialized = raw.replace("${DESKTOP_REPO_PATH}", DESKTOP_REPO_PATH)
    out = RESULTS_DIR / "bicameral.mcp.materialized.json"
    out.write_text(materialized, encoding="utf-8")
    return out


MCP_CONFIG_PATH = _materialize_mcp_config()


@dataclass
class FlowResult:
    flow_id: str
    prompt_file: str
    verdict: str  # "PASS" | "FAIL" | "ERROR"
    body: str
    tool_calls: list[dict] = field(default_factory=list)
    transcript_path: str = ""


RESULTS: list[FlowResult] = []


def section(result: FlowResult) -> None:
    RESULTS.append(result)
    line = result.body.splitlines()[0] if result.body else ""
    print(f"[{result.flow_id}] {result.verdict} — {line[:100]}")


# ── Claude Code CLI invocation ──────────────────────────────────────────


def run_claude_session(flow_id: str, prompt: str) -> tuple[list[dict], pathlib.Path, int]:
    """Invoke ``claude -p`` with stream-json output. Return (tool_calls, transcript_path, exit_code).

    stream-json emits one JSON object per line on stdout — system init, user
    prompts, assistant turns (with tool_use blocks), tool results, and a final
    result object. We capture all lines for the audit trail and extract
    tool_use blocks for assertions.
    """
    transcript_path = RESULTS_DIR / f"{flow_id}.ndjson"

    cmd = [
        "claude",
        "-p",
        prompt,
        "--mcp-config",
        str(MCP_CONFIG_PATH),
        "--strict-mcp-config",
        # Allow bicameral MCP tools + Read/Grep so skills can inspect bound files.
        # Bash is intentionally NOT allowed — bicameral skills shouldn't need shell.
        # Comma-separated single arg is unambiguous vs space-separated variadic.
        "--allowed-tools",
        "mcp__bicameral,Read,Grep",
        "--add-dir",
        DESKTOP_REPO_PATH,
        "--output-format",
        "stream-json",
        "--verbose",  # required by stream-json for full event detail
        "--no-session-persistence",
        "--max-budget-usd",
        "2.0",
        "--dangerously-skip-permissions",
    ]

    print(f"\n=== {flow_id} — invoking claude (cwd=pilot/mcp) ===")
    proc = subprocess.run(
        cmd,
        cwd=pathlib.Path(__file__).resolve().parents[2],  # pilot/mcp
        capture_output=True,
        text=True,
        timeout=300,
    )

    transcript_path.write_text(proc.stdout, encoding="utf-8")
    if proc.returncode != 0:
        sys.stderr.write(
            f"[{flow_id}] claude CLI exit={proc.returncode}\n"
            f"  stderr (last 500 chars): {proc.stderr[-500:]}\n"
        )

    tool_calls = _extract_tool_calls(proc.stdout)
    return tool_calls, transcript_path, proc.returncode


def _extract_tool_calls(stream_json: str) -> list[dict]:
    """Walk stream-json output, extract every tool_use block under mcp__bicameral.

    stream-json shape: one JSON object per line. Assistant messages contain
    ``message.content`` arrays with ``{"type":"tool_use","name":"...","input":{...}}``.
    """
    calls: list[dict] = []
    for line in stream_json.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Assistant turns carry tool_use blocks
        if obj.get("type") == "assistant":
            content = (obj.get("message") or {}).get("content") or []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls.append(
                        {
                            "name": block.get("name", ""),
                            "input": block.get("input") or {},
                            "id": block.get("id", ""),
                        }
                    )
    return calls


def _bicameral_tool_calls(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["name"].startswith("mcp__bicameral__")]


def _calls_named(calls: list[dict], suffix: str) -> list[dict]:
    """Return calls whose tool name ends with the given suffix (server-name-agnostic)."""
    return [c for c in calls if c["name"].endswith(suffix) or c["name"].endswith(f"_{suffix}")]


# ── Per-flow assertions ─────────────────────────────────────────────────


def _ingest_payload(call: dict) -> dict:
    """Extract the inner payload from an ingest tool call.

    The MCP tool schema wraps the IngestPayload in a ``payload`` key. Some
    skill versions also list mappings under ``decisions`` (the natural-LLM
    spelling) rather than ``mappings`` (the internal field). Handle both.
    """
    inp = call.get("input") or {}
    return inp.get("payload") or inp


def _ingest_items(call: dict) -> list[dict]:
    p = _ingest_payload(call)
    return p.get("decisions") or p.get("mappings") or []


def assert_flow_1(calls: list[dict]) -> tuple[bool, str]:
    bcalls = _bicameral_tool_calls(calls)
    ingest_calls = _calls_named(bcalls, "bicameral_ingest")
    if not ingest_calls:
        return False, (
            f"expected bicameral.ingest to be called; saw {len(bcalls)} bicameral "
            f"calls: {[c['name'] for c in bcalls]}"
        )

    items = _ingest_items(ingest_calls[0])
    if len(items) < 1:
        payload = _ingest_payload(ingest_calls[0])
        return False, (
            f"ingest called without decisions/mappings "
            f"(payload keys: {list(payload.keys())})"
        )

    return True, (
        f"bicameral.ingest called with {len(items)} item(s); "
        f"total bicameral calls: {len(bcalls)}"
    )


def assert_flow_2(calls: list[dict]) -> tuple[bool, str]:
    bcalls = _bicameral_tool_calls(calls)
    preflight_calls = _calls_named(bcalls, "bicameral_preflight")
    if not preflight_calls:
        return False, (
            f"expected bicameral.preflight to be called; saw {len(bcalls)} bicameral "
            f"calls: {[c['name'] for c in bcalls]}"
        )

    file_paths = preflight_calls[0]["input"].get("file_paths") or []
    if not file_paths or not any("cherry-pick.ts" in p for p in file_paths):
        return False, (
            f"preflight called without expected file_paths; "
            f"got: {file_paths}"
        )

    return True, (
        f"bicameral.preflight called with file_paths={file_paths}; "
        f"total bicameral calls: {len(bcalls)}"
    )


def assert_flow_3(calls: list[dict]) -> tuple[bool, str]:
    bcalls = _bicameral_tool_calls(calls)
    names = [c["name"].split("__")[-1] for c in bcalls]

    has_link_commit = any("link_commit" in n for n in names)
    has_resolve = any("resolve_compliance" in n for n in names)

    if not has_link_commit:
        return False, f"expected link_commit; saw: {names}"
    if not has_resolve:
        return False, f"expected resolve_compliance; saw: {names}"

    # Verify resolve_compliance carried verdicts of expected shape
    # (input may wrap in 'payload' depending on tool schema version)
    resolve_calls = _calls_named(bcalls, "bicameral_resolve_compliance")
    if resolve_calls:
        rinput = resolve_calls[0]["input"] or {}
        rpayload = rinput.get("payload") or rinput
        verdicts = rpayload.get("verdicts") or []
    else:
        verdicts = []
    if not verdicts:
        return False, "resolve_compliance called without verdicts"

    return True, (
        f"link_commit + resolve_compliance both called; verdicts={len(verdicts)}; "
        f"sequence: {names}"
    )


def assert_flow_4(calls: list[dict]) -> tuple[bool, str]:
    bcalls = _bicameral_tool_calls(calls)
    ingest_calls = _calls_named(bcalls, "bicameral_ingest")
    if not ingest_calls:
        return False, f"expected ingest with agent_session source; saw: {[c['name'] for c in bcalls]}"

    # Source can live at payload.source (top-level) or per-decision via
    # span.source_type. Check both, since the MCP tool schema wraps in payload.
    payload = _ingest_payload(ingest_calls[0])
    top_source = payload.get("source", "")
    span_sources: list[str] = []
    for m in _ingest_items(ingest_calls[0]):
        span = m.get("span") or {}
        if "source_type" in span:
            span_sources.append(span["source_type"])

    is_agent_session = top_source == "agent_session" or "agent_session" in span_sources
    if not is_agent_session:
        return False, (
            f"ingest source not agent_session; "
            f"top_source={top_source!r}, span_source_types={span_sources}"
        )

    return True, (
        f"bicameral.ingest called with agent_session source "
        f"(payload.source={top_source!r})"
    )


def assert_flow_5(calls: list[dict]) -> tuple[bool, str]:
    bcalls = _bicameral_tool_calls(calls)
    history_calls = _calls_named(bcalls, "bicameral_history")
    if not history_calls:
        return False, f"expected bicameral.history; saw: {[c['name'] for c in bcalls]}"

    # Flow 5 prompt also asks to seed two decisions and ratify one — so we
    # expect at least one ingest and at least one ratify call too.
    ingest_calls = _calls_named(bcalls, "bicameral_ingest")
    ratify_calls = _calls_named(bcalls, "bicameral_ratify")

    seeded = bool(ingest_calls)
    ratified = bool(ratify_calls)

    if not (seeded and ratified):
        return False, (
            f"history called but seed pre-conditions weak: "
            f"ingest={len(ingest_calls)}, ratify={len(ratify_calls)}"
        )

    return True, (
        f"bicameral.history called; ingest seeded={len(ingest_calls)}, "
        f"ratified={len(ratify_calls)}"
    )


FLOW_PLAN: list[tuple[str, str, Callable[[list[dict]], tuple[bool, str]]]] = [
    ("Flow 1", "flow-1-ingest.md", assert_flow_1),
    ("Flow 2", "flow-2-preflight.md", assert_flow_2),
    ("Flow 3", "flow-3-commit-sync.md", assert_flow_3),
    ("Flow 4", "flow-4-session-end.md", assert_flow_4),
    ("Flow 5", "flow-5-history.md", assert_flow_5),
]


# ── Main ────────────────────────────────────────────────────────────────


def main() -> int:
    print("=== v0 user flow e2e — Claude Code CLI sessions ===")
    print(f"DESKTOP_REPO_PATH:  {DESKTOP_REPO_PATH}")
    print(f"MCP config:         {MCP_CONFIG_PATH}")
    print(f"Transcripts:        {RESULTS_DIR}")
    print(f"Flows:              {len(FLOW_PLAN)}\n")

    for flow_id, prompt_file, asserter in FLOW_PLAN:
        prompt_path = PROMPTS_DIR / prompt_file
        prompt = prompt_path.read_text(encoding="utf-8")
        try:
            tool_calls, transcript_path, exit_code = run_claude_session(flow_id, prompt)
        except subprocess.TimeoutExpired:
            section(
                FlowResult(
                    flow_id=flow_id,
                    prompt_file=prompt_file,
                    verdict="ERROR",
                    body="claude CLI session timed out (>300s)",
                )
            )
            continue
        except Exception as exc:
            section(
                FlowResult(
                    flow_id=flow_id,
                    prompt_file=prompt_file,
                    verdict="ERROR",
                    body=f"claude CLI invocation failed: {exc!r}",
                )
            )
            continue

        passed, detail = asserter(tool_calls)
        bicameral_calls = _bicameral_tool_calls(tool_calls)

        body = (
            f"prompt:                   {prompt_file}\n"
            f"claude exit:              {exit_code}\n"
            f"transcript:               {transcript_path.relative_to(RESULTS_DIR.parents[1])}\n"
            f"total tool calls:         {len(tool_calls)}\n"
            f"bicameral tool calls:     {len(bicameral_calls)}\n"
            f"  → {[c['name'].split('__')[-1] for c in bicameral_calls]}\n\n"
            f"assertion: {detail}\n"
        )
        section(
            FlowResult(
                flow_id=flow_id,
                prompt_file=prompt_file,
                verdict="PASS" if passed else "FAIL",
                body=body,
                tool_calls=tool_calls,
                transcript_path=str(transcript_path),
            )
        )

    print("\n\n=== REPORT ===\n")
    overall_pass = all(r.verdict == "PASS" for r in RESULTS)
    for r in RESULTS:
        print(f"\n## {r.flow_id} — {r.verdict}\n")
        print(r.body)

    print("\n=== SUMMARY ===\n")
    print(f"{'Flow':<10} {'Verdict':<8}")
    print(f"{'-' * 10} {'-' * 8}")
    for r in RESULTS:
        print(f"{r.flow_id:<10} {r.verdict:<8}")
    print(f"\nOverall: {'PASS' if overall_pass else 'FAIL'}")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
