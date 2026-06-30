# Disposition: `claim_evaluator` persistence shape — superseded by #428

**Tracks:** #78
**Determination:** **Superseded.** Do **not** land a `claim_evaluator` table. The design question #78 exists to answer is now answered — better — by **#428 (predicate grounding)**. This doc records why, and carves out the one narrow residual #428 does not cover.
**Author:** Kevin Knapp (@Knapp-Kevin)
**Source:** `docs/spec-governance-feedback.md` §2 (Q1 deferral) · supersedes the four-shape schema sketch.

---

## TL;DR

| | |
|---|---|
| Original #78 question | How do we *persist* a way to verify an L1 claim that can't be codegenome-fingerprinted? Four candidate shapes: (a) stored-proc ID, (b) probe URL + threshold, (c) fixture path, (d) external test-suite pointer. |
| What changed | **#428** ("behavioral / cross-cutting decision grounding", opened 2026-05-27, P2/architecture/product) reframed the same problem and proposed `decision -> evaluates(predicate)` — a **code-graph predicate** evaluated over the index `code_locator` already builds. |
| Why #428 wins | White-box (uses the graph we already have) beats black-box (run an external test/probe). It is native to drift detection, and it can express **negative** assertions ("no raw SQL", "PII never to disk") that a pass/fail claim_evaluator can only weakly proxy. #428 cross-references #50 but pointedly not #78 — it occupies #78's design space. |
| Residual #428 does **not** cover | Pure **runtime** properties that no static predicate can express — latency ("emit a verdict within 200 ms"), availability, throughput. These genuinely need a probe/test. |
| Recommendation | Close #78 as superseded-by-#428, **or** re-scope it to *"runtime-claim evaluator (probe/latency residual)"* and nest it under #428. Either way: don't build the four-shape table. |
| Schema note | The original feedback estimated a v13 bump; the ladder is at **v20** today. Moot — nothing new should land here. |

---

## §1 — The two answers to the same question

Both issues answer *"how do we verify an L1 claim that isn't about a single symbol?"* They differ on **where the truth comes from:**

| | #78 `claim_evaluator` (this issue) | #428 predicate grounding (supersedes) |
|---|---|---|
| Source of verdict | **External evidence** — run a test, hit a probe, compare to a threshold. | **The code graph** — evaluate a predicate over the symbol + call/data-flow index. |
| Box | Black-box. Treats the system as opaque and observes outputs. | White-box. Inspects the structure we already index. |
| Negative assertions ("no raw SQL") | Can't express directly — only a test that *happens* to fail. | First-class: `forbid: call_site(name="execute") where ¬wrapped_by(orm.*)`. |
| Drift integration | Bolted on — needs an external runner/CI hook on a schedule. | Native — predicate re-evaluation on `link_commit`, alongside content-hash compare. |
| New infra | Test runners, probe endpoints, threshold config, sandboxing of operator code. | A small predicate DSL + an evaluator over an index that already exists. |

For the structural/static subset of L1 claims — the large majority — #428 is strictly the better design. It reuses the codegenome graph instead of standing up a parallel evaluation substrate, and it catches the violation *at the new violation site* rather than only where a proxy test happened to look.

## §2 — Why the four-shape table is the wrong artifact

The four shapes in #78 collapse on inspection:

- **(c) fixture path** and **(d) test-suite pointer** are "run code that asserts a structural property." For anything expressible over the graph, #428's predicate evaluator does this *without* shelling out to an external suite — and gives a drift signal located at the offending code, not a red CI job with no pointer.
- **(a) stored-proc / callable** is the same shape as the already-shipped ingest eval-hook (`filters/evaluator.py::run_eval_hook`, `module.path:function_name`). We don't need a *persistence schema* to know that pattern works; if a structural callable is ever wanted, it's a predicate strategy under #428, not a new table.
- **(b) probe URL + threshold** is the only shape that survives — and only for runtime properties (§3).

Landing a table that models all four would bake in exactly the over-fit the original §2 deferral warned about, *and* duplicate the `evaluates` edge #428 already needs.

## §3 — The residual (the only thing worth keeping warm)

Some L1 claims are about **runtime behaviour**, not code structure, and no static predicate can express them:

- "Emit a compliance verdict within **200 ms** of bind." (latency)
- "The bind endpoint stays available under N concurrent sessions." (availability/throughput)

These need an actual measurement — a probe or a perf test — exactly shape (b)/(d). **If and only if** a concrete runtime evaluator is ever requested:

- Model **just that slice**, not the four-shape table.
- Reuse #428's `evaluates` edge — `decision -> evaluates -> runtime_evaluator` — so there is one grounding relationship, not two.
- A `runtime_evaluator` row is then minimal: `{ strategy: "probe_url"|"test_selector", target, threshold?, comparator?, timeout_ms, on_error }`.
- Carry over the **one** design rule worth preserving from the original sketch (§4 below).

Until that concrete runtime evaluator exists, this stays a tracking note — same gate as before, now correctly scoped to the residual rather than the whole space.

## §4 — The one rule worth carrying forward: infra failure ≠ claim violation

Whatever lands (a #428 predicate evaluator *or* a future runtime probe), preserve this posture:

> A probe being down, a predicate evaluator crashing, or a test timing out means **"we don't know,"** not **"the claim is violated."** Default to `skip`/`unknown` and surface "evaluation unavailable" distinctly from "ungrounded." Only a clean, completed below-threshold / failing result flips grounding.

This mirrors the shipped ingest eval-hook ("best-effort, never raises, failures don't kill the loop") and the warn+audit-emit gating stance we already hold elsewhere. Manufacturing a violation from a transient outage trains operators to ignore the signal.

## §5 — Recommended disposition

1. **Mark #78 superseded by #428** — either close it with a pointer, or relabel to *"runtime-claim evaluator (probe/latency residual)"* and nest under #428's umbrella. Drop the `parked`/four-shape framing.
2. **Do not** add a `claim_evaluator` table to the schema ladder.
3. When #428 lands its `evaluates(predicate)` edge, note in that PR that the runtime residual (§3) reuses the same edge if it ever materializes.
4. Keep §4's failure posture in whichever evaluator ships first.

*This doc replaces the earlier four-shape schema sketch, which was written before #428 reframed the problem.*
