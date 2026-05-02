# Preflight Failure Scenarios

> **Purpose:** Living catalog of failure modes the v0.10.x preflight surface is built to defend against. Tracks which are handled, which are partial, which are open. Doc is project-level — not tied to any single issue or phase. New modes are added as we discover them (telemetry, review, production incidents).

## v0.10.x architecture recap

In v0.10.0, BM25 keyword retrieval was deleted from `handle_preflight` and the responsibility split across two layers:

1. **Skill layer.** The caller LLM reads `bicameral.history()` (full ledger grouped by feature) and reasons about which feature groups are relevant. *This is where vocabulary-mismatch failures live now.*
2. **Handler layer.** `bicameral.preflight(topic, file_paths)` returns only:
   - region-anchored decisions (`binds_to` lookup over `file_paths`)
   - HITL state (collision-pending, context-pending — global, no topic gate)
   - guided-mode flag (always fires when set)

Two kill switches and a dedup layer also affect what the handler returns:
- `BICAMERAL_PREFLIGHT_MUTE` env var — silences the handler for the session
- 5-minute per-session dedup, today keyed on `(topic)` only — a known coarseness; see M7

The catalog tags each row with the layer it originates at.

## Three primary axes

1. **Miss (§A)** — a relevant decision exists in the ledger but doesn't surface
2. **False fire (§B)** — preflight surfaces something the developer didn't need
3. **Cost / latency regression (§C)** — payload tokens or handler latency exceed budget; **first-class because the new architecture sends the full ledger payload on every call**, scaling linearly with ledger size. Any future optimization (semantic prefilter, lazy/two-pass history, file-path → feature-group hint) needs a baseline to regress against.

**Other axes worth tracking** — named here so they're not silently lost; expanded only when we hit one:
- Ranking quality (right decision, wrong rank — within a feature group)
- Sync / race (eval and prod diverge on timing)
- Trust / attribution (right decision, wrong metadata)
- Measurement meta (the eval itself misleads us)
- Distribution drift (works at N, fails at 10×N — partial overlap with §C)
- Ergonomic (correct but practically annoying)

**Out of scope** for preflight (separate concerns): ingest-time decision extraction, drift verdict correctness, auth / ACL / multi-user collision.

This doc is **prioritized, not exhaustive**. Telemetry (§D) is the long-term mechanism for surfacing failures we didn't anticipate.

Status legend:
- ✅ handled correctly today
- ⚪ partial — known limit, acknowledged
- 🔧 known regression risk — guard required
- ❌ open — work needed
- ⛔ out of scope, deferred

---

## A. Miss scenarios

| # | Layer | Scenario | Concrete example | Status |
|---|---|---|---|---|
| **M1** | skill | D→C vocab mismatch — caller LLM doesn't link biz-language decision to impl-language topic when reading history | Decision: *"Customers cannot be billed twice for the same order"* / Topic: `idempotent payment processing` | ⚪ depends on LLM judgment |
| **M2** | skill | C→D reverse — impl-language decision, biz-language topic | Decision: *"Use Redis SETNX with 24h TTL for dedup keys"* / Topic: `prevent duplicate customer orders` | ⚪ |
| **M3** | skill | Internal acronym / jargon | Decision: *"Audit log captures every admin action..."* / Topic: `SOC2 compliance trail` | ⚪ |
| **M4** | skill | Ungrounded decision (no `binds_to`) — only surfaces if skill judges its feature group relevant from history | Decision (status=ungrounded): *"Permission checks always run server-side"* / Topic: `permission middleware client check` | ⚪ |
| **M5** | handler | Region-anchored miss — caller didn't pass `file_paths` | Topic: `update auth config` / `file_paths=[]` — handler returns no region matches; only HITL/guided can fire | ⚪ acknowledged caller responsibility; HITL still global |
| **M6** | handler | Transitive — decision pinned to a dependency of `file_paths` | Decision pinned to `auth/jwt.py` / `file_paths=["auth/login_handler.py"]` (imports `jwt`) | ❌ region lookup only sees the direct file |
| **M7** | handler | Dedup-key coarseness — current key is `(topic)`; same topic with changed `file_paths`, new HITL state, or a fresh ledger revision is silenced | (a) Topic re-asked after a relevant decision lands; (b) topic kept stable while `file_paths` shifts to a different region; (c) HITL condition resolves mid-window | ❌ open — broaden cache key to `(topic, normalized_file_paths, ledger_revision)` and invalidate on HITL change |
| **M8** | meta | Skill skips `bicameral.history()` despite non-empty ledger (skill-step adherence drift) | Caller LLM jumps straight to `bicameral.preflight` and never reads history | ⛔ skill-conformance, not handler-eval scope |
| **M9** | meta | `BICAMERAL_PREFLIGHT_MUTE` set, developer forgot it's on | Env var carried over from prior debug session | ⛔ intentional kill switch |

---

## B. False-fire scenarios

The handler's false-fire surface shrunk dramatically in v0.10.0 — without a BM25 path, most of the old false-fire shapes (single-token overlap, common-word noise, code-paste topic) became unreachable. The remaining shapes are mostly skill-layer.

| # | Layer | Scenario | Concrete example | Status |
|---|---|---|---|---|
| **FF1** | skill | Skill drills into irrelevant feature group from history | History returns 8 features; skill flags 3 as "maybe relevant"; only 1 actually is | ⚪ LLM judgment |
| **FF2** | handler | `guided_mode=true` short-circuits — fired=true with no actionable signal | `guided_mode=true` and no region/HITL match | 🔧 documented intentional behavior, but reads as false-fire |
| **FF3** | skill | Drifted-but-irrelevant decision surfaced because it lives in a feature group skill judged relevant | Drifted decision in `payments` feature; topic *"add Stripe webhook"* — payment is the right group, but this drifted decision is about refunds | ⚪ noise, not action — measure but don't gate |
| **FF4** | handler | HITL surfaces on every code-touch call regardless of topic | Collision-pending decision in unrelated area surfaces because HITL is global | ✅ desired — HITL is intentional global signal |

---

## C. Cost / latency

The v0.10.x skill flow sends the entire ledger payload on every preflight (no BM25 prefilter). Cost scales linearly with ledger size. We measure the deterministic surface here; LLM-in-the-loop cost is in §D-phase-2.

| # | Metric | Measurement | Status |
|---|---|---|---|
| **C1** | `bicameral.history()` payload tokens | At N = 10, 100, 1000 feature groups (synthetic ledger dict, JSON-serialized) | ✅ baselined (`tests/eval/cost_baseline.jsonl`) |
| **C2** | `bicameral.preflight()` response size | Real `memory://` SurrealDB seeded with N synthetic features at N = 10, 100, 1000 | ✅ baselined |
| **C3** | Handler latency p50 / p95 | Real `memory://` SurrealDB seeded with N synthetic features at N = 10, 100, 1000 — measures handler logic + SurrealDB query time + serialization (excludes skill LLM step) | ✅ baselined |
| **C4** | End-to-end skill cycle | history + reasoning + preflight | baseline TBD (LLM-in-the-loop, phase 2) |

Asymmetric ±20% regression rule with absolute noise floors (10 tokens / 0.5ms): a PR that increases any C1/C2/C3 metric beyond floor + threshold fails the advisory phase 3 step. Improvements never alert. Re-record with `BICAMERAL_EVAL_RECORD_BASELINE=1` and commit `tests/eval/cost_baseline.jsonl` when the new value is intentional.

---

## D. Telemetry — capturing real failures (planned design)

> **Status: design only.** None of the behavior described below is shipped today. There is no `preflight_id` field in `PreflightResponse`, no telemetry writer, no engagement tagging, no triage CLI. This section is the *target shape* of the loop; every concrete step is gated behind an unchecked item in the implementation queue. Anyone reading this as the current API contract will be misled — read the queue, not the prose.

Synthetic data tests what we *think* will fail; telemetry, once built, will surface what *actually* fails.

**Planned loop:**

1. Telemetry **will be opt-in** — disabled unless `BICAMERAL_PREFLIGHT_TELEMETRY=1`. When enabled, every preflight call will append a `preflight_event` row to `~/.bicameral/preflight_events.jsonl` with a stable `preflight_id` (UUIDv4 per invocation) and the deterministic fields needed for attribution: `{ts, session_id, preflight_id, topic_hash, file_paths_hash, fired, surfaced_ids, reason}`. Raw `topic` and `file_paths` will **not be written by default** — only their salted SHA-256 hashes — to avoid leaking ticket titles, customer names, codenames, or tenant-prefixed paths. A separate `BICAMERAL_PREFLIGHT_TELEMETRY_RAW=1` flag will opt into raw capture for developers who explicitly want it locally. Retention: rotate at 30 days or 50 MB, whichever first. Zero hot-path cost (file append, no DB).
2. Downstream tool calls (`link_commit`, `bind`, `update`, `ratify`) will attribute engagement using the explicit `preflight_id` returned in the prior `bicameral.preflight()` response — **not** "most recent in session", which mis-attributes when developers fire multiple preflights or work in parallel and would systematically poison promoted dataset rows. The handler will emit `preflight_id` in its response; the skill will plumb it back on the next tool call. When the skill cannot supply `preflight_id`, attribution will fall back to scope overlap on `file_paths_hash` (flagged as `attribution=fallback` so dataset triage can downweight it). **Engaged** = appears downstream against this `preflight_id` → useful. **Ignored** = never re-referenced before the session ends → likely false fire.
3. SessionEnd will reconcile: **suspected miss** = drift detected on a decision near the developer's edits that wasn't surfaced and wasn't in a drilled-into feature group; **suspected false fire** = surfaced/drilled with zero engagement.
4. Output → `~/.bicameral/failure_review.jsonl`. Human triage labels: real miss / real false fire / borderline / not a failure.
5. Periodically promote labeled rows to `tests/eval/real_dataset.jsonl`. CI runs eval against both synthetic and real; divergence = distribution drift signal.

**Privacy stance (target).** Telemetry will be local-only (never leaves the developer's machine), opt-in via env var, hashed by default (raw via separate flag), and retention-capped. Treat `~/.bicameral/preflight_events.jsonl` as developer-private — anyone promoting rows to `tests/eval/real_dataset.jsonl` is responsible for redacting business context before the row leaves the local checkout.

---

## Implementation queue

Tick as work lands. Items are independent capabilities — order is suggestive, not enforced.

**Cost / latency baseline (§C — phase 1):**
- [x] Token-counting harness for `bicameral.history()` payloads — synthetic ledgers at N=10, 100, 1000 (`tests/eval/_synthetic_ledger.py` + `_token_count.py`, tiktoken cl100k_base)
- [x] Real-ledger seeder (`tests/eval/_seed_ledger.py`) — translates synthetic dict through `adapter.ingest_payload` so C2 + C3 measure against a real `memory://` SurrealDB
- [x] Response-size and latency benchmark for `bicameral.preflight()` — real ledger seeded at each N; p50/p95 measured on real SurrealDB query path
- [x] Baselines committed to `tests/eval/cost_baseline.jsonl` (C1/C2 platform-agnostic via `recorded_on=any` since tokens/bytes are deterministic; C3 per-platform on Darwin — Linux skip-when-missing, record on first run with `BICAMERAL_EVAL_RECORD_BASELINE=1` and commit)
- [x] Regression gate: asymmetric ±20% rule with noise floors (10 tokens / 0.5ms); advisory CI step in `.github/workflows/preflight-eval.yml` phase 3

**Handler-layer coverage (M5, M6, M7):**
- [ ] Eval rows for M5 (no `file_paths` → no region surface; HITL still global)
- [ ] Eval rows for M6 (transitive — decision pinned to dependency of `file_paths`)
- [ ] File-graph primitive in `code_locator/` (M6 fix)
- [ ] `_graph_expand_file_paths` in `handlers/preflight.py` (M6 fix)
- [ ] Eval rows for M7 — three sub-cases: (a) dedup-window swallow after fresh ledger event, (b) topic stable + `file_paths` changes, (c) topic stable + HITL state changes
- [ ] Broaden dedup cache key to `(topic, normalized_file_paths, ledger_revision)` and invalidate on HITL change (M7 fix)

**Attribution & dedup hardening:**
- [ ] Emit `preflight_id` (UUIDv4) in `PreflightResponse`
- [ ] Plumb `preflight_id` through downstream tool calls (`link_commit`, `bind`, `update`, `ratify`) so engagement attribution is by explicit ID, not session-global "most recent"

**False-fire handler coverage (FF2, FF4):**
- [ ] Eval row for FF2 (guided_mode=true with no signal — assert fired=True, document expectation)
- [ ] Eval row for FF4 (HITL fires regardless of topic — assert intentional, document)

**Skill-layer eval (§A skill rows + FF1, FF3 — phase 2):**
- [x] LLM-in-the-loop eval scaffold (fixed model + prompt + temperature) — `tests/eval/_skill_judge.py` + `run_preflight_skill_eval.py`, Sonnet 4.6, temp 0, fixture replay keyed on (model, SKILL.md SHA, input SHA)
- [x] Synthetic feature-group set with vocabulary-mismatch traps — initial 3 rows: M1 (vocab mismatch), M4 (ungrounded), FF1 (irrelevant drilling). Add M2, M3, FF3 incrementally.
- [x] FF1 false-positive set — covered by FF1_irrelevant_drilling
- [ ] FF3 false-positive set (drifted-but-irrelevant)
- [ ] M2, M3 vocab traps
- [x] Cost-per-eval-run measurement — fixture-replay keeps CI cost at ~$0 unless SKILL.md or dataset changes; cache miss on a 3-row dataset = ~$0.05 with Sonnet 4.6

**Telemetry capture (§D):**
- [ ] Telemetry opt-in via `BICAMERAL_PREFLIGHT_TELEMETRY=1` (default off)
- [ ] Default-redacted capture — salted SHA-256 of `topic` and `file_paths`; raw mode only via `BICAMERAL_PREFLIGHT_TELEMETRY_RAW=1`
- [ ] Retention policy — rotate `preflight_events.jsonl` at 30 days or 50 MB
- [ ] `preflight_event` writer (~50 LOC, append-only JSONL) — captures `preflight_id`, hashed topic/paths, plus skill-judged feature groups when available
- [ ] Downstream-tool engagement tagging by `preflight_id` (with `file_paths_hash` overlap as fallback, flagged `attribution=fallback`)
- [ ] SessionEnd reconciliation
- [ ] Triage CLI for `failure_review.jsonl` — surfaces hashed events; raw mode toggles raw display
- [ ] Redaction checklist before any row is promoted to `tests/eval/real_dataset.jsonl`
- [ ] CI dual-eval (synthetic + real, divergence reported)

**Other axes (deferred until first incident under each):**
- [ ] Ranking quality within a feature group — MRR / NDCG via `ranx`
- [ ] Sync / race — telemetry alert when `sync_metrics.sync_catchup_ms` exceeds threshold
- [ ] Distribution drift — beyond what §C covers
