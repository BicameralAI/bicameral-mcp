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
READY_TIMEOUT=90        # claude TUI must show input box within this — longer
                        # because fresh-runner state walks 5+ onboarding dialogs
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

# ── Auth: ANTHROPIC_API_KEY (NOT CLAUDE_CODE_OAUTH_TOKEN) ──────────────
# Verified locally and matches GH issue #32463: interactive `claude` reads
# but does NOT honour `CLAUDE_CODE_OAUTH_TOKEN`. It DOES honour
# `ANTHROPIC_API_KEY`, but on first run it shows a "Detected a custom API
# key in your environment / Do you want to use this API key?" picker that
# we have to dismiss in `wait_for_claude_ready`. The assertions job keeps
# using OAuth (its `claude -p` path honours that env var fine).
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "[demo] WARNING: ANTHROPIC_API_KEY unset — interactive claude will hit the 'Select login method' picker with no way to advance" >&2
fi

# ── Materialize MCP config (mirrors run_e2e_flows.py) ───────────────────
sed \
  -e "s|\${DESKTOP_REPO_PATH}|$DESKTOP_REPO_PATH|g" \
  -e "s|\${LEDGER_DIR}|$LEDGER_DIR|g" \
  "$MCP_CONFIG_TEMPLATE" > "$MCP_CONFIG_MATERIALIZED"

# ── PostToolUse hook: surface "new commit detected" so bicameral-sync
#    auto-fires link_commit after the agent runs git commit/merge/pull.
#    Imports the EXACT command string from setup_wizard.py so the recording
#    exercises what a real bicameral-mcp setup installs — single source of
#    truth, no drift between test and production. ─────────────────────────
SETTINGS_FILE="$RESULTS_DIR/claude-settings-with-hook.json"
python3 - "$MCP_DIR" "$SETTINGS_FILE" <<'PY'
import json, sys, pathlib
mcp_root, dst = sys.argv[1], sys.argv[2]
sys.path.insert(0, mcp_root)
from setup_wizard import _BICAMERAL_POST_COMMIT_COMMAND
settings = {
    "hooks": {
        "PostToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": _BICAMERAL_POST_COMMIT_COMMAND}],
            }
        ]
    }
}
pathlib.Path(dst).write_text(json.dumps(settings, indent=2))
PY

# ── Reset desktop-clone to the pinned HEAD between scenes — flow 3 makes
#    a real commit, so without a reset the second-onwards run starts off a
#    polluted base. Pinned commit is the workflow's DESKTOP_PINNED_COMMIT. ─
reset_desktop_repo() {
  if [ -d "$DESKTOP_REPO_PATH/.git" ]; then
    (cd "$DESKTOP_REPO_PATH" && git reset --hard FETCH_HEAD 2>/dev/null \
      || git reset --hard HEAD 2>/dev/null) >/dev/null 2>&1 || true
  fi
}
reset_desktop_repo

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
# Walks the first-run onboarding dialog stack on a fresh CI runner.
# Verified locally against claude 2.1.126 with HOME=tmpdir, ANTHROPIC_API_KEY
# set: dismissals reach the `^❯ ` input prompt at t≈7s.
#
# Sequence (each fires at most once per session):
#   1. Theme picker  ("Choose the text style ... run /theme")
#        — Enter (default option 2 = Dark mode is preselected)
#   2. API key picker ("Detected a custom API key in your environment")
#        — '1' (override the preselected "No (recommended)" with "Yes")
#   3. Security notes ("Security notes: ... Press Enter to continue…")
#        — Enter
#   4. Trust folder  ("Quick safety check ... trust this folder")
#        — Enter (default option 1 = Yes is preselected)
#   5. New MCP server prompt ("New MCP server found in .mcp.json")
#        — Enter (default option 1 = Use this and all future)
#   6. Bypass-permissions warning ("Claude Code running in Bypass Permissions mode")
#        — '2' (override the preselected "No, exit" with "Yes, I accept")
#
# Detection: search WHOLE pane (not `tail -3`) — claude renders dialogs at a
# fixed row near the middle of a tall pane. The `^❯` anchor at column 0
# matches only the actual input prompt, not the menu rows ` ❯ 2. ...` which
# have a leading space.
wait_for_claude_ready() {
  local session=$1
  local i=0
  declare -A dismissed=()
  while [ $i -lt $READY_TIMEOUT ]; do
    if ! tmux has-session -t "$session" 2>/dev/null; then
      echo "  warning: $session died before TUI was ready" >&2
      return 1
    fi
    local pane
    pane="$(tmux capture-pane -t "$session" -p 2>/dev/null || true)"

    # Ready
    if printf '%s' "$pane" | grep -q '^❯'; then
      return 0
    fi

    # Onboarding dialogs — each at most once per session
    if [ -z "${dismissed[theme]:-}" ] && \
       printf '%s' "$pane" | grep -qE 'Choose the text style|run /theme'; then
      tmux send-keys -t "$session" Enter
      dismissed[theme]=1; sleep 2; i=$((i+2)); continue
    fi
    if [ -z "${dismissed[api_key]:-}" ] && \
       printf '%s' "$pane" | grep -q 'Detected a custom API key'; then
      tmux send-keys -t "$session" '1'
      dismissed[api_key]=1; sleep 2; i=$((i+2)); continue
    fi
    if [ -z "${dismissed[security]:-}" ] && \
       printf '%s' "$pane" | grep -q 'Security notes:'; then
      tmux send-keys -t "$session" Enter
      dismissed[security]=1; sleep 2; i=$((i+2)); continue
    fi
    if [ -z "${dismissed[trust]:-}" ] && \
       printf '%s' "$pane" | grep -q 'trust this folder'; then
      tmux send-keys -t "$session" Enter
      dismissed[trust]=1; sleep 2; i=$((i+2)); continue
    fi
    if [ -z "${dismissed[mcp]:-}" ] && \
       printf '%s' "$pane" | grep -q 'New MCP server found'; then
      tmux send-keys -t "$session" Enter
      dismissed[mcp]=1; sleep 2; i=$((i+2)); continue
    fi
    if [ -z "${dismissed[bypass]:-}" ] && \
       printf '%s' "$pane" | grep -q 'Bypass Permissions mode'; then
      tmux send-keys -t "$session" '2'
      dismissed[bypass]=1; sleep 2; i=$((i+2)); continue
    fi

    sleep 1
    i=$((i+1))
  done
  echo "  warning: claude TUI never showed input prompt for $session" >&2
  return 1
}

# type_prompt <session> <body> [total_seconds]
# Types body character-by-character so the recording shows a human-paced
# typing animation (default ~3s total regardless of length, like the user
# asked). Embedded newlines are inserted via M-Enter (Alt+Return) — the
# only escape that preserves newlines in claude TUI's input box without
# submitting (verified locally). Final Enter submits.
type_prompt() {
  local session=$1
  local body=$2
  local total_secs=${3:-3}
  local len=${#body}
  if [ "$len" -le 0 ]; then return; fi
  local delay
  delay=$(python3 -c "print(round(max(0.005, ${total_secs} / ${len}), 4))")
  local i ch
  for ((i=0; i<len; i++)); do
    ch="${body:$i:1}"
    if [ "$ch" = $'\n' ]; then
      tmux send-keys -t "$session" M-Enter
    else
      tmux send-keys -t "$session" -l "$ch"
    fi
    sleep "$delay"
  done
  sleep 0.3
  tmux send-keys -t "$session" Enter
}

# wait_for_agent_idle <session>
# Claude TUI keeps the `❯ ` input prompt rendered at a fixed row even while
# streaming, so the prompt-visible test is necessary but not sufficient. The
# real signal that the agent stopped is pane stability — when the streaming
# output stops mutating for IDLE_STABLE_FOR consecutive samples, we're idle.
wait_for_agent_idle() {
  local session=$1
  local stable_count=0
  local i=0
  local prev=""
  while [ $i -lt $IDLE_MAX_WAIT ]; do
    if ! tmux has-session -t "$session" 2>/dev/null; then
      echo "  warning: $session died mid-response" >&2
      return 1
    fi
    local pane
    pane="$(tmux capture-pane -t "$session" -p 2>/dev/null || true)"
    if [ "$pane" = "$prev" ] && printf '%s' "$pane" | grep -q '^❯'; then
      stable_count=$((stable_count+1))
      if [ $stable_count -ge $IDLE_STABLE_FOR ]; then
        return 0
      fi
    else
      stable_count=0
    fi
    prev=$pane
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

run_scene() {
  local N=$1
  local FILE=$2
  local SESSION="scene-${N}"
  local PROMPT_FILE="$PROMPTS_DIR/$FILE"
  local CLAUDE_LOG="$RESULTS_DIR/claude-scene-${N}.stderr"
  local CLAUDE_EXIT="$RESULTS_DIR/claude-scene-${N}.exit"
  local PANE_DUMP="$RESULTS_DIR/scene-${N}-pane.txt"
  local RUNNER="$RESULTS_DIR/claude-scene-${N}.sh"
  echo "=== Scene ${N} (${FILE}) ==="

  # New MCP process per scene → port may change. Wipe stale port file so the
  # poll below only sees this scene's value.
  rm -f "$PORT_FILE" "$CLAUDE_LOG" "$CLAUDE_EXIT"

  echo "scene_${N}_start=$(now_offset)" >> "$SCENE_BOUNDS_FILE"

  # Per-scene runner: redirects claude's stderr to a log and writes its exit
  # code to a sibling file, so a startup failure (bad flag, missing OAuth,
  # MCP crash) leaves actionable diagnostics instead of a silent dead pane.
  # `--no-session-persistence` and `--max-budget-usd` are intentionally NOT
  # passed — both are documented as `--print`-only and cause an immediate
  # exit-1 in interactive mode (verified locally against claude 2.1.x).
  cat > "$RUNNER" <<EOF
#!/usr/bin/env bash
cd "$DESKTOP_REPO_PATH"
exec 2>"$CLAUDE_LOG"
claude \\
    --mcp-config "$MCP_CONFIG_MATERIALIZED" \\
    --strict-mcp-config \\
    --settings "$SETTINGS_FILE" \\
    --allowed-tools mcp__bicameral,Read,Grep,Edit,Bash \\
    --add-dir "$DESKTOP_REPO_PATH" \\
    --dangerously-skip-permissions
echo "exit=\$?" > "$CLAUDE_EXIT"
EOF
  chmod +x "$RUNNER"

  tmux new-session -d -s "$SESSION" -x 110 -y 40 "$RUNNER" || {
    echo "  ERROR: tmux new-session failed for $SESSION" >&2
    echo "scene_${N}_end=$(now_offset)" >> "$SCENE_BOUNDS_FILE"
    return 1
  }

  xterm -geometry 100x40+0+0 -fa Monospace -fs 11 \
    -bg black -fg white -title "claude — scene ${N}: ${FILE}" \
    -e bash -lc "tmux attach -t $SESSION; sleep 2" \
    >/tmp/xterm-scene-${N}.log 2>&1 &
  XTERM_PIDS+=($!)

  if ! wait_for_claude_ready "$SESSION"; then
    {
      echo "--- last pane capture ---"
      tmux capture-pane -t "$SESSION" -p 2>/dev/null || echo "(no pane — session dead)"
      echo "--- claude stderr ---"
      cat "$CLAUDE_LOG" 2>/dev/null || echo "(no stderr log)"
      echo "--- claude exit ---"
      cat "$CLAUDE_EXIT" 2>/dev/null || echo "(no exit file — process may still be alive)"
    } > "$PANE_DUMP"
    echo "  ERROR: scene ${N} did not reach ready state — diagnostics in $PANE_DUMP" >&2
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    echo "scene_${N}_end=$(now_offset)" >> "$SCENE_BOUNDS_FILE"
    return 1
  fi

  PROMPT_BODY="${DASHBOARD_PREAMBLE}$(cat "$PROMPT_FILE")"
  type_prompt "$SESSION" "$PROMPT_BODY" 3

  if PORT="$(poll_port_file)"; then
    refresh_chromium_for_port "$PORT"
  else
    echo "  warning: scene ${N} dashboard.port never appeared — right pane may be stale" >&2
  fi

  wait_for_agent_idle "$SESSION" || true

  # Pause so the dashboard SSE settles into its final state for this scene
  # (also masks the chromium reload flicker on the next scene behind a still
  # frame of the closing state).
  sleep 3

  # Trigger SessionEnd hook (capture-corrections may auto-fire here), then
  # wait for the tmux session to die naturally.
  tmux send-keys -t "$SESSION" '/exit' Enter
  wait_for_session_dead "$SESSION"

  tmux capture-pane -t "$SESSION" -p -S - 2>/dev/null > "$PANE_DUMP" || true

  echo "scene_${N}_end=$(now_offset)" >> "$SCENE_BOUNDS_FILE"
  return 0
}

# `set +e` around each scene so a single failure doesn't abort the whole run —
# we still want the partial recording + diagnostics for the scenes that did
# work. Failed scenes still emit start/end bounds (zero-length window) so the
# downstream split logic walks them as empty cuts.
for entry in "${SCENES[@]}"; do
  N="${entry%%:*}"
  FILE="${entry#*:}"
  set +e
  run_scene "$N" "$FILE"
  rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    echo "  (scene ${N} failed; continuing to next)" >&2
  fi
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

# Failed scenes produce a zero-length (or near-zero) window. Skip them so we
# don't emit empty mp4s that break the downstream concat.
cut_scene() {
  local from=$1 to=$2 dst=$3
  local span
  span="$(python3 -c "print(max(0.0, float('$to') - float('$from')))")"
  if python3 -c "import sys; sys.exit(0 if float('$span') >= 0.5 else 1)"; then
    ffmpeg -y -i "$FULL_MP4" -ss "$from" -to "$to" "${ENC_FLAGS[@]}" "$dst" \
      >>/tmp/ffmpeg-int-split.log 2>&1 || rm -f "$dst"
  else
    echo "  skip: $(basename "$dst") window=${span}s (scene likely failed)" >&2
    rm -f "$dst"
  fi
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

# pm/dev concat — only include scene mp4s that actually exist (a failed scene
# leaves no file behind; see cut_scene). Skip the concat entirely if every
# input is missing.
write_concat_list() {
  local list=$1
  shift
  : > "$list"
  for f in "$@"; do
    if [ -s "$f" ]; then
      echo "file '$f'" >> "$list"
    fi
  done
}

run_concat() {
  local list=$1 out=$2
  if [ ! -s "$list" ]; then
    echo "  warning: $(basename "$out") concat list empty — skipping" >&2
    return 0
  fi
  ffmpeg -y -f concat -safe 0 -i "$list" "${ENC_FLAGS[@]}" "$out" \
    >>/tmp/ffmpeg-int-concat.log 2>&1
}

PM_OUT="$OUT_DIR/pm.mp4"
PM_LIST="$RESULTS_DIR/pm-int-concat.txt"
write_concat_list "$PM_LIST" "$S1" "$TRANSITION" "$S5"
run_concat "$PM_LIST" "$PM_OUT"

DEV_OUT="$OUT_DIR/dev.mp4"
DEV_LIST="$RESULTS_DIR/dev-int-concat.txt"
write_concat_list "$DEV_LIST" "$S2" "$S3" "$S4"
run_concat "$DEV_LIST" "$DEV_OUT"

# Clean up scratch files; keep per-scene mp4s + pm.mp4 + dev.mp4 + full-int.mp4.
rm -f "$PM_LIST" "$DEV_LIST" "$TRANSITION"

echo "=== Interactive recording + split complete ==="
ls -la "$OUT_DIR"
