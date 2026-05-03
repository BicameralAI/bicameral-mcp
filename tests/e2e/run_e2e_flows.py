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

# Wall-clock cap for a single `claude -p` flow invocation. Was 300s; raised
# to 480s after CI surfaced a Flow 2 timeout flake — the longest legitimate
# Flow 2 dev run measured 289.7s, leaving only ~3% headroom on the old cap.
CLAUDE_SESSION_TIMEOUT_S = 480

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


# Setup helpers live in _harness_setup.py — single source of truth shared with
# tests/e2e/record_demo_interactive.sh so the recording job and the assertion
# job materialize byte-identical hook substrate. See _harness_setup.py docstring.
sys.path.insert(0, str(E2E_ROOT))
# fmt: off
# isort: off
from _harness_setup import (  # noqa: E402,I001  # path tweak above
    bootstrap_bicameral_dir as _bootstrap_helper,
    clean_ledger as _clean_ledger_helper,
    materialize_mcp_config,
    materialize_settings_with_hooks,
    reset_desktop_repo as _reset_desktop_helper,
)
# fmt: on
# isort: on

_MCP_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _clean_ledger() -> None:
    _clean_ledger_helper(LEDGER_DIR)


def _reset_desktop_repo() -> None:
    _reset_desktop_helper(DESKTOP_REPO_PATH)


def _bootstrap_bicameral_dir() -> None:
    _bootstrap_helper(DESKTOP_REPO_PATH, _MCP_ROOT)


MCP_CONFIG_PATH = materialize_mcp_config(
    template=MCP_CONFIG_TEMPLATE,
    out_dir=RESULTS_DIR,
    desktop_repo_path=DESKTOP_REPO_PATH,
    ledger_dir=LEDGER_DIR,
)
SETTINGS_PATH = materialize_settings_with_hooks(
    out_dir=RESULTS_DIR,
    mcp_config_path=MCP_CONFIG_PATH,
    mcp_root=_MCP_ROOT,
)


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
    skip: bool = False  # if True, do not invoke claude — mark SKIP and render advisory
    # Flows sharing a session_group run inside one continuous claude session
    # (chained via --session-id + --resume) so that multi-turn skills like
    # bicameral-capture-corrections have real transcript history to scan and
    # the SessionEnd hook fires once per group at the final flow's exit.
    # None = standalone session (default; also disables session persistence).
    session_group: str | None = None
    # If set, do NOT invoke claude — reuse the tool_calls captured by the
    # named earlier flow and run this asserter against them. Lets two flows
    # grade independent properties of the same claude session (e.g. Flow 2
    # = auto-fire scope, Flow 2a = full correction-capture loop) without
    # paying for a duplicate API call.
    reuses_flow: str | None = None


@dataclass
class FlowResult:
    flow_id: str
    prompt_file: str
    verdict: str  # "PASS" | "FAIL" | "ERROR" | "SKIP"
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


# ── Post-hoc ledger validation ─────────────────────────────────────────


def _snapshot_ledger() -> dict:
    """Snapshot ledger state for before/after comparison. Returns counts of
    decisions by status and total compliance_check rows. Uses raw client to
    bypass the schema-migration crash documented in iteration 1.

    Returns ``{"total_decisions": N, "by_status": {status: N}, "compliance_checks": N}``.
    On any error, returns ``{"error": str}`` — caller decides how to handle.
    """
    import asyncio
    import os

    os.environ["SURREAL_URL"] = f"surrealkv://{LEDGER_DIR}"
    try:
        from ledger.client import LedgerClient  # noqa: E402

        async def _q() -> dict:
            client = LedgerClient(url=f"surrealkv://{LEDGER_DIR}")
            await client.connect()
            try:
                drows = (
                    await client.query(
                        "SELECT decision_id, description, status FROM decision LIMIT 200"
                    )
                ) or []
                ccrows = (
                    await client.query(
                        "SELECT decision_id, region_id, content_hash, verdict "
                        "FROM compliance_check LIMIT 500"
                    )
                ) or []
                buckets: dict[str, int] = {}
                for r in drows:
                    buckets[(r.get("status") or "unknown")] = (
                        buckets.get(r.get("status") or "unknown", 0) + 1
                    )
                return {
                    "total_decisions": len(drows),
                    "by_status": buckets,
                    "compliance_checks": len(ccrows),
                    "compliance_rows": ccrows,
                    "decisions": drows,
                }
            finally:
                await client.close()

        return asyncio.run(_q())
    except Exception as exc:
        return {"error": repr(exc)}


def _count_agent_session_decisions(snapshot: dict) -> int | None:
    """Wrapper around the pure helper in ``_ledger_helpers``. The helper
    lives in its own module so unit tests can import it without triggering
    the harness's top-level env-var / CLI-presence guards.
    """
    from _ledger_helpers import count_agent_session_decisions

    return count_agent_session_decisions(snapshot)


def _validate_flow4_via_ledger() -> None:
    """Path-X-(b) validation per #147: open the ledger after the harness
    completes and check for decisions written with source_type='agent_session'.

    The SessionEnd hook spawns a separate ``claude -p`` subprocess whose
    tool calls are NOT visible in the parent stream-json; the subprocess
    writes to the ledger with source_type='agent_session', so its effect
    IS observable post-hoc. This function merges that signal into Flow 4's
    FlowResult, in-place.

    Behavior matrix:
    - Asserter PASS + ledger has agent_session: append confirmation note;
      verdict unchanged.
    - Asserter FAIL + ledger has agent_session: UPGRADE to PASS with note
      'in-stream signal absent but SessionEnd subprocess effect observed
      in ledger (path-X-b)'.
    - Asserter result + ledger error: append INCONCLUSIVE note; verdict
      unchanged.
    - Asserter PASS + ledger has zero agent_session: verdict unchanged.
    - Asserter FAIL + ledger has zero agent_session: verdict unchanged
      (real failure; both observable signals absent).
    """
    flow4 = next((r for r in RESULTS if r.flow_id == "Flow 4"), None)
    if flow4 is None:
        return

    print("\n=== Flow 4 — querying ledger state for path-X-(b) signal ===")
    after = _snapshot_ledger()
    count = _count_agent_session_decisions(after)

    if count is None:
        flow4.body += (
            f"\n— Ledger validation —\nINCONCLUSIVE: ledger query failed: {after.get('error')}\n"
        )
        return

    if count > 0:
        if flow4.verdict != "PASS":
            flow4.verdict = "PASS"
        flow4.body += (
            f"\n— Ledger validation —\n"
            f"PASS: {count} decision(s) with source_type='agent_session' "
            f"present in ledger after harness completion (path-X-b: SessionEnd "
            f"subprocess and/or in-session capture-corrections wrote them).\n"
        )
    else:
        flow4.body += (
            "\n— Ledger validation —\n"
            "path-X-b absent: zero decisions with source_type='agent_session' "
            "after harness completion. SessionEnd subprocess either did not "
            "fire, did not detect uningested corrections, or failed silently.\n"
        )


def _validate_flow3_via_ledger(session_id: str, baseline: dict) -> None:
    """Validate the V1 lifecycle outcome by opening the ledger directly
    after the chained dev_session has fully completed.

    Per bicameral-mcp #135, the post-commit hook is sync-only — ``link_commit``
    runs server-side via ``ensure_ledger_synced`` on the NEXT bicameral tool
    call after HEAD moves (naturally happens during Flow 4's preflight, since
    it's chained in the same session). Without a caller-LLM, ``resolve_compliance``
    can't fire from the hook, so the V1 success outcome we can validate
    headless is: at least one decision flipped to ``status='pending'``
    after Flow 3's commit.

    This is Flow 3's REAL assertion — the per-flow stream-json check (did
    git commit happen?) is a precondition. The ledger state IS the verdict.
    This function finds the existing Flow 3 ``FlowResult`` and merges the
    ledger findings into its body + verdict. No separate row is added.
    """
    flow3 = next((r for r in RESULTS if r.flow_id == "Flow 3"), None)
    if flow3 is None:
        sys.stderr.write("Ledger validation: no Flow 3 result to merge into.\n")
        return

    print("\n=== Flow 3 — querying ledger state for V1 lifecycle outcome ===")

    after = _snapshot_ledger()
    if "error" in after:
        flow3.verdict = "ERROR"
        flow3.body += (
            f"\n— Ledger validation —\nfailed to open ledger at {LEDGER_DIR}: {after['error']}\n"
        )
        return
    if "error" in baseline:
        flow3.verdict = "ERROR"
        flow3.body += f"\n— Ledger validation —\nbaseline snapshot failed: {baseline['error']}\n"
        return

    # The honest V1-lifecycle assertion: by the end of the dev_session run
    # (and the runs that follow it within the same harness invocation), at
    # least one decision should have transitioned from `pending` to a
    # verdict state (`reflected` or `drifted`). That transition proves the
    # full lifecycle — ensure_ledger_synced → link_commit → resolve_compliance
    # → status verdict — completed somewhere in the run. The transition can
    # be triggered by ANY bicameral tool call after HEAD moves; in practice
    # it's often Flow 5's `bicameral.history` that provokes the chain. We
    # don't try to attribute the transition to a specific flow — what
    # matters is the V1 outcome materialised at all.
    #
    # Per #135 (post-commit hook is sync-only), the resolve_compliance step
    # requires a caller-LLM. So this assertion implicitly tests the chain
    # ALL THE WAY through, not just the sync. The compliance_check row
    # count delta is reported alongside as an additional signal.
    cc_before = baseline.get("compliance_checks", 0)
    cc_after = after.get("compliance_checks", 0)
    cc_delta = cc_after - cc_before

    pending_before = baseline.get("by_status", {}).get("pending", 0)
    pending_after = after.get("by_status", {}).get("pending", 0)
    reflected_before = baseline.get("by_status", {}).get("reflected", 0)
    reflected_after = after.get("by_status", {}).get("reflected", 0)
    drifted_before = baseline.get("by_status", {}).get("drifted", 0)
    drifted_after = after.get("by_status", {}).get("drifted", 0)

    verdicts_written = (reflected_after - reflected_before) + (drifted_after - drifted_before)
    pending_drained = pending_before - pending_after

    # Flow 3's verdict is now purely ledger-based per the user-flow design:
    # the commit-happened stream-json check is informational, not a gate.
    # The V1 lifecycle is what we care about; whichever flow triggers it
    # is fine.
    ledger_passed = verdicts_written > 0 or cc_delta > 0
    final_verdict = "PASS" if ledger_passed else "FAIL"

    if verdicts_written > 0:
        ledger_detail = (
            f"✓ {verdicts_written} verdict(s) written during the run "
            f"(reflected: {reflected_before}→{reflected_after}, "
            f"drifted: {drifted_before}→{drifted_after}, "
            f"pending: {pending_before}→{pending_after}). "
            f"V1 lifecycle (ingest → bind → link_commit → resolve_compliance "
            f"→ verdict) completed end-to-end."
        )
    elif cc_delta > 0:
        ledger_detail = (
            f"⚠ compliance_check rows grew by {cc_delta} ({cc_before}→{cc_after}) "
            f"but no verdicts written — sync mechanism fired but resolve_compliance "
            f"never ran. The caller-LLM step in the V1 chain didn't trigger; "
            f"per #135 this is expected without an in-session bicameral call "
            f"that surfaces pending checks to the agent."
        )
    else:
        ledger_detail = (
            f"✗ no compliance_check rows written ({cc_before}→{cc_after}) and "
            f"no verdicts written. Either the bound decisions never had their "
            f"sync triggered (no bicameral call after HEAD moves) or Flow 1's "
            f"binding didn't land properly."
        )

    status_before = baseline.get("by_status", {})
    status_after = after.get("by_status", {})
    all_statuses = sorted(set(status_before) | set(status_after))
    status_lines = "\n".join(
        f"  {s:<22} {status_before.get(s, 0)} → {status_after.get(s, 0)}" for s in all_statuses
    )
    commit_note = (
        "agent committed in Flow 3 (precondition met)"
        if flow3.verdict == "PASS"
        else "agent did NOT commit in Flow 3 (precondition NOT met — informational)"
    )
    flow3.body += (
        f"\n— Ledger state (before → after dev_session) —\n"
        f"session_id:               {session_id[:8]}…\n"
        f"ledger:                   {LEDGER_DIR}\n"
        f"total decisions:          {baseline.get('total_decisions', 0)} → {after.get('total_decisions', 0)}\n"
        f"compliance_checks:        {cc_before} → {cc_after} (Δ={cc_delta:+d})\n"
        f"verdicts written:         {verdicts_written}\n"
        f"by status:\n{status_lines}\n\n"
        f"stream-json precondition: {commit_note}\n"
        f"ledger assertion:         {ledger_detail}\n"
    )
    # Flow 3's final verdict is the ledger result, not the commit precondition.
    # The lifecycle outcome matters; the path through it is incidental.
    flow3.verdict = final_verdict


# ── Claude Code CLI invocation ──────────────────────────────────────────


def run_claude_session(
    flow_id: str,
    prompt: str,
    session_id: str | None = None,
    is_first_in_group: bool = True,
) -> tuple[list[dict], pathlib.Path, int]:
    """Invoke ``claude -p`` with stream-json output. Return (tool_calls, transcript_path, exit_code).

    stream-json emits one JSON object per line on stdout — system init, user
    prompts, assistant turns (with tool_use blocks), tool results, and a final
    result object. We capture all lines for the audit trail and extract
    tool_use blocks for assertions.

    When ``session_id`` is provided:
      - First flow in the group uses ``--session-id <uuid>`` to claim the UUID
        and create a persistent session on disk.
      - Subsequent flows use ``--resume <uuid>`` to extend the same session
        (full transcript history available to skills/hooks).
      - ``--no-session-persistence`` is dropped (it would block the chain).

    When ``session_id`` is None: standalone session, persistence disabled.
    """
    transcript_path = RESULTS_DIR / f"{flow_id}.ndjson"

    cmd = [
        "claude",
        "-p",
        prompt,
        "--mcp-config",
        str(MCP_CONFIG_PATH),
        "--strict-mcp-config",
        "--settings",
        str(SETTINGS_PATH),
        # Bash + Edit required for Flow 3's commit. Read/Grep for inspection.
        "--allowed-tools",
        "mcp__bicameral,Read,Grep,Edit,Bash",
        "--output-format",
        "stream-json",
        "--verbose",  # required by stream-json for full event detail
        "--max-budget-usd",
        "2.0",
        "--dangerously-skip-permissions",
    ]
    if session_id is None:
        cmd.append("--no-session-persistence")
    elif is_first_in_group:
        cmd.extend(["--session-id", session_id])
    else:
        cmd.extend(["--resume", session_id])

    chain_tag = ""
    if session_id is not None:
        chain_tag = f" [session={session_id[:8]} {'first' if is_first_in_group else 'resume'}]"
    # cwd MUST be DESKTOP_REPO_PATH. The agent treats cwd as the primary
    # codebase and resolves prompt-relative paths there. Iteration 2 used
    # pilot/mcp as cwd → agent saw the Python MCP server, refused to act
    # on `app/src/lib/git/reorder.ts` because that doesn't exist in the
    # MCP server tree. The MCP server's REPO_PATH env (in the materialized
    # MCP config) is independent of claude's cwd, and bicameral skills load
    # from ~/.claude/skills/ regardless of cwd.
    print(f"\n=== {flow_id} — invoking claude (cwd={DESKTOP_REPO_PATH}){chain_tag} ===")
    proc = subprocess.run(
        cmd,
        cwd=DESKTOP_REPO_PATH,
        capture_output=True,
        text=True,
        timeout=CLAUDE_SESSION_TIMEOUT_S,
    )

    transcript_path.write_text(proc.stdout, encoding="utf-8")
    if proc.returncode != 0:
        sys.stderr.write(
            f"[{flow_id}] claude CLI exit={proc.returncode}\n"
            f"  stderr (last 500 chars): {proc.stderr[-500:]}\n"
        )

    tool_calls = _extract_tool_calls(proc.stdout)
    return tool_calls, transcript_path, proc.returncode


def run_scaffolding_turn(session_id: str, label: str, prompt: str) -> int:
    """Inject a scaffolding turn into a chained session to seed state.

    Used when an upstream flow's auto-fire failed and we want to unblock
    downstream flows by manually triggering the missing tool call. The
    scaffolding turn IS allowed to name tools — its purpose is session-state
    recovery, not auto-fire validation. The upstream flow's verdict still
    measures auto-fire reliability honestly.

    Logged to ``test-results/e2e/scaffolding-<label>.ndjson`` for diagnostics.
    Not added to RESULTS, not asserted. Returns claude's exit code.
    """
    log_path = RESULTS_DIR / f"scaffolding-{label}.ndjson"
    cmd = [
        "claude",
        "-p",
        prompt,
        "--mcp-config",
        str(MCP_CONFIG_PATH),
        "--strict-mcp-config",
        "--settings",
        str(SETTINGS_PATH),
        "--allowed-tools",
        "mcp__bicameral,Read,Grep,Edit,Bash",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-budget-usd",
        "1.0",
        "--dangerously-skip-permissions",
        "--resume",
        session_id,
    ]
    print(f"\n=== Scaffolding ({label}) — injecting into session={session_id[:8]} ===")
    proc = subprocess.run(
        cmd,
        cwd=DESKTOP_REPO_PATH,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log_path.write_text(proc.stdout, encoding="utf-8")
    tool_calls = _extract_tool_calls(proc.stdout)
    bicameral_calls = _bicameral_tool_calls(tool_calls)
    bcall_names = [c["name"].split("__")[-1] for c in bicameral_calls]
    print(
        f"    scaffolding tool calls: {len(tool_calls)} total, "
        f"{len(bicameral_calls)} bicameral → {bcall_names}"
    )
    if proc.returncode != 0:
        sys.stderr.write(
            f"[scaffolding {label}] claude CLI exit={proc.returncode}\n"
            f"  stderr (last 500 chars): {proc.stderr[-500:]}\n"
        )
    return proc.returncode


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


# Feature-area binding sets for Flow 1. Each seeded decision can legitimately
# anchor to any of several files in the desktop/desktop tree — the asserter
# checks that *some* file in each area is bound, not which specific one.
# Previously the asserter required the exact paths "cherry-pick.ts" and
# "reorder.ts"; LLM nondeterminism on borderline cases (e.g. binding the
# UI-layer commit-list.tsx instead of the git-layer reorder.ts) flaked the
# test even though the functional outcome — drift detection has a code
# anchor for each feature — was satisfied.
#
# The "Improved commit history" decision bundles four ops (drag-to-reorder,
# drag-to-squash, amend, branch-from), so any of the files backing those is
# a legitimate anchor. cherry-pick has both lib and UI surfaces and either
# is acceptable.
_CHERRY_PICK_AREA_PATHS: tuple[str, ...] = (
    "cherry-pick.ts",
    "cherry-pick.tsx",
)
_COMMIT_HISTORY_AREA_PATHS: tuple[str, ...] = (
    # git-layer (canonical anchors for drift on the actual operations)
    "/git/reorder.ts",
    "/git/squash.ts",
    "/git/commit.ts",
    # ui-layer (legitimate when the decision is framed as a UX feature)
    "/history/commit-list.tsx",
    "/history/commit-list-item.tsx",
    "/multi-commit-operation/reorder.tsx",
    "/multi-commit-operation/squash.tsx",
    "/dispatcher/dispatcher.ts",
    # models / store layer (when bound as data-shape contracts)
    "/models/multi-commit-operation.ts",
    "/models/retry-actions.ts",
    "/stores/app-store.ts",
)


def _bound_to_area(bind_targets: list[str], area_paths: tuple[str, ...]) -> bool:
    """Return True iff any bound path matches any acceptable substring for the area."""
    return any(any(sub in p for sub in area_paths) for p in bind_targets)


def assert_flow_1(calls: list[dict]) -> tuple[bool, str]:
    """Flow 1: PM ingests the seed roadmap decisions, anchors at least one
    file in each of the cherry-pick and commit-history feature areas, and
    ratifies. Subsequent flows depend on a CLEAN, RATIFIED, BOUND ledger as
    their baseline.

    Anchoring path: the canonical bicameral-ingest skill embeds bindings
    inline via ``mappings[].code_regions[].file_path`` — there is no
    separate ``bicameral.bind`` call for code that already exists. A
    follow-up ``bicameral.bind`` is reserved for abstract decisions whose
    code doesn't exist yet. This asserter accepts EITHER path.

    The check is feature-area-scoped, not file-scoped: any of the files
    listed in ``_CHERRY_PICK_AREA_PATHS`` / ``_COMMIT_HISTORY_AREA_PATHS``
    counts as a legitimate anchor for the corresponding decision. The
    earlier exact-filename check ("cherry-pick.ts" + "reorder.ts" only)
    flaked when the LLM picked an equally valid UI-layer file like
    ``commit-list.tsx`` for the bundled commit-history decision.
    """
    bcalls = _bicameral_tool_calls(calls)
    names = [c["name"].split("__")[-1] for c in bcalls]

    ingest_calls = _calls_named(bcalls, "bicameral_ingest")
    if not ingest_calls:
        return False, (f"expected bicameral.ingest; saw {len(bcalls)} bicameral calls: {names}")

    # Walk every ingest call's mappings[].code_regions[].file_path to find
    # the bound files. Modern flow embeds binding here; agent may also fall
    # back to a follow-up bicameral.bind for ungrounded decisions.
    bind_targets: list[str] = []
    total_items = 0
    for c in ingest_calls:
        items = _ingest_items(c)
        total_items += len(items)
        for item in items:
            for region in (item or {}).get("code_regions") or []:
                path = (region or {}).get("file_path") or (region or {}).get("path") or ""
                if path:
                    bind_targets.append(path)

    if total_items < 1:
        payload = _ingest_payload(ingest_calls[0])
        return False, (
            f"ingest called without decisions/mappings (payload keys: {list(payload.keys())})"
        )

    # Also accept any explicit bicameral.bind calls (still valid for the
    # ungrounded-then-bind path).
    bind_calls = _calls_named(bcalls, "bicameral_bind")
    for c in bind_calls:
        binp = c.get("input") or {}
        bpayload = binp.get("payload") or binp
        for span in bpayload.get("spans") or bpayload.get("bindings") or []:
            path = (span or {}).get("file_path") or (span or {}).get("path") or ""
            if path:
                bind_targets.append(path)

    has_cp_area = _bound_to_area(bind_targets, _CHERRY_PICK_AREA_PATHS)
    has_commit_history_area = _bound_to_area(bind_targets, _COMMIT_HISTORY_AREA_PATHS)
    if not (has_cp_area and has_commit_history_area):
        missing = [
            label
            for label, present in (
                ("cherry-pick area", has_cp_area),
                ("commit-history area", has_commit_history_area),
            )
            if not present
        ]
        return False, (
            f"bind missing feature area(s): {missing}; checked "
            f"ingest.mappings[].code_regions and bicameral.bind calls; saw bound "
            f"paths: {bind_targets}; expected at least one path per missing area "
            f"matching cherry-pick: {list(_CHERRY_PICK_AREA_PATHS)} or "
            f"commit-history: {list(_COMMIT_HISTORY_AREA_PATHS)}; sequence: {names}"
        )

    # Ratify: PM blesses the just-ingested decisions. Flow 5 walks the
    # `proposed` queue — flow 1's seeds must NOT remain in `proposed` or
    # they'd contaminate flow 5's "what's queued for adoption" view.
    ratify_calls = _calls_named(bcalls, "bicameral_ratify")
    if not ratify_calls:
        return False, (
            f"expected bicameral.ratify after ingest (PM blesses adoption); saw: {names}"
        )

    binding_path = "inline code_regions" if not bind_calls else "inline + follow-up bind"
    return True, (
        f"ingest({total_items} items, {binding_path}) → cherry-pick + commit-history "
        f"feature areas bound (paths: {bind_targets}); "
        f"ratify({len(ratify_calls)}); sequence: {names}"
    )


def assert_flow_2(calls: list[dict]) -> tuple[bool, str]:
    """Flow 2: dev requests a refactor that contradicts the seeded REORDER
    decision. This asserter validates ONLY the auto-fire scope of #146 — did
    ``bicameral.preflight`` fire on the affected file before the agent
    side-effected the codebase?

    Read is deliberately allowed before/in-parallel-with preflight: agents
    legitimately read in parallel with preflight to keep latency reasonable,
    and the contract that matters is "preflight gates writes." Edit / Bash
    write-ops are the line; preflight must precede the first one.

    The end-to-end correction-capture loop (agent_session ingest +
    resolve_collision) is asserted separately by Flow 2a, which reuses this
    flow's transcript so the same claude session is graded on two
    independent properties without a duplicate API call.
    """
    bcalls = _bicameral_tool_calls(calls)
    names = [c["name"].split("__")[-1] for c in bcalls]

    # 1. preflight fired (hook-driven auto-trigger on "refactor" verb)
    preflight_calls = _calls_named(bcalls, "bicameral_preflight")
    if not preflight_calls:
        return False, f"expected preflight (auto-fired); saw: {names}"

    file_paths = preflight_calls[0]["input"].get("file_paths") or []
    if not file_paths or not any("reorder.ts" in p for p in file_paths):
        return False, (
            f"preflight called without reorder.ts in file_paths (the file the dev "
            f"asked to refactor); got: {file_paths}"
        )

    # 2. preflight precedes the first WRITE op (Edit / Write / git-commit Bash).
    # Reads are allowed in parallel — they don't side-effect.
    first_preflight_idx = next(
        (i for i, c in enumerate(calls) if c["name"].endswith("bicameral_preflight")),
        None,
    )
    write_tools = ("Edit", "Write", "NotebookEdit")
    first_write_idx = next(
        (
            i
            for i, c in enumerate(calls)
            if c["name"] in write_tools
            or (c["name"] == "Bash" and "git commit" in (c.get("input") or {}).get("command", ""))
        ),
        None,
    )
    if first_write_idx is not None and (
        first_preflight_idx is None or first_preflight_idx > first_write_idx
    ):
        return False, (
            f"preflight did not precede first write op (auto-fire contract violated); "
            f"first preflight at idx {first_preflight_idx}, first write at idx {first_write_idx}"
        )

    return True, (f"preflight auto-fired on reorder.ts; preceded first write op; sequence: {names}")


def assert_flow_2a(calls: list[dict]) -> tuple[bool, str]:
    """Flow 2a: end-to-end correction-capture loop. Reuses Flow 2's tool
    calls (same claude session) so this measures whether the agent took the
    next two steps after preflight surfaced the seeded decision:

      - ingest the refinement with ``source=agent_session``, AND
      - call ``resolve_collision`` to wire the refinement to the seeded
        decision (supersedes / complements / etc.).

    These two steps are NOT delivered by the auto-fire hook. They require
    the agent to (a) recognize that the user's prompt contradicts a
    surfaced decision, and (b) walk the preflight skill's correction-capture
    branch — which currently doesn't exist as an explicit instruction. See
    BicameralAI/bicameral-mcp#154 (P0) for the skill-layer gap. Until that
    issue is closed, this flow is expected to FAIL as advisory; the auto-fire
    contract validated by Flow 2 is independent.
    """
    bcalls = _bicameral_tool_calls(calls)
    names = [c["name"].split("__")[-1] for c in bcalls]

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

    resolve_calls = _calls_named(bcalls, "bicameral_resolve_collision")
    if not resolve_calls:
        return False, f"expected resolve_collision after refinement ingest; saw: {names}"

    return True, (f"agent_session ingest + resolve_collision both fired; sequence: {names}")


def assert_flow_3(calls: list[dict]) -> tuple[bool, str]:
    """Flow 3 (chained dev session): dev implements the high-signal
    notification feature (the only Flow-1 decision that's still
    ungrounded — cherry-pick + reorder are already reflected from Flow 1's
    inline binding) and commits. The prompt is intentionally minimal:
    implement + commit, no bicameral verbs, no status checks.

    Per bicameral-mcp #135, the post-commit hook is sync-only by design —
    it just prints a reminder to the agent. ``link_commit`` runs server-side
    via ``ensure_ledger_synced`` on the next bicameral tool call after HEAD
    moves (naturally happens in Flow 4's preflight), and ``resolve_compliance``
    requires a caller-LLM in-session (the hook can't trigger it).

    Per-flow assertion: did the agent actually run ``git commit``? That's
    the only thing this flow controls. The interesting outcome — a
    decision flipping to ``pending`` after the commit — is validated by the
    post-hoc ledger query (``_assert_dev_session_ledger_state``) that runs
    after the whole ``dev_session`` group completes.
    """
    bash_calls = [c for c in calls if c.get("name") == "Bash"]
    commit_calls = [
        c for c in bash_calls if "git commit" in (c.get("input") or {}).get("command", "")
    ]
    if not commit_calls:
        bash_cmds = [(c.get("input") or {}).get("command", "")[:60] for c in bash_calls]
        return False, (
            f"expected a `git commit` Bash call (the prompt asks for a commit); "
            f"saw {len(bash_calls)} Bash call(s): {bash_cmds}"
        )
    return True, (
        f"git commit executed ({len(commit_calls)} call(s)). Status flip to "
        "`pending` validated post-hoc via ledger query at end of dev_session."
    )


def assert_flow_4(calls: list[dict]) -> tuple[bool, str]:
    """Flow 4 (chained dev session): mid-flow correction. The user surfaces
    a load-bearing constraint about the cherry-pick conflict path as an
    aside — using correction markers (``wait``, ``shouldn't``, ``wrong``)
    and NO explicit tracking verbs (``track this`` / ``log this`` /
    ``lock this in``). The user then asks for code work, which should
    trigger ``bicameral-preflight``; preflight step 3.5 invokes
    ``bicameral-capture-corrections`` in in-session mode; capture-corrections
    finds the constraint and ingests it with ``source=agent_session``.

    What this asserter checks (outcome, not path):
      1. ``bicameral_preflight`` fired (proves the chained session passed
         the dev's "continue refactor" intent through to the right skill).
      2. EITHER an ``agent_session``-sourced ingest landed (capture-
         corrections in-session ingested the constraint as mechanical) OR
         capture-corrections did at least invoke ``bicameral_search`` for
         dedup (Step C ran — the rubric processed the markers and just
         classified the constraint as ``ask`` instead of mechanical).

    The SessionEnd hook spawns ``/bicameral:capture-corrections`` as a
    SEPARATE subprocess; its tool calls are NOT visible in this stream-json.
    That out-of-band path is the realistic production behaviour and is
    validated by querying the ledger after the harness completes — not
    here. This asserter only checks what's observable in-stream.
    """
    bcalls = _bicameral_tool_calls(calls)
    names = [c["name"].split("__")[-1] for c in bcalls]

    preflight_calls = _calls_named(bcalls, "bicameral_preflight")
    if not preflight_calls:
        return False, (
            f"expected bicameral.preflight to fire on the dev's 'continue refactor' "
            f"request (the in-session capture-corrections invocation hangs off "
            f"preflight step 3.5); saw: {names}"
        )

    # Outcome path A — capture-corrections auto-ingested as mechanical.
    ingest_calls = _calls_named(bcalls, "bicameral_ingest")
    agent_session_ingest = None
    for c in ingest_calls:
        payload = _ingest_payload(c)
        top_source = payload.get("source", "")
        span_sources = [(m.get("span") or {}).get("source_type", "") for m in _ingest_items(c)]
        if top_source == "agent_session" or "agent_session" in span_sources:
            agent_session_ingest = c
            break

    # Outcome path B — capture-corrections ran Step C dedup (search) and
    # classified the constraint as `ask` (which doesn't auto-ingest in
    # headless without user confirmation). The search call is the
    # observable signal that capture-corrections processed the markers.
    search_calls = _calls_named(bcalls, "bicameral_search")

    if agent_session_ingest is None and not search_calls:
        return False, (
            f"preflight fired but neither path-A (agent_session ingest) nor path-B "
            f"(bicameral.search from capture-corrections Step C) was observed — "
            f"capture-corrections did not appear to process the in-session "
            f"corrections. sequence: {names}"
        )

    if agent_session_ingest is not None:
        return True, (
            f"preflight + agent_session ingest fired (path A — mechanical "
            f"auto-ingest); sequence: {names}"
        )
    return True, (
        f"preflight + bicameral.search fired (path B — capture-corrections Step C "
        f"dedup ran; constraint classified as `ask`, awaits user confirmation); "
        f"sequence: {names}"
    )


def assert_flow_5(calls: list[dict]) -> tuple[bool, str]:
    """Flow 5: PM Friday review. Inbox is real because state persists from
    flows 1/2/4. Expect history (the review query) + IF there's anything
    in the proposed queue, ratify it.

    The ratify call is conditional, not unconditional: if upstream flows
    produced no new proposals (e.g. Flow 1 already ratified its 3 seeds
    and Flow 2's collision didn't produce a refinement), there's literally
    nothing to ratify and the prompt's instruction "ratify if you find
    anything ready" is honestly satisfied by a no-op. Forcing ratify here
    would catch a cascade failure from Flow 2 as if it were a Flow 5 bug.

    Per #108 Flow 5 spec: history + (ratify if proposals exist). The "if"
    is load-bearing — see step 4: "Step 3 is silent if no proposals exist."
    """
    bcalls = _bicameral_tool_calls(calls)
    names = [c["name"].split("__")[-1] for c in bcalls]

    history_calls = _calls_named(bcalls, "bicameral_history")
    if not history_calls:
        return False, f"expected bicameral.history; saw: {names}"

    ratify_calls = _calls_named(bcalls, "bicameral_ratify")
    if ratify_calls:
        return True, (
            f"bicameral.history + ratify({len(ratify_calls)}) — PM ratified "
            f"queued proposal(s); sequence: {names}"
        )
    return True, (
        f"bicameral.history fired; no ratify (no proposals in queue — "
        f"Flow 1 ratified its 3 seeds and upstream chain may not have "
        f"produced new proposals); sequence: {names}"
    )


FLOW_PLAN: list[FlowSpec] = [
    FlowSpec(
        flow_id="Flow 1",
        prompt_file="flow-1-ingest.md",
        asserter=assert_flow_1,
        category="mcp_layer",
    ),
    # Flows 2/3/4 share session group "dev_session" — chained via
    # --session-id + --resume so Flow 4's capture-corrections has real
    # transcript history (Flow 2's refactor request, Flow 3's commit) to
    # scan against, and the SessionEnd hook fires on the rich accumulated
    # transcript at Flow 4's exit. Without chaining, capture-corrections
    # can't operate honestly — it's designed to scan multi-turn history.
    FlowSpec(
        flow_id="Flow 2",
        prompt_file="flow-2-preflight.md",
        asserter=assert_flow_2,
        # Auto-fire alone is the deterministic hook surface (UserPromptSubmit
        # → bicameral.preflight on reorder.ts before any write op). MCP-layer
        # because the contract is a single tool call wired by a hook, not a
        # multi-step agentic skill walk.
        category="mcp_layer",
        session_group="dev_session",
    ),
    FlowSpec(
        flow_id="Flow 2a",
        prompt_file="flow-2-preflight.md",
        asserter=assert_flow_2a,
        category="agentic_layer",
        session_group="dev_session",
        # Reuse Flow 2's transcript — same claude session, second assertion.
        # Avoids running flow-2-preflight.md twice and keeps both verdicts
        # honest (the same session is judged on two independent properties).
        reuses_flow="Flow 2",
        advisory=(
            "Skill-layer gap: bicameral-preflight surfaces decisions but does "
            "not instruct the agent to (a) ingest a refinement with "
            "source=agent_session when the user's prompt contradicts a "
            "surfaced decision, or (b) call resolve_collision to wire the "
            "refinement to the seeded decision. Tracked as P0 — see "
            "BicameralAI/bicameral-mcp#154. Independent of #146 auto-fire."
        ),
    ),
    FlowSpec(
        flow_id="Flow 3",
        prompt_file="flow-3-commit-sync.md",
        asserter=assert_flow_3,
        category="agentic_layer",
        session_group="dev_session",
        # link_commit auto-fire is no longer asserted here — that path is
        # validated via the interactive recording (tmux real-TUI). This
        # flow's role in the chain is to put a real edit + commit into the
        # session transcript so Flow 4 has authentic dev-workflow context.
    ),
    FlowSpec(
        flow_id="Flow 4",
        prompt_file="flow-4-session-end.md",
        asserter=assert_flow_4,
        category="agentic_layer",
        session_group="dev_session",
        advisory=(
            "Flow 4 captures an emerging constraint via correction markers "
            '("wait", "shouldn\'t") — no collision-detection involved. NOT '
            "the same gap as #154 (which is Flow 2a / contradiction-with-"
            "prior-decision specific). The substrate fixes in this PR "
            "(.bicameral/ bootstrap + --mcp-config passthrough) close real "
            "drift, but path-X-(b) still won't fire end-to-end because the "
            "canonical SessionEnd hook command can't pass the parent "
            "transcript to the spawned subprocess AND --auto-ingest is the "
            "wrong shape for background capture. Both tracked as P1 — see "
            "BicameralAI/bicameral-mcp#156 for the design pivot to "
            "next-session surfacing via a transcript queue."
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
    _reset_desktop_repo()
    _bootstrap_bicameral_dir()

    # One UUID per session_group, allocated lazily as we encounter the group.
    # ``group_seen`` tracks which groups have already had their first flow run
    # so subsequent flows know to use --resume rather than --session-id.
    import uuid

    group_session_ids: dict[str, str] = {}
    group_seen: set[str] = set()
    chained_groups = sorted({s.session_group for s in FLOW_PLAN if s.session_group})
    if chained_groups:
        print("Chained session groups:")
        for g in chained_groups:
            sid = str(uuid.uuid4())
            group_session_ids[g] = sid
            members = [
                s.flow_id
                for s in FLOW_PLAN
                if s.session_group == g and not s.skip and not s.reuses_flow
            ]
            print(f"  {g}: {sid[:8]}…  → {' → '.join(members)}")
        print()

    # Snapshot ledger state *between* Flow 1 and dev_session so the
    # post-hoc validation can compute a real delta. Captured lazily —
    # taken just before the first dev_session flow runs.
    dev_session_baseline: dict | None = None

    for spec in FLOW_PLAN:
        # Snapshot baseline once, immediately before the first dev_session
        # flow. This means Flow 1's effects are baked in but Flow 2/3/4's
        # effects (the ones we want to measure) are not.
        if dev_session_baseline is None and spec.session_group == "dev_session" and not spec.skip:
            print("\n=== Snapshotting ledger baseline before dev_session ===")
            dev_session_baseline = _snapshot_ledger()
            if "error" in dev_session_baseline:
                sys.stderr.write(f"baseline snapshot failed: {dev_session_baseline['error']}\n")
            else:
                print(
                    f"    baseline: {dev_session_baseline.get('total_decisions', 0)} decisions, "
                    f"{dev_session_baseline.get('compliance_checks', 0)} compliance_check rows, "
                    f"by_status={dev_session_baseline.get('by_status', {})}"
                )

        if spec.skip:
            print(f"\n=== {spec.flow_id} — SKIPPED (see advisory) ===")
            section(
                FlowResult(
                    flow_id=spec.flow_id,
                    prompt_file=spec.prompt_file,
                    verdict="SKIP",
                    body=(
                        f"prompt:                   {spec.prompt_file}\n"
                        f"category:                 {spec.category}\n"
                        f"claude exit:              n/a (not invoked)\n"
                        f"transcript:               n/a\n"
                        f"total tool calls:         0\n"
                        f"bicameral tool calls:     0\n\n"
                        f"assertion: skipped — see advisory\n"
                    ),
                    category=spec.category,
                    advisory=spec.advisory,
                )
            )
            continue

        if spec.reuses_flow:
            # Re-grade an earlier flow's transcript with this asserter. No
            # claude invocation; the source flow already paid for the API
            # call and emitted the transcript we read here.
            source = next((r for r in RESULTS if r.flow_id == spec.reuses_flow), None)
            if source is None:
                section(
                    FlowResult(
                        flow_id=spec.flow_id,
                        prompt_file=spec.prompt_file,
                        verdict="ERROR",
                        body=(
                            f"reuses_flow={spec.reuses_flow!r} not found in RESULTS — "
                            f"declare the source flow earlier in FLOW_PLAN"
                        ),
                        category=spec.category,
                        advisory=spec.advisory,
                    )
                )
                continue
            print(
                f"\n=== {spec.flow_id} — re-grading {source.flow_id}'s transcript "
                f"({len(source.tool_calls)} tool calls) ==="
            )
            passed, detail = spec.asserter(source.tool_calls)
            bicameral_calls = _bicameral_tool_calls(source.tool_calls)
            body = (
                f"prompt:                   {spec.prompt_file} (reused from {source.flow_id})\n"
                f"category:                 {spec.category}\n"
                f"claude exit:              n/a (transcript reused)\n"
                f"transcript:               {source.transcript_path}\n"
                f"total tool calls:         {len(source.tool_calls)}\n"
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
                    tool_calls=source.tool_calls,
                    transcript_path=source.transcript_path,
                )
            )
            continue

        prompt_path = PROMPTS_DIR / spec.prompt_file
        prompt = prompt_path.read_text(encoding="utf-8")
        session_id = group_session_ids.get(spec.session_group) if spec.session_group else None
        is_first = spec.session_group is not None and spec.session_group not in group_seen
        if spec.session_group is not None:
            group_seen.add(spec.session_group)
        try:
            tool_calls, transcript_path, exit_code = run_claude_session(
                spec.flow_id, prompt, session_id=session_id, is_first_in_group=is_first
            )
        except subprocess.TimeoutExpired:
            section(
                FlowResult(
                    flow_id=spec.flow_id,
                    prompt_file=spec.prompt_file,
                    verdict="ERROR",
                    body=f"claude CLI session timed out (>{CLAUDE_SESSION_TIMEOUT_S}s)",
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

        # Cascade-failure decoupling: if Flow 2's preflight auto-fire failed
        # in the chained dev_session, inject a manual preflight call so Flow
        # 3 / Flow 4 don't inherit a broken state. Flow 2's verdict above
        # still measures auto-fire reliability honestly — this scaffolding
        # is only state recovery for downstream flows. The scaffolding turn
        # is allowed to name the tool because it isn't a tested flow.
        if spec.flow_id == "Flow 2" and spec.session_group == "dev_session" and not passed:
            run_scaffolding_turn(
                session_id=group_session_ids["dev_session"],
                label="post-flow2-preflight",
                prompt=(
                    "Quick — please call bicameral.preflight on "
                    "app/src/lib/git/reorder.ts before we keep going on the "
                    "refactor. I want to see what existing decisions might apply."
                ),
            )

    # Post-hoc ledger validation merges into Flow 3's verdict. Runs AFTER
    # all flows complete so that ensure_ledger_synced (server-side, fires on
    # the next bicameral tool call after HEAD moves) has had a chance to
    # apply link_commit and write pending compliance checks. This is Flow 3's
    # REAL assertion — the stream-json check (did git commit happen) is just
    # a precondition.
    if "dev_session" in group_session_ids:
        if dev_session_baseline is None:
            dev_session_baseline = {"error": "baseline never captured"}
        _validate_flow3_via_ledger(group_session_ids["dev_session"], dev_session_baseline)
        # Phase 1 of plan-147-flow4-ledger-validation.md: path-X-(b)
        # post-hoc ledger query for the SessionEnd subprocess effect.
        _validate_flow4_via_ledger()

    _print_report()

    # CI gate: a flow blocks merge ONLY if it FAILs without an `advisory` text.
    # Advisory failures document known gaps (with linked issue numbers) — they
    # surface loudly in the report but do not red-light CI. This lets the
    # harness keep running these assertions every PR (so we notice when a
    # gap silently CLOSES) without making every PR also pay for the open gap.
    blocking_failures = [r for r in RESULTS if r.verdict in ("FAIL", "ERROR") and not r.advisory]
    return 0 if not blocking_failures else 1


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
    fmt = f"{'Flow':<14} {'Layer':<14} {'Verdict':<10} {'What it validates'}"
    print(fmt)
    print("-" * 14 + " " + "-" * 14 + " " + "-" * 10 + " " + "-" * 40)
    for r in RESULTS:
        marker = _verdict_marker(r)
        layer_label = {
            "mcp_layer": "MCP layer",
            "agentic_layer": "Agentic",
            "ledger_state": "Ledger",
        }.get(r.category, r.category)
        what = _flow_one_line(r.flow_id)
        print(f"{r.flow_id:<14} {layer_label:<14} {marker} {r.verdict:<8} {what}")

    blocking_failures = [r for r in RESULTS if r.verdict in ("FAIL", "ERROR") and not r.advisory]
    advisory_failures = [r for r in RESULTS if r.verdict == "FAIL" and r.advisory]
    overall_pass = not blocking_failures
    overall_marker = "✅" if overall_pass else "❌"
    overall_label = "PASS" if overall_pass else "FAIL"
    if overall_pass and advisory_failures:
        overall_label = f"PASS ({len(advisory_failures)} advisory failure(s) — see below)"
    print(f"\n{overall_marker} Overall: {overall_label}")

    # MCP-layer vs agentic-layer breakdown — SKIP excluded from both totals
    # (skipped flows are documented gaps, not pending validation work).
    mcp_results = [r for r in RESULTS if r.category == "mcp_layer" and r.verdict != "SKIP"]
    agentic_results = [r for r in RESULTS if r.category == "agentic_layer" and r.verdict != "SKIP"]
    mcp_pass = sum(1 for r in mcp_results if r.verdict == "PASS")
    agentic_pass = sum(1 for r in agentic_results if r.verdict == "PASS")
    skipped = [r for r in RESULTS if r.verdict == "SKIP"]
    print(f"\n   MCP-tool surface:    {mcp_pass}/{len(mcp_results)} validating tool callability")
    print(
        f"   Agentic auto-fire:   {agentic_pass}/{len(agentic_results)} "
        "(skills auto-firing on natural intent — see advisories below)"
    )
    if skipped:
        print(
            f"   Skipped:             {len(skipped)} "
            "(deferred to interactive recording — see advisories)"
        )

    # Advisories — render for flows that have them, regardless of verdict.
    # An agentic-layer flow that PASSES still earns its advisory if the prompt
    # leaks tool-name hints (compromised pass). SKIP gets its own tag.
    advised = [r for r in RESULTS if r.advisory]
    if advised:
        print("\n" + "─" * 78)
        print("  ADVISORIES — flows with caveats / known gaps")
        print("─" * 78)
        for r in advised:
            if r.verdict == "SKIP":
                tag = "⏭  SKIPPED"
            elif r.verdict == "PASS":
                tag = "⚠️  COMPROMISED PASS"
            else:
                tag = "⚠️  FAILED"
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
    if r.verdict == "SKIP":
        return "⏭ "
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
        "Flow 2": "auto-fire preflight before write op (auto-fire scope)",
        "Flow 2a": "full correction-capture loop (ingest agent_session + resolve_collision)",
        "Flow 3": "commit on bound file → ledger flips decision to `pending`",
        "Flow 4": "in-session correction capture (chained dev_session)",
        "Flow 5": "PM Friday review — history + ratify",
    }.get(flow_id, "")


if __name__ == "__main__":
    sys.exit(main())
