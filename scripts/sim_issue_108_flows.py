"""
sim_issue_108_flows.py — End-to-end validation of BicameralAI/bicameral#108 spec flows.

Tests each of the 6 canonical flows from the spec doc against the live
bicameral-mcp implementation:

  Flow 1  — Record decisions from a meeting (ingest → ratify; collision/context_for surfacing)
  Flow 2  — Begin to write code (preflight)
  Flow 3  — Commit code → compliance verdict → "reflected"  (incl. out-of-session committer case)
  Flow 3a — Feature branch nuance (ephemeral bind)
  Flow 4  — End a coding session  (server-side: source="agent_session" ingest)
  Flow 5  — Review what's been tracked  (history axes)

Each flow asserts the spec invariants and reports PASS/FAIL.

Run:  python scripts/sim_issue_108_flows.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, "/Users/jinhongkuan/github/bicameral/pilot/mcp")

os.environ.setdefault("SURREAL_URL", "memory://")

RESULTS: list[tuple[str, str, str]] = []  # (flow_id, verdict, body)


def section(flow_id: str, verdict: str, body: str) -> None:
    RESULTS.append((flow_id, verdict, body.rstrip()))
    line = body.splitlines()[0] if body else ""
    print(f"[{flow_id}] {verdict} — {line[:100]}")


def make_fresh_ledger():
    import importlib

    import adapters.ledger as _al

    importlib.reload(_al)
    return _al.get_ledger()


async def make_temp_ctx(repo_path: str, session_id: str = "sim-issue-108"):
    from adapters.code_locator import get_code_locator

    os.environ["REPO_PATH"] = repo_path
    ledger = make_fresh_ledger()
    await ledger.connect()

    class Ctx:
        pass

    ctx = Ctx()
    ctx.repo_path = repo_path
    ctx.session_id = session_id
    ctx.authoritative_ref = "main"
    ctx.authoritative_sha = ""
    ctx.head_sha = ""
    ctx.drift_analyzer = None
    ctx._sync_state = {}
    ctx.ledger = ledger
    ctx.code_graph = get_code_locator()
    return ctx


def init_temp_git(prefix: str) -> str:
    tmpdir = tempfile.mkdtemp(prefix=prefix)
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=tmpdir, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "sim@sim.com"],
        cwd=tmpdir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Sim"],
        cwd=tmpdir,
        check=True,
        capture_output=True,
    )
    return tmpdir


def commit_file(repo: str, relpath: str, content: str, message: str) -> None:
    p = pathlib.Path(repo) / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True
    )


# ── Flow 1: Record decisions from a meeting ────────────────────────────


async def flow_1_record_decisions() -> None:
    """
    Flow 1 invariants per spec:
      - ingest returns context_for_candidates (NOT supersession_candidates)
      - new decisions land at signoff.state='proposed', status='ungrounded'
      - ratify transitions signoff.state proposed → ratified
      - unratified decisions stay status='ungrounded' regardless of compliance
    """
    tmpdir = init_temp_git("bicam_flow1_")
    commit_file(tmpdir, "stub.py", "def stub(): pass\n", "init")

    try:
        ctx = await make_temp_ctx(tmpdir, "sim-flow1")

        from handlers.ingest import handle_ingest
        from handlers.ratify import handle_ratify
        from ledger.queries import project_decision_status

        ingest_result = await handle_ingest(
            ctx,
            {
                "repo": tmpdir,
                "query": "auth policy decision",
                "mappings": [
                    {
                        "intent": "All API endpoints must reject unauthenticated requests with HTTP 401",
                        "feature_group": "Auth",
                        "decision_level": "L2",
                        "span": {
                            "text": "All API endpoints must reject unauthenticated requests with HTTP 401",
                            "source_type": "slack",
                            "source_ref": "eng-channel",
                            "meeting_date": "2026-04-30",
                            "speakers": ["Jin"],
                        },
                    }
                ],
            },
        )

        # Invariant 1: IngestResponse should NOT have supersession_candidates field
        # (this was the spec drift we corrected)
        has_supersession = hasattr(ingest_result, "supersession_candidates")
        # Invariant 2: should have context_for_candidates field
        has_context_for = hasattr(ingest_result, "context_for_candidates")

        decision_id = ingest_result.created_decisions[0].decision_id

        # Read raw signoff to verify state
        inner = getattr(ctx.ledger, "_inner", ctx.ledger)
        raw_rows = await inner._client.query(
            f"SELECT signoff FROM {decision_id} LIMIT 1"
        )
        raw_signoff = (raw_rows[0].get("signoff") or {}) if raw_rows else {}
        signoff_state_post_ingest = raw_signoff.get("state", "?")
        status_post_ingest = await project_decision_status(inner._client, decision_id)

        # Ratify
        rat = await handle_ratify(ctx, decision_id=decision_id, signer="sim-flow1")
        signoff_state_post_ratify = rat.signoff.get("state", "?")
        status_post_ratify = await project_decision_status(inner._client, decision_id)

        passed = (
            not has_supersession
            and has_context_for
            and signoff_state_post_ingest == "proposed"
            and status_post_ingest == "ungrounded"
            and signoff_state_post_ratify == "ratified"
            and status_post_ratify
            == "ungrounded"  # still ungrounded — bind not yet called
        )

        body = (
            f"Spec invariant — IngestResponse.supersession_candidates absent: "
            f"{not has_supersession}  (expected True per #108 corrected spec)\n"
            f"Spec invariant — IngestResponse.context_for_candidates present: "
            f"{has_context_for}  (expected True)\n"
            f"\nDecision lifecycle:\n"
            f"  decision_id:                   {decision_id}\n"
            f"  status post-ingest:            {status_post_ingest}  (expected: ungrounded)\n"
            f"  signoff.state post-ingest:     {signoff_state_post_ingest}  (expected: proposed)\n"
            f"  signoff.state post-ratify:     {signoff_state_post_ratify}  (expected: ratified)\n"
            f"  status post-ratify (no bind):  {status_post_ratify}  (expected: ungrounded)\n"
            f"\nKey invariant from spec: unratified decisions stay status='ungrounded' regardless\n"
            f"of any compliance verdicts. Ratification is the gate to drift tracking — but the\n"
            f"ledger doesn't downgrade ratified-but-unbound decisions; status stays ungrounded.\n"
        )
        section("Flow 1", "PASS" if passed else "FAIL", body)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Flow 2: Begin to write code (preflight) ──────────────────────────


async def flow_2_preflight() -> None:
    """
    Flow 2 — current preflight contract (post-#108 spec text):

    The #108 spec text says preflight does "BM25 search on the topic". The
    implementation comment at handlers/preflight.py:378-379 disagrees:
      "Topic-based keyword search is intentionally removed; the skill reads
       bicameral.history() directly and uses LLM reasoning to identify
       relevant feature groups."

    Current preflight surface:
      - Region-anchored lookup via caller-supplied file_paths (high precision)
      - Topic-independent HITL annotations: unresolved_collisions, context_pending_ready
      - The `topic` parameter is echoed back and used for dedup; does NOT drive matching.

    Test the actual current contract:
      - bind a decision to a file
      - preflight(topic=..., file_paths=[that file]) → region match surfaces decision
      - response carries unresolved_collisions (HITL surface)
    """
    tmpdir = init_temp_git("bicam_flow2_")
    commit_file(tmpdir, "auth.py", "def require_auth():\n    pass\n", "init")

    try:
        ctx = await make_temp_ctx(tmpdir, "sim-flow2")

        from handlers.bind import handle_bind
        from handlers.ingest import handle_ingest
        from handlers.preflight import handle_preflight
        from handlers.ratify import handle_ratify

        ingest_r = await handle_ingest(
            ctx,
            {
                "repo": tmpdir,
                "query": "auth gate decision",
                "mappings": [
                    {
                        "intent": "All API endpoints must reject unauthenticated requests with HTTP 401",
                        "feature_group": "Auth",
                        "decision_level": "L2",
                        "span": {
                            "text": "All API endpoints reject unauthenticated requests with HTTP 401",
                            "source_type": "slack",
                            "source_ref": "eng-channel",
                            "meeting_date": "2026-04-30",
                            "speakers": ["Jin"],
                        },
                    }
                ],
            },
        )
        decision_id = ingest_r.created_decisions[0].decision_id
        await handle_ratify(ctx, decision_id=decision_id, signer="sim-flow2")
        await handle_bind(
            ctx,
            bindings=[
                {
                    "decision_id": decision_id,
                    "file_path": "auth.py",
                    "symbol_name": "require_auth",
                    "start_line": 1,
                    "end_line": 2,
                    "purpose": "Auth gate",
                }
            ],
        )

        # Preflight with file_paths — region-anchored lookup is the actual matching path.
        r = await handle_preflight(ctx, topic="auth", file_paths=["auth.py"])
        fired = getattr(r, "fired", False)
        decisions = getattr(r, "decisions", []) or []
        sources_chained = getattr(r, "sources_chained", []) or []
        has_unresolved_collisions_field = hasattr(r, "unresolved_collisions")
        unresolved_collisions = getattr(r, "unresolved_collisions", []) or []

        region_match_present = "region" in sources_chained or len(decisions) >= 1

        passed = region_match_present and has_unresolved_collisions_field

        body = (
            f"Region-anchored preflight (current contract):\n"
            f"  topic:                              'auth' (echoed; does NOT drive matching)\n"
            f"  file_paths:                         ['auth.py']  (the actual match input)\n"
            f"  fired:                              {fired}\n"
            f"  decisions surfaced:                 {len(decisions)}  (region-bound decisions)\n"
            f"  sources_chained:                    {sources_chained}  (expected: ['region', ...])\n"
            f"  reason:                             {getattr(r, 'reason', '?')}\n"
            f"  unresolved_collisions field:        {has_unresolved_collisions_field}  (HITL surface)\n"
            f"  unresolved_collisions count:        {len(unresolved_collisions)}  (none seeded)\n"
            f"\n*** SPEC DRIFT (Flow 2 step 1) ***\n"
            f"Spec says: 'bicameral.preflight → BM25 search on the topic + divergence/gap\n"
            f"analysis + collision_pending check'.\n"
            f"Reality: topic-BM25 was intentionally removed. Per handlers/preflight.py:378-379,\n"
            f"the caller LLM reads bicameral.history() and reasons over it; preflight only\n"
            f"does region-anchored lookup (file_paths) + HITL surfacing\n"
            f"(unresolved_collisions, context_pending_ready). Spec text needs a follow-up\n"
            f"correction to match implementation.\n"
        )
        section("Flow 2", "PASS" if passed else "FAIL", body)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Flow 3: Commit → compliance verdict → "reflected" ──────────────────


async def flow_3_commit_to_reflected() -> None:
    """
    Flow 3 invariants per spec:
      - link_commit emits pending_compliance_checks list + flow_id UUID
      - resolve_compliance(verdict='compliant') transitions status pending → reflected
      - Full V1 path: ingest → ratify → bind → commit → link_commit → resolve_compliance → reflected
      - Out-of-session committer case: pending state surfaces in sync_status (drives dashboard tooltip)
    """
    tmpdir = init_temp_git("bicam_flow3_")
    commit_file(tmpdir, "auth.py", "def require_auth():\n    pass\n", "init")

    try:
        ctx = await make_temp_ctx(tmpdir, "sim-flow3")

        from handlers.bind import handle_bind
        from handlers.detect_drift import handle_detect_drift
        from handlers.ingest import handle_ingest
        from handlers.ratify import handle_ratify
        from handlers.resolve_compliance import handle_resolve_compliance
        from ledger.queries import project_decision_status

        # ingest + ratify + bind
        ingest_r = await handle_ingest(
            ctx,
            {
                "repo": tmpdir,
                "query": "auth gate",
                "mappings": [
                    {
                        "intent": "All API endpoints must reject unauthenticated requests with HTTP 401",
                        "feature_group": "Auth",
                        "decision_level": "L2",
                        "span": {
                            "text": "Reject unauthenticated requests with 401",
                            "source_type": "slack",
                            "source_ref": "eng-channel",
                            "meeting_date": "2026-04-30",
                            "speakers": ["Jin"],
                        },
                    }
                ],
            },
        )
        decision_id = ingest_r.created_decisions[0].decision_id
        await handle_ratify(ctx, decision_id=decision_id, signer="sim-flow3")

        bind_r = await handle_bind(
            ctx,
            bindings=[
                {
                    "decision_id": decision_id,
                    "file_path": "auth.py",
                    "symbol_name": "require_auth",
                    "start_line": 1,
                    "end_line": 2,
                    "purpose": "Auth gate",
                }
            ],
        )
        bind_ok = bind_r.bindings and not bind_r.bindings[0].error
        if not bind_ok:
            section(
                "Flow 3",
                "FAIL",
                f"bind failed: {bind_r.bindings[0].error if bind_r.bindings else '?'}",
            )
            return

        # Out-of-session committer simulation: modify file, commit, detect_drift
        # (no caller-LLM in the loop yet — pending_compliance_checks accumulates)
        commit_file(
            tmpdir,
            "auth.py",
            "def require_auth(request):\n    if not request.get('token'):\n        raise PermissionError('401')\n",
            "feat: implement auth gate",
        )

        drift_r = await handle_detect_drift(ctx, file_path="auth.py")
        sync_status = getattr(drift_r, "sync_status", None)
        pending_checks = getattr(sync_status, "pending_compliance_checks", []) or []
        flow_id = getattr(sync_status, "flow_id", "") or ""

        inner = getattr(ctx.ledger, "_inner", ctx.ledger)
        status_pending = await project_decision_status(inner._client, decision_id)

        # Out-of-session-committer invariant: status === 'pending' is the state that
        # drives the dashboard tooltip. Tooltip text in dashboard.html:
        #   "Pending compliance — run /bicameral-sync in your Claude Code session to resolve."
        out_of_session_state_correct = (
            status_pending == "pending" and len(pending_checks) >= 1
        )

        # Caller-LLM resolves the queue (this is what /bicameral-sync does)
        verdicts = [
            {
                "decision_id": c.decision_id,
                "region_id": c.region_id,
                "content_hash": c.content_hash,
                "verdict": "compliant",
                "confidence": "high",
                "explanation": "require_auth raises 401 for missing token — matches the decision",
            }
            for c in pending_checks
        ]
        if verdicts:
            await handle_resolve_compliance(
                ctx, phase="drift", verdicts=verdicts, flow_id=flow_id
            )

        status_after = await project_decision_status(inner._client, decision_id)

        passed = (
            out_of_session_state_correct
            and bool(flow_id)
            and status_after == "reflected"
        )

        body = (
            f"Pre-resolve (out-of-session committer state):\n"
            f"  status:                       {status_pending}  (expected: pending — drives dashboard tooltip)\n"
            f"  pending_compliance_checks:    {len(pending_checks)}  (expected: ≥1)\n"
            f"  flow_id present:              {bool(flow_id)}  (expected: True — UUID for verdict batching)\n"
            f"\nPost-/bicameral-sync resolution:\n"
            f"  verdicts written:             {len(verdicts)}\n"
            f"  status after resolve:         {status_after}  (expected: reflected)\n"
            f"\nFull V1 path verified: ingest → ratify → bind → commit → link_commit\n"
            f"→ resolve_compliance(compliant) → status='reflected'.\n"
            f"\nOut-of-session committer invariant: status='pending' surfaces in sync_status\n"
            f"and is the state the dashboard tooltip nudges users to resolve.\n"
        )
        section("Flow 3", "PASS" if passed else "FAIL", body)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Flow 3a: Feature branch ephemeral bind ─────────────────────────────


async def flow_3a_ephemeral_branch() -> None:
    """
    Flow 3a invariants per spec:
      - bind on feature branch → bind_result.content_hash == H_branch, ephemeral=True
      - link_commit on feature branch → status=reflected, ephemeral=True
      - switch to main without merging → ensure_ledger_synced fires; stale repair detects
        compliance_check.ephemeral=True; status → drifted (correct — not reflected on main)
    """
    tmpdir = init_temp_git("bicam_flow3a_")
    commit_file(tmpdir, "feat.py", "def feature():\n    return 'main'\n", "init")

    # Create feature branch
    subprocess.run(
        ["git", "checkout", "-b", "feature/x"],
        cwd=tmpdir,
        check=True,
        capture_output=True,
    )
    commit_file(
        tmpdir, "feat.py", "def feature():\n    return 'branch'\n", "feat: branch impl"
    )

    try:
        ctx = await make_temp_ctx(tmpdir, "sim-flow3a")

        from handlers.bind import handle_bind
        from handlers.detect_drift import handle_detect_drift
        from handlers.ingest import handle_ingest
        from handlers.ratify import handle_ratify
        from handlers.resolve_compliance import handle_resolve_compliance
        from ledger.queries import project_decision_status

        ingest_r = await handle_ingest(
            ctx,
            {
                "repo": tmpdir,
                "query": "feature decision",
                "mappings": [
                    {
                        "intent": "feature() returns the literal 'branch' for the new flow",
                        "feature_group": "Feature",
                        "decision_level": "L2",
                        "span": {
                            "text": "feature returns 'branch'",
                            "source_type": "slack",
                            "source_ref": "eng-channel",
                            "meeting_date": "2026-04-30",
                            "speakers": ["Jin"],
                        },
                    }
                ],
            },
        )
        did = ingest_r.created_decisions[0].decision_id
        await handle_ratify(ctx, decision_id=did, signer="sim-flow3a")

        bind_r = await handle_bind(
            ctx,
            bindings=[
                {
                    "decision_id": did,
                    "file_path": "feat.py",
                    "symbol_name": "feature",
                    "start_line": 1,
                    "end_line": 2,
                    "purpose": "Branch impl",
                }
            ],
        )
        bind_hash = bind_r.bindings[0].content_hash

        # Force fresh sync sweep: handle_bind doesn't invalidate the sync cache,
        # so we add a noop commit between bind and detect_drift (same pattern as Run 8/11).
        commit_file(
            tmpdir,
            "feat.py",
            "def feature():\n    return 'branch'\n# noop touch\n",
            "noop: trigger sync",
        )

        # detect_drift on branch → resolve compliant → status=reflected ephemeral=True
        drift_r = await handle_detect_drift(ctx, file_path="feat.py")
        sync_status = getattr(drift_r, "sync_status", None)
        # ephemeral lives on LinkCommitResponse (sync_status), NOT on BindResult.
        bind_ephemeral = getattr(sync_status, "ephemeral", False)
        pending_checks = getattr(sync_status, "pending_compliance_checks", []) or []
        flow_id = getattr(sync_status, "flow_id", "") or ""

        if pending_checks:
            verdicts = [
                {
                    "decision_id": c.decision_id,
                    "region_id": c.region_id,
                    "content_hash": c.content_hash,
                    "verdict": "compliant",
                    "confidence": "high",
                    "explanation": "feature() returns 'branch' as the decision specifies",
                }
                for c in pending_checks
            ]
            await handle_resolve_compliance(
                ctx, phase="drift", verdicts=verdicts, flow_id=flow_id
            )

        inner = getattr(ctx.ledger, "_inner", ctx.ledger)
        status_on_branch = await project_decision_status(inner._client, did)

        # Switch back to main — ensure_ledger_synced should fire on next tool call
        # and the stale repair should mark the decision drifted (since H_main != H_branch).
        subprocess.run(
            ["git", "checkout", "main"], cwd=tmpdir, check=True, capture_output=True
        )
        # Force fresh sync by invalidating any caches
        try:
            from handlers.link_commit import invalidate_sync_cache

            invalidate_sync_cache(ctx)
        except Exception:
            pass

        # Trigger stale-repair via detect_drift (which calls link_commit internally)
        await handle_detect_drift(ctx, file_path="feat.py")
        status_on_main = await project_decision_status(inner._client, did)

        passed = (
            bind_ephemeral is True
            and status_on_branch == "reflected"
            and status_on_main != "reflected"  # should be drifted (or pending) on main
        )

        body = (
            f"On feature branch:\n"
            f"  link_commit.ephemeral:        {bind_ephemeral}  (expected: True — commit not reachable from main)\n"
            f"  bind_result.content_hash:     {bind_hash[:20]}...  (H_branch)\n"
            f"  status post-resolve:          {status_on_branch}  (expected: reflected)\n"
            f"\nAfter switching to main (no merge):\n"
            f"  status:                       {status_on_main}  (expected: NOT reflected — stale repair fired)\n"
            f"\nSpec invariant: status='reflected' on a feature branch is branch-scoped.\n"
            f"It becomes 'drifted' on main until the PR merges.\n"
        )
        section("Flow 3a", "PASS" if passed else "FAIL", body)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Flow 4: End coding session (server-side: source="conversation" ingest) ──


async def flow_4_session_end_capture() -> None:
    """
    Flow 4 — session-end capture-corrections (server-side surface).

    Spec drift: the #108 spec text says `source="conversation"`, but the
    implementation's canonical source-type map (`handlers/history.py`
    `_SOURCE_TYPE_MAP`) only includes:
        transcript | slack | document | agent_session | manual
    plus the legacy aliases notion → document, implementation_choice → manual.
    "conversation" is not in the map and falls through to "manual".

    The intended semantic for "AI surfaced from a Claude Code session" is
    `agent_session` — that's the canonical value. Spec text needs a
    follow-up correction.

    Underlying invariant under test:
      - capture-corrections at session end writes uningested decisions as
        proposals, with the source-type round-tripping through history.
    """
    tmpdir = init_temp_git("bicam_flow4_")
    commit_file(tmpdir, "stub.py", "def stub(): pass\n", "init")

    try:
        ctx = await make_temp_ctx(tmpdir, "sim-flow4")

        from handlers.ingest import handle_ingest
        from ledger.queries import project_decision_status

        # Use canonical "agent_session" (the implementation value for AI-surfaced
        # decisions captured from a Claude Code session). Spec text says
        # "conversation"; this is the spec/impl drift to surface.
        ingest_r = await handle_ingest(
            ctx,
            {
                "repo": tmpdir,
                "query": "session-end capture",
                "source": "agent_session",
                "mappings": [
                    {
                        "intent": "Database connection pool size should be tuned per environment, not hardcoded",
                        "feature_group": "Infrastructure",
                        "decision_level": "L2",
                        "span": {
                            "text": "DB pool size per environment",
                            "source_type": "agent_session",
                            "source_ref": "claude-code-session-uuid-abc123",
                            "meeting_date": "2026-04-30",
                            "speakers": ["Jin", "Claude"],
                        },
                    }
                ],
            },
        )
        decision_id = ingest_r.created_decisions[0].decision_id

        inner = getattr(ctx.ledger, "_inner", ctx.ledger)
        raw_rows = await inner._client.query(
            f"SELECT signoff FROM {decision_id} LIMIT 1"
        )
        signoff_state = (
            (raw_rows[0].get("signoff") or {}).get("state", "?") if raw_rows else "?"
        )
        status = await project_decision_status(inner._client, decision_id)

        # Verify source_type round-trips (history readback is the user-facing surface)
        from handlers.history import handle_history

        hist = await handle_history(ctx)
        all_decisions = [d for fg in hist.features for d in fg.decisions]
        # HistoryDecision uses .id (not .decision_id); .sources is a list of source dicts
        target = next((d for d in all_decisions if d.id == decision_id), None)
        sources = target.sources if target else []
        # HistorySource is a Pydantic model — attribute access, not .get()
        source_types = (
            [getattr(s, "source_type", "?") for s in sources] if sources else []
        )
        source_type_round_trip = source_types[0] if source_types else "?"

        passed = (
            signoff_state == "proposed"
            and status == "ungrounded"
            and source_type_round_trip == "agent_session"
        )

        body = (
            f"Session-end capture-corrections (server-side ingest surface):\n"
            f"  decision_id:               {decision_id}\n"
            f"  signoff.state:             {signoff_state}  (expected: proposed)\n"
            f"  status:                    {status}  (expected: ungrounded)\n"
            f"  source_type round-trip:    {source_type_round_trip}  (expected: agent_session)\n"
            f"\n*** SPEC DRIFT (Flow 4 step 3) ***\n"
            f"Spec says source='conversation'. Implementation does NOT accept that as a\n"
            f"canonical source type — handlers/history.py _SOURCE_TYPE_MAP only knows\n"
            f"{{transcript, slack, document, agent_session, manual}} (+ legacy aliases\n"
            f"notion→document, implementation_choice→manual). 'conversation' falls through\n"
            f"to 'manual'. The intended canonical value for AI-surfaced session decisions\n"
            f"is 'agent_session'. Spec text needs a follow-up correction.\n"
            f"\nUnderlying invariant verified: ingest writes proposal,\n"
            f"signoff.state='proposed', status='ungrounded'. Ratification deferred.\n"
        )
        section("Flow 4", "PASS" if passed else "FAIL", body)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Flow 5: Review what's been tracked ────────────────────────────────


async def flow_5_history_axes() -> None:
    """
    Flow 5 invariants per spec:
      - bicameral.history returns full ledger dump grouped by feature
      - each decision shows BOTH status and signoff_state badges (orthogonal axes)
      - status ∈ {reflected, drifted, pending, ungrounded}
      - signoff.state ∈ {proposed, ratified, rejected, collision_pending, context_pending, superseded}
    """
    tmpdir = init_temp_git("bicam_flow5_")
    commit_file(tmpdir, "stub.py", "def stub(): pass\n", "init")

    try:
        ctx = await make_temp_ctx(tmpdir, "sim-flow5")

        from handlers.history import handle_history
        from handlers.ingest import handle_ingest
        from handlers.ratify import handle_ratify

        # Seed two decisions: one ratified, one proposed
        for i, (intent, fg) in enumerate(
            [
                ("Pricing tier discounts apply on orders over $100", "Pricing"),
                (
                    "Monthly active user metric counts unique session_id per 30 days",
                    "Metrics",
                ),
            ]
        ):
            await handle_ingest(
                ctx,
                {
                    "repo": tmpdir,
                    "query": f"seed {i}",
                    "mappings": [
                        {
                            "intent": intent,
                            "feature_group": fg,
                            "decision_level": "L2",
                            "span": {
                                "text": intent,
                                "source_type": "slack",
                                "source_ref": "eng-channel",
                                "meeting_date": "2026-04-30",
                                "speakers": ["Jin"],
                            },
                        }
                    ],
                },
            )

        hist_pre = await handle_history(ctx)
        # Ratify the first decision (HistoryDecision uses .id, not .decision_id)
        first_id = hist_pre.features[0].decisions[0].id
        await handle_ratify(ctx, decision_id=first_id, signer="sim-flow5")

        hist = await handle_history(ctx)
        all_decisions = [d for fg in hist.features for d in fg.decisions]

        valid_status = {"reflected", "drifted", "pending", "ungrounded"}
        valid_signoff = {
            "proposed",
            "ratified",
            "rejected",
            "collision_pending",
            "context_pending",
            "superseded",
        }

        all_have_status = all(d.status in valid_status for d in all_decisions)
        all_have_signoff = all(
            (d.signoff_state in valid_signoff) for d in all_decisions
        )
        feature_count = len(hist.features)

        # Verify the orthogonalization: the ratified decision should show
        # status='ungrounded' AND signoff_state='ratified' (two independent axes)
        ratified_dec = next((d for d in all_decisions if d.id == first_id), None)
        ratified_axes_correct = (
            ratified_dec is not None
            and ratified_dec.status == "ungrounded"
            and ratified_dec.signoff_state == "ratified"
        )

        passed = (
            feature_count >= 2
            and all_have_status
            and all_have_signoff
            and ratified_axes_correct
        )

        body = f"Feature groups: {feature_count}\n\n"
        for fg in hist.features:
            body += f"  [{fg.name}] — {len(fg.decisions)} decision(s)\n"
            for d in fg.decisions:
                body += f"    status={d.status}  signoff_state={d.signoff_state}  '{d.summary[:50]}'\n"

        body += (
            f"\nSpec invariant — orthogonal axes:\n"
            f"  all decisions have valid status:        {all_have_status}\n"
            f"  all decisions have valid signoff_state: {all_have_signoff}\n"
            f"  ratified+ungrounded composes correctly: {ratified_axes_correct}\n"
            f"\nThe two independent axes:\n"
            f"  status        = code-compliance: reflected | drifted | pending | ungrounded\n"
            f"  signoff.state = human-approval:  proposed | ratified | rejected | superseded |\n"
            f"                                   collision_pending | context_pending\n"
        )
        section("Flow 5", "PASS" if passed else "FAIL", body)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── main ────────────────────────────────────────────────────────────────


async def main():
    print("=== sim_issue_108_flows.py — End-to-end #108 spec validation ===\n")

    await flow_1_record_decisions()
    await flow_2_preflight()
    await flow_3_commit_to_reflected()
    await flow_3a_ephemeral_branch()
    await flow_4_session_end_capture()
    await flow_5_history_axes()


asyncio.run(main())

print("\n\n=== REPORT ===\n")
overall = "PASS" if all(v == "PASS" for _, v, _ in RESULTS) else "PARTIAL/FAIL"
for flow_id, verdict, body in RESULTS:
    print(f"\n## {flow_id} — {verdict}\n")
    print(body)
    print()

print("\n=== SUMMARY ===\n")
print(f"{'Flow':<10} {'Verdict':<8}")
print(f"{'-' * 10} {'-' * 8}")
for flow_id, verdict, _ in RESULTS:
    print(f"{flow_id:<10} {verdict:<8}")
print(f"\nOverall: {overall}")
