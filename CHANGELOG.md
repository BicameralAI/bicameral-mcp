# Changelog

All notable changes to bicameral-mcp are tracked here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.4.19 — 2026-04-16 — source-span surfacing + business-requirement scope for ingest and gap judge

Three themes this release: (1) the source_span → status surfacing fix
that started the release, (2) narrowing the gap-judge rubric to
business requirement gaps only, (3) restricting ingest to track only
implementation decisions tied to business drivers.

### Scope narrowed — business requirement gaps only

- **`handlers/gap_judge.py`** — rubric reframed. All 5 categories
  now surface **business requirement gaps** only (product, policy,
  and commitment holes a PM, founder, compliance reviewer, or
  procurement lead would need to resolve). Engineering gaps (wire
  protocols, migration scripts, Dockerfile content, CI pipelines,
  retries, race conditions, schema indices) are explicitly rejected
  in each category's prompt.
- **`infrastructure_gap` reframed** — no longer "does the code have
  a Dockerfile?" but "did the team sign off on the business
  commitments (cost center, vendor lock-in, SLA, data residency,
  scale assumption) this decision implies?" `requires_codebase_crawl`
  flipped to `False`, `canonical_paths` cleared. The rubric is now
  pure source-excerpt reasoning — no filesystem crawl step.
- **`GapRubric.version`** bumped to `v0.4.19`. Judgment prompt
  rewritten to enforce the business-only scope and to reject
  codebase-citation findings (no filesystem tools in this rubric).
- **`skills/bicameral-judge-gaps/SKILL.md`** — rewritten to match:
  each category prompt names specifically what to reject (technical
  failure modes, migration mechanics, wire protocols), plus a new
  anti-pattern entry forbidding engineering-gap findings.

### Ingest filter — business-tied decisions only

- **`skills/bicameral-ingest/SKILL.md`** (step 1) — added a
  **business-tie filter** between the HARD EXCLUDE and INCLUDE
  rules. Engineering-only and security-only decisions (retry logic,
  dependency bumps, refactor cleanup, test hygiene, CSRF/JWT
  rotation, Prometheus counters) are rejected unless the same
  source names a business driver (compliance deadline, customer
  contract, pricing/packaging commitment, SLA, regulated-data
  handling, named stakeholder-observable outcome).
- **Worked examples updated** — Example 3 now shows a compound
  sentence where every decision carries a business driver;
  Example 4 is the same shape without a driver and extracts zero;
  Example 5 demonstrates the security-hygiene rejection (key
  rotation out, GDPR redaction in).
- The filter lives in the skill (caller-LLM), not the server — the
  no-LLM-in-the-server invariant from `git-for-specs.md` is
  preserved.

### Fixed — source-span surfacing

Closes a long-standing gap where natural-format ingest populated
`source_span` rows but status output never surfaced them. Three
underlying bugs were found and fixed as one coherent patch:

### Fixed

- **`adapter.py:547` — `upsert_intent()` call dropped `meeting_date` /
  `speakers`**. The natural-ingest normalizer built a span dict with
  those fields, but the adapter only passed them to `upsert_source_span`.
  As a result the `intent` row's columns were always empty even when
  the source_span had the data.
- **`queries.py::get_all_decisions` — no `yields` JOIN**. The status
  handler's query read only `intent` columns, so `source_excerpt` was
  unreachable. Added the `<-yields<-source_span.{text, meeting_date,
  speakers}` traversal (mirrors `search_by_bm25`'s v0.4.14 pattern) and
  the empty-span / synthetic-span filter that drops `text == description`
  placeholders written by `_reground_ungrounded`.
- **`schema.py` — `speakers TYPE array` silently dropped items**.
  SurrealDB 2.x embedded drops array values when the field type omits
  an inner type; `TYPE array<string>` persists them. Applied to both
  `intent.speakers` and `source_span.speakers`. Discovered while
  sanity-checking the natural-ingest round-trip.

### Added

- **`IngestDecision.source_excerpt`** — optional raw passage per
  decision in the natural-format payload. When provided, stored as
  `source_span.text`; when omitted, `_normalize_payload` falls back to
  the decision description as a placeholder (and the downstream filter
  suppresses it from the rendered excerpt, preserving v0.4.14 search
  semantics).
- **`DecisionStatusEntry.source_excerpt` / `meeting_date` /
  `speakers`** — three new fields on the status response contract.
  Populated from the intent row first (fresh ingests) with a
  source_span fallback (legacy rows ingested before the adapter fix).

### Migration

Schema version stays at 2 — the `array<string>` change is idempotent
on empty rows (which is what all existing `speakers` values are after
the silent-drop bug). No data migration required.

## 0.4.18 — 2026-04-15 — `bicameral.doctor` + hard-remove `bicameral.drift`

Closes the drift → scan_branch → doctor sunset arc started in v0.4.17.
`bicameral.doctor` replaces `bicameral.drift` as the user-facing
"check for drift" entry point with a single composition tool that
auto-detects scope: file if the caller names a file, branch + repo-
wide ledger summary otherwise, empty if there's nothing to scan.

### Added

- **`bicameral.doctor(file_path?, base_ref?, head_ref?, use_working_tree?)`**
  — new MCP tool. No explicit `scope` argument — the handler infers
  file vs branch vs empty from what was passed in. One-stop answer
  to "what's drifted" / "what's broken" / "run a health check"
  requests.
- **`DoctorResponse`** Pydantic contract with `scope` ∈ {`file`,
  `branch`, `empty`} plus optional `file_scan` (full
  `DetectDriftResponse`), `branch_scan` (full `ScanBranchResponse`),
  `ledger_summary` (compact repo-wide status counts), and
  `action_hints`.
- **`DoctorLedgerSummary`** — five-number status summary (`total`,
  `drifted`, `pending`, `ungrounded`, `reflected`) computed from a
  single `get_all_decisions` roundtrip. Surfaced alongside the
  branch scan so the agent can frame branch drift against ledger-
  wide health ("2 of the 5 repo-wide drifts are outside this
  branch").
- **`handlers/doctor.py`** — auto-detecting composition handler.
  Delegates to `handle_detect_drift` for file scope, composes
  `handle_scan_branch` + `_build_ledger_summary` for branch scope.
  Dedup/merge action hints across sub-scans by `kind` so the agent
  sees one set, not two.
- **`skills/bicameral-doctor/SKILL.md`** — trigger phrasings, per-
  scope rendering rules, branch-vs-ledger contrast framing, explicit
  "don't fan out scan_branch as a second opinion" anti-pattern.

### Removed

- **`bicameral.drift`** — the tool is gone from `list_tools()` and
  the dispatch block. Soft-deprecated in v0.4.17, hard-removed now.
  Per-file drift is still available via `bicameral.doctor(file_path=...)`
  with byte-identical response content nested under
  `DoctorResponse.file_scan`.
- **`skills/bicameral-drift/SKILL.md`** and
  **`.claude/skills/bicameral-drift/SKILL.md`** — deleted. The
  trigger phrasings they covered now live on `bicameral-doctor`.

### Kept

- **`handlers/detect_drift.py`** stays in place as an **internal
  helper**. `handle_detect_drift` is no longer exposed as an MCP
  tool, but `handle_doctor` imports and calls it for the file-scope
  path. `raw_decisions_to_drift_entries` stays as the shared pure
  helper feeding both doctor and scan_branch.

### Tests

- `tests/test_v0418_doctor.py` — 12 tests across four layers:
  - Pure composition (hint merging dedup, empty-scope response shape)
  - Logic with stubbed sub-handlers (empty scope, branch scope with
    ledger summary, file scope delegation, non-fatal
    `get_all_decisions` failure)
  - Server-level guards (drift removed from `list_tools`,
    `handle_detect_drift` still importable, `raw_decisions_to_drift_entries`
    still importable)
  - Integration against real surreal ledger + seeded git repo
    (file scope end-to-end, branch scope end-to-end with a real
    range_diff)
- 65/65 combined regression across v0.4.18 + v0.4.17 + v0.4.16 +
  v0.4.14 + v0.4.8.

### Migration

**Breaking for callers of the raw `bicameral.drift` tool**, but
soft-landed: the tool was already marked DEPRECATED in v0.4.17's
description, so any agent reading the schema at that time saw the
notice. Callers need to switch to `bicameral.doctor(file_path=...)`
to get the same per-file behavior — the response content is
byte-identical, just nested inside `DoctorResponse.file_scan`.

**Not breaking for callers of the `bicameral-drift` skill**: the
skill is gone, but `bicameral-doctor`'s trigger phrasings fully
cover the old skill's phrasings, and the default scope for a
file-less request is now a branch sweep with ledger context —
strictly richer than the old drift behavior.

No schema changes to the ledger. Pre-v0.4.18 `IngestResponse` /
`BriefResponse` / `ScanBranchResponse` shapes are unchanged.

## 0.4.17 — 2026-04-15 — `scan_branch` + drift soft-deprecate + jargon lint

Three things land together in a small release. Motivated by a live
v0.4.16 dogfood where the agent kept recommending `bicameral.drift`
file-by-file to investigate discrepancies (fan out N calls, N turns
of LLM cost), because no tool in the set handled "what's wrong
across the whole branch" in a single call.

### Added

- **`bicameral.scan_branch(base_ref?, head_ref?, use_working_tree?)`**
  — new MCP tool (the 11th). Audits every decision that touches any
  file changed between `base_ref` (default: `BICAMERAL_AUTHORITATIVE_REF`
  or `main`) and `head_ref` (default: `HEAD`). Deduplicates decisions
  by `intent_id` across the full file set — a decision touching
  three files shows up once, not three times. Reuses the v0.4.11
  range-diff sweep primitives (`get_changed_files_in_range`,
  `_MAX_SWEEP_FILES` cap).
- **`ScanBranchResponse`** Pydantic contract with `base_ref`,
  `head_ref`, `sweep_scope` (`head_only` / `range_diff` /
  `range_truncated`), `range_size`, per-status counts, deduped
  decisions list, files_changed list, undocumented_symbols union,
  and action_hints.
- **`handlers/scan_branch.py`** — composition handler that reuses
  `get_changed_files_in_range` + `ctx.ledger.get_decisions_for_file`
  + the extracted `raw_decisions_to_drift_entries` helper. Pure
  deterministic, no LLM.
- **`handlers/action_hints.generate_hints_for_scan_branch`** — fires
  `review_drift` + `ground_decision` hints on the new response,
  same intensity-gated pattern as the existing generators.
- **`skills/bicameral-scan-branch/SKILL.md`** — trigger phrasings
  ("what's drifted on this branch", "scan my PR", "review this
  branch"), rendering rules (drifted first, verbatim source
  excerpts, hints verbatim), and an explicit "don't fan out
  parallel drift calls" anti-pattern.
- **`ledger.status.resolve_ref(ref, repo_path)`** — generic git
  ref resolver (the existing `resolve_head` now delegates to it).
  Handles branch names, tags, short SHAs.

### Deprecated

- **`bicameral.drift` — soft-deprecated in v0.4.17**. The tool
  still works unchanged; the wire contract is preserved. The tool
  description carries a `DEPRECATED` prefix and the
  `bicameral-drift` SKILL.md is rewritten to:
  1. Route multi-file intents to `bicameral.scan_branch`
  2. Only fire on explicit single-file phrasings
  3. Explicitly forbid fan-out loops of drift calls as a
     multi-file workaround
  Hard removal planned for v0.4.18 alongside `bicameral.doctor`.

### Fixed

- **Backend jargon hygiene lint** — new
  `tests/test_v0417_jargon_hygiene.py` blocks `BM25`, `tree-sitter`,
  `SurrealDB`, `RRF`, `Jaccard`, `graph-fusion`, `canonical_id`,
  `UUIDv5`, `JCS`, `@0@`, `Pydantic` from appearing in any
  `SKILL.md` file or any `Tool(description=...)` block in
  `server.py`. Caught three real leftover leaks in the
  `.claude/skills/` mirror tree that hadn't been re-synced after
  the v0.4.16 cleanup:
  - `.claude/skills/bicameral-ingest/SKILL.md` — "BM25 +
    graph-fusion mapping" still present
  - `.claude/skills/bicameral-reset/SKILL.md` — "SurrealDB
    instance" still present
  All skill mirrors are now byte-identical to their canonical
  `skills/` counterparts.
- **`handlers/detect_drift.py` refactor** — extracted
  `raw_decisions_to_drift_entries(raw_decisions)` as a pure
  module-level helper. `handle_detect_drift` and the new
  `handle_scan_branch` both call it, so a per-decision field drift
  between the two is now impossible by construction.

### Tests

- `tests/test_v0417_scan_branch.py` — 13 tests: honest empty path,
  head-only fallback when base is unreachable, multi-file dedup by
  intent_id, status counts match entries, `review_drift` hint fires,
  `ground_decision` hint fires, range-truncated at `_MAX_SWEEP_FILES`,
  default base ref env var resolution, working-tree flag threads
  through, pure helper regression, same-ref integration empty case,
  full end-to-end single-file range, `handle_detect_drift` regression
  after the helper extraction.
- `tests/test_v0417_jargon_hygiene.py` — 4 tests: skill file scan,
  tool description scan, synthetic-jargon smoke test (guards
  against no-op regexes), legitimate vocabulary smoke test (guards
  against over-eager regexes).
- **53/53 combined regression** across v0.4.17 + v0.4.16 test sets.

### Migration

No schema changes. `bicameral.drift` is fully backward compatible —
pre-v0.4.17 callers keep working. `bicameral.scan_branch` is
additive; agents that don't know about it simply don't call it.
The jargon lint only fails on NEW jargon landing in skill files or
tool descriptions — existing user-facing text is already clean.

## 0.4.16 — 2026-04-15 — Caller-Session Gap Judge + Natural-Format Fix

Two things land in this release: the new v0.4.16 gap-judge rubric
(caller-session LLM, server never reasons) and a load-bearing fix to
the natural-format ingest path that was silently dropping decisions
during a live dogfood of the demo gallery.

### Added

- **`bicameral.judge_gaps(topic)`** — new MCP tool (the 10th). Returns
  a `GapJudgmentPayload` containing decisions in scope, source
  excerpts, cross-symbol related decision ids, phrasing-based gaps,
  a 5-category rubric, and a natural-language judgment prompt. The
  **caller's Claude session** applies the rubric — the server never
  calls an LLM, never holds an API key. Preserves the
  `no-LLM-in-the-server` invariant from `git-for-specs.md`.
- **5-category rubric, fixed order** (picked from the Timesink
  Standard for wow × safety — all "absence of" detections, low
  hallucination risk):
  1. `missing_acceptance_criteria` (`bullet_list`)
  2. `underdefined_edge_cases` (`happy_sad_table`) — the "sad path
     never specified" category; load-bearing for the public demo
     gallery Flow 01 promise
  3. `infrastructure_gap` (`checklist`, **requires codebase crawl**)
     — the agent uses its own Glob/Read/Grep tools against the
     category's `canonical_paths` (`.github/workflows/`, `Dockerfile`,
     `docker-compose.yml`, `terraform/`, `k8s/`, `.env.example`,
     `infra/`, `deploy/`) to verify implied infra
  4. `underspecified_integration` (`dependency_radar`)
  5. `missing_data_requirements` (`checklist`)
- **`IngestResponse.judgment_payload`** — always populated when the
  ingest → brief auto-chain fires and the brief has at least one
  decision. Standalone `bicameral.brief` calls never carry it.
- **`handlers/gap_judge.py`** — pure context-pack builder. Reuses
  `handle_search_decisions` for retrieval, groups matches by
  `(symbol, file_path)` to populate `related_decision_ids`, reuses
  `_extract_gaps` to forward phrasing-based gaps as pre-cited
  evidence.
- **`skills/bicameral-judge-gaps/SKILL.md`** — caller-session rubric
  application contract. Tells the agent to reason over the pack in
  its own LLM context, render one section per category in rubric
  order, cite every finding, and surface verbatim.
- **Pre-ingest boundary detection** in `skills/bicameral-ingest/SKILL.md`
  (step 0). When the input is oversize (≥2000 tokens, ≥3 H1
  headings, ≥5 speaker turns, or ≥3 topical themes), the skill
  instructs the agent to propose a segmentation preview, wait for
  user confirmation (edit / merge / rename / skip), fan out
  `bicameral.ingest` per segment, and roll up a single aggregate
  summary at the end. Structural signals first (markdown headings,
  speaker turns, timestamp clusters); semantic clustering only as
  fallback. Entirely skill-side — no server changes.
- **Post-brief judge-gaps chain** in `skills/bicameral-ingest/SKILL.md`
  (step 6). When the response carries a `judgment_payload`, the
  ingest skill delegates rubric rendering to `bicameral-judge-gaps`.

### Fixed

- **`handlers/ingest._normalize_payload` — natural-format field-name
  drift.** The SKILL.md example documented
  `decisions: [{ text: "..." }]` while the handler only read
  `d.description or d.title`. Pydantic silently dropped the unknown
  `text` field, every decision evaporated, and the only output was
  `action_items` whose `text` field was likewise dropped — producing
  `[Action: <owner>] ` phantom prefixes that BM25-grounded against
  any symbol containing "Action" in its name (live dogfood: matched
  to unrelated `use-toast.ts` Action enums). Fix: added `text` as a
  tolerant alias on both `IngestDecision` and `IngestActionItem`,
  updated `_normalize_payload` to fall through to the alias, added
  an empty-drop guard so action items with no body never produce a
  phantom prefix.
- **`skills/bicameral-ingest/SKILL.md`** — natural-format example
  rewritten to document canonical `description` / `action` fields
  with `text` explicitly called out as a tolerant alias. Removed
  the self-contradicting "do NOT invent title/description" warning
  (those are the canonical fields, not forbidden ones). Also ported
  the rich HARD EXCLUDE table + 3 worked examples from the
  historically-divergent `.claude/skills/` mirror so both trees now
  carry the same extraction guidance.
- **`skills/bicameral-search/SKILL.md`** — removed the "who decided
  it" instruction. `DecisionMatch` has no author/speaker field; the
  agent could never fulfill the promise. Replaced with explicit
  guidance to cite `source_ref` + `meeting_date` + `source_excerpt`.
- **`skills/bicameral-preflight/SKILL.md`** — `reason` enum list was
  missing `guided_mode_off`. Completed the list with per-reason gloss.
- **`skills/bicameral-brief/SKILL.md`** — claimed "six fields";
  actual `BriefResponse` has 10. Reworded as "six presentation
  buckets plus metadata". Added `judgment_payload` delegation note
  for the chained-from-ingest case.
- **`server.py` — `bicameral.ingest` tool description** now fully
  documents the natural-format field shape (canonical + alias
  fields, priority order, `query` requirement) so an agent reading
  just the tool schema gets correct field names.
- **Backend jargon leaking into user-facing tool descriptions.**
  Replaced `BM25` / `SurrealDB` / `tree-sitter` references in the
  `bicameral.search` / `bicameral.ingest` / `bicameral.reset` tool
  descriptions — and in the `search_code` / `extract_symbols`
  code-locator tools — with user-facing terminology ("match
  confidence", "ledger instance", "semantic search over the symbol
  graph", "static parsing").

### Tests

- `tests/test_v0416_gap_judge.py` — 12 tests: rubric shape + literal
  guards, `_build_context_decisions` unit test for cross-symbol
  `related_decision_ids`, honest empty path, context pack build,
  phrasing-gap forwarding, ingest chain attach, empty-brief skip,
  standalone-brief guard, non-fatal chain failure.
- `tests/test_v0416_natural_format_fields.py` — 12 tests pinning the
  dogfood fix: canonical `description` / `title` / `action` survive,
  `text` alias works, priority order (`description > title > text`),
  empty-text decisions are dropped, empty-text action_items are
  dropped (the specific guard against the `[Action: owner] ` phantom),
  the exact dogfood payload shape produces 3 real mappings, mixed
  canonical + alias in one payload, default `owner="unassigned"`.

### Migration

No schema changes. `IngestResponse` grows one optional field
(`judgment_payload`), default `None`. Pre-v0.4.16 clients that ignore
the field see no change. `BriefResponse` is entirely unchanged.
Agents following the pre-v0.4.16 SKILL.md example literally (with
`{text: "..."}`) continue to work, because the handler now accepts
`text` as an alias on both decisions and action_items. Agents using
the canonical `description` / `action` fields also continue to work.

## 0.4.14 — 2026-04-15 — Source Excerpt + Meeting Date in Read Responses

The "tie meeting context to code" value prop only worked at write time.
At read time, the brief / drift / search responses returned the
decision text and the source_ref string but stripped the raw source
passage that produced the decision — even though `source_span.text`
was sitting in the ledger waiting to be surfaced. Surfaced during
demo gallery work when the visual was forced to either invent
context or look thin.

### Added

- **`DecisionMatch.source_excerpt` + `meeting_date`** (search responses)
- **`DriftEntry.source_excerpt` + `meeting_date`** (drift responses)
- **`BriefDecision.source_excerpt` + `meeting_date`** (brief responses)
- **`search_by_bm25`** now pulls source_span.text + meeting_date via
  `<-yields<-source_span.{text, meeting_date}` reverse traversal in
  the same query — no extra DB roundtrip.
- **`get_decisions_for_file`** does a follow-up batched query against
  the matched intent IDs to backfill the same fields. Single round-trip
  regardless of how many intents touch the file.
- **Synthetic-span filter**: `_reground_ungrounded` writes
  placeholder source_spans where `text == intent.description` to
  trigger lazy grounding. Both query paths filter those out so the
  excerpt always reflects the original meeting passage, never the
  bookkeeping placeholder.

### Tests

- 4 new cases in `tests/test_v0414_source_excerpt.py`:
  - search response surfaces source_excerpt + meeting_date
  - brief response surfaces source_excerpt + meeting_date
  - drift response surfaces source_excerpt + meeting_date (via the
    follow-up batched query)
  - empty source_span text → empty source_excerpt (graceful, no
    leak from synthetic reground spans)

### Migration

No schema changes. `source_span.text` and `source_span.meeting_date`
were already stored at ingest. v0.4.14 just plumbs them through to
the read responses. Pre-v0.4.14 clients that ignore `source_excerpt`
and `meeting_date` see no change.

## 0.4.13 — 2026-04-14 — Content-Addressable Dedup (Team Mode Hardening)

Closes the team-mode dedup gap. Previously, when two developers
ingested the same source independently, the dedup key was
`(description, source_ref)` — vulnerable to whitespace, casing,
Unicode punctuation variants, and source_ref format drift (e.g.
`#payments:1726113809.330439` vs `payments-1726113809330439`). Now
the dedup key is a content-addressable `canonical_id` derived
deterministically from the canonicalized payload via JCS + UUIDv5,
so two writers producing the same logical event produce the same ID
regardless of formatting variance.

Pattern source: web research on production dedup approaches (git's
content-addressable storage, Wikidata canonical IDs, NATS subject
naming, Stripe idempotency keys derived from request body). The
pattern collapses cleanly because bicameral's deterministic-side
graph (intent ↔ symbol ↔ code_region) only needs DB-level edge
uniqueness; the ambiguous side (source ↔ intent) gets the canonical
ID treatment.

### Added

- **`ledger/canonical.py`** — pure-function module for canonical ID
  derivation:
  - `canonicalize_source_ref(source_type, raw)` — normalizes Slack /
    Notion / GitHub / transcript references to a stable form. Strips
    format separators (`#`, `-`, `.`, `:`), lowercases, extracts
    stable tokens (timestamps, page UUIDs).
  - `canonicalize_text(text)` — NFC normalization + Unicode
    punctuation variant replacement (curly quotes, em dash, ellipsis,
    nbsp) + lowercase + whitespace collapse. Closes the
    paraphrase-as-formatting gap.
  - `canonical_json_bytes(obj)` — JCS-lite (RFC 8785) serialization:
    sorted keys, no whitespace, deterministic byte output. No
    third-party dependency.
  - `canonical_intent_id(description, source_type, source_ref)` —
    composes the three steps above and computes
    `UUIDv5(BICAMERAL_NAMESPACE, jcs)`. Same input → same UUID, every
    writer.
  - `canonical_source_span_id(...)` — same shape for source_span
    nodes.
- **`intent.canonical_id` field + `idx_intent_canonical UNIQUE`**
  index in `ledger/schema.py`. New ingests stamp the canonical_id;
  the unique index rejects duplicate inserts at the DB level.
- **DB-level edge UNIQUE indexes**:
  - `idx_yields_unique` on `yields(in, out)`
  - `idx_maps_to_unique` on `maps_to(in, out)`
  - `idx_implements_unique` on `implements(in, out)`
  - `idx_depends_on_unique` on `depends_on(in, out, edge_type)` —
    different edge types between the same regions ARE legitimate
    distinct edges, so `edge_type` is part of the key.
  Pushes idempotency into the DB layer so application code doesn't
  have to remember to check.
- **Content-addressable event filenames**. `EventFileWriter.write`
  now derives a 12-char content hash from `(event_type, payload)`
  via JCS + UUIDv5 and uses it as the filename suffix:
  `{timestamp}-{content_hash}.json`. Two writers producing the same
  logical event produce the same suffix. When the event files end
  up in the same git repo on sync, git sees them as identical files
  (same path, same content) instead of a merge conflict —
  filesystem-level dedup via content addressing. Pattern from NATS
  JetStream's per-subject discard-policy via subject-as-identity.

### Changed

- **`upsert_intent`** now uses `canonical_id` as the primary dedup
  key. Falls back to legacy `(description, source_ref)` lookup +
  backfills `canonical_id` on legacy rows so pre-v0.4.13 ledgers
  upgrade transparently on first re-ingest. New ingests are stamped
  with `canonical_id` immediately.

### Tests

- 25 new cases in `tests/test_v0413_canonical_dedup.py`:
  - Source ref canonicalization (Slack 3 variants, Notion title +
    UUID, GitHub `/` vs `#`, transcript whitespace, unknown type
    fallback)
  - Text canonicalization (curly quotes, em dash, nbsp, whitespace,
    casing, NFC)
  - Canonical ID determinism (same input → same UUID, distinguishes
    real differences, valid UUID v5 string format)
  - JCS sorted-key + no-whitespace invariants
  - End-to-end: `ledger.ingest_payload` twice with whitespace +
    source_ref format variance produces 1 row, not 2
  - Different decisions on the same source produce different rows
- Full v0.4.13 regression: 211 passed.

### Migration

No breaking changes. New `intent.canonical_id` field defaults to
`""` and is populated by:
- New ingests (stamp on first call to `upsert_intent`)
- Legacy fallback path: when an old `(description, source_ref)` row
  is matched, `canonical_id` is backfilled before the update returns

The `idx_intent_canonical UNIQUE` index allows multiple `""` values
(SurrealDB treats empty strings as distinct), so legacy rows with
empty canonical_id don't conflict. As they get touched by re-ingest,
they pick up canonical IDs and start participating in the dedup
gate.

Edge UNIQUE indexes apply to NEW edges only — existing duplicate
edges from pre-v0.4.13 ingests are not deduped automatically.
Run `bicameral.reset(confirm=true)` followed by re-ingest if you
want to clean up legacy duplicates.

## 0.4.12.1 — 2026-04-14 — Team Adapter Signature Drift Hotfix

Hotfix for a class of latent regressions in `events/team_adapter.py`
that have been silently breaking team mode since v0.4.6. Surfaced
during v0.4.12 preflight dogfooding on bicameral's own repo (which
runs in team mode) — every `link_commit` call had been failing with
`TypeError: TeamWriteAdapter.ingest_commit() got an unexpected keyword
argument 'authoritative_ref'`, causing the bicameral ledger's 23
decisions to be stuck `ungrounded` because the grounding sweep never
ran.

Six releases of latent breakage. Caught by dogfooding the v0.4.12
preflight tool against a real team-mode repo for the first time.

### Fixed

- **`TeamWriteAdapter.ingest_commit`** now forwards the
  `authoritative_ref` kwarg added by v0.4.6's pollution guard. Without
  this, every team-mode `handle_link_commit` call raised TypeError.
- **`TeamWriteAdapter.ingest_payload`** now forwards the `ctx` kwarg
  added by v0.4.6's pollution fix. Without this, team-mode ingest
  raised TypeError on every call.
- **`TeamWriteAdapter.backfill_empty_hashes`** added as a pass-through.
  Used by `handle_link_commit` for the v0.4.5 self-heal sweep. Was
  silently degraded via `hasattr()` check — backfill never ran in
  team mode.
- **`TeamWriteAdapter.get_all_source_cursors`** added as a pass-through.
  Used by `handle_reset` for dry-run summaries. Would have raised
  AttributeError on first team-mode reset call.
- **`TeamWriteAdapter.wipe_all_rows`** added as a pass-through. Used
  by `handle_reset(confirm=True)`. Would have raised AttributeError on
  first team-mode confirmed reset.

### Added

- **`tests/test_v0412_1_team_adapter_drift.py`** — 28 cases that use
  `inspect.signature` to assert the wrapper's public methods accept
  the same kwargs as the inner adapter. Any future signature drift
  fails CI loudly. The exact regression pattern that broke v0.4.6
  silently for six releases is now blocked at PR time.

### Migration

No schema changes. No API surface changes. Pure wrapper hardening.
Existing team-mode users will find that `link_commit` actually runs
sweeps now, which means previously-stuck-`ungrounded` decisions will
flip to `reflected` or `drifted` based on real code state. May surface
a backlog of latent drift on first run after the upgrade — that's
expected and correct.

## 0.4.12 — 2026-04-14 — Preflight (Proactive Context Surfacing)

Adds `bicameral.preflight(topic)` — a proactive context-surfacing tool
the agent calls BEFORE implementing code. Returns prior decisions,
drifted regions, divergent decision pairs, and unresolved open
questions linked to the topic, gated by the user's `guided_mode`
setting. Closes the loop on the "tech debt accrues because developers
make tiny architectural decisions without full insight" problem —
bicameral now pushes the relevant context at the agent without
waiting for an explicit query.

### Added

- **`bicameral.preflight(topic, participants?)`** — new MCP tool. Wraps
  `bicameral.search` (and conditionally `bicameral.brief`) with a
  Python-enforced gate that decides whether to surface based on
  `ctx.guided_mode`:

  - **Normal mode** (`guided_mode=false`, default) — *less intense*.
    `fired=true` only when search matches contain **actionable signal**
    (drift, ungrounded, divergence, open question). Plain matches are
    silenced. Trust contract: surface only when there's something the
    developer actually needs to know.
  - **Guided mode** (`guided_mode=true`) — *standard*. `fired=true` on
    any matches. Surface even on plain matches; the user opted into
    the loud experience.

  The gate logic lives in `handlers/preflight.py`, not in skill
  markdown — enforced regardless of agent compliance.

- **`PreflightResponse` contract** — populated when `fired=true`,
  empty when `fired=false`. Carries `decisions`, `drift_candidates`,
  `divergences`, `open_questions`, `action_hints` (with intensity
  inherited from `guided_mode`), and `sources_chained` (which tools
  the handler called: `["search"]` or `["search", "brief"]`).

- **`bicameral-preflight` skill** at
  `pilot/mcp/skills/bicameral-preflight/SKILL.md`. Auto-fires on
  implementation verbs (`add`, `build`, `create`, `implement`,
  `modify`, `refactor`, `update`, `fix`). Has an explicit "SKIP FOR"
  list (read-only questions, doc-only edits, dependency updates,
  typo fixes) so it doesn't fire on bare maintenance prompts.
  Renders the `PreflightResponse` with a `(bicameral surfaced — ...)`
  attribution prefix when `fired=true`. **Produces zero output when
  `fired=false`** — the trust contract.

- **Per-session topic dedup** in `ctx._sync_state["preflight_topics"]`.
  Same topic preflight-checked within 5 minutes of the session is
  silently skipped (`reason="recently_checked"`). Avoids the
  "developer asks 4 follow-up questions about the same Stripe
  webhook → preflight fires 4 times" annoyance.

- **`BICAMERAL_PREFLIGHT_MUTE`** env var. One-line mute for the
  current session. Truthy values (`1 / true / yes / on`) make
  `handle_preflight` return `fired=false` with
  `reason="preflight_disabled"` for every call.

- **Brief chain** is conditional. The handler chains to
  `bicameral.brief` when search has matches AND any match is drifted
  or ungrounded — that's the cheap signal for "there's more to know."
  In guided mode, the chain fires unconditionally (because the user
  wants the full picture). When brief throws or returns empty, the
  handler falls back to search-derived decisions and continues.

### Robustness contract

- **Fail open everywhere.** Search fails → `fired=false` silently.
  Brief fails → fall back to search-only rendering. Topic invalid →
  silent skip. Dedup hit → silent skip. Empty matches → silent skip.
  Preflight is never a hard blocker on bicameral being unavailable.
- **Honest empty path.** `fired=false` means the agent produces ZERO
  output to the user about preflight. No "I checked and found
  nothing" noise.
- **Verbatim attribution.** Every cited decision in the rendered block
  carries its `source_ref` so the user can trace it.
- **Topic validation** is deterministic (≥4 chars, ≥2 non-stopword
  content tokens, not a generic catch-all). Implementation verbs
  (`implement`, `build`, etc.) are stopwords so "implement webhook"
  fails validation but "implement Stripe webhook" passes.

### Tests

- 26 cases in `tests/test_v0412_preflight.py` covering: topic
  validation, dedup hits + TTL expiry, env var mute, every fired /
  not-fired path (no_matches, no_actionable_signal, topic_too_generic,
  recently_checked, preflight_disabled, fired), normal-vs-guided mode
  interaction (Q1=B), brief chain conditional firing (Q1=B), search
  failure fail-open, brief failure fall-back.
- Full v0.4.12 regression: 189 passed.

### Migration

No schema changes. New tool `bicameral.preflight` is additive — v0.4.11
clients ignore it. The skill auto-fires only when Claude's skill matcher
picks it up; users who haven't installed the skill see no change.

## 0.4.11 — 2026-04-14 — Latent Drift Fix (Range-Diff Sweep + Distinct Counters)

Fixes a class of "invisible drift" where decisions silently went stale
because `link_commit` only swept files in HEAD's own diff. After a
gap of N commits without a bicameral invocation, drift introduced by
intermediate commits stayed hidden until someone happened to re-edit
the same files. Now `link_commit` sweeps every file touched between
the last sync cursor and HEAD, so dark-period drift surfaces on the
next call.

### Fixed

- **Latent drift via head-only sweep**. `ingest_commit` previously
  enumerated changed files via `git show <head> --name-only`, which
  only sees the head commit's own diff. Drift introduced by commits
  N+1..N+5 was invisible if the user didn't run a bicameral tool
  during that window, then ran one against commit N+5 whose own diff
  didn't re-touch the drifted files. Fix: when the sync cursor lags
  HEAD, run `git diff --name-only last_synced..HEAD` and sweep every
  file in the range. New `sweep_scope` field on `LinkCommitResponse`
  reports `head_only` (first sync, or fallback) vs `range_diff`
  (default after first sync) vs `range_truncated` (range exceeded
  cap; sweep was partial). Range cap defaults to 200 files; remainder
  catches up on next sync.
- **Inflated drift counters via per-(region, intent) counting**.
  `decisions_drifted` and `decisions_reflected` previously incremented
  once per `(region, intent)` pair that flipped — a decision with N
  regions all flipping in the same sweep counted as N. Witnessed on
  the Accountable demo where one Google Calendar decision flipped 4
  regions and the counter reported `decisions_drifted=4` while only
  1 distinct intent was actually drifted. Fix: dedupe by intent_id
  via sets; counters now report the number of distinct decisions
  whose status flipped, matching what users mentally expect from
  "how many decisions just changed status."

### Added

- **`LinkCommitResponse.sweep_scope`** —
  `Literal["head_only", "range_diff", "range_truncated"]`. Tells the
  caller whether this sweep saw HEAD-only files or the full
  last_synced..HEAD range. A "backlog sweep" after a dark period
  reports `range_diff` with a large `range_size`, so a UI can frame
  "47 decisions drifted" as "first scan after 6 weeks" instead of
  "what the hell happened today."
- **`LinkCommitResponse.range_size`** — number of files swept this
  run. Zero for the `no_changes` and `already_synced` fast paths.
- **`get_changed_files_in_range(base_sha, head_sha, repo_path)`** in
  `ledger/status.py`. Runs `git diff --name-only base..head`. Returns
  `None` (sentinel) when the diff fails (force-push, shallow clone,
  unreachable base SHA) so the caller can fall back to head-only
  scope without crashing.

### Migration

No schema changes. Existing `LinkCommitResponse` consumers see two
new optional fields with sane defaults (`sweep_scope="head_only"`,
`range_size=0`) — backward compatible. The semantic shift is in the
counter values: deployments that scraped the old per-region counts
will see smaller numbers in `decisions_drifted` /
`decisions_reflected` because the same flip is now counted once per
intent instead of once per region. The new behavior matches what the
field name implies; the old behavior was a bug.

## 0.4.10 — 2026-04-14 — Guided Mode (Always-On Hints)

Reframes v0.4.9's tester mode. **`action_hints` now fire whenever
findings exist, regardless of mode.** The flag controls **intensity**,
not existence. Also adds setup-wizard configuration so the choice is
durable across sessions.

### Renamed

- `tester_mode` → **`guided_mode`** everywhere (context field, env
  var, skill name, tests, docstrings). Reads better, matches the
  user-facing language ("guide me through this codebase").
- `BICAMERAL_TESTER_MODE` → **`BICAMERAL_GUIDED_MODE`** env var.
- `bicameral-tester` skill → **`bicameral-guided`** skill.
- `tests/test_v049_tester_mode.py` → `tests/test_v0410_guided_mode.py`.

### Changed — semantic shift

- **Hints are always-on.** Pre-v0.4.10, `action_hints` was empty
  unless tester mode was enabled. Now it's populated whenever the
  response contains findings — drifted decisions, ungrounded matches,
  divergent pairs, open questions. The `guided_mode` flag controls:
  - **`blocking: bool`** — `True` in guided mode (skill contract
    forbids writes), `False` in normal mode (advisory only).
  - **`message` tone** — imperative ("review BEFORE making changes")
    in guided mode, advisory ("heads up — N decision(s) look
    drifted") in normal mode. Two distinct message variants per hint
    kind so the user can tell at a glance.
- **Normal mode is no longer silent.** A regular search query that
  returns a drifted decision now surfaces a non-blocking hint. The
  agent should mention it to the user (one line is enough) and
  continue. This makes bicameral consistently push signal at the
  user, with intensity dialed by their setup choice.

### Added

- **Setup wizard prompt** — `bicameral setup` now asks:
  ```
    Interaction intensity:
      1. Normal  — bicameral flags discrepancies as advisory hints (default)
      2. Guided  — bicameral stops you when it detects discrepancies
    Choice [1/2]:
  ```
  The choice is written to `.bicameral/config.yaml` as `guided: true`
  or `guided: false`. Edit the file directly to change later.
- **Config file resolution.** `BicameralContext.from_env()` reads
  `.bicameral/config.yaml` for the durable `guided` flag.
  `BICAMERAL_GUIDED_MODE` env var (truthy: `1 / true / yes / on`,
  falsy: `0 / false / no / off`) is a one-off override that wins
  over the file. Unset → fall back to the file → fall back to
  `false`.
- **`bicameral-guided` SKILL.md** — full contract for both intensity
  modes. Replaces the v0.4.9 `bicameral-tester` skill.

### Migration

No schema changes. Existing v0.4.9 installs:

- `BICAMERAL_TESTER_MODE` env var no longer recognized — set
  `BICAMERAL_GUIDED_MODE` instead. (Same truthy/falsy semantics.)
- `.bicameral/config.yaml` files without a `guided:` field default to
  `false` (normal mode). Re-run `bicameral setup` to be prompted, or
  add `guided: true` / `guided: false` manually.
- Pre-v0.4.10 callers that ignored `action_hints` are unaffected —
  the field is still optional and still populates a list, just one
  that's no longer always empty.

## 0.4.9 — 2026-04-14 — Tester Mode + Search Status Fix

Phase 2 of v0.4.8. Adds an opt-in **tester mode** that makes
`bicameral.search` and `bicameral.brief` responses emit **blocking
action hints** the agent must address before any write operation.
Hints surface drifted decisions, ungrounded decisions, divergent
decision pairs, and unresolved open questions linked to the query
scope. For onboarding, demos, and skill evaluation flows where you
want bicameral to push signal at the agent instead of waiting for
the agent to ask.

### Added

- **`BicameralContext.tester_mode: bool`** — parsed from
  `BICAMERAL_TESTER_MODE` env var at context construction. Accepts
  `1 / true / yes / on` (case-insensitive). Off by default.
- **`ActionHint`** — Pydantic model in `contracts.py`. Four kinds:
  - `review_drift` — drifted decisions in the match set
  - `ground_decision` — ungrounded decisions in the match set
  - `resolve_divergence` — two non-superseded decisions contradict on
    the same symbol (brief only)
  - `answer_open_questions` — open-question-shaped gaps in scope
    (brief only)
  Each hint has `kind`, `message`, `blocking: bool`, and `refs`.
- **`SearchDecisionsResponse.action_hints` + `BriefResponse.action_hints`**
  — optional list fields. Empty when `tester_mode=False`, so regular
  mode is byte-identical to v0.4.8 except for the new empty field.
- **`handlers/action_hints.py`** — pure post-compute hint generators.
  Zero extra DB roundtrips; inspect already-computed response objects
  and emit hints derived from their contents.
- **`bicameral-tester` skill** — new top-level skill doc explaining
  when to enable tester mode, what the blocking contract is, how to
  debug hints that don't fire.
- **`bicameral-search` + `bicameral-brief` SKILL.md** — new "Tester
  Mode Contract" sections teaching the agent to address blocking hints
  before any write.

### Fixed

- **Pre-existing search status bug** — `handle_search_decisions` was
  reading `status` from `raw_regions[0]` but `code_region` rows don't
  carry a status field (it's on the intent node). Every search match
  had been silently reported as `pending` regardless of real state,
  masking drifted decisions from callers. Now reads intent-level
  `status` from the `search_by_bm25` row, which already selects it.
  This is load-bearing for Phase 2: without the fix, the
  `review_drift` hint generator couldn't fire because no match ever
  looked drifted to it. Surfaced during the Accountable drift demo
  walkthrough (see `thoughts/shared/plans/2026-04-14-accountable-drift-demo.md`).

### Migration

No schema changes. `action_hints` is an optional list defaulting to
`[]`, so v0.4.8 clients ignore it. Tester mode is off by default —
existing deployments are byte-identical in non-tester mode. The search
status bug fix is backward-compatible: reflected / drifted decisions
that were silently misreported as pending now show the correct status.
This may change downstream UI grouping ("why is this now drifted?") —
the answer is it was ALWAYS drifted, v0.4.9 just stopped hiding it.

## 0.4.8 — 2026-04-14 — Ingest → Brief Auto-Chain

`bicameral.ingest` now automatically fires `bicameral.brief` on a topic
derived from the payload and returns the brief embedded in
`IngestResponse.brief`. Callers get divergence detection, drift signal, gap
extraction, and suggested meeting questions in the same round-trip that
produced the new decisions — no second tool call required. The killer
feature: fresh-ingest contradiction alerts. If the new ingest adds a
decision that conflicts with an existing one on the same symbol, the
chained brief surfaces the divergence at the moment of creation instead
of silently.

### Added

- **`IngestResponse.brief: BriefResponse | None`** — fused response field.
  Populated when the payload has a derivable topic; `None` when it
  doesn't (payload with only action_items, empty title, empty query).
- **`_derive_brief_topic(payload)`** — picks topic via priority chain:
  `payload.query` → longest raw decision description → `payload.title`
  → empty. Reads raw `payload["decisions"][...]` to avoid the
  `[Action:]` / `[Open Question]` prefixes that `_normalize_payload`
  injects into `mapping.intent`. Word-boundary truncation at 200 chars.
- **Within-call sync dedup guard** (`handlers/link_commit.py`) —
  `_sync_cache_lookup` / `_store_sync_cache` / `invalidate_sync_cache`
  short-circuit back-to-back `handle_link_commit("HEAD")` calls within
  the same MCP invocation so auto-chains don't do N× backfill + drift
  sweeps. Caches the **full** `LinkCommitResponse` so downstream
  `sync_status` consumers see real `regions_updated` numbers, not
  synthetic zeros. Re-reads live HEAD via `git rev-parse` on every
  lookup so a mid-call commit bypasses the stale cache.
- **`bicameral-ingest` skill step 5** — teaches the agent to present
  `IngestResponse.brief` following the bicameral-brief presentation
  rules (divergences first, drift candidates, decisions, gaps,
  suggested_questions verbatim).

### Fixed

- **Pre-existing `bicameral.brief` double-sync** — `handle_brief` was
  calling `link_commit(HEAD)` twice per invocation in v0.4.6 / v0.4.7
  (once directly, once via its chained `handle_search_decisions`). The
  v0.4.8 dedup guard collapses this to one sync. Brief gets faster on
  every call, not just auto-chained ones.

### Migration

No schema changes. `IngestResponse.brief` is optional, so v0.4.7 clients
ignore it. The sync dedup is transparent — callers can't tell a dedup
hit from the original call except by the normalized
`reason="already_synced"` string (which matches the ledger's own
idempotency wording).

## 0.4.7 — 2026-04-14 — FC-3 Vocab Cache Similarity Gate

Fixes witnessed cross-contamination where the vocab cache reused an unrelated
intent's code regions — and, worse, labeled them with the original intent's
`purpose` text. Observed live on Accountable 2026-04-14: a "Stripe payment-link
fallback" decision inherited 8 bogus regions from an earlier "weekly bulletin
page" ingest because both descriptions shared incidental tokens.

### Fixed

- **FC-3a — Vocab cache BM25 cross-match.** `lookup_vocab_cache` now returns
  `(symbols, matched_query_text)`. `handle_ingest` computes Jaccard similarity
  over non-stopword 4+ char tokens and discards hits below 0.5, forcing a
  fall-through to fresh grounding via `ground_mappings`. Deterministic, no LLM
  in the critical indexing path (per `git-for-specs.md`).
- **FC-3b — Stale `purpose` field on reused regions.** `_validate_cached_regions`
  now accepts `current_description` and rewrites every returned region's
  `purpose` field so reused regions carry the *current* intent's text, not the
  cached one's.

### Migration

No manual action required. `v0.4.6 → v0.4.7` is a handler-layer fix. Existing
vocab_cache rows remain valid; the gate rejects false positives on read.

## 0.4.6 — 2026-04-14 — Adoption Floor (Trust + First Wow)

Five initiatives: FC-1 BM25 degeneracy guard, FC-2 multi-region grounding
via graph-channel fusion, authoritative-ref + pollution guards at both write
sites, `bicameral.brief` pre-meeting one-pager, and `bicameral.reset` fail-safe
valve.

### Added

- **`bicameral.brief(topic, participants?)`** — Pre-meeting one-pager generator.
  Returns decisions in scope, drift candidates, gaps, and divergences (Branch
  Problem Instance 4: non-superseded contradictory decisions on the same
  symbol). 3-5 suggested meeting questions. Heuristic-only in v0.4.6, no LLM.
  Skill at `skills/bicameral-brief/SKILL.md` enforces "divergences surface
  BEFORE decisions" rule.
- **`bicameral.reset(confirm=false)`** — Nuke-and-replay recovery path for a
  polluted ledger. Dry run by default, returns replay plan from
  `source_cursor` rows. `confirm=true` wipes every bicameral row scoped to
  the current repo via graph traversal (multi-repo isolation preserved).
  Skill at `skills/bicameral-reset/SKILL.md` enforces two-call dry-run pattern.
- **FC-2 multi-region grounding** — `_ground_single` in
  `adapters/code_locator.py` seeds the graph channel in
  `search_code(query, symbol_ids=...)` with fuzzy-validated symbol IDs,
  activating the RRF fusion layer (`code_locator/fusion/rrf.py`) that was
  previously built but unwired. Multi-file features (React component + hook
  + supabase function + migration) now ground to multiple real implementation
  files instead of collapsing to a single BM25 tiebreak winner. Pure wiring
  fix — no new infrastructure.

### Fixed

- **FC-1 BM25 degeneracy guard** — `RealCodeLocatorAdapter.ground_mappings`
  refuses to ground descriptions whose tokens reduce to fewer than 2 entries
  in the corpus vocabulary. Closes the spurious-anchor path where
  open-question intents ("GitHub Discussions vs Slack") collapsed to the
  densest-term-frequency file. New `Bm25sClient.count_corpus_tokens()` helper.
- **Silent branch pollution — Bug 1 (F1, `link_commit` write site)** —
  `ingest_commit` refuses baseline writes when the current branch name
  (via `git rev-parse --abbrev-ref HEAD`) doesn't match the authoritative
  ref. Drift is reported in memory but stored hashes, sync cursor, and intent
  status are not mutated. Branch-name comparison survives normal commits
  advancing main.
- **Silent branch pollution — Bug 3 (F1a, `ingest_payload` write site)** —
  `ingest_payload` now stamps baseline hashes against `ctx.authoritative_sha`
  instead of current HEAD. Fixes the day-1 poisoning vector where ingesting
  from a feature branch birthed a polluted ledger on a fresh install. A
  warning log line fires at ingest time when the user is on a non-authoritative
  ref.
- **Authoritative-ref auto-detection** — New helpers `detect_authoritative_ref`
  and `resolve_ref_sha` in `code_locator_runtime.py`. Resolution order:
  `BICAMERAL_AUTHORITATIVE_REF` env var → `git symbolic-ref refs/remotes/origin/HEAD`
  → fallback `"main"`. Stored on `BicameralContext` as `authoritative_ref` +
  `authoritative_sha` fields.

### Added — tests (+49 cases)

- `tests/test_fc1_bm25_degeneracy.py` — 11 cases: helper unit tests,
  5 fixture-driven degenerate queries, ground_mappings integration
- `tests/test_fc2_multi_region_grounding.py` — 4 cases: graph channel
  activation, multi-file emission, `max_symbols` cap, BM25-only fallback
- `tests/test_phase2_brief.py` — 17 cases: conflict heuristics, divergence
  detector, gap extractor, question generator, end-to-end with seeded ledger
- `tests/test_reset.py` — 4 cases: dry-run plan, actual wipe, multi-repo
  isolation, replay plan fidelity
- `tests/test_authoritative_ref.py` — 5 cases: env override, origin/HEAD
  detection, main fallback, resolve_ref_sha happy + missing
- `tests/test_pollution_bug.py` — 2 end-to-end cases with branched tmp git
  repo: ingest on branch stamps main-anchored hashes, link_commit on
  branch leaves stored baselines untouched

### Migration

No manual action required. `v0.4.5 → v0.4.6` is a handler + skill layer
release. No schema changes.

- Users on multi-branch workflows: set `BICAMERAL_AUTHORITATIVE_REF=<branch>`
  to override detection if `git symbolic-ref refs/remotes/origin/HEAD`
  doesn't return the expected branch (shallow clones, single-branch repos).
- Users whose v0.4.5 ledger got polluted by ingesting from a feature branch:
  run `bicameral.reset confirm=true` to wipe and replay from scratch.

### Known issues — will be fixed in v0.4.7

- **FC-3: Vocab cache cross-contamination + stale purpose field.** The
  vocab cache uses SurrealDB's `@0@` BM25 operator to match incoming
  descriptions against stored `query_text`, then reuses the cached symbols
  array without a similarity threshold. Two unrelated intents sharing
  incidental tokens can cross-match, and `_validate_cached_regions`
  preserves the cached region's `purpose` field (= the original intent's
  description), cross-wiring intents visually. Witnessed 2026-04-14 on
  Accountable: a Stripe payment-link fallback decision inherited 8 bogus
  regions from an earlier weekly-bulletin ingest. **Workaround:**
  `bicameral.reset confirm=true` clears vocab cache and re-ingest fresh.
  Fix planned for v0.4.7 — similarity gate on cache reuse + `purpose`
  field rewrite on hit.

### Rollback

Each feature is independently revertable via env var:
- `BICAMERAL_AUTHORITATIVE_REF=` (empty) → pollution guard disabled
- Removing `skills/bicameral-brief/` → brief skill unloaded

---

## 0.4.5 — 2026-04-14

### Fixed

- **Ingest now stamps a baseline `content_hash` at HEAD for every grounded
  region.** Previously `ingest_payload` only computed a hash when the caller
  explicitly passed `commit_hash` in the payload, which the MCP `bicameral_ingest`
  handler never did — so every bulk transcript ingest persisted empty hashes
  and decisions were permanently stuck in `pending`. Now `ingest_payload`
  resolves HEAD from `repo_path` when no commit_hash is supplied, computes a
  baseline hash for every region, and derives the intent's initial status from
  that hash. Freshly ingested decisions are born `reflected` when their
  grounded code exists at HEAD.
- **Empty-hash regions from older ledgers are now self-healed.** `handle_link_commit`
  runs a repo-scoped backfill sweep before the normal drift loop, walking any
  code regions with an empty `content_hash` and handing them to
  `HashDriftAnalyzer`, which adopts the current git state as the baseline and
  flips the owning intents to `reflected`. No forced migration, no new tooling —
  just call `bicameral_status` or any other handler that triggers a
  `link_commit` and legacy ledgers heal themselves.
- **`HashDriftAnalyzer.analyze_region` self-heals missing baselines.** When
  `stored_hash == ""` and the analyzer can compute a real hash at the requested
  ref, it returns `reflected` with the new hash as the baseline instead of the
  old `ungrounded` verdict. Used by both the new backfill path and the regular
  drift sweep.

### Added

- Per-region status aggregation on intents: an intent with multiple code
  regions now adopts the loudest status across them (drifted > reflected >
  pending > ungrounded), so a single drifted region always raises an alarm
  even if other regions still reflect.
- `SurrealDBLedgerAdapter.backfill_empty_hashes(repo_path, drift_analyzer=...)`
  — public method to run the backfill sweep on demand. Idempotent and scoped
  by repo, so multi-repo SurrealDB instances stay isolated.
- `ledger.queries.get_regions_without_hash(client, repo="")` — helper query
  used by the backfill sweep to find legacy regions.
- New test module `tests/test_phase1_l1_wiring.py` with four regression
  scenarios: ingest→reflected, edit→drifted, phantom range→not reflected,
  and legacy empty-hash backfill→reflected.

### Migration

No manual action required. Existing ledgers backfill themselves on the next
`bicameral_status`, `bicameral_link_commit`, or any other tool that drives a
`link_commit`. Users whose files aren't touched by subsequent commits can
also simply re-run their original bulk ingest — v0.4.5 stamps hashes at ingest
time, so re-ingestion produces correct status for every grounded decision
without any further work.

---

## 0.4.4 — 2026-04-13

- Submodule bump for grounding reuse + coverage loop (Phase 3 of the
  code-locator drift fix plan).

## 0.4.3 — 2026-04-12

- Few-shot `bicameral-ingest` skill update (`ff2eff7`).

## 0.4.2 — 2026-04-11

- Skills bundle + CLAUDE.md context files.

## 0.4.1 — 2026-04-10

- Ingest pipeline hardening: input contracts, payload normalization, freshness
  guards.

## 0.4.0 — 2026-04-08

- Event-sourced collaboration + BicameralContext request-scoped snapshot
  isolation.
