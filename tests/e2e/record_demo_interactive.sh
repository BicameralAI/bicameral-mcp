#!/usr/bin/env bash
#
# Interactive demo recording — tmux-driven real claude TUI.
#
# Replaces headless `claude -p` with an interactive `claude` session inside a
# tmux pane, with prompts typed via `tmux send-keys -l` for human-paced input.
# The point: in headless mode bicameral skills (preflight, capture-corrections)
# don't reliably auto-fire on natural dev language — the agent does premise-
# checking via Bash/Read/Grep first. Interactive mode is the path where the
# agentic layer (auto-fire, semantic discovery, automatic corrections) is
# actually visible — and the demo punchline is "the agent surfaces context
# without being asked."
#
# Status: SKETCH. Layered on top of the recording infra outlined in
# `thoughts/shared/plans/2026-04-30-v0-userflow-demo-recording.md`. This file
# focuses on the tmux+keystroke mechanics; the Xvfb + ffmpeg + chromium
# split-screen wrapper from that plan stays the same.
#
# Prereqs (Linux runner): tmux, xterm, claude CLI, bicameral-mcp on PATH.
# Optional: Xvfb + ffmpeg + chromium for the recording wrapper.

set -euo pipefail

E2E_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPTS_DIR="${E2E_ROOT}/prompts"
RESULTS_DIR="$(cd "${E2E_ROOT}/../.." && pwd)/test-results/e2e"
LEDGER_DIR="${RESULTS_DIR}/ledger.db"
MCP_CONFIG="${RESULTS_DIR}/bicameral.mcp.materialized.json"

: "${DESKTOP_REPO_PATH:?DESKTOP_REPO_PATH must be set to the desktop/desktop clone}"

# Wipe ledger so the run is reproducible (same contract as run_e2e_flows.py)
rm -rf "${LEDGER_DIR}"

# Materialize the MCP config (substitute env-var placeholders) — same shape
# as the headless harness, factored out so they share state.
python3 - <<PY
import pathlib
template = pathlib.Path("${E2E_ROOT}/bicameral.mcp.json").read_text()
out = pathlib.Path("${MCP_CONFIG}")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(
    template
    .replace("\${DESKTOP_REPO_PATH}", "${DESKTOP_REPO_PATH}")
    .replace("\${LEDGER_DIR}", "${LEDGER_DIR}")
)
PY

# ─── tmux + send-keys driver ────────────────────────────────────────────
#
# For each flow:
#   1. Start a fresh detached tmux session running interactive claude
#      with the same MCP config + allowed-tools as headless mode.
#   2. Wait for claude to render its prompt (rough proxy: sleep until the
#      pane has visible content — could tighten with `tmux capture-pane`
#      polling for the input indicator).
#   3. Send the natural prompt one ~80-char chunk at a time with a small
#      pause between chunks (human typing rhythm — also gives bicameral
#      skills time to react if any are configured to fire pre-submit).
#   4. Send Enter, then wait for the agent to finish. "Finish" is
#      detected by polling for the prompt indicator returning (i.e., the
#      agent stopped emitting tokens and is waiting for next input).
#   5. Capture the pane contents to a transcript file, kill tmux.
#
# When wrapped in the Xvfb+ffmpeg+chromium split-screen recorder from the
# plan doc, the xterm attached to the tmux session is what ffmpeg records
# on the left half. The dashboard sidecar (spawned via bicameral.dashboard
# inside the session) renders on the right half.

FLOWS=(
    "Flow1:flow-1-ingest.md"
    "Flow2:flow-2-preflight.md"
    "Flow3:flow-3-commit-sync.md"
    "Flow4:flow-4-session-end.md"
    "Flow5:flow-5-history.md"
)

for entry in "${FLOWS[@]}"; do
    NAME="${entry%%:*}"
    FILE="${entry#*:}"
    SESSION="bicameral-demo-${NAME}"
    TRANSCRIPT="${RESULTS_DIR}/${NAME}-interactive.txt"

    echo "=== ${NAME} (${FILE}) ==="

    # 1. Detached tmux running interactive claude
    tmux new-session -d -s "${SESSION}" -x 200 -y 50 \
        "claude \
            --mcp-config '${MCP_CONFIG}' \
            --strict-mcp-config \
            --allowed-tools 'mcp__bicameral,Read,Grep' \
            --add-dir '${DESKTOP_REPO_PATH}' \
            --no-session-persistence \
            --max-budget-usd 2.0 \
            --dangerously-skip-permissions"

    # 2. Wait for claude prompt to be ready. Rough heuristic — could be
    # tightened by capture-pane polling for the actual input cursor.
    sleep 6

    # 3. Type the natural prompt at human pace. send-keys -l sends the
    # literal characters (no escape interpretation), so prompts with
    # special chars survive. Chunk by line for natural cadence.
    PROMPT_FILE="${PROMPTS_DIR}/${FILE}"
    while IFS= read -r line; do
        tmux send-keys -t "${SESSION}" -l "${line}"
        sleep 0.1
        # In claude TUI, plain Enter submits — to insert a literal newline
        # within a prompt body, agents typically use Shift-Enter or paste.
        # For a multi-line prompt, "paste-style" via send-keys -l of the
        # whole text in one shot is more reliable than per-line submission.
        # See PASTE_MODE alternative below.
    done < "${PROMPT_FILE}"

    # 4. Submit
    tmux send-keys -t "${SESSION}" Enter

    # 5. Wait for agent to finish. Naive: sleep generously. Production:
    # poll `tmux capture-pane -p` for prompt return.
    sleep 90

    # Capture transcript
    tmux capture-pane -t "${SESSION}" -p -S - > "${TRANSCRIPT}"

    # Kill the session before next flow
    tmux kill-session -t "${SESSION}"
done

echo ""
echo "Interactive transcripts in: ${RESULTS_DIR}"
echo ""
echo "NOTE: This script captures terminal output only. To get split-screen"
echo "      MP4s with the dashboard, wrap this in the Xvfb+ffmpeg+chromium"
echo "      recorder from thoughts/shared/plans/2026-04-30-v0-userflow-demo-recording.md."

# ─── Caveats / open questions ──────────────────────────────────────────
#
# - claude TUI multi-line input: a single send-keys -l of the full prompt
#   may render as one line; if claude needs multi-line input, it's
#   preferable to switch to `tmux load-buffer` + `tmux paste-buffer`,
#   which behaves more like a real paste (preserves newlines).
# - "Wait for agent to finish": sleep 90s is a placeholder. Real impl:
#   loop on `tmux capture-pane -p` and watch for the prompt indicator
#   returning at the bottom of the pane.
# - SessionEnd hook: capture-corrections fires on session end. With
#   interactive claude, that means when the user types `exit` or hits
#   Ctrl+C. The driver should send `exit\n` after the agent quiets down
#   so SessionEnd actually fires.
# - Auto-fire reliability: this script does NOT prove auto-fire works in
#   interactive mode either. It's the prerequisite for testing it. After
#   recording, eyeball the transcript for whether bicameral.* tools fired
#   without the prompt naming them.
