#!/usr/bin/env bash
# Record a single continuous split-screen demo session of the v0 user flow,
# then post-split the recording into pm.mp4 (PM persona) and dev.mp4
# (Dev persona). pm.mp4 has a transition slide between the
# pre-implementation and post-implementation chapters.
#
# Layout (1920x1080):
#   ┌──────────────────────────┬──────────────────────────┐
#   │  xterm                   │  chromium                │
#   │  claude -p <composite>   │  http://localhost:<port> │
#   │  (one continuous session │  bicameral dashboard     │
#   │  spanning all 3 scenes)  │  (live SSE updates)      │
#   └──────────────────────────┴──────────────────────────┘
#
# Single claude session = single MCP process = single in-memory ledger.
# That's what makes Scene 3 (PM post-impl) authentically reflect Scene 2's
# (Dev) commits — the dashboard SSE keeps state across the whole arc.
#
# This script runs only in the GitHub workflow's optional manual-dispatch
# path (`record_demo=true`). It is `continue-on-error` at the workflow
# level — a flake here never gates merge.

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────
DISPLAY_NUM=99
RES_W=1920
RES_H=1080
HALF_W=$((RES_W / 2))
RES="${RES_W}x${RES_H}"
FRAMERATE=10
TRANSITION_DURATION=4

E2E_DIR="$(cd "$(dirname "$0")" && pwd)"
MCP_DIR="$(cd "$E2E_DIR/../.." && pwd)"
OUT_DIR="$MCP_DIR/docs/demos/v0-userflow-e2e"
RESULTS_DIR="$MCP_DIR/test-results/e2e"
MCP_CONFIG_TEMPLATE="$E2E_DIR/bicameral.mcp.json"
MCP_CONFIG_MATERIALIZED="$RESULTS_DIR/bicameral.mcp.materialized.json"
PORT_FILE="$HOME/.bicameral/dashboard.port"
COMPOSITE_PROMPT_FILE="$E2E_DIR/prompts/composite-demo.md"
DEMO_RENDERER="$E2E_DIR/demo_renderer.py"

DESKTOP_REPO_PATH="${DESKTOP_REPO_PATH:-/tmp/desktop-clone}"

mkdir -p "$OUT_DIR" "$RESULTS_DIR" "$(dirname "$PORT_FILE")"

if [ ! -d "$DESKTOP_REPO_PATH" ]; then
  echo "ERROR: DESKTOP_REPO_PATH=$DESKTOP_REPO_PATH does not exist." >&2
  exit 2
fi

for bin in Xvfb fluxbox xterm ffmpeg claude bicameral-mcp python3; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "ERROR: required binary '$bin' not found on PATH." >&2
    exit 2
  fi
done

# Pick whichever chromium-compatible browser is available. GitHub's
# ubuntu-latest runners ship google-chrome-stable; Linux desktops often
# have chromium via snap. All four accept the same Chromium-style flags.
CHROME_BIN="$(command -v google-chrome-stable \
  || command -v google-chrome \
  || command -v chromium \
  || command -v chromium-browser \
  || true)"
if [ -z "$CHROME_BIN" ]; then
  echo "ERROR: no chromium-compatible browser found on PATH." >&2
  echo "  tried: google-chrome-stable, google-chrome, chromium, chromium-browser" >&2
  exit 2
fi
echo "[demo] using browser: $CHROME_BIN"

# ── Materialize MCP config (mirrors run_e2e_flows.py) ───────────────────
sed "s|\${DESKTOP_REPO_PATH}|$DESKTOP_REPO_PATH|g" \
  "$MCP_CONFIG_TEMPLATE" > "$MCP_CONFIG_MATERIALIZED"

# Reset port file so the chromium poll only sees this run's value.
rm -f "$PORT_FILE"

# ── Start Xvfb + minimal WM ─────────────────────────────────────────────
Xvfb ":${DISPLAY_NUM}" -screen 0 "${RES}x24" -nolisten tcp >/tmp/xvfb.log 2>&1 &
XVFB_PID=$!
export DISPLAY=":${DISPLAY_NUM}"
sleep 1

fluxbox >/tmp/fluxbox.log 2>&1 &
FLUXBOX_PID=$!
sleep 1

cleanup() {
  set +e
  kill "$FLUXBOX_PID" "$XVFB_PID" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT

# ── Recording paths ─────────────────────────────────────────────────────
FULL_MP4="$OUT_DIR/full.mp4"
TRANSCRIPT="$RESULTS_DIR/composite-demo-transcript.ndjson"
SCENES_FILE="$RESULTS_DIR/composite-demo-scenes.txt"

export DEMO_TRANSCRIPT="$TRANSCRIPT"
export DEMO_SCENES_FILE="$SCENES_FILE"

PROMPT_BODY="$(cat "$COMPOSITE_PROMPT_FILE")"

# ── Start ffmpeg recording ──────────────────────────────────────────────
T0=$(date +%s.%N)
ffmpeg -y -f x11grab -video_size "$RES" -framerate "$FRAMERATE" \
  -i ":${DISPLAY_NUM}" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
  "$FULL_MP4" >/tmp/ffmpeg-record.log 2>&1 &
FFMPEG_PID=$!
sleep 1

# ── Build claude command piped through the demo renderer ────────────────
# stream-json gives us the tool-use timeline for scene detection;
# demo_renderer.py pretty-prints it back to readable text in the xterm.
# Bash is allowed for `git add`/`git commit` (per composite-demo.md);
# Edit is allowed so claude can modify cherry-pick.ts live.
CLAUDE_CMD=(
  claude -p "$PROMPT_BODY"
  --mcp-config "$MCP_CONFIG_MATERIALIZED"
  --strict-mcp-config
  --allowed-tools "mcp__bicameral,Read,Grep,Edit,Bash"
  --add-dir "$DESKTOP_REPO_PATH"
  --output-format stream-json
  --verbose
  --no-session-persistence
  --max-budget-usd 5.0
  --dangerously-skip-permissions
)

CLAUDE_LINE=""
for arg in "${CLAUDE_CMD[@]}"; do
  CLAUDE_LINE+=$(printf ' %q' "$arg")
done

# ── Launch xterm running claude → renderer ──────────────────────────────
(
  cd "$DESKTOP_REPO_PATH"  # so claude's Bash git commands run against the fixture repo
  xterm -geometry 100x40+0+0 -fa Monospace -fs 11 \
    -bg black -fg white -title "claude — composite demo (3 scenes)" \
    -e bash -lc "${CLAUDE_LINE# } | python3 ${DEMO_RENDERER}; echo; echo '[demo] all scenes complete — recording wraps in 4s'; sleep 4" \
    >/tmp/xterm-composite.log 2>&1 &
  echo $! > /tmp/xterm-composite.pid
)
XTERM_PID=$(cat /tmp/xterm-composite.pid)

# ── Poll for dashboard.port (up to 60s) and launch chromium ─────────────
PORT=""
for _ in $(seq 1 60); do
  if [ -f "$PORT_FILE" ]; then
    PORT="$(tr -d '[:space:]' < "$PORT_FILE" || true)"
    [ -n "$PORT" ] && break
  fi
  sleep 1
done

CHROMIUM_PID=""
if [ -n "$PORT" ]; then
  "$CHROME_BIN" --no-sandbox --disable-gpu \
    --window-size="${HALF_W},${RES_H}" \
    --window-position="${HALF_W},0" \
    --user-data-dir="/tmp/chromium-composite" \
    --no-first-run --no-default-browser-check \
    --new-window "http://localhost:${PORT}" \
    >/tmp/chromium-composite.log 2>&1 &
  CHROMIUM_PID=$!
else
  echo "  warning: dashboard port never appeared; recording xterm-only" >&2
fi

# ── Wait for claude to finish (cap 25 min) ──────────────────────────────
COMPOSITE_TIMEOUT=1500
WAITED=0
while kill -0 "$XTERM_PID" 2>/dev/null; do
  sleep 2
  WAITED=$((WAITED + 2))
  if [ "$WAITED" -ge "$COMPOSITE_TIMEOUT" ]; then
    echo "  warning: composite demo exceeded ${COMPOSITE_TIMEOUT}s — killing xterm" >&2
    kill "$XTERM_PID" 2>/dev/null || true
    break
  fi
done

# Brief pause so dashboard SSE settles into its final state on the right.
sleep 4

# ── Stop ffmpeg cleanly so the moov atom is flushed ─────────────────────
kill -INT "$FFMPEG_PID" 2>/dev/null || true
wait "$FFMPEG_PID" 2>/dev/null || true

if [ -n "$CHROMIUM_PID" ]; then
  kill "$CHROMIUM_PID" 2>/dev/null || true
  wait "$CHROMIUM_PID" 2>/dev/null || true
fi

if [ ! -s "$FULL_MP4" ]; then
  echo "ERROR: $FULL_MP4 missing or empty — nothing to split" >&2
  exit 1
fi

echo "=== full.mp4 written ($(stat -c%s "$FULL_MP4" 2>/dev/null || stat -f%z "$FULL_MP4") bytes) ==="
echo "=== Scene markers ==="
cat "$SCENES_FILE" 2>/dev/null || echo "(no scenes file)"

# ── Extract scene boundaries (epoch → seconds-from-T0) ──────────────────
to_offset() {
  python3 - "$T0" "$1" <<'PY'
import sys
t0 = float(sys.argv[1])
t = float(sys.argv[2])
print(f"{max(0.0, t - t0):.3f}")
PY
}

SCENE_1_TO_2_EPOCH="$(grep '^scene_1_to_2=' "$SCENES_FILE" 2>/dev/null | tail -1 | cut -d= -f2 || true)"
SCENE_2_TO_3_EPOCH="$(grep '^scene_2_to_3=' "$SCENES_FILE" 2>/dev/null | tail -1 | cut -d= -f2 || true)"

# ── Fallback path: if scene markers are missing, keep full.mp4 as the
# only artifact — pm/dev split is impossible without timestamps. ────────
if [ -z "$SCENE_1_TO_2_EPOCH" ] || [ -z "$SCENE_2_TO_3_EPOCH" ]; then
  echo "WARNING: scene boundary markers missing — emitting full.mp4 only" >&2
  echo "  (pm.mp4 / dev.mp4 will not be generated)"
  ls -la "$OUT_DIR"
  exit 0
fi

T1="$(to_offset "$SCENE_1_TO_2_EPOCH")"
T2="$(to_offset "$SCENE_2_TO_3_EPOCH")"
echo "Scene boundaries (s from T0): t1=$T1  t2=$T2"

# ── Trim full.mp4 into three pieces (re-encoded for frame-accurate cuts) ─
PM_PRE="$RESULTS_DIR/pm-pre.mp4"
DEV_OUT="$OUT_DIR/dev.mp4"
PM_POST="$RESULTS_DIR/pm-post.mp4"

# Common encoder flags so all pieces share codec/format for safe concat.
ENC_FLAGS=(
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p
  -r "$FRAMERATE"
  -an
)

ffmpeg -y -i "$FULL_MP4" -ss 0 -to "$T1" "${ENC_FLAGS[@]}" "$PM_PRE" \
  >>/tmp/ffmpeg-split.log 2>&1
ffmpeg -y -i "$FULL_MP4" -ss "$T1" -to "$T2" "${ENC_FLAGS[@]}" "$DEV_OUT" \
  >>/tmp/ffmpeg-split.log 2>&1
ffmpeg -y -i "$FULL_MP4" -ss "$T2" "${ENC_FLAGS[@]}" "$PM_POST" \
  >>/tmp/ffmpeg-split.log 2>&1

# ── Generate transition slide between PM-pre and PM-post ────────────────
TRANSITION="$RESULTS_DIR/transition.mp4"
FONT_BOLD="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Three lines centered on a deep navy background. Font sizes chosen for
# 1920x1080 readability; colors match a darkmode-dashboard palette so
# the transition feels of-a-piece with the rest of the demo.
ffmpeg -y \
  -f lavfi -i "color=c=#0a0e27:s=${RES_W}x${RES_H}:d=${TRANSITION_DURATION}:r=${FRAMERATE}" \
  -vf "drawtext=fontfile='${FONT_BOLD}':text='— Pre-implementation complete —':fontsize=58:fontcolor=#8aa0c8:x=(w-text_w)/2:y=(h-text_h)/2-180,
       drawtext=fontfile='${FONT_BOLD}':text='Dev now implements the change':fontsize=78:fontcolor=#ffffff:x=(w-text_w)/2:y=(h-text_h)/2-60,
       drawtext=fontfile='${FONT_REG}':text='(see dev.mp4 — preflight, edit, commit, link_commit, resolve_compliance)':fontsize=30:fontcolor=#8aa0c8:x=(w-text_w)/2:y=(h-text_h)/2+40,
       drawtext=fontfile='${FONT_BOLD}':text='Returning to PM after the implementation has landed':fontsize=46:fontcolor=#ffd76a:x=(w-text_w)/2:y=(h-text_h)/2+160" \
  "${ENC_FLAGS[@]}" -t "$TRANSITION_DURATION" "$TRANSITION" \
  >>/tmp/ffmpeg-transition.log 2>&1

# ── Concat pm.mp4 = pm-pre + transition + pm-post ───────────────────────
PM_CONCAT_LIST="$RESULTS_DIR/pm-concat.txt"
{
  echo "file '$PM_PRE'"
  echo "file '$TRANSITION'"
  echo "file '$PM_POST'"
} > "$PM_CONCAT_LIST"

PM_OUT="$OUT_DIR/pm.mp4"
ffmpeg -y -f concat -safe 0 -i "$PM_CONCAT_LIST" \
  "${ENC_FLAGS[@]}" "$PM_OUT" >>/tmp/ffmpeg-concat.log 2>&1

# Clean up the scratch trims; keep full.mp4 + dev.mp4 + pm.mp4 in OUT_DIR.
rm -f "$PM_PRE" "$PM_POST" "$TRANSITION" "$PM_CONCAT_LIST"

echo "=== Demo recording + split complete ==="
ls -la "$OUT_DIR"
