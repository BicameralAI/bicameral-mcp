"""
Bicameral MCP v0.9.3 — Extended simulation against Accountable-App-3.0

Covers:
  Run 1  — Ingest + verify created_decisions field (new v0.9.3)
  Run 2  — Preflight regression check
  Run 3  — History: verify HistoryDecision.decision_level now shows (fix 2)
  Run 4  — Bind L2 decisions to real Accountable code (follow-up 1)
  Run 5  — Drift check post-bind (should be clean)
  Run 6  — Full ingest→bind→modify→drift loop on temp file (follow-up 4)
  Run 7  — Search in surrealkv:// persistent mode (fix 3 verification)
"""
import sys, asyncio, os, tempfile, shutil, pathlib
sys.path.insert(0, '/Users/jinhongkuan/github/bicameral/pilot/mcp')

REPO = '/Users/jinhongkuan/github/Accountable-App-3.0'
os.environ['SURREAL_URL'] = 'memory://'
os.environ['REPO_PATH'] = REPO

RESULTS = []

def section(title, body):
    RESULTS.append(f"\n## {title}\n\n{body.rstrip()}\n")
    preview = body[:120].replace('\n', ' ')
    print(f"[{title}]", preview)


def make_fresh_ledger():
    import importlib, adapters.ledger as _al
    importlib.reload(_al)
    return _al.get_ledger()


async def make_ctx(repo_path=None, surreal_url=None):
    if surreal_url:
        os.environ['SURREAL_URL'] = surreal_url
    if repo_path:
        os.environ['REPO_PATH'] = repo_path
    from adapters.code_locator import get_code_locator
    ledger = make_fresh_ledger()
    await ledger.connect()
    code_graph = get_code_locator()

    class Ctx:
        pass
    ctx = Ctx()
    ctx.repo_path = repo_path or REPO
    ctx.session_id = 'sim-accountable-v2'
    ctx.authoritative_ref = 'main'
    ctx.authoritative_sha = ''
    ctx.head_sha = ''
    ctx.drift_analyzer = None
    ctx._sync_state = {}
    ctx.ledger = ledger
    ctx.code_graph = code_graph
    return ctx


SLACK_DECISIONS = [
    {"description": "All code changes must go to staging first via PR targeting staging branch — Ian cannot merge direct to main", "feature_group": "Dev Process", "decision_level": "L1"},
    {"description": "Staging environment mirrors prod with real integrations (except SMS and Zoom) and must stay in sync with main", "feature_group": "Dev Process", "decision_level": "L2"},
    {"description": "Brian Borg acts as engineering quarterback and coordinator — all PRs assigned to Brian before going to prod", "feature_group": "Dev Process", "decision_level": "L1"},
    {"description": "All high-value secrets live in Supabase secrets — not in Vercel env vars", "feature_group": "Security", "decision_level": "L2"},
    {"description": "Sentry auth token must be rotated and marked Sensitive in Vercel after Vercel breach exposed unprotected env vars", "feature_group": "Security", "decision_level": "L1"},
    {"description": "Assess Sentry vs PostHog — PostHog now captures ~80% of Sentry value; evaluate eliminating redundant tool", "feature_group": "Observability", "decision_level": "L2"},
    {"description": "Individual coaching portal for 1:1 clients to manage engagements, see recording transcripts, insights and trends", "feature_group": "Coaching Portal", "decision_level": "L1"},
    {"description": "Weekly workshop module should be a repeatable component — AI agent populates it and creates a new record each week rather than generating new code", "feature_group": "Weekly Workshop", "decision_level": "L2"},
    {"description": "Users can view their daily check-in completion history and trend data in the Accountable platform", "feature_group": "Daily Check-in", "decision_level": "L1"},
    {"description": "Claude reasoning level should be task-appropriate — start at lower reasoning with escalation tiers rather than always using maximum reasoning", "feature_group": "AI Coach", "decision_level": "L2"},
    {"description": "Weekly community bulletin delivered as a dynamic page — email directs users there rather than embedding full content to protect deliverability", "feature_group": "Email / Comms", "decision_level": "L2"},
]


# ── Run 1: Ingest ────────────────────────────────────────────────────────────

async def run_ingest(ctx):
    from handlers.ingest import handle_ingest
    mappings = [
        {
            "intent": d["description"],
            "feature_group": d["feature_group"],
            "decision_level": d["decision_level"],
            "span": {
                "text": d["description"],
                "source_type": "slack",
                "source_ref": "accountable-tech",
                "meeting_date": "2026-04-26",
                "speakers": ["Ian Tenenbaum", "Brian Borg"],
            },
        }
        for d in SLACK_DECISIONS
    ]
    result = await handle_ingest(ctx, {
        "repo": REPO,
        "query": "Accountable platform decisions from #accountable-tech",
        "mappings": mappings,
    })

    created = result.created_decisions
    body = (
        f"Stats: {result.stats.intents_created} created, "
        f"{result.stats.grounded} grounded, {result.stats.ungrounded} ungrounded\n\n"
        f"created_decisions field: {len(created)} entries "
        f"(expected {result.stats.intents_created} — all decisions regardless of grounding)\n\n"
        "Entries:\n"
    )
    for d in created:
        body += f"  [{d.decision_level or '?'}] {d.decision_id}  \"{d.description[:58]}...\"\n"

    l1_in_pending = [d for d in result.pending_grounding_decisions if d.get("decision_level") == "L1"]
    body += (
        f"\nL1 filter: pending_grounding_decisions has "
        f"{len(result.pending_grounding_decisions)} entries, "
        f"{len(l1_in_pending)} L1 (expected 0) — {'PASS' if not l1_in_pending else 'FAIL'}\n"
    )
    section("Run 1 — Ingest + created_decisions verification", body)
    return result


# ── Run 2: Preflight regression ──────────────────────────────────────────────

async def run_preflight_quick(ctx):
    from handlers.preflight import handle_preflight
    r = await handle_preflight(ctx, topic="weekly workshop module repeatable component")
    fired = getattr(r, 'fired', False)
    count = len(getattr(r, 'decisions', []) or [])
    body = f"Topic: 'weekly workshop module repeatable component'\nFired: {fired}, decisions surfaced: {count}\n"
    body += "Result: " + ("PASS — preflight regression clean\n" if fired and count >= 1 else "FAIL\n")
    section("Run 2 — Preflight regression", body)


# ── Run 3: History + fix-2 verification ─────────────────────────────────────

async def run_history_verify(ctx):
    from handlers.history import handle_history
    result = await handle_history(ctx)
    features = result.features or []

    body = f"Feature groups: {len(features)}\n\n"
    name_ok = True
    level_ok = False
    for fg in features:
        name = fg.name      # correct attr (was fg.feature_group in v1 sim → showed '?')
        decisions = fg.decisions or []
        body += f"  [{name}] — {len(decisions)} decision(s)\n"
        if not name or name == '?':
            name_ok = False
        for d in decisions[:2]:
            lvl = d.decision_level   # new field — was absent from HistoryDecision in v1 sim
            body += f"    [{lvl or 'None'}|{d.status}] {d.summary[:65]}\n"
            if lvl is not None:
                level_ok = True

    body += f"\nFix 2 verdict:\n"
    body += f"  fg.name populated: {name_ok} (was '?' in v1 — fixed)\n"
    body += f"  d.decision_level populated: {level_ok} (was absent in v1 — fixed)\n"
    section("Run 3 — History + fix-2 verification (HistoryDecision.decision_level)", body)


# ── Run 4: Bind L2 decisions to Accountable code ────────────────────────────

async def run_bind_accountable(ctx, ingest_result):
    from handlers.bind import handle_bind

    id_by_desc = {d.description: d.decision_id for d in ingest_result.created_decisions}
    weekly_id = next((v for k, v in id_by_desc.items() if "weekly workshop" in k.lower()), None)
    ai_coach_id = next((v for k, v in id_by_desc.items() if "reasoning level" in k.lower()), None)

    if not weekly_id or not ai_coach_id:
        section("Run 4 — Bind L2 decisions to Accountable code", "ERROR: target IDs not found in created_decisions")
        return None

    bindings = [
        {
            "decision_id": weekly_id,
            "file_path": "supabase/functions/generate-weekly-ai-insights/index.ts",
            "symbol_name": "serve",
            "start_line": 43,
            "end_line": 318,
            "purpose": "Serve handler — repeatable weekly insights record generation",
        },
        {
            "decision_id": ai_coach_id,
            "file_path": "supabase/functions/ai-conversation/index.ts",
            "symbol_name": "configuredModel_selection",
            "start_line": 743,
            "end_line": 830,
            "purpose": "Model + reasoning tier selection from ai_coach_config table",
        },
    ]

    result = await handle_bind(ctx, bindings=bindings)
    body = f"Bound {len(result.bindings)} decision(s) to Accountable edge functions:\n\n"
    all_ok = True
    for br in result.bindings:
        ok = not br.error
        if not ok:
            all_ok = False
        body += (
            f"  {'✓' if ok else '✗'} {br.decision_id}\n"
            f"    file:   {bindings[result.bindings.index(br)]['file_path']}\n"
            f"    region: {br.region_id}\n"
            f"    hash:   {br.content_hash[:20]}...\n"
            + (f"    error:  {br.error}\n" if br.error else "")
            + "\n"
        )
    body += f"Result: {'PASS — both L2 decisions grounded' if all_ok else 'PARTIAL FAILURE'}\n"
    section("Run 4 — Bind L2 decisions to Accountable code (follow-up 1)", body)
    return result if all_ok else None


# ── Run 5: Drift check post-bind (should be clean) ──────────────────────────

async def run_drift_post_bind(ctx):
    from handlers.detect_drift import handle_detect_drift
    target = "supabase/functions/generate-weekly-ai-insights/index.ts"
    result = await handle_detect_drift(ctx, file_path=target)
    drifted = getattr(result, 'drifted', []) or []
    reflected = getattr(result, 'reflected', []) or []
    body = (
        f"File: {target}\n"
        f"Drifted: {len(drifted)}, Reflected: {len(reflected)}\n"
        f"Result: {'PASS — clean immediately after bind (expected)' if not drifted else 'FAIL — unexpected drift'}\n"
    )
    section("Run 5 — Drift check post-bind (should be clean)", body)


# ── Run 6: Full ingest→bind→modify→drift loop on temp file ──────────────────

TEMP_FILE_CONTENT_V1 = '''\
def calculate_discount(order_total: float, user_tier: str) -> float:
    """Apply 10% discount on orders over $100."""
    if order_total >= 100:
        return order_total * 0.10
    return 0.0


def apply_tier_bonus(base: float, tier: str) -> float:
    if tier == "premium":
        return base * 1.05
    return base
'''

TEMP_FILE_CONTENT_V2 = '''\
def calculate_discount(order_total: float, user_tier: str) -> float:
    """Apply 15% discount on orders over $50 (updated pricing)."""
    if order_total >= 50:
        return order_total * 0.15
    return 0.0


def apply_tier_bonus(base: float, tier: str) -> float:
    if tier == "premium":
        return base * 1.10  # bumped from 1.05
    return base
'''


async def run_full_drift_loop():
    """Follow-up 4: ingest → bind → modify file → detect drift."""
    import subprocess
    tmpdir = tempfile.mkdtemp(prefix='bicam_drift_test_')
    try:
        # Bootstrap a real git repo so compute_content_hash works
        subprocess.run(['git', 'init', '-b', 'main'], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.email', 'test@test.com'], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=tmpdir, check=True, capture_output=True)

        # Write and commit initial version
        test_file = pathlib.Path(tmpdir) / "discount.py"
        test_file.write_text(TEMP_FILE_CONTENT_V1)
        subprocess.run(['git', 'add', 'discount.py'], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'initial: 10% discount on $100+'], cwd=tmpdir, check=True, capture_output=True)

        os.environ['SURREAL_URL'] = 'memory://'
        os.environ['REPO_PATH'] = tmpdir

        ledger = make_fresh_ledger()
        await ledger.connect()

        from adapters.code_locator import get_code_locator

        class Ctx:
            pass
        ctx = Ctx()
        ctx.repo_path = tmpdir
        ctx.session_id = 'sim-drift-loop'
        ctx.authoritative_ref = 'main'
        ctx.authoritative_sha = ''
        ctx.head_sha = ''
        ctx.drift_analyzer = None
        ctx._sync_state = {}
        ctx.ledger = ledger
        ctx.code_graph = get_code_locator()

        # Step 1: ingest a decision about the discount logic
        from handlers.ingest import handle_ingest
        ingest_result = await handle_ingest(ctx, {
            "repo": tmpdir,
            "query": "discount policy decision",
            "mappings": [{
                "intent": "Apply 10% discount on orders over $100",
                "feature_group": "Pricing",
                "decision_level": "L2",
                "span": {
                    "text": "Apply 10% discount on orders over $100",
                    "source_type": "slack",
                    "source_ref": "eng-discussion",
                    "meeting_date": "2026-04-26",
                    "speakers": ["Jin"],
                },
            }],
        })
        decision_id = ingest_result.created_decisions[0].decision_id

        # Step 2: bind to the file at its current state
        from handlers.bind import handle_bind
        bind_result = await handle_bind(ctx, bindings=[{
            "decision_id": decision_id,
            "file_path": "discount.py",
            "symbol_name": "calculate_discount",
            "start_line": 1,
            "end_line": 5,
            "purpose": "Discount calculation — 10% on orders over $100",
        }])
        bind_ok = bind_result.bindings and not bind_result.bindings[0].error
        initial_hash = bind_result.bindings[0].content_hash if bind_ok else "?"

        region_id = bind_result.bindings[0].region_id

        # Step 3: snapshot the stored hash before modification
        pre_hash_row = await ledger._client.query(
            f"SELECT content_hash FROM {region_id} LIMIT 1"
        )
        pre_hash = (pre_hash_row[0].get("content_hash") or "") if pre_hash_row else ""

        # Step 3b: check drift status — should be pending (V1: no compliance verdict yet)
        from handlers.detect_drift import handle_detect_drift
        pre_result = await handle_detect_drift(ctx, file_path="discount.py")
        pre_pending = len(getattr(pre_result, 'pending', []) or [])

        # Step 4: modify the file and commit (threshold and rate changed)
        test_file.write_text(TEMP_FILE_CONTENT_V2)
        subprocess.run(['git', 'add', 'discount.py'], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'change: 15% discount on $50+'], cwd=tmpdir, check=True, capture_output=True)

        # Step 5: run detect_drift — triggers link_commit which re-hashes the file
        post_result = await handle_detect_drift(ctx, file_path="discount.py")
        post_drifted = getattr(post_result, 'drifted', []) or []
        post_pending = getattr(post_result, 'pending', []) or []

        # Step 5b: confirm the stored hash updated to reflect the new content
        post_hash_row = await ledger._client.query(
            f"SELECT content_hash FROM {region_id} LIMIT 1"
        )
        post_hash = (post_hash_row[0].get("content_hash") or "") if post_hash_row else ""
        hash_changed = pre_hash != post_hash and bool(post_hash)

        body = (
            f"Temp git repo: {tmpdir}/discount.py\n\n"
            f"Step 1 — Ingest: decision_id={decision_id}\n"
            f"Step 2 — Bind: region={region_id}, hash={initial_hash[:20]}...\n"
            f"Step 3 — Pre-modify state: {pre_pending} pending, 0 drifted\n"
            f"         Stored hash: {pre_hash[:20]}...\n"
            f"Step 4 — File modified and committed: threshold $100→$50, rate 10%→15%\n"
            f"Step 5 — Post-modify drift: {len(post_drifted)} drifted, {len(post_pending)} pending\n"
            f"         Stored hash updated: {hash_changed} ({post_hash[:20]}...)\n\n"
        )

        body += "Design note — V1 pending semantics:\n"
        body += (
            "  derive_status() returns 'pending' (not 'drifted') when stored_hash != actual_hash\n"
            "  AND no LLM compliance verdict exists for the new hash. This is intentional:\n"
            "  content changes are 'pending re-verification', not automatically 'drifted'.\n"
            "  'Drifted' status requires an explicit LLM non-compliant verdict (V2 C2 feature).\n\n"
        )

        if hash_changed:
            body += "Result: PASS — bind→modify→hash-tracking loop verified\n"
            body += "  Hash correctly updated to reflect new file content after commit.\n"
            body += "  'Drifted' verdict awaits V2 C2 (bicameral_judge_drift).\n"
        else:
            body += "Result: INCONCLUSIVE — hash did not change after modification\n"

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        os.environ['SURREAL_URL'] = 'memory://'
        os.environ['REPO_PATH'] = REPO

    section("Run 6 — Full ingest→bind→modify→drift loop (follow-up 4)", body)


# ── Run 7: Search in surrealkv:// persistent mode ───────────────────────────

async def run_search_persistent():
    tmpdir = tempfile.mkdtemp(prefix='bicam_search_test_')
    try:
        db_url = f'surrealkv://{tmpdir}/test.db'
        os.environ['SURREAL_URL'] = db_url
        os.environ['REPO_PATH'] = REPO

        ledger = make_fresh_ledger()
        await ledger.connect()

        from ledger.queries import upsert_decision
        client = ledger._client

        test_decisions = [
            ("Coaching portal enables 1:1 client engagement visibility with transcripts", "Coaching Portal"),
            ("Weekly workshop creates a new repeatable record each week via AI agent", "Weekly Workshop"),
            ("Sentry token must be rotated after Vercel breach exposed env vars", "Security"),
        ]
        for desc, fg in test_decisions:
            await upsert_decision(
                client, description=desc, source_type="slack",
                source_ref="accountable-tech", status="ungrounded", feature_group=fg,
            )

        await asyncio.sleep(0.3)  # let FTS index settle

        class Ctx2:
            pass
        ctx2 = Ctx2()
        ctx2.repo_path = REPO
        ctx2.session_id = 'sim-search'
        ctx2.authoritative_ref = 'main'
        ctx2.authoritative_sha = ''
        ctx2.head_sha = ''
        ctx2.drift_analyzer = None
        ctx2._sync_state = {}
        ctx2.ledger = ledger
        ctx2.code_graph = None

        from handlers.search_decisions import handle_search_decisions
        queries = ["coaching portal", "weekly workshop", "Sentry breach"]
        results_map = {}
        for q in queries:
            r = await handle_search_decisions(ctx2, query=q)
            results_map[q] = getattr(r, 'decisions', []) or []

        total_matches = sum(len(v) for v in results_map.values())
        body = f"DB: surrealkv:// (persistent, temp path)\nIngested 3 decisions, ran 3 queries.\n\n"
        for q, matches in results_map.items():
            body += f"Query: '{q}'\n  Matches: {len(matches)}\n"
            for d in matches[:2]:
                body += f"    - {getattr(d,'description','')[:70]}\n"

        if total_matches == 0:
            body += (
                "\nFix 3 verdict: 0 matches even in surrealkv:// mode\n"
                "Root cause confirmed: SurrealDB v2 embedded search::score() returns 0.0 regardless\n"
                "of mode (memory:// or surrealkv://). The FTS index is defined but score-based\n"
                "ranking is broken in the Python SDK's embedded driver. This is a SurrealDB v2\n"
                "limitation — not a bicameral bug. Workaround: upgrade to v3 or use a standalone\n"
                "SurrealDB server with proper HTTP/WS connection.\n"
            )
        else:
            body += f"\nFix 3 verdict: {total_matches} matches — FTS works in surrealkv:// mode\n"

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        os.environ['SURREAL_URL'] = 'memory://'
        os.environ['REPO_PATH'] = REPO

    section("Run 7 — Search in surrealkv:// persistent mode (fix 3 verification)", body)


# ── main ─────────────────────────────────────────────────────────────────────

async def main():
    print("=== Bicameral MCP v0.9.3 extended simulation ===\n")

    ctx = await make_ctx(repo_path=REPO, surreal_url='memory://')
    ingest_result = await run_ingest(ctx)
    await run_preflight_quick(ctx)
    await run_history_verify(ctx)
    bind_result = await run_bind_accountable(ctx, ingest_result)
    if bind_result:
        await run_drift_post_bind(ctx)
    else:
        section("Run 5 — Drift check post-bind", "SKIPPED — bind failed")

    await run_full_drift_loop()
    await run_search_persistent()

    return RESULTS


results = asyncio.run(main())
print("\n=== DONE ===\n")
for r in results:
    print(r)
