#!/usr/bin/env bash
# Interactive demo recording — tmux-driven real claude TUI, per-scene sessions.
#
# Implementation of thoughts/shared/plans/2026-05-01-interactive-recording-spec.md.
# Replaces the headless `claude -p` + demo_renderer.py path with five real
# interactive Claude Code sessions (one per flow), driven by `tmux send-keys` /
# bracketed paste. State carries across scenes via the shared surrealkv ledger
# (matching run_e2e_flows.py's persistence contract).
#
# Layout (1920x1080):
#   ┌──────────────────────────┬──────────────────────────┐
#   │  xterm                   │  chromium                │
#   │  attached to tmux pane   │  http://localhost:<port> │
#   │  running interactive     │  bicameral dashboard     │
#   │  claude TUI              │  (live SSE updates)      │
#   └──────────────────────────┴──────────────────────────┘
#
# Output (in $OUT_DIR):
#   - full-int.mp4   — raw continuous capture of all 5 scenes (no transition)
#   - scene-1.mp4 … scene-5.mp4 — per-scene splits
#   - pm.mp4         — scene-1 + transition slide + scene-5
#   - dev.mp4        — scene-2 + scene-3 + scene-4
#
# Legacy `record_demo.sh` is intentionally retained as a fallback path; the
# workflow's `recording` job has `continue-on-error: true`, so a flake here
# leaves the assertion artifacts intact.
#
# Prereqs (Linux runner): Xvfb, fluxbox, xterm, ffmpeg, tmux, claude CLI,
# bicameral-mcp, python3, chromium-compatible browser, DejaVu fonts.

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────
DISPLAY_NUM=99
RES_W=1920
RES_H=1080
HALF_W=$((RES_W / 2))
RES="${RES_W}x${RES_H}"
FRAMERATE=10
TRANSITION_DURATION=4

# Per-scene polling caps (see spec §6.1, §6.3, §6.4).
READY_TIMEOUT=30        # claude TUI must show input box within this
IDLE_MAX_WAIT=300       # 5 min cap per scene for agent finish
IDLE_STABLE_FOR=8       # input box must persist for N consecutive samples
SESSION_DEAD_GRACE=60   # post-/exit grace for SessionEnd hook to run
PORT_POLL_TIMEOUT=45    # post-paste wait for dashboard.port to appear

E2E_DIR="$(cd "$(dirname "$0")" && pwd)"
MCP_DIR="$(cd "$E2E_DIR/../.." && pwd)"
OUT_DIR="$MCP_DIR/docs/demos/v0-userflow-e2e"
RESULTS_DIR="$MCP_DIR/test-results/e2e"
LEDGER_DIR="$RESULTS_DIR/ledger.db"
MCP_CONFIG_TEMPLATE="$E2E_DIR/bicameral.mcp.json"
MCP_CONFIG_MATERIALIZED="$RESULTS_DIR/bicameral.mcp.materialized.json"
PROMPTS_DIR="$E2E_DIR/prompts"
PORT_FILE="$HOME/.bicameral/dashboard.port"

DESKTOP_REPO_PATH="${DESKTOP_REPO_PATH:-/tmp/desktop-clone}"

mkdir -p "$OUT_DIR" "$RESULTS_DIR" "$(dirname "$PORT_FILE")"

if [ ! -d "$DESKTOP_REPO_PATH" ]; then
  echo "ERROR: DESKTOP_REPO_PATH=$DESKTOP_REPO_PATH does not exist." >&2
  exit 2
fi

for bin in Xvfb fluxbox xterm ffmpeg claude bicameral-mcp python3 tmux; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "ERROR: required binary '$bin' not found on PATH." >&2
    exit 2
  fi
done

CHROME_BIN="$(command -v google-chrome-stable \
  || command -v google-chrome \
  || command -v chromium \
  || command -v chromium-browser \
  || true)"
if [ -z "$CHROME_BIN" ]; then
  echo "ERROR: no chromium-compatible browser found on PATH." >&2
  exit 2
fi
echo "[demo] using browser: $CHROME_BIN"

# ── Materialize MCP config (mirrors run_e2e_flows.py) ───────────────────
sed \
  -e "s|\${DESKTOP_REPO_PATH}|$DESKTOP_REPO_PATH|g" \
  -e "s|\${LEDGER_DIR}|$LEDGER_DIR|g" \
  "$MCP_CONFIG_TEMPLATE" > "$MCP_CONFIG_MATERIALIZED"

# Wipe persistent ledger between runs (state must persist across the 5 scenes
# within a run, but not leak across runs — same contract as run_e2e_flows.py).
rm -rf "$LEDGER_DIR"
rm -f "$PORT_FILE"

# ── Start Xvfb + minimal WM ─────────────────────────────────────────────
Xvfb ":${DISPLAY_NUM}" -screen 0 "${RES}x24" -nolisten tcp >/tmp/xvfb.log 2>&1 &
XVFB_PID=$!
export DISPLAY=":${DISPLAY_NUM}"
sleep 1

fluxbox >/tmp/fluxbox.log 2>&1 &
FLUXBOX_PID=$!
sleep 1

CHROMIUM_PID=""
CURRENT_PORT=""
FFMPEG_PID=""
XTERM_PIDS=()

cleanup() {
  set +e
  if [ -n "$FFMPEG_PID" ]; then
    kill -INT "$FFMPEG_PID" 2>/dev/null
    wait "$FFMPEG_PID" 2>/dev/null
  fi
  if [ -n "$CHROMIUM_PID" ]; then
    kill "$CHROMIUM_PID" 2>/dev/null
    wait "$CHROMIUM_PID" 2>/dev/null
  fi
  for s in $(tmux list-sessions -F '#S' 2>/dev/null | grep '^scene-' || true); do
    tmux kill-session -t "$s" 2>/dev/null
  done
  for p in "${XTERM_PIDS[@]}"; do
    kill "$p" 2>/dev/null
  done
  kill "$FLUXBOX_PID" "$XVFB_PID" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT

# ── Recording paths ─────────────────────────────────────────────────────
FULL_MP4="$OUT_DIR/full-int.mp4"
SCENE_BOUNDS_FILE="$RESULTS_DIR/scene-bounds-int.txt"
: > "$SCENE_BOUNDS_FILE"

# ── Helpers ──────────────────────────────────────────────────────────────

# now_offset — seconds elapsed since ffmpeg started (T0)
now_offset() {
  python3 - "$T0" "$(date +%s.%N)" <<'PY'
import sys
print(f"{max(0.0, float(sys.argv[2]) - float(sys.argv[1])):.3f}")
PY
}

# wait_for_claude_ready <session>
# Poll the bottom of the tmux pane for the input-box border characters
# (╭ ╰ │) or the legacy `>` indicator. Pinned TUI version (in workflow)
# keeps the regex stable.
wait_for_claude_ready() {
  local session=$1
  local i=0
  while [ $i -lt $READY_TIMEOUT ]; do
    if tmux capture-pane -t "$session" -p 2>/dev/null \
        | tail -3 | grep -q '^[╭╰│ ]\|^>'; then
      return 0
    fi
    sleep 1
    i=$((i+1))
  done
  echo "  warning: claude TUI never showed input box for $session" >&2
  return 1
}

# paste_prompt <session> <body>
# Bracketed paste preserves multi-line prompts as one input chunk; the agent
# only submits when the trailing Enter is sent separately. printf %s avoids
# tacking a stray trailing newline onto the buffer.
paste_prompt() {
  local session=$1
  local body=$2
  local buf="prompt-$session"
  printf '%s' "$body" | tmux load-buffer -b "$buf" -
  tmux paste-buffer -t "$session" -b "$buf" -d -p
  sleep 1
  tmux send-keys -t "$session" Enter
}

# wait_for_agent_idle <session>
# "Done" = the input indicator persists for IDLE_STABLE_FOR consecutive
# samples (1s each). Resets on any non-match — protects against false
# positives if the agent pauses briefly between tool calls.
wait_for_agent_idle() {
  local session=$1
  local stable_count=0
  local i=0
  while [ $i -lt $IDLE_MAX_WAIT ]; do
    if tmux capture-pane -t "$session" -p 2>/dev/null \
        | tail -3 | grep -q '^[╭╰│ ]\|^>'; then
      stable_count=$((stable_count+1))
      if [ $stable_count -ge $IDLE_STABLE_FOR ]; then
        return 0
      fi
    else
      stable_count=0
    fi
    sleep 1
    i=$((i+1))
  done
  echo "  warning: agent_idle timed out after ${IDLE_MAX_WAIT}s for $session" >&2
  return 1
}

# wait_for_session_dead <session>
# After /exit, claude runs the SessionEnd hook (capture-corrections may fire)
# before the process actually exits. Wait for natural death; force-kill only
# after the grace period to avoid polluting the ledger mid-hook.
wait_for_session_dead() {
  local session=$1
  local i=0
  while tmux has-session -t "$session" 2>/dev/null; do
    sleep 1
    i=$((i+1))
    if [ $i -ge $SESSION_DEAD_GRACE ]; then
      echo "  warning: $session didn't exit after ${SESSION_DEAD_GRACE}s — force-killing" >&2
      tmux kill-session -t "$session" 2>/dev/null
      break
    fi
  done
}

# poll_port_file — wait up to PORT_POLL_TIMEOUT for the dashboard sidecar to
# write its bound port. Returns the port on stdout (empty on timeout).
poll_port_file() {
  local i=0
  while [ $i -lt $PORT_POLL_TIMEOUT ]; do
    if [ -f "$PORT_FILE" ]; then
      local p
      p="$(tr -d '[:space:]' < "$PORT_FILE" || true)"
      if [ -n "$p" ]; then
        printf '%s' "$p"
        return 0
      fi
    fi
    sleep 1
    i=$((i+1))
  done
  return 1
}

# refresh_chromium_for_port <port>
# Each scene = new MCP process = new port. Kill the previous chromium and
# relaunch on the new port (spec §6.5 option A). The brief flicker visually
# emphasises the scene boundary; option B (standalone dashboard sidecar) is
# a deferred follow-up.
refresh_chromium_for_port() {
  local new_port=$1
  if [ "$new_port" = "$CURRENT_PORT" ] && [ -n "$CHROMIUM_PID" ] && kill -0 "$CHROMIUM_PID" 2>/dev/null; then
    return 0
  fi
  if [ -n "$CHROMIUM_PID" ]; then
    kill "$CHROMIUM_PID" 2>/dev/null || true
    wait "$CHROMIUM_PID" 2>/dev/null || true
  fi
  "$CHROME_BIN" --no-sandbox --disable-gpu \
    --window-size="${HALF_W},${RES_H}" \
    --window-position="${HALF_W},0" \
    --user-data-dir="/tmp/chromium-int-${new_port}" \
    --no-first-run --no-default-browser-check \
    --new-window "http://localhost:${new_port}" \
    >>/tmp/chromium-int.log 2>&1 &
  CHROMIUM_PID=$!
  CURRENT_PORT=$new_port
}

# ── Start ffmpeg (continuous capture) ────────────────────────────────────
T0=$(date +%s.%N)
ffmpeg -y -f x11grab -video_size "$RES" -framerate "$FRAMERATE" \
  -i ":${DISPLAY_NUM}" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
  "$FULL_MP4" >/tmp/ffmpeg-int.log 2>&1 &
FFMPEG_PID=$!
sleep 1

# ── Per-scene loop ──────────────────────────────────────────────────────
# One tmux+claude session per flow, mirroring run_e2e_flows.py exactly. State
# persists via the shared surrealkv ledger; what differs from headless is the
# real TUI rendering and the human-paced typed input.
SCENES=(
  "1:flow-1-ingest.md"
  "2:flow-2-preflight.md"
  "3:flow-3-commit-sync.md"
  "4:flow-4-session-end.md"
  "5:flow-5-history.md"
)

# Dashboard preamble — kept out of the flow prompt files so the assertion
# harness (which doesn't record) can reuse them as-is. Each scene's MCP
# process has its own port; this preamble triggers the dashboard tool so
# the port file is written and we can point chromium at it.
DASHBOARD_PREAMBLE='Before doing anything else, call bicameral.dashboard so a live dashboard sidecar is bound to this MCP process. Then continue with the request below.

'

for entry in "${SCENES[@]}"; do
  N="${entry%%:*}"
  FILE="${entry#*:}"
  SESSION="scene-${N}"
  PROMPT_FILE="$PROMPTS_DIR/$FILE"
  echo "=== Scene ${N} (${FILE}) ==="

  # New MCP process per scene → port may change. Wipe stale port file so the
  # poll below only sees this scene's value.
  rm -f "$PORT_FILE"

  echo "scene_${N}_start=$(now_offset)" >> "$SCENE_BOUNDS_FILE"

  # 1. Detached tmux running interactive claude (no -p) with the same MCP +
  #    allowed-tools shape as run_e2e_flows.py.
  CLAUDE_CMD="claude \
      --mcp-config $(printf %q "$MCP_CONFIG_MATERIALIZED") \
      --strict-mcp-config \
      --allowed-tools mcp__bicameral,Read,Grep \
      --add-dir $(printf %q "$DESKTOP_REPO_PATH") \
      --no-session-persistence \
      --max-budget-usd 5.0 \
      --dangerously-skip-permissions"

  tmux new-session -d -s "$SESSION" -x 110 -y 40 \
    "cd $(printf %q "$DESKTOP_REPO_PATH") && $CLAUDE_CMD"

  # 2. xterm attached to the tmux pane (left half). `;` (not `&&`) so the
  #    closing `sleep 2` runs even when tmux attach exits non-zero (which
  #    happens when the session dies underneath it).
  xterm -geometry 100x40+0+0 -fa Monospace -fs 11 \
    -bg black -fg white -title "claude — scene ${N}: ${FILE}" \
    -e bash -lc "tmux attach -t $SESSION; sleep 2" \
    >/tmp/xterm-scene-${N}.log 2>&1 &
  XTERM_PIDS+=($!)

  # 3. Wait for claude TUI to render its input box.
  wait_for_claude_ready "$SESSION" || true

  # 4. Paste the dashboard preamble + flow prompt, then submit.
  PROMPT_BODY="${DASHBOARD_PREAMBLE}$(cat "$PROMPT_FILE")"
  paste_prompt "$SESSION" "$PROMPT_BODY"

  # 5. The dashboard tool writes the port file once it runs. Poll for it,
  #    then (re)launch chromium on the right half.
  if PORT="$(poll_port_file)"; then
    refresh_chromium_for_port "$PORT"
  else
    echo "  warning: scene ${N} dashboard.port never appeared — right pane may be stale" >&2
  fi

  # 6. Wait for the agent to finish responding.
  wait_for_agent_idle "$SESSION" || true

  # 7. Pause so the dashboard SSE settles into its final state for this
  #    scene (also masks the chromium reload flicker on the next scene
  #    behind a still frame of the closing state).
  sleep 3

  # 8. Trigger SessionEnd hook (capture-corrections may auto-fire here),
  #    then wait for the tmux session to die naturally.
  tmux send-keys -t "$SESSION" '/exit' Enter
  wait_for_session_dead "$SESSION"

  # Capture pane contents for diagnostics (best-effort — session may already
  # be gone if force-killed).
  tmux capture-pane -t "$SESSION" -p -S - 2>/dev/null \
    > "$RESULTS_DIR/scene-${N}-pane.txt" || true

  echo "scene_${N}_end=$(now_offset)" >> "$SCENE_BOUNDS_FILE"
done

# Tail pause so ffmpeg captures a clean closing frame after scene 5.
sleep 3

# ── Stop ffmpeg cleanly ──────────────────────────────────────────────────
kill -INT "$FFMPEG_PID" 2>/dev/null || true
wait "$FFMPEG_PID" 2>/dev/null || true
FFMPEG_PID=""

if [ -n "$CHROMIUM_PID" ]; then
  kill "$CHROMIUM_PID" 2>/dev/null || true
  wait "$CHROMIUM_PID" 2>/dev/null || true
  CHROMIUM_PID=""
fi

if [ ! -s "$FULL_MP4" ]; then
  echo "ERROR: $FULL_MP4 missing or empty — nothing to split" >&2
  exit 1
fi

echo "=== full-int.mp4 written ($(stat -c%s "$FULL_MP4" 2>/dev/null || stat -f%z "$FULL_MP4") bytes) ==="
echo "=== Scene boundaries (offsets from T0) ==="
cat "$SCENE_BOUNDS_FILE"

# ── Read boundary timestamps ─────────────────────────────────────────────
get_bound() { grep "^${1}=" "$SCENE_BOUNDS_FILE" | tail -1 | cut -d= -f2; }

T_S1="$(get_bound scene_1_start)"
T_E1="$(get_bound scene_1_end)"
T_S2="$(get_bound scene_2_start)"
T_E2="$(get_bound scene_2_end)"
T_S3="$(get_bound scene_3_start)"
T_E3="$(get_bound scene_3_end)"
T_S4="$(get_bound scene_4_start)"
T_E4="$(get_bound scene_4_end)"
T_S5="$(get_bound scene_5_start)"
T_E5="$(get_bound scene_5_end)"

# Fallback path: if any boundary is missing, keep full-int.mp4 only — the
# split is meaningless without a complete set of timestamps.
for v in "$T_S1" "$T_E1" "$T_S2" "$T_E2" "$T_S3" "$T_E3" "$T_S4" "$T_E4" "$T_S5" "$T_E5"; do
  if [ -z "$v" ]; then
    echo "WARNING: scene boundary missing — emitting full-int.mp4 only" >&2
    ls -la "$OUT_DIR"
    exit 0
  fi
done

# ── Trim into per-scene mp4s (re-encoded for safe concat) ───────────────
ENC_FLAGS=(
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p
  -r "$FRAMERATE"
  -an
)

cut_scene() {
  local from=$1 to=$2 dst=$3
  ffmpeg -y -i "$FULL_MP4" -ss "$from" -to "$to" "${ENC_FLAGS[@]}" "$dst" \
    >>/tmp/ffmpeg-int-split.log 2>&1
}

S1="$OUT_DIR/scene-1.mp4"
S2="$OUT_DIR/scene-2.mp4"
S3="$OUT_DIR/scene-3.mp4"
S4="$OUT_DIR/scene-4.mp4"
S5="$OUT_DIR/scene-5.mp4"

cut_scene "$T_S1" "$T_E1" "$S1"
cut_scene "$T_S2" "$T_E2" "$S2"
cut_scene "$T_S3" "$T_E3" "$S3"
cut_scene "$T_S4" "$T_E4" "$S4"
cut_scene "$T_S5" "$T_E5" "$S5"

# ── Generate transition slide (matches legacy aesthetic) ─────────────────
TRANSITION="$RESULTS_DIR/transition-int.mp4"
FONT_BOLD="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

ffmpeg -y \
  -f lavfi -i "color=c=#0a0e27:s=${RES_W}x${RES_H}:d=${TRANSITION_DURATION}:r=${FRAMERATE}" \
  -vf "drawtext=fontfile='${FONT_BOLD}':text='— Pre-implementation complete —':fontsize=58:fontcolor=#8aa0c8:x=(w-text_w)/2:y=(h-text_h)/2-180,
       drawtext=fontfile='${FONT_BOLD}':text='Dev now implements the change':fontsize=78:fontcolor=#ffffff:x=(w-text_w)/2:y=(h-text_h)/2-60,
       drawtext=fontfile='${FONT_REG}':text='(see dev.mp4 — preflight, commit-sync, session-end capture)':fontsize=30:fontcolor=#8aa0c8:x=(w-text_w)/2:y=(h-text_h)/2+40,
       drawtext=fontfile='${FONT_BOLD}':text='Returning to PM after the implementation has landed':fontsize=46:fontcolor=#ffd76a:x=(w-text_w)/2:y=(h-text_h)/2+160" \
  "${ENC_FLAGS[@]}" -t "$TRANSITION_DURATION" "$TRANSITION" \
  >>/tmp/ffmpeg-int-transition.log 2>&1

# ── pm.mp4 = scene-1 + transition + scene-5 ─────────────────────────────
PM_OUT="$OUT_DIR/pm.mp4"
PM_LIST="$RESULTS_DIR/pm-int-concat.txt"
{
  echo "file '$S1'"
  echo "file '$TRANSITION'"
  echo "file '$S5'"
} > "$PM_LIST"
ffmpeg -y -f concat -safe 0 -i "$PM_LIST" "${ENC_FLAGS[@]}" "$PM_OUT" \
  >>/tmp/ffmpeg-int-concat.log 2>&1

# ── dev.mp4 = scene-2 + scene-3 + scene-4 ───────────────────────────────
DEV_OUT="$OUT_DIR/dev.mp4"
DEV_LIST="$RESULTS_DIR/dev-int-concat.txt"
{
  echo "file '$S2'"
  echo "file '$S3'"
  echo "file '$S4'"
} > "$DEV_LIST"
ffmpeg -y -f concat -safe 0 -i "$DEV_LIST" "${ENC_FLAGS[@]}" "$DEV_OUT" \
  >>/tmp/ffmpeg-int-concat.log 2>&1

# Clean up scratch files; keep per-scene mp4s + pm.mp4 + dev.mp4 + full-int.mp4.
rm -f "$PM_LIST" "$DEV_LIST" "$TRANSITION"

echo "=== Interactive recording + split complete ==="
ls -la "$OUT_DIR"
