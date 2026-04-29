#!/usr/bin/env python3
"""
Bicameral MCP demo simulator.
Prints realistic tool call outputs for each flow.
Usage: python simulate.py <flow>
  flows: ingest | preflight | sync | history
"""
import sys, time, textwrap

RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
GREEN = "\033[32m"
CYAN  = "\033[36m"
YELLOW= "\033[33m"
BLUE  = "\033[34m"
MAGENTA="\033[35m"
RED   = "\033[31m"
WHITE = "\033[97m"
GRAY  = "\033[90m"

def p(text="", delay=0):
    print(text)
    if delay:
        time.sleep(delay)

def header(title, subtitle=""):
    p()
    p(f"{BOLD}{CYAN}{'━' * 60}{RESET}")
    p(f"{BOLD}{WHITE}  {title}{RESET}")
    if subtitle:
        p(f"{DIM}  {subtitle}{RESET}")
    p(f"{BOLD}{CYAN}{'━' * 60}{RESET}")
    p()

def tool_call(name, args_str=""):
    p(f"{DIM}┌─ tool call{RESET}")
    p(f"{CYAN}│  {BOLD}{name}{RESET}")
    if args_str:
        for line in args_str.strip().split("\n"):
            p(f"{DIM}│  {line}{RESET}")
    p(f"{DIM}└{'─'*50}{RESET}")
    time.sleep(0.3)

def tool_result(content):
    p(f"{GREEN}◆ result{RESET}")
    for line in content.strip().split("\n"):
        p(f"  {line}")
    p()

def llm_output(text):
    p()
    for line in text.strip().split("\n"):
        p(f"{WHITE}{line}{RESET}")
    p()


# ─── FLOW 1: INGEST ──────────────────────────────────────────────────────────
def flow_ingest():
    header(
        "Flow 1 · Record decisions from a meeting",
        "Transcript in → ledger updated → gaps surfaced → decisions ratified"
    )

    p(f"{GRAY}# Paste your meeting notes or transcript...{RESET}")
    p(f"{BOLD}>{RESET} ingest decisions from today's API design meeting")
    p()
    time.sleep(0.5)

    tool_call("bicameral.ingest", """\
source: "meeting"
decisions:
  - "Use REST over GraphQL for the public API to minimize client complexity"
  - "Paginate /events with cursor-based pagination, not offset"
  - "Rate limit: 1000 req/min per API key, 429 with Retry-After header"
  - "Auth via Bearer token in Authorization header, no cookie sessions"
""")
    time.sleep(0.8)

    tool_result(f"""\
{GREEN}✓{RESET} {BOLD}4 decisions ingested{RESET}   signoff: proposed
  d:a1f2  REST over GraphQL for public API          {YELLOW}○ ungrounded{RESET}
  d:b3c4  Cursor-based pagination on /events         {YELLOW}○ ungrounded{RESET}
  d:e5f6  Rate limit 1000 req/min · 429+Retry-After  {YELLOW}○ ungrounded{RESET}
  d:g7h8  Bearer token auth · no cookies             {YELLOW}○ ungrounded{RESET}

{CYAN}→ judge_gaps auto-chained{RESET}""")

    tool_call("bicameral.judge_gaps")
    time.sleep(0.5)
    tool_result(f"""\
{BOLD}Gap analysis context pack ready{RESET}
  rubric: v0.4.19 · 4 decisions evaluated""")

    llm_output(f"""\
{BOLD}Gap Analysis{RESET}
{CYAN}◆ Missing error budget{RESET}  No decision covers retry behavior from client side
  (what happens when 429 is received — exponential backoff policy?)
{CYAN}◆ Auth scope granularity{RESET}  Bearer tokens — are scopes defined?
  (read-only vs read-write vs admin differentiation missing)

{DIM}2 gaps flagged · 4 decisions ready to ratify{RESET}""")

    p(f"{BOLD}>{RESET} ratify all — the gaps are nice-to-have, we'll address later")
    p()
    time.sleep(0.4)

    tool_call("bicameral.ratify", 'decision_ids: ["d:a1f2","d:b3c4","d:e5f6","d:g7h8"]')
    time.sleep(0.5)
    tool_result(f"""\
{GREEN}✓{RESET} {BOLD}4 decisions ratified{RESET}  drift tracking {GREEN}active{RESET}
  signoff: ✓ ratified · 2026-04-28
  status:  pending  (awaiting first commit sync)""")


# ─── FLOW 2: PREFLIGHT ───────────────────────────────────────────────────────
def flow_preflight():
    header(
        "Flow 2 · Begin to write code",
        "Surface binding decisions and active drift before touching any file"
    )

    p(f"{BOLD}>{RESET} implement the /events endpoint pagination")
    p()
    time.sleep(0.5)

    tool_call("bicameral.preflight", 'feature_hint: "events pagination"')
    time.sleep(0.7)

    tool_result(f"""\
{BOLD}Preflight · 3 decisions in scope{RESET}

  {GREEN}✓ compliant{RESET}  d:b3c4  Cursor-based pagination on /events
    bound → {CYAN}api/routes/events.py:42-89{RESET}  last reflected: HEAD~3
    {DIM}content hash matches · no drift{RESET}

  {YELLOW}⚡ drifted{RESET}   d:e5f6  Rate limit 1000 req/min
    bound → {CYAN}middleware/rate_limit.py:14-31{RESET}
    {RED}Hash mismatch detected{RESET} — file changed at HEAD~1
    {DIM}diff: limit changed from 1000 to 500 (PR #47){RESET}

  {YELLOW}○ ungrounded{RESET} d:g7h8  Bearer token auth
    {DIM}no code region bound yet — use bicameral.bind after locating symbol{RESET}

{CYAN}→ capture-corrections scanning last 12 turns...{RESET}
  {DIM}1 uningested correction found{RESET}
  {GREEN}→ bicameral.ingest (mechanical, silent){RESET}""")

    llm_output(f"""\
{BOLD}Action items before editing:{RESET}

{RED}1. Rate limit drift{RESET}  middleware/rate_limit.py:14-31
   Decision says 1000 req/min · code shows 500 · need to reconcile
   Run {CYAN}bicameral.resolve_compliance{RESET} after reviewing the change

{YELLOW}2. Auth ungrounded{RESET}  Bearer token decision has no code binding
   Locate the auth middleware and run {CYAN}bicameral.bind{RESET}

{GREEN}3. Pagination compliant ✓{RESET}  safe to extend api/routes/events.py""")


# ─── FLOW 3: SYNC (COMMIT) ───────────────────────────────────────────────────
def flow_sync():
    header(
        "Flow 3 · Commit code",
        "Auto-fires on next tool call after HEAD moves · injects compliance checks"
    )

    p(f"{GRAY}# You just ran: git commit -m 'fix: rate limit back to 1000/min'{RESET}")
    p(f"{BOLD}>{RESET} run the test suite")
    p()
    time.sleep(0.5)

    p(f"{DIM}[ensure_ledger_synced auto-fired — HEAD moved]{RESET}")
    p()
    time.sleep(0.3)

    tool_call("bicameral.link_commit", """\
commit: "a4f9d2e"
message: "fix: rate limit back to 1000/min\"""")
    time.sleep(0.8)

    tool_result(f"""\
{BOLD}Sync complete{RESET}  HEAD a4f9d2e
  regions updated:  3
  decisions:        {GREEN}3 reflected{RESET} · 0 drifted

{YELLOW}▸ pending_compliance_checks (1){RESET}
  flow_id: 7c3a-b291
  ┌─────────────────────────────────────────────────────┐
  │  d:e5f6  Rate limit 1000 req/min · 429+Retry-After  │
  │  file:   middleware/rate_limit.py:14-31              │
  │  code:   RATE_LIMIT_RPM = 1000  # restored          │
  └─────────────────────────────────────────────────────┘""")

    llm_output(f"""\
{BOLD}Reviewing rate limit change...{RESET}
  middleware/rate_limit.py:14  RATE_LIMIT_RPM = 1000  ← matches decision
  middleware/rate_limit.py:18  retry_after = 60        ← Retry-After header present""")

    tool_call("bicameral.resolve_compliance", """\
flow_id: "7c3a-b291"
region_id: "r:e5f6"
verdict: "compliant"
rationale: "RATE_LIMIT_RPM restored to 1000, Retry-After header confirmed present\"""")
    time.sleep(0.4)
    tool_result(f"""\
{GREEN}✓ verdict recorded{RESET}  d:e5f6 → compliant
  status: reflected · signoff: ✓ ratified""")


# ─── FLOW 5: HISTORY ─────────────────────────────────────────────────────────
def flow_history():
    header(
        "Flow 5 · Show what's been tracked",
        "Full ledger dump · canonical ratification surface"
    )

    p(f"{BOLD}>{RESET} show me all tracked decisions")
    p()
    time.sleep(0.5)

    tool_call("bicameral.history")
    time.sleep(0.7)

    tool_result(f"""\
{BOLD}Decision Ledger{RESET}  2026-04-28  ·  7 decisions across 2 features

{BOLD}{CYAN}API Design{RESET}
  {GREEN}✓ reflected{RESET}  ✓  d:a1f2  REST over GraphQL for public API
  {GREEN}✓ reflected{RESET}  ✓  d:b3c4  Cursor pagination on /events
  {GREEN}✓ reflected{RESET}  ✓  d:e5f6  Rate limit 1000/min · 429+Retry-After
  {YELLOW}○ ungrounded{RESET} ✓  d:g7h8  Bearer token auth · no cookies

{BOLD}{CYAN}Auth{RESET}
  {YELLOW}○ pending{RESET}   ○  d:k1l2  OAuth2 PKCE for web clients          {DIM}← AI-surfaced ~{RESET}
  {YELLOW}○ pending{RESET}   ○  d:m3n4  Refresh token rotation on use         {DIM}← AI-surfaced ~{RESET}
  {DIM}─ superseded{RESET}  d:p5q6  Session cookies for web              {DIM}← superseded by d:g7h8{RESET}

{CYAN}⚪ 2 unratified proposals in [Auth] — ratify now?{RESET}""")

    p(f"{BOLD}>{RESET} yes, ratify the OAuth decisions")
    p()
    time.sleep(0.4)

    tool_call("bicameral.ratify", 'decision_ids: ["d:k1l2","d:m3n4"]')
    time.sleep(0.4)
    tool_result(f"""\
{GREEN}✓{RESET} {BOLD}2 decisions ratified{RESET}  drift tracking active
  d:k1l2  OAuth2 PKCE for web clients
  d:m3n4  Refresh token rotation on use
  status: pending (ungrounded — bind to activate hash tracking)""")

    p()
    p(f"{BOLD}{GREEN}{'━' * 60}{RESET}")
    p(f"{BOLD}{GREEN}  Ledger up to date · 6 ratified · 0 drifted{RESET}")
    p(f"{BOLD}{GREEN}{'━' * 60}{RESET}")
    p()


if __name__ == "__main__":
    flow = sys.argv[1] if len(sys.argv) > 1 else "all"
    if flow == "ingest":
        flow_ingest()
    elif flow == "preflight":
        flow_preflight()
    elif flow == "sync":
        flow_sync()
    elif flow == "history":
        flow_history()
    else:
        flow_ingest()
        p("\n" + "─" * 60 + "\n")
        time.sleep(1)
        flow_preflight()
        p("\n" + "─" * 60 + "\n")
        time.sleep(1)
        flow_sync()
        p("\n" + "─" * 60 + "\n")
        time.sleep(1)
        flow_history()
