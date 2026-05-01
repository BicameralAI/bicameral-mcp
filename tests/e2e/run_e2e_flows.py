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
from collections.abc import Callable
from dataclasses import dataclass, field

E2E_ROOT = pathlib.Path(__file__).resolve().parent
PROMPTS_DIR = E2E_ROOT / "prompts"
MCP_CONFIG_TEMPLATE = E2E_ROOT / "bicameral.mcp.json"
RESULTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "test-results" / "e2e"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Persistent ledger shared across the 5 flow sessions in a single run, wiped
# at the start of each run so flow-1 seeds → flow-2 refines → flow-3 reflects
# → flow-4 captures → flow-5 ratifies, all against the same ledger state.
LEDGER_DIR = RESULTS_DIR / "ledger.db"

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
        "ERROR: 'bicameral-mcp' command not found on PATH.\nInstall via: pip install -e .\n"
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
    materialized = raw.replace("${DESKTOP_REPO_PATH}", DESKTOP_REPO_PATH).replace(
        "${LEDGER_DIR}", str(LEDGER_DIR)
    )
    out = RESULTS_DIR / "bicameral.mcp.materialized.json"
    out.write_text(materialized, encoding="utf-8")
    return out


def _clean_ledger() -> None:
    """Wipe the persistent ledger between harness runs.

    State must persist across the 5 sequential claude sessions within a run
    (so the PM in flow 5 sees decisions from flows 1/2/4), but must NOT leak
    across runs (so each run is reproducible and CI is deterministic).
    """
    if LEDGER_DIR.exists():
        shutil.rmtree(LEDGER_DIR, ignore_errors=True)


MCP_CONFIG_PATH = _materialize_mcp_config()


@dataclass
class FlowSpec:
    """Each flow declares its layer so failures can be triaged honestly.

    - ``mcp_layer`` flows use prompts that explicitly invoke MCP tools (ingest,
      link_commit, ratify, etc.). They validate that the tool surface works.
      Failure here = real broken tool.
    - ``agentic_layer`` flows use natural-developer-voice prompts and rely on
      bicameral skills to AUTO-FIRE on intent (e.g. preflight on "refactor X",
      capture-corrections at session end). Failure here is an advisory regression
      signal: skills aren't reliably triggering in headless ``claude -p`` mode.
      The interactive recording path (tmux-driven real TUI) is the primary
      validator for this layer; this harness tracks the gap.
    """

    flow_id: str
    prompt_file: str
    asserter: Callable[[list[dict]], tuple[bool, str]]
    category: str  # "mcp_layer" | "agentic_layer"
    advisory: str = ""  # rendered when the flow FAILs to explain what it means


@dataclass
class FlowResult:
    flow_id: str
    prompt_file: str
    verdict: str  # "PASS" | "FAIL" | "ERROR"
    body: str
    category: str = "mcp_layer"
    advisory: str = ""
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
            f"ingest called without decisions/mappings (payload keys: {list(payload.keys())})"
        )

    return True, (
        f"bicameral.ingest called with {len(items)} item(s); total bicameral calls: {len(bcalls)}"
    )


def assert_flow_2(calls: list[dict]) -> tuple[bool, str]:
    """Flow 2: dev requests a refactor that contradicts the seeded cherry-pick
    spec. Expect preflight to auto-fire, surface the collision, agent ingests
    a refinement (agent_session source), and links it via resolve_collision.

    The point: prove the correction dynamic produces a NEW decision in the
    ledger as `proposed` — the inbox flow 5 ratifies from.
    """
    bcalls = _bicameral_tool_calls(calls)
    names = [c["name"].split("__")[-1] for c in bcalls]

    # 1. preflight fired (auto-trigger on "refactor" verb against the file)
    preflight_calls = _calls_named(bcalls, "bicameral_preflight")
    if not preflight_calls:
        return False, f"expected preflight (auto-fired); saw: {names}"

    file_paths = preflight_calls[0]["input"].get("file_paths") or []
    if not file_paths or not any("cherry-pick.ts" in p for p in file_paths):
        return False, f"preflight called without cherry-pick.ts in file_paths; got: {file_paths}"

    # 2. ingest fired with agent_session source — the refinement
    ingest_calls = _calls_named(bcalls, "bicameral_ingest")
    refinement_ingest = None
    for c in ingest_calls:
        payload = _ingest_payload(c)
        top_source = payload.get("source", "")
        span_sources = [(m.get("span") or {}).get("source_type", "") for m in _ingest_items(c)]
        if top_source == "agent_session" or "agent_session" in span_sources:
            refinement_ingest = c
            break
    if refinement_ingest is None:
        return False, (
            f"expected ingest of refinement with agent_session source; "
            f"saw {len(ingest_calls)} ingest call(s), none with agent_session"
        )

    # 3. resolve_collision fired — wires the refinement to the seeded decision
    resolve_calls = _calls_named(bcalls, "bicameral_resolve_collision")
    if not resolve_calls:
        return False, f"expected resolve_collision after collision surfaced; saw: {names}"

    return True, (
        f"preflight (cherry-pick.ts) + agent_session ingest + resolve_collision all fired; "
        f"sequence: {names}"
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
        f"link_commit + resolve_compliance both called; verdicts={len(verdicts)}; sequence: {names}"
    )


def assert_flow_4(calls: list[dict]) -> tuple[bool, str]:
    bcalls = _bicameral_tool_calls(calls)
    ingest_calls = _calls_named(bcalls, "bicameral_ingest")
    if not ingest_calls:
        return (
            False,
            f"expected ingest with agent_session source; saw: {[c['name'] for c in bcalls]}",
        )

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
        f"bicameral.ingest called with agent_session source (payload.source={top_source!r})"
    )


def assert_flow_5(calls: list[dict]) -> tuple[bool, str]:
    """Flow 5: PM Friday review. Inbox is real because state persists from
    flows 1/2/4. Expect history (the review query) + ratify (PM blesses the
    refinement). No in-session seed needed any more — that's the whole
    point of switching to surrealkv.
    """
    bcalls = _bicameral_tool_calls(calls)
    names = [c["name"].split("__")[-1] for c in bcalls]

    history_calls = _calls_named(bcalls, "bicameral_history")
    if not history_calls:
        return False, f"expected bicameral.history; saw: {names}"

    ratify_calls = _calls_named(bcalls, "bicameral_ratify")
    if not ratify_calls:
        return False, (
            f"expected ratify on a proposed decision (PM blessing flow-2 refinement); saw: {names}"
        )

    return True, f"bicameral.history called; ratified={len(ratify_calls)}; sequence: {names}"


FLOW_PLAN: list[FlowSpec] = [
    FlowSpec(
        flow_id="Flow 1",
        prompt_file="flow-1-ingest.md",
        asserter=assert_flow_1,
        category="mcp_layer",
    ),
    FlowSpec(
        flow_id="Flow 2",
        prompt_file="flow-2-preflight.md",
        asserter=assert_flow_2,
        category="agentic_layer",
        advisory=(
            "TWO GAPS surfaced — both are product signal, not test design:\n"
            "  (1) AUTO-FIRE: the preflight skill claims to auto-fire on natural refactor "
            "prompts, but in headless `claude -p` the agent prefers to verify the premise "
            "(Bash/Read/Grep) before invoking any bicameral skill. Skill descriptions are "
            "losing the priority race against the agent's engineering instincts.\n"
            "  (2) SEMANTIC GROUNDING NOT WIRED THROUGH PREFLIGHT: even when preflight is "
            "explicitly called, lookup against a file path returns no matches unless that "
            "path was explicitly bind()'d. CodeGenome (semantic grounding) is integrated "
            "into link_commit + bind but NOT into preflight — so 'Reorder commits via "
            "drag/drop' decision text does NOT bridge to reorder.ts at preflight time. "
            "The pre-coding context surface stays direct-binding-only.\n"
            "Validate the agentic auto-fire path via interactive recording (tmux TUI). "
            "Wiring CodeGenome through preflight is a separate product fix."
        ),
    ),
    FlowSpec(
        flow_id="Flow 3",
        prompt_file="flow-3-commit-sync.md",
        asserter=assert_flow_3,
        category="mcp_layer",
    ),
    FlowSpec(
        flow_id="Flow 4",
        prompt_file="flow-4-session-end.md",
        asserter=assert_flow_4,
        category="agentic_layer",
        advisory=(
            "COMPROMISED PASS: this flow only succeeds because the prompt explicitly tells "
            "the agent to ingest with `agent_session` source. The bicameral-capture-corrections "
            "skill itself was NOT auto-fired. To genuinely validate session-end correction "
            "capture, the prompt would need to state a load-bearing constraint conversationally "
            "(without tool-name hints) and rely on the SessionEnd hook to invoke the skill. "
            "That dynamic is not testable in headless mode today."
        ),
    ),
    FlowSpec(
        flow_id="Flow 5",
        prompt_file="flow-5-history.md",
        asserter=assert_flow_5,
        category="mcp_layer",
    ),
]


# ── Main ────────────────────────────────────────────────────────────────


def main() -> int:
    print("=== v0 user flow e2e — Claude Code CLI sessions ===")
    print(f"DESKTOP_REPO_PATH:  {DESKTOP_REPO_PATH}")
    print(f"MCP config:         {MCP_CONFIG_PATH}")
    print(f"Ledger (persisted): {LEDGER_DIR}")
    print(f"Transcripts:        {RESULTS_DIR}")
    print(f"Flows:              {len(FLOW_PLAN)}\n")

    _clean_ledger()

    for spec in FLOW_PLAN:
        prompt_path = PROMPTS_DIR / spec.prompt_file
        prompt = prompt_path.read_text(encoding="utf-8")
        try:
            tool_calls, transcript_path, exit_code = run_claude_session(spec.flow_id, prompt)
        except subprocess.TimeoutExpired:
            section(
                FlowResult(
                    flow_id=spec.flow_id,
                    prompt_file=spec.prompt_file,
                    verdict="ERROR",
                    body="claude CLI session timed out (>300s)",
                    category=spec.category,
                    advisory=spec.advisory,
                )
            )
            continue
        except Exception as exc:
            section(
                FlowResult(
                    flow_id=spec.flow_id,
                    prompt_file=spec.prompt_file,
                    verdict="ERROR",
                    body=f"claude CLI invocation failed: {exc!r}",
                    category=spec.category,
                    advisory=spec.advisory,
                )
            )
            continue

        passed, detail = spec.asserter(tool_calls)
        bicameral_calls = _bicameral_tool_calls(tool_calls)

        body = (
            f"prompt:                   {spec.prompt_file}\n"
            f"category:                 {spec.category}\n"
            f"claude exit:              {exit_code}\n"
            f"transcript:               {transcript_path.relative_to(RESULTS_DIR.parents[1])}\n"
            f"total tool calls:         {len(tool_calls)}\n"
            f"bicameral tool calls:     {len(bicameral_calls)}\n"
            f"  → {[c['name'].split('__')[-1] for c in bicameral_calls]}\n\n"
            f"assertion: {detail}\n"
        )
        section(
            FlowResult(
                flow_id=spec.flow_id,
                prompt_file=spec.prompt_file,
                verdict="PASS" if passed else "FAIL",
                body=body,
                category=spec.category,
                advisory=spec.advisory,
                tool_calls=tool_calls,
                transcript_path=str(transcript_path),
            )
        )

    _print_report()

    overall_pass = all(r.verdict == "PASS" for r in RESULTS)
    return 0 if overall_pass else 1


def _print_report() -> None:
    """Print the per-flow detail, then a sharable summary table that surfaces
    the MCP-layer vs agentic-layer split and any advisory text on failures.
    The summary is designed to be paste-able into a PR comment or shared
    alongside the demo recording so reviewers can see at a glance which
    flows validate the tool surface vs which flows still need the agentic
    layer to come through.
    """
    print("\n\n=== PER-FLOW DETAIL ===\n")
    for r in RESULTS:
        marker = _verdict_marker(r)
        print(f"\n## {r.flow_id} — {marker} {r.verdict}  ({r.category})\n")
        print(r.body)

    # Header banner
    print("\n" + "═" * 78)
    print("  e2e SUMMARY — sharable")
    print("═" * 78 + "\n")

    # Table
    fmt = f"{'Flow':<8} {'Layer':<14} {'Verdict':<10} {'What it validates'}"
    print(fmt)
    print("-" * 8 + " " + "-" * 14 + " " + "-" * 10 + " " + "-" * 40)
    for r in RESULTS:
        marker = _verdict_marker(r)
        layer_label = "MCP layer" if r.category == "mcp_layer" else "Agentic"
        what = _flow_one_line(r.flow_id)
        print(f"{r.flow_id:<8} {layer_label:<14} {marker} {r.verdict:<8} {what}")

    overall_pass = all(r.verdict == "PASS" for r in RESULTS)
    overall_marker = "✅" if overall_pass else "❌"
    print(f"\n{overall_marker} Overall: {'PASS' if overall_pass else 'FAIL'}")

    # MCP-layer vs agentic-layer breakdown
    mcp_results = [r for r in RESULTS if r.category == "mcp_layer"]
    agentic_results = [r for r in RESULTS if r.category == "agentic_layer"]
    mcp_pass = sum(1 for r in mcp_results if r.verdict == "PASS")
    agentic_pass = sum(1 for r in agentic_results if r.verdict == "PASS")
    print(f"\n   MCP-tool surface:    {mcp_pass}/{len(mcp_results)} validating tool callability")
    print(
        f"   Agentic auto-fire:   {agentic_pass}/{len(agentic_results)} "
        "(skills auto-firing on natural intent — see advisories below)"
    )

    # Advisories — only render for flows that have them, regardless of verdict.
    # An agentic-layer flow that PASSES still earns its advisory if the prompt
    # leaks tool-name hints (compromised pass).
    advised = [r for r in RESULTS if r.advisory]
    if advised:
        print("\n" + "─" * 78)
        print("  ADVISORIES — flows with caveats / known gaps")
        print("─" * 78)
        for r in advised:
            tag = "⚠️  FAILED" if r.verdict != "PASS" else "⚠️  COMPROMISED PASS"
            print(f"\n  {r.flow_id} — {tag}")
            print(f"  {r.advisory}")

    # What this means
    if any(r.advisory for r in RESULTS):
        print("\n" + "─" * 78)
        print("  CORRECTION-PATH STATUS")
        print("─" * 78)
        print(
            "  The end-to-end correction dynamic ('dev contradicts spec → preflight\n"
            "  catches → refinement captured → PM ratifies') is NOT validated by\n"
            "  this headless harness. MCP tool surface is callable and functional;\n"
            "  agentic auto-fire is the open gap.\n\n"
            "  Validate the agentic layer via the interactive recording path\n"
            "  (tmux-driven real claude TUI). See tests/e2e/record_demo.sh."
        )
    print()


def _verdict_marker(r: FlowResult) -> str:
    if r.verdict == "PASS" and not r.advisory:
        return "✅"
    if r.verdict == "PASS" and r.advisory:
        return "⚠️ "  # passes but compromised — caveat in advisories section
    if r.verdict == "FAIL" and r.advisory:
        return "⚠️ "  # advisory failure — known gap, not a tool bug
    return "❌"


def _flow_one_line(flow_id: str) -> str:
    return {
        "Flow 1": "ingest decisions from a doc",
        "Flow 2": "auto-fire preflight on natural refactor request",
        "Flow 3": "link_commit + resolve_compliance after a code change",
        "Flow 4": "session-end correction capture",
        "Flow 5": "PM Friday review — history + ratify",
    }.get(flow_id, "")


if __name__ == "__main__":
    sys.exit(main())
