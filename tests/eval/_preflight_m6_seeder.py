"""Per-M6Case ledger + ctx seeder for the M6 preflight retrieval eval (#58 Phase A).

Builds a FRESH in-memory ledger per case (per signoff Q4: per-run temp-dir
+ memory://). The seeded ledger contains exactly ONE intended decision
with realistic status + binding shape so the runner's recall measurement
isn't polluted by cross-case bleed.

Why per-case freshness:
  - Preflight responses depend on full ledger state. Reusing a ledger
    across cases would mean every preflight sees every prior case's
    decisions; the recall metric loses its meaning.
  - Vocabulary mismatch cases need a clean BM25 index — neighboring
    descriptions can accidentally boost or suppress matches.
  - Unbound cases require status=ungrounded with no binds_to edge; a
    reused ledger could have stale edges from prior cases.

Three seeding paths, dispatched on ``case.miss_mode``:

  vocabulary_mismatch  → ingest decision + bind to a generic code region
                          (so the region path doesn't trivially surface it
                           when the caller passes file_paths — but the
                           caller doesn't pass file_paths in vocab cases
                           anyway; this is for shape consistency).
  unbound_decision     → ingest decision with status=ungrounded; do NOT bind.
  transitive_relevance → ingest decision + bind to intended_file_path; the
                          caller's file_paths name a DIFFERENT file that
                          imports the intended_file_path. Requires a real
                          (synthetic) code_graph with import edges.

Returns ``(ctx, intended_decision_id, preflight_response)`` so the runner
can classify the outcome.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures" / "preflight_m6"))

from dataset import M6Case  # type: ignore[import-not-found]  # noqa: E402, I001


async def seed_m6_case_into_fresh_ctx(
    case: M6Case,
) -> tuple[Any, str, Any]:
    """Seed one M6 case into a fresh ledger + ctx; return preflight response.

    Returns ``(ctx, intended_decision_id, preflight_response)``.
    Caller-runner classifies on ``intended_decision_id in response.decisions``.

    Per-case isolation: each call creates a new tempdir for REPO_PATH +
    a fresh memory:// ledger. Caller MUST NOT reuse the ctx across cases.
    """
    # Lazy imports — these pull in surrealdb + the full handler stack, so
    # we keep them out of module init so importing `dataset.py` stays
    # cheap (it's used by the renderer too, which doesn't need surrealdb).
    from adapters.code_locator import reset_code_locator_cache  # noqa: E402
    from adapters.ledger import reset_ledger_singleton  # noqa: E402
    from context import BicameralContext  # noqa: E402
    from handlers.bind import handle_bind  # noqa: E402
    from handlers.ingest import handle_ingest  # noqa: E402
    from handlers.preflight import handle_preflight  # noqa: E402

    tmpdir = tempfile.mkdtemp(prefix=f"m6_{case.case_id}_")
    repo_root = Path(tmpdir) / "repo"
    repo_root.mkdir()

    # Per-case git init — handle_bind + ensure_ledger_synced both walk HEAD.
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "m6@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "M6 Eval"], cwd=repo_root, check=True)

    # Materialize the case's files so handle_bind can resolve them at HEAD.
    # For vocab + unbound cases the file is a synthetic stub; for transitive
    # cases we materialize BOTH the intended file (where the decision binds)
    # AND a caller file that imports it.
    files_to_seed: list[tuple[str, str]] = []
    if case.miss_mode == "transitive_relevance":
        # intended file — the decision binds here
        intended_body = (
            f"# {case.intended_description[:80]}\n"
            f"def {case.intended_symbol or '_intended'}():\n"
            "    pass\n"
        )
        files_to_seed.append((case.intended_file_path, intended_body))
        # caller file — what the developer names; imports intended file
        # Compute a relative import path that the import-graph indexer can
        # follow. This is a simplified Python-style import; the real symbol
        # index parses tree-sitter and may or may not catch this — for
        # Phase A's measurement, we exercise the full path including any
        # imperfect import recognition.
        for caller_path in case.file_paths:
            module_path = case.intended_file_path.replace("/", ".").rsplit(".", 1)[0]
            caller_body = (
                f"# caller for M6 case {case.case_id}\n"
                f"from {module_path} import {case.intended_symbol or '_intended'}\n"
                "def _caller():\n"
                f"    return {case.intended_symbol or '_intended'}()\n"
            )
            files_to_seed.append((caller_path, caller_body))
    else:
        # Vocab + unbound cases: materialize a single placeholder file so
        # the synthetic-repo has at least one indexed symbol (avoids the
        # eager-init failure path from #243). Not bound to the decision
        # for unbound cases.
        files_to_seed.append(("src/placeholder.py", "# placeholder for M6 synthetic repo\npass\n"))

    for rel_path, body in files_to_seed:
        abs_path = repo_root / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(body)

    subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "m6 seed"],
        cwd=repo_root,
        check=True,
    )

    # Set env + reset singletons so BicameralContext.from_env picks up the
    # fresh path / fresh ledger.
    prev_repo = os.environ.get("REPO_PATH")
    prev_surreal = os.environ.get("SURREAL_URL")
    # #216 LLM-08 — the ingest rate limiter has burst=10 / refill=1/s by
    # default. The eval runs 25 cases back-to-back in the same process;
    # the first ~11 cases consume the burst + refills, and cases 12+
    # raise `_IngestRefused("rate_limit_exceeded")` during seeding,
    # corrupting the recall measurement (seeder errors aren't agent
    # misses, but they DO eat the cases' slots). The rate limiter is
    # for production agent-loop safety, not eval throughput. Disable for
    # this run via the documented env var (see `handlers.ingest.
    # _check_rate_limit` docstring).
    prev_ingest_rate = os.environ.get("BICAMERAL_INGEST_RATE_LIMIT_DISABLE")
    os.environ["REPO_PATH"] = str(repo_root)
    os.environ["SURREAL_URL"] = "memory://"
    os.environ["BICAMERAL_INGEST_RATE_LIMIT_DISABLE"] = "1"
    reset_ledger_singleton()
    reset_code_locator_cache()

    try:
        ctx = BicameralContext.from_env()

        # Ingest the intended decision via the real ingest path so the
        # row has realistic shape (source_type, span, status). Internal
        # format with code_regions=[] (we'll bind separately when needed).
        ingest_resp = await handle_ingest(
            ctx,
            {
                "query": case.intended_description[:120],
                "repo": f"m6-{case.case_id}",
                "mappings": [
                    {
                        "intent": case.intended_description,
                        "span": {
                            "source_type": case.source_type,
                            "text": case.intended_description,
                            "source_ref": f"m6-{case.case_id}",
                            "speakers": ["m6@example.com"],
                            "meeting_date": "2026-05-10",
                        },
                        "symbols": [],
                        "code_regions": [],
                        "signoff": {
                            "state": "ratified",
                            "signer": "m6@example.com",
                            "ratified_at": "2026-05-10T00:00:00Z",
                            "session_id": None,
                        },
                    }
                ],
            },
        )

        # Pull the freshly-created decision_id from the ingest response.
        pending = getattr(ingest_resp, "pending_grounding_decisions", None) or []
        if not pending:
            # Some ingest paths don't surface pending_grounding_decisions on
            # the response — fall back to the created_decisions field.
            created = getattr(ingest_resp, "created_decisions", None) or []
            intended_decision_id = str(created[0]["decision_id"]) if created else ""
        else:
            intended_decision_id = str(pending[0]["decision_id"])

        # Per-mode binding step.
        if case.miss_mode == "transitive_relevance" and intended_decision_id:
            # Bind to the intended file (NOT the caller's file). The
            # caller's file_paths import the intended file, so 1-hop
            # graph expansion should surface this binding.
            await handle_bind(
                ctx,
                bindings=[
                    {
                        "decision_id": intended_decision_id,
                        "file_path": case.intended_file_path,
                        "symbol_name": case.intended_symbol or "_intended",
                    }
                ],
            )
        elif case.miss_mode == "vocabulary_mismatch" and intended_decision_id:
            # Bind to a generic placeholder — caller doesn't pass file_paths
            # so the region path won't be exercised. Binding here ensures
            # status=ratified rather than ungrounded.
            await handle_bind(
                ctx,
                bindings=[
                    {
                        "decision_id": intended_decision_id,
                        "file_path": "src/placeholder.py",
                        "symbol_name": "_placeholder",
                    }
                ],
            )
        # unbound_decision: intentionally skip binding so status stays
        # ungrounded and the region path skips this decision.

        # Drive preflight.
        response = await handle_preflight(
            ctx,
            topic=case.topic,
            file_paths=list(case.file_paths) or None,
        )
        return ctx, intended_decision_id, response

    finally:
        # Restore env, drop singletons so the next case starts clean.
        if prev_repo is None:
            os.environ.pop("REPO_PATH", None)
        else:
            os.environ["REPO_PATH"] = prev_repo
        if prev_surreal is None:
            os.environ.pop("SURREAL_URL", None)
        else:
            os.environ["SURREAL_URL"] = prev_surreal
        if prev_ingest_rate is None:
            os.environ.pop("BICAMERAL_INGEST_RATE_LIMIT_DISABLE", None)
        else:
            os.environ["BICAMERAL_INGEST_RATE_LIMIT_DISABLE"] = prev_ingest_rate
        reset_ledger_singleton()
        reset_code_locator_cache()
