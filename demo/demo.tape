# Bicameral MCP — Use Case Demo
# Records the 5 core user activity flows from the call stack reference

Output demo/demo.gif
Output demo/demo.mp4

# Terminal settings
Set Shell "zsh"
Set FontSize 14
Set FontFamily "JetBrains Mono"
Set Width 920
Set Height 580
Set Theme "Dracula"
Set TypingSpeed 30ms
Set Padding 24

# ─── TITLE CARD ─────────────────────────────────────────────────────────────
Type "clear"
Enter
Sleep 300ms

Type `echo ""`
Enter
Type `echo "  Bicameral MCP  ·  decision ledger for your codebase"`
Enter
Type `echo "  v$(bicameral-mcp --version | awk '{print $2}')  ·  5 core flows"`
Enter
Type `echo ""`
Enter
Sleep 2s

# ─── SHOW TOOLS ─────────────────────────────────────────────────────────────
Type "bicameral-mcp --help"
Enter
Sleep 2s

Type "clear"
Enter
Sleep 300ms

# ─── FLOW 1: INGEST ─────────────────────────────────────────────────────────
Type `python3 demo/simulate.py ingest`
Enter
Sleep 200ms

# Slow down to let each section breathe
Sleep 1s    # header
Sleep 800ms # tool call
Sleep 1.2s  # tool result
Sleep 800ms # judge_gaps
Sleep 1s    # llm output gap analysis
Sleep 800ms # ratify prompt
Sleep 1s    # ratify result
Sleep 3s    # read end of flow

Type "clear"
Enter
Sleep 500ms

# ─── FLOW 2: PREFLIGHT ──────────────────────────────────────────────────────
Type `python3 demo/simulate.py preflight`
Enter
Sleep 200ms

Sleep 1s    # header
Sleep 800ms # tool call
Sleep 1.5s  # tool result with drift detection
Sleep 1.2s  # llm action items
Sleep 3s    # read

Type "clear"
Enter
Sleep 500ms

# ─── FLOW 3: SYNC ───────────────────────────────────────────────────────────
Type `python3 demo/simulate.py sync`
Enter
Sleep 200ms

Sleep 1s    # header
Sleep 600ms # auto-fired notice
Sleep 1s    # link_commit result
Sleep 1s    # llm reviewing
Sleep 800ms # resolve_compliance
Sleep 800ms # verdict
Sleep 3s    # read

Type "clear"
Enter
Sleep 500ms

# ─── FLOW 5: HISTORY + RATIFY ───────────────────────────────────────────────
Type `python3 demo/simulate.py history`
Enter
Sleep 200ms

Sleep 1s    # header
Sleep 1.5s  # history result full ledger
Sleep 800ms # ratify prompt
Sleep 800ms # ratify result
Sleep 800ms # summary banner
Sleep 4s    # final pause

Type "clear"
Enter
Sleep 300ms
