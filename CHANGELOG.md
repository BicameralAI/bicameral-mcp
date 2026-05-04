# Changelog

All notable changes to bicameral-mcp are tracked here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `handlers/preflight.py` — `_region_anchored_preflight` now expands caller-supplied `file_paths` by 1 hop along the code-locator graph (callers / callees / imports / inheritance) before the `binds_to` lookup. Lifts the strict exact-match recall ceiling so a decision bound to `app/src/lib/git/reorder.ts` surfaces when the caller passes the structurally-near `app/src/ui/multi-commit-operation/reorder.tsx`. Decisions reached only via expansion carry `confidence=0.7` (vs `0.9` for direct pins). `sources_chained` includes `"region_graph_expanded"` when expansion contributed at least one hit. Bounded by `code_locator/config.py::max_neighbors_per_result` to prevent hub-file explosion. Closes #173.
- `adapters/code_locator.py::RealCodeLocatorAdapter.expand_file_paths_via_graph` — public method backing the expansion. Returns `(expanded, added)` so callers can mark provenance.
- `skills/bicameral-preflight/SKILL.md` Step 2 — documents that the server expands by 1 hop, with caller-side `confidence` and `sources_chained` semantics.

### Changed

### Fixed

### Schema

### Security

## v0.18.0 -- event vocabulary extension: ratify + supersede (#97)

Extends the existing Phase 1 JSONL emitter with two new event types so the shipped vocabulary matches the v0 architecture description. Team-mode replay now restores ratify and supersede outcomes alongside the pre-existing ingest/bind/link_commit events.

### Added

- `events/team_adapter.py` -- `apply_ratify` and `apply_supersede` wrappers; emit `decision_ratified.completed` / `decision_superseded.completed` with `canonical_id` so cross-author replay can resolve to peer-local rows.
- `events/materializer.py` -- replay cases for the two new event types; warns + skips on unresolved canonical_id (out-of-order cross-author replay).
- `ledger/adapter.py` -- idempotent `apply_ratify(decision_id, signoff)` and `apply_supersede(new_id, old_id, ...)` adapter methods.
- `ledger/queries.py` -- `get_canonical_id` and `find_decision_by_canonical_id` helpers.
- `tests/test_team_event_replay.py` -- round-trip coverage for ratify, supersede (with edge replay), and an ingest regression guard.

### Changed

- `handlers/ratify.py` -- routes the actual write through `ledger.apply_ratify` instead of the inline UPDATE + project + update_status sequence. Pre-write idempotency check unchanged.
- `handlers/resolve_collision.py` -- routes the supersede branch through `ledger.apply_supersede`. Frozen-signoff merge moves into the adapter so it's reachable from replay.

### Closes

Partial close of #97 -- event vocabulary wedge. CHANGEFEED extension to `code_subject` / `subject_identity` / `binds_to` / `code_region` and the SHA256 chain remain open.

## v0.17.2 -- governance architecture documentation (#111)

New `docs/semantic-drift-governance.md` describing the shipped governance surface (Phases 1-4 from the #108-#112 plan): contracts, engine, config, HITL bypass flow, MCP tools, and the non-blocking absolute. Includes two Mermaid diagrams (lifecycle and inference-vs-determinism) and explicit cross-references to existing docs.

### Closes
#111

## v0.17.1 -- preflight HITL bypass flow (#112)

Wires the deterministic engine into the preflight HITL surface. Unresolved signoff states (proposed, ai_surfaced, needs_context, collision_pending, context_pending) trigger AskUserQuestion prompts with mandatory bypass option. Bypass writes a `preflight_prompt_bypassed` event via `preflight_telemetry.py` (V4 idempotent within 1-hour recency window) without mutating decision state. The engine reads recent bypass events and drops one tier of escalation for recently-bypassed decisions.

### Added

- `governance/contracts.py` -- `HITLPrompt`, `HITLPromptOption` (mandatory bypass last)
- `contracts.py` -- `hitl_prompts: list[HITLPrompt] = []` on `PreflightResponse`; `RecordBypassResponse`
- `preflight_telemetry.py` -- `write_bypass_event` (V4 idempotent), `recent_bypass_seconds` (F3 bounded tail-read, ≤1000 lines)
- `bicameral.record_bypass` MCP tool (returns `{recorded, deduped, reason}`)
- `handlers/preflight.py` -- HITL trigger logic + JSONL-driven recency wired into `engine.evaluate`
- `handlers/record_bypass.py` -- new MCP write handler
- `skills/bicameral-preflight/SKILL.md` -- step 5.4 documents trigger conditions, mandatory-last bypass, and the tier-drop semantics

### Closes

#112

## v0.17.0 -- governance contracts + escalation engine (#108-#110, Phases 1-3 of #108-#112 plan)

Adds `governance/` package with the deterministic escalation policy engine, decision/risk/escalation metadata contracts, and the consolidated `GovernanceFinding` wrapper. Engine is non-blocking by design (`config.allow_blocking: Literal[False]` locks the type). Phase 4 (HITL bypass flow) and Phase 5 (docs) ship in follow-up PRs.

### Added
- `governance/contracts.py` -- GovernanceMetadata, GovernanceFinding, GovernancePolicyResult; `derive_governance_metadata` with L1/L2/L3 default mapping
- `governance/finding_factories.py` -- builders + `consolidate()` per (decision_id, region_id)
- `governance/engine.py` -- pure deterministic evaluator decomposed into `_check_required_conditions`, `_apply_class_defaults`, `_apply_bypass_downgrade`, `_apply_max_native_ceiling`
- `governance/config.py` -- `.bicameral/governance.yml` parser with locked `allow_blocking: Literal[False]`; fail-soft on malformed YAML
- `bicameral.evaluate_governance` MCP tool (read-only)
- `governance_finding` field on PreflightResponse
- `decision.governance` schema field (v14 -> v15 migration)
- `docs/governance.example.yml` canonical config example

### Closes

#109, #110 (Phases 1-2 fully)

### Refs

#108 (engine landed; Phase 4 HITL bypass flow ships separately for #112)

## [Unreleased]

### Fixed

- **Post-commit hook now actually syncs the ledger (#124).** The
  `bicameral-mcp setup` (Guided mode) post-commit hook called
  `bicameral-mcp link_commit HEAD`, which was never a registered CLI
  subcommand — every commit since the hook was introduced silently
  failed via `>/dev/null 2>&1 || true` and the user never saw the
  argparse error. This release adds the missing `link_commit`
  subcommand (with a `--quiet` flag for hook scripts), replaces the
  silent-failure suppression with stderr-loud reporting captured to
  `${HOME}/.bicameral/hook-errors.log` (still exits 0 so commits never
  block), and adds a smoke test that walks every command referenced
  in installed hook scripts and asserts each is registered. **Existing
  Guided-mode installs start working automatically; no reinstall
  required.**

### Added

- **`bicameral-mcp branch-scan` CLI + opt-in pre-push git hook (#48).**
  New console subcommand prints a terminal summary of drifted decisions
  for HEAD; calls `link_commit` under the hood. Installed as a git
  pre-push hook via `bicameral-mcp setup --with-push-hook`. Surfaces
  drift warnings before `git push` completes, with a `Push anyway? [y/N]`
  prompt when attached to a TTY. Non-blocking by default;
  `BICAMERAL_PUSH_HOOK_BLOCK=1` forces hard-block on drift. Idempotent
  install. Path C: skips silently when no `~/.bicameral/ledger.db`
  exists. New module `cli/branch_scan.py`; new
  `_install_git_pre_push_hook` in `setup_wizard.py`; new `--with-push-hook`
  flag in `bicameral-mcp setup`. Issue #48.

### Removed

- **Sticky PR-comment drift-report GitHub Action (#49 / PR #113).**
  Reverted before reaching a numbered release. The action was
  installed under `.github/workflows/` of `bicameral-mcp` itself,
  which conflated dogfooding with delivery: the feature is meant to
  ship to *customer* repos, not police our own CI. Removed the
  workflow, the `cli/drift_report.py` renderer, the
  `.github/scripts/post_drift_comment.py` poster, and their tests.
  A future re-introduction will package the same artifacts as a
  template under a non-CI path so users opt in by copying it.

## v0.16.0 -- decision_level classifier + MCP primitives (#77 + Phase 5+6 of #76 in sibling PR)

Adds a heuristic decision-level classifier, a single-row write helper for
`decision.decision_level`, two MCP primitives that expose classification to
agents, and a bulk-classify CLI for offline backfill. The companion #76
dashboard work (amber unclassified badge, filter dropdown, inline edit POST
endpoint) ships in a sibling PR against the same `dev` branch.

### Added

- **New module: `classify/heuristic.py`** -- pure-function port of the L1/L2/L3
  rules documented at `skills/bicameral-ingest/SKILL.md` lines 178-217. Single
  public entrypoint `classify(description, source="") -> (level, rationale)`.
  Deterministic, no IO, no LLM, no network. Regression-tested against the
  7 fixtures at `tests/fixtures/ingest_level_classification/` (7/7 pass).
- **New helper: `ledger.queries.update_decision_level(client, decision_id,
  level)`** -- single-row write helper, sibling of `update_decision_status`.
  Idempotent. Includes defensive `_DECISION_ID_RE` shape validation
  (`^decision:[A-Za-z0-9_]+$`) before SurrealQL interpolation
  (audit S1 defense-in-depth) and a `_VALID_LEVELS` membership check.
  Raises `DecisionNotFound` when the row does not exist.
- **New MCP primitives** (two tools, NOT a bulk wrapper):
  - `bicameral.list_unclassified_decisions(decision_ids?)` -- read-only.
    Returns `proposals[]` with `proposed_level`, `rationale`, and
    `confidence` ("low" when the heuristic defaulted with no signal).
  - `bicameral.set_decision_level(decision_id, level, rationale?)` --
    single-row write, idempotent. Errors come back structured
    (`{ok: false, error: ...}`) rather than raised, so agents recover
    per-row without aborting the loop.
- **New contracts**: `UnclassifiedProposal`,
  `ListUnclassifiedDecisionsResponse`, `SetDecisionLevelResponse`.
- **New CLI: `bicameral-mcp-classify`** (entrypoint at `cli.classify:main`).
  Default is dry-run (prints a proposal table); `--apply` writes the
  proposed levels via the same `update_decision_level` helper. Progress
  output every 100 rows for large batches. Reuses the heuristic and the
  ledger helper -- one write path, three callers (CLI, MCP tool, future
  dashboard endpoint).

### Closes

#77

## v0.16.1 -- Dashboard decision_level surfacing (#76 part 1)

Read-side UI for `decision_level`. The pre-existing L1/L2/L3 badges
(shipped in #71 / CodeGenome Phase 1+2) are preserved; this PR adds the
missing amber **Unclassified** state for rows where `decision_level` is
NULL plus a top-of-page filter dropdown so reviewers can scope the
ledger view to a single level (or to the unclassified backlog).

### Added

- `.lvl-unclassified` CSS class in `assets/dashboard.html` -- amber
  (`rgb(249, 115, 22)`) badge that pairs visually with the existing
  L1/L2/L3 family.
- Rendering branch in `renderDec` for null `decision_level`: emits a
  `lvl-unclassified` badge labeled `Unclassified` and stamps the row
  with `data-level="unclassified"`.
- Each rendered decision row now carries
  `data-level="L1"|"L2"|"L3"|"unclassified"` and the
  `decision-row` class so client-side filters can target it.
- `<select id="lvl-filter">` in the topbar with five options
  (All / L1 / L2 / L3 / Unclassified) wired to a new
  `applyLevelFilter(value)` JS helper that toggles row visibility via
  `style.display`.
- `tests/test_dashboard_unclassified_rendering.py` -- six HTML-pattern
  assertions covering the CSS rule, the render branch, the dropdown
  markup, and the filter function. The dashboard render path is inline
  JS in the HTML template, so the tests assert against the
  source-of-truth template rather than booting a DOM.

### Deferred to part 2

- Inline-edit POST endpoint (Phase 6 of the plan). It calls
  `ledger.queries.update_decision_level`, which lands in the sibling
  classifier PR (#77). Part 2 ships once that helper is on `dev`.

### Closes

Refs #76 (part 1 of 2)

## v0.15.0 — Preflight telemetry capture loop (pieces 1–4) — built via [QorLogic SDLC](https://github.com/MythologIQ-Labs-LLC/qor-logic)

First slice of the failure-mode triage workflow from #65. Adds a local-only,
**default-off** capture loop that records bicameral.preflight events plus
downstream tool engagement, attributable per-call via a new ``preflight_id``.
The data is for self-triage of false fires / silent misses; it never leaves
the user's machine and is not part of the existing PostHog relay path.

### Added

- **New module: `preflight_telemetry.py`** (top-level, sibling of
  `telemetry.py` — they are independent capture systems). Provides:
  - `_get_or_create_salt()` — per-install salt at `~/.bicameral/salt`,
    `os.urandom(32)`, mode `0o600` on POSIX. Race-safe init: `os.O_EXCL`
    create with a `FileExistsError` fallback that reads the winner's
    bytes (audit MF1 inline fix).
  - `hash_topic(topic)` and `hash_file_paths(paths)` — salted SHA-256
    truncated to 16 hex chars (~64 bits). `hash_file_paths` is
    order-independent so `["a.py","b.py"]` and `["b.py","a.py"]` collide
    by design.
  - `new_preflight_id()` — fresh UUIDv4.
  - `write_preflight_event(...)` — JSONL append at
    `~/.bicameral/preflight_events.jsonl`, mode `0o600`.
  - `write_engagement(...)` — JSONL append at
    `~/.bicameral/engagements.jsonl`, mode `0o600`. Falls back to
    subset-match attribution against recent preflight events when no
    explicit `preflight_id` is supplied.
  - `_maybe_rotate(path)` — rotates at 50 MB or 30 days, keeps the most
    recent 5 rotations. Uses `os.replace` (atomic on Windows + POSIX).
- **`preflight_id` plumb-through** — new optional `str | None` field on
  `PreflightResponse`, `LinkCommitResponse`, `BindResponse`, and
  `RatifyResponse`. The `update.py` handler returns dicts and now adds a
  `preflight_id` key to every return shape (audit S3 — 11 sites). Each
  affected handler (`handle_link_commit`, `handle_bind`, `handle_ratify`,
  `handle_update`) gains a keyword-only `preflight_id: str | None = None`
  parameter.
- **MCP tool inputSchema** — `preflight_id` (optional string) added to
  `bicameral.preflight`, `bicameral.link_commit`, `bicameral.bind`,
  `bicameral.update`, `bicameral.ratify`. Existing skills that don't pass
  it keep working unchanged.
- **Tests** — `tests/test_preflight_telemetry.py` (19 cases covering
  salt, hash, writers, rotation, race-loser MF1) and
  `tests/test_preflight_id_plumbing.py` (9 cases covering the response
  field on each affected handler).

### Privacy stance

- **Opt-in.** Default is OFF. Set `BICAMERAL_PREFLIGHT_TELEMETRY=1` to
  capture; unsetting it makes every writer a no-op.
- **Hashed by default.** Topic and file_paths are stored as 16-char
  salted SHA-256 prefixes. Set `BICAMERAL_PREFLIGHT_TELEMETRY_RAW=1` to
  additionally store plaintext — separate, explicit opt-in.
- **`surfaced_ids` are written raw.** They are opaque ledger
  `decision_id` strings, already non-PII. Hashing them would defeat the
  triage join with `failure_review.jsonl` (the only useful join).
  Documented as an invariant in the module docstring.
- **Local-only.** All files live under `~/.bicameral/`, mode `0o600`.
  Data never leaves the machine; this is a separate path from the
  PostHog relay in `telemetry.py`.
- **Bounded retention.** 50 MB rolling cap per file; 30-day mtime
  ceiling; keep last 5 rotations.

### Out of scope (deferred to follow-up plans)

- **Piece 5 — SessionEnd reconciliation skill** (#65-pt2). Reads the
  JSONL files, classifies entries as `suspected_miss` /
  `suspected_false_fire` / `normal`, writes `failure_review.jsonl`.
- **Piece 6 — Triage CLI + redaction** (#65-pt3). `bicameral-mcp triage`
  CLI for labeling failure rows; promotion to
  `tests/eval/real_dataset.jsonl` requires explicit redaction.

### Closes

#65 (pieces 1–4 only — pieces 5–6 tracked separately)

## v0.14.0 — Local-only telemetry counters + usage summary + first-boot consent — built via [QorLogic SDLC](https://github.com/MythologIQ-Labs-LLC/qor-logic)

Privacy-first observability foundation. Adds a local-only counter sink
that runs alongside (not replacing) the existing network relay, a new
`bicameral.usage_summary` MCP tool that aggregates ledger and counter
state into actionable percentages, and a non-blocking first-boot notice
so users upgrading to this binary see the telemetry policy before any
data flows.

### Added

- **`local_counters.py`** (#39) — append-only JSONL sink at
  `~/.bicameral/counters.jsonl`. Records only `{tool_name, delta=1, ts}`
  per call. Mode `0o600` on POSIX; thread-safe; no network egress.
  Always-on regardless of network telemetry consent — counters are
  local introspection, distinct from the relay. Kill-switch:
  `BICAMERAL_LOCAL_COUNTERS=0`. API: `increment(tool_name)` and
  `read_counters() -> dict[str, int]`.
- **`consent.py`** (#39) — owns `~/.bicameral/consent.json`,
  `telemetry_allowed()` predicate, and `notify_if_first_run()`. Marker
  shape: `{telemetry, policy_version, acknowledged_at, acknowledged_via}`
  with `acknowledged_via` distinguishing `"wizard"` (explicit choice)
  from `"first_boot_notice"` (passive ack). `POLICY_VERSION` constant
  re-fires the notice for everyone once when telemetry policy changes.
- **`bicameral.usage_summary`** MCP tool (#42) — aggregate readout over
  the last N days (default 7). Returns ingest/bind call counts (from
  the local counters file), decision counts by status (from ledger),
  reflected/drift percentages, cosmetic-drift percentage (from
  compliance_check verdicts), and error rate. Privacy-preserving:
  aggregate counts and floats only.
- **First-boot consent notice** — non-blocking, fires once per
  `policy_version` via stderr (always) and MCP `notifications/message`
  (when an active session is available). Server keeps running; if
  marker write fails, notice is logged at debug and the server
  continues. Test escape hatch: `BICAMERAL_SKIP_CONSENT_NOTICE=1`.

### Changed

- **`telemetry.send_event` now uses `consent.telemetry_allowed()`** as
  the single gating predicate. Behavior preserved for users without a
  marker (default-on); newly opted-out users (marker says `disabled`
  via the wizard) suppress the relay even when env var is unset.
- **`telemetry.send_event` always increments the local counter** before
  the relay path — never raises, wrapped in try/except. Counter
  failure cannot affect the caller; relay path runs independently.
- **`setup_wizard._select_telemetry`** now calls
  `consent.write_consent(via="wizard")` after the user's choice. Hard
  fails (raises `OSError`) if the marker cannot be written — guarantees
  a "no" answer never silently leaves telemetry on.
- **`server.serve_stdio`** calls `consent.notify_if_first_run()` once
  during startup. Wrapped in try/except — startup is never blocked by
  notice machinery.

### CI

- `BICAMERAL_SKIP_CONSENT_NOTICE: "1"` added to the test job env in
  `.github/workflows/test-mcp-regression.yml` so test runs do not emit
  notices into job logs.
- `tests/conftest.py` adds a session-scoped autouse fixture that
  reroutes `~/.bicameral/` to a per-session tmp dir and sets the skip
  env var. Stdlib only — no third-party fixture plugin.

### Closes

#39, #42.

## v0.13.0 — CodeGenome Phase 4 (#61) — semantic drift evaluation in `resolve_compliance` (M3) — built via [QorLogic SDLC](https://github.com/MythologIQ-Labs-LLC/qor-logic)

Final PR in the three-phase CodeGenome rollout (issues #59 / #60 /
#61). Adds a deterministic cosmetic-vs-semantic classifier that
auto-resolves drifted regions whose change is structurally cosmetic
(docstrings, comments, import re-order, whitespace, signature- and
neighbor-equivalent edits) BEFORE the caller LLM is asked for a
verdict. Cuts noise on the M3 metric. Default behavior is
**unchanged** unless callers opt in via `BICAMERAL_CODEGENOME_ENHANCE_DRIFT`.

### Added

- **Drift classifier** (`codegenome/drift_classifier.py`,
  `codegenome/drift_service.py`) — issue-mandated weighted scoring
  (signature 0.30, neighbors 0.25, diff_lines 0.30, no_new_calls
  0.15). Verdict: ≥0.80 cosmetic (auto-resolve), ≤0.30 semantic, else
  uncertain (caller LLM still decides, with a structured hint).
- **Multi-language line categorizers** (`codegenome/_line_categorizers/`)
  — Python, JavaScript, TypeScript, Go, Rust, Java, C#. Per-language
  rules for docstring / comment / import / signature recognition.
- **Call-site extractor** (`code_locator/indexing/call_site_extractor.py`)
  — sibling of `symbol_extractor`; extracts `set[str]` of called
  callable names per language for the `no_new_calls` signal.
- **Schema v14** — `compliance_check` table redefined with
  `CHANGEFEED 30d INCLUDE ORIGINAL`; new `semantic_status` field
  (option<string>, ASSERT enum
  `['semantically_preserved', 'semantic_change']`); new
  `evidence_refs` field (array<string>). Additive migration
  (`_migrate_v13_to_v14`).
- **`PendingComplianceCheck.pre_classification`** — typed
  `PreClassificationHint | None` field. Populated when the
  classifier scored the change in the uncertain band; carries
  `verdict`, `confidence`, per-signal contributions, and
  `evidence_refs`. Advisory hint for the caller LLM.
- **`ComplianceVerdict.semantic_status` + `.evidence_refs`** —
  optional fields on caller verdicts. Persisted to
  `compliance_check.semantic_status` and
  `compliance_check.evidence_refs` for the audit trail.
- **`ResolveComplianceAccepted.semantic_status`** — echoes the
  caller's claim through the response.
- **`LinkCommitResponse.auto_resolved_count`** — number of regions
  the classifier auto-resolved as cosmetic in this commit's sweep.

### Changed

- `_run_drift_classification_pass` runs after `_run_continuity_pass`
  in `handlers/link_commit.py`, sharing the same
  `cg_config.enhance_drift` flag (one feature, one toggle).
- `handlers/resolve_compliance.py` accepts and persists the new
  optional verdict fields.
- `skills/bicameral-sync/SKILL.md` documents the
  `auto_resolved_count`, `pre_classification` hint, and the
  optional `semantic_status` + `evidence_refs` on caller verdicts.

### Schema compatibility

- v13 → v14 (additive); rolling upgrade safe.
- v14 = "0.13.0" placeholder; release-eng pins final value at PR merge.

### M3 benchmark

`tests/test_m3_benchmark.py` runs a 30-case corpus (Python 12 + JS 3
+ TS 3 + Go 3 + Rust 3 + Java 3 + C# 3) through the classifier.
False-positive rate (semantic mis-classified as cosmetic) on the
corpus: **0%** (target: < 5%).

---

## v0.12.0 — CodeGenome Phase 3 (#60) — continuity evaluation in `link_commit` — built via [QorLogic SDLC](https://github.com/MythologIQ-Labs-LLC/qor-logic)

Second PR in the three-phase CodeGenome rollout (issues #59 / #60 / #61).
Adds the per-region continuity matcher: when a drifted region's identity
moved or was renamed, the bind is auto-redirected to the new location
before the caller LLM is asked for a verdict. Default behavior is
**unchanged** unless callers opt in via the new `BICAMERAL_CODEGENOME_ENHANCE_DRIFT`
flag.

### Added

- **Continuity matcher** (`codegenome/continuity.py`,
  `codegenome/continuity_service.py`) — deterministic 4-signal scoring
  (signature_hash, neighbors Jaccard, name match, kind) with
  per-region resolution and 7-step ledger write sequence
  (compute_identity → upsert_code_region → upsert_subject_identity
  → write_subject_version → relate_has_version → write_identity_supersedes
  → update_binds_to_region).
- **Schema v12** — new `subject_version` table; `identity_supersedes`
  edge; `subject_identity.neighbors_at_bind` field. Additive migration
  (`_migrate_v11_to_v12`).
- **`LinkCommitResponse.continuity_resolutions`** — additive optional
  field; populated when `enhance_drift` is enabled.
- **9 new ledger queries** + adapter wrappers:
  `relate_has_version`, `write_subject_version`, `write_identity_supersedes`,
  `update_binds_to_region`, `create_code_region`, `get_region_metadata`,
  expanded `link_decision_to_subject` (now carries `region_id`),
  `find_subject_identities_for_decision`.
- **PR #73 review hardening** (CodeRabbit + Devin):
  - Fixed silent `AttributeError` in `_resolve_symbol_id_for_span`
    (`sqlite_db_path` typo) that made neighbor signal permanently zero
    in production.
  - Reused `self._db` handle in neighbor lookup (no per-call
    SQLite open/leak).
  - Wrapped `update_binds_to_region` DELETE+RELATE in BEGIN/COMMIT
    transaction.
  - Added partial-bind rollback on edge-write failure in
    `_persist_subject_and_identity`.
  - `link_decision_to_subject` now carries originating `region_id` on
    the `about` edge so multi-region decisions don't flatten subjects.
  - Replaced the `upsert_code_region` adapter wrapper with a
    `create_code_region`-backed implementation so continuity redirects
    always target a distinct new region id (no in-place clobber).
  - `DriftContext` now seeded with the bound region's actual span +
    identity_type via `get_region_metadata` (was hardcoded to
    `"unknown"`/`0,0`, dropping 20% of the continuity score).
  - Pydantic `confidence: float` constrained to `[0.0, 1.0]` via
    `Field(ge=0.0, le=1.0)`.

### Schema compatibility

- v11 → v12 (additive); rolling upgrade safe.

---

## v0.11.0 — CodeGenome Phase 1+2 (#59) — adapter boundary + identity records — built via [QorLogic SDLC](https://github.com/MythologIQ-Labs-LLC/qor-logic)

Foundation PR for the three-phase CodeGenome rollout (issues #59 / #60 / #61).
Adds a stable adapter boundary, deterministic identity computation, and
side-effect-only identity-record writes at bind time. Default behavior is
**unchanged** unless callers opt in via two environment variables.

### Added

- **CodeGenome adapter package** (`codegenome/`) — abstract
  `CodeGenomeAdapter` ABC with four methods (`resolve_subjects`,
  `compute_identity`, `evaluate_drift`, `build_evidence_packet`); only
  `compute_identity` ships in this release. Concrete
  `DeterministicCodeGenomeAdapter` implements the v1 location-based
  identity (no LLM, no embeddings) reusing the existing tree-sitter +
  `ledger.status.hash_lines` stack.
- **Pydantic boundary contracts** (`codegenome/contracts.py`):
  `SubjectCandidateModel`, `EvidenceRecordModel`, `EvidencePacketModel`.
- **Confidence helpers** (`codegenome/confidence.py`): `noisy_or` and
  `weighted_average` plus `DEFAULT_CONFIDENCE_WEIGHTS` constants
  consumed by future phase-3/phase-4 callers.
- **Feature flags** (`codegenome/config.py`,
  `BicameralContext.codegenome_config`) — every flag defaults `False`.
  New environment variables, all opt-in:
  - `BICAMERAL_CODEGENOME_ENABLED`
  - `BICAMERAL_CODEGENOME_WRITE_IDENTITY_RECORDS`
  - `BICAMERAL_CODEGENOME_ENHANCE_DRIFT` *(reserved for #60)*
  - `BICAMERAL_CODEGENOME_ENHANCE_SEARCH` *(reserved)*
  - `BICAMERAL_CODEGENOME_EXPOSE_EVIDENCE_PACKETS` *(reserved)*
  - `BICAMERAL_CODEGENOME_CHAMBER_EVALUATIONS` *(reserved)*
  - `BICAMERAL_CODEGENOME_BENCHMARK_MODE` *(reserved)*
- **Adapter factory** (`adapters/codegenome.py::get_codegenome()`),
  parallel to `get_ledger`, `get_code_locator`, `get_drift_analyzer`.
- **SurrealDB schema v10 → v11 migration** (`_migrate_v10_to_v11`,
  additive only):
  - Tables: `code_subject`, `subject_identity`, `subject_version`.
  - Relations: `has_identity` (subject→identity), `has_version`
    (subject→version), `about` (decision→subject).
  - Migration writes nothing to the new tables; identity records are
    only created when the flags above are set.
- **Bind-time identity write** (`handlers/bind.py` +
  `codegenome/bind_service.py::write_codegenome_identity`) — a
  side-effect that runs after `ledger.bind_decision()` succeeds when
  `codegenome.identity_writes_active()` is `True`. Failure inside the
  identity write is caught and logged; the `BindResponse` /
  `BindResult` shape is unchanged. Identity records are queryable via
  `ledger.find_subject_identities_for_decision(decision_id)`.
- **L1 exemption guard** (`handlers/bind.py` +
  `ledger.queries.get_decision_level`) — only decisions explicitly
  tagged `decision_level = "L2"` enter the codegenome identity graph.
  L1 decisions (behavioral commitments evaluated against evidence, not
  code regions) are intentionally ungrounded at the identity layer; L3
  is never tracked; `NULL` (unclassified) is treated as L3 by the
  tolerant policy — preserves backward-compat for existing ingest
  payloads, classification can be added later without re-binding. Per
  the "L1/L2: Claim/Identity" spec-governance proposal §4.2.

### Identity model (deterministic_location_v1)

```text
structural_signature = f"{file_path}:{start_line}:{end_line}"
signature_hash       = blake2b(structural_signature, digest_size=32)
address              = f"cg:{signature_hash}"
content_hash         = ledger.status.hash_lines(body, start, end)
                       (sha256 with whitespace normalization, identical
                        to code_region.content_hash by construction)
confidence           = 0.65
identity_type        = "deterministic_location_v1"
model_version        = "deterministic-location-v1"
```

`content_hash` reuses the existing ledger hash function rather than the
literal `blake2b(body)` from the issue spec so that
`subject_identity.content_hash` and `code_region.content_hash` compare
byte-for-byte equal at bind time — required by the issue's exit criterion.

### Migration notes

- Schema migrates automatically on next connect via the existing migration
  runner. Migration is **additive** (no existing tables touched). No data
  loss; rollback to v10 requires a manual `bicameral_reset(confirm=True)`.
- `SCHEMA_COMPATIBILITY[11]` ships pinned to `"0.11.0"` as a sourced
  placeholder. Release-engineering pins the final value at PR merge.

### Tests

- 49 codegenome unit + integration tests, all passing:
  `tests/test_codegenome_{adapter,confidence,config,bind_integration}.py`.
- Zero regressions against the existing test suite.

---

---

## v0.10.8 — ephemeral/authoritative V2

### Fixed — drift detection on feature branches

`link_commit` now correctly flags drift when code on a feature branch diverges
from a previously-verified decision, even before the branch is merged to main.
A new branch-delta sweep (`git diff auth...HEAD --name-only`) covers all files
touched across the entire feature branch, not just the latest commit — earlier
commits in a long-running branch are no longer missed. `sweep_scope` gains a
new `"branch_delta"` value in the response.

### Fixed — stale "compliant" status across branch switches

Switching from one feature branch to another no longer leaves false `reflected`
statuses behind. Feature branches now derive status locally by comparing
`actual_hash` vs `stored_hash` without mutating `code_region.content_hash`
(pollution guard preserved).

### Added — ephemeral verdict promotion

Verdicts written on a feature branch are marked `ephemeral=True`. When the
same content hash lands on the authoritative branch (via `ingest_commit` or
`resolve_compliance`), the row is promoted to `ephemeral=False` automatically —
no duplicate compliance work required.

## v0.10.7 — fix update/sync skill confusion

### Fixed — `bicameral update` no longer triggers `/bicameral:sync`

Added a dedicated `/bicameral:update` skill so "update", "upgrade", and
"new version" requests route to the binary upgrade flow rather than the
post-commit ledger sync. The `bicameral-sync` skill description now
explicitly lists "update/upgrade/new version" as **never-fire** triggers
and cross-references `/bicameral:update`.

## v0.10.6 — wait-time disclaimer + resolve_compliance latency telemetry

### Added — one-time product stage note on first preflight

On the first `bicameral.preflight` call per device, the response now includes
a `product_stage` field with a plain-English note: "some operations may take
a few minutes — this is expected." The note is shown once (gated by
`~/.bicameral/onboarded` marker file) and surfaced verbatim by the preflight
skill. Sets wait-time expectations before users hit a slow ingest or compliance
sweep for the first time.

### Added — `verdict_count` diagnostic on `resolve_compliance`

`bicameral.resolve_compliance` now emits a `verdict_count` integer in its
PostHog diagnostic alongside the already-recorded `duration_ms`. Enables
segmenting slow calls (was the latency from 1 verdict or 20?) without any
user-visible change.

## v0.10.5 — hook replace strategy + bicameral-config skill

### Fixed — `_install_claude_hooks` replace-not-skip strategy

`bicameral.update apply` and `bicameral-mcp setup` previously skipped hook
installation entirely when any "bicameral" hook was already present in
`.claude/settings.json`. This meant updated hook commands (e.g. the
`bicameral-sync` PostToolUse trigger added in v0.10.3) never reached existing
installs. The function now removes all stale bicameral-tagged hook entries
and writes the current `_BICAMERAL_POST_COMMIT_COMMAND` and
`_BICAMERAL_SESSION_END_COMMAND` on every run, making hook updates idempotent
and self-healing.

### Fixed — `_reinstall_skills` installs git post-commit hook for guided users

`_reinstall_skills` (called during `bicameral.update apply`) now reads the
`guided:` flag from `.bicameral/config.yaml` and calls
`_install_git_post_commit_hook` when guided mode is active, closing the gap
where the git hook was never updated during upgrades.

### Added — `bicameral-config` skill

New `/bicameral:config` skill provides a fully interactive configuration
walkthrough: reads the current `config.yaml`, walks through collaboration
mode / guided mode / telemetry settings one-by-one, writes the updated config,
reinstalls skills and hooks via subprocess (using the new binary's code), and
reports exactly what changed.

## v0.10.4 — auto-migration on upgrade

`bicameral.update apply` now automatically applies any pending destructive
schema migration immediately after pip install, without requiring a manual
`bicameral.reset`. After pip install, a subprocess using the new binary
connects to the ledger, detects `DestructiveMigrationRequired`, runs
`force_migrate` (schema DDL), wipes scoped data, and returns a
`migration_replay_plan` in the update response. The agent can then
re-ingest each entry to restore the ledger. If auto-migration fails, the
response falls back to the previous advisory warning directing the user to
call `bicameral.reset(confirm=True)` manually.

## v0.10.3 — post-commit sync + ephemeral branch-delta display

### Added — `bicameral-sync` skill

New canonical skill for the full `link_commit → resolve_compliance` loop.
Fires when the PostToolUse hook emits "bicameral: new commit detected" or
when `_sync_guidance` appears in any tool response. Without this skill,
compliance checks remain `pending` after a commit and status never becomes
authoritative `reflected`/`drifted`. All existing skills that previously
inlined the compliance resolution rubric now reference `bicameral-sync` for
that step, with a compact canonical `resolve_compliance` call block in place
of the duplicated evaluation prose.

### Added — git post-commit hook (Guided mode)

`bicameral-mcp setup` now installs a `.git/hooks/post-commit` hook when the
user selects Guided mode. The hook calls `bicameral-mcp link_commit HEAD`
after every commit (hash-level sync) so the ledger is never stale even for
terminal commits outside Claude Code. The guided mode choice label in the
setup wizard explicitly states this will happen. Idempotent: appends to an
existing hook if one exists without overwriting it.

The Claude Code `PostToolUse` hook message was updated to say "run
`/bicameral:sync`" (full semantic sync) rather than "call
`bicameral.link_commit`" (hash-level only).

### Added — ephemeral branch-delta indicator in dashboard and history

`HistoryDecision.ephemeral: bool` is now set to `True` when a decision's
current `reflected`/`drifted` status was determined by a feature-branch
commit not yet in the authoritative ref. `handle_history` runs a single bulk
query against `compliance_check WHERE ephemeral = true AND pruned = false`
after building the feature list, then marks matching decisions.

The dashboard renders a `⎇` badge in the state cell with tooltip "Status
from feature branch — not yet verified on main". `bicameral-history/SKILL.md`
instructs text-rendering agents to append `⎇` after the status for ephemeral
decisions.

### Fixed — stale field names in `bicameral-ingest/SKILL.md`

The compliance verification step (Step 3b) used `intent_id` and `compliant:
bool` — field names from the v0.4.x API. Updated to the current contract:
`decision_id` and `verdict: "compliant" | "drifted" | "not_relevant"`.

### Fixed — stale `intent_id` references in `bicameral-scan-branch/SKILL.md`

Action hints `refs` arrays were documented as containing `intent_id`s;
corrected to `decision_id`s to match `handlers/action_hints.py`.

## v0.9.2 — desync optimization V1 — measurement + read-path advisory

V1 of a two-part desync-correctness initiative. V1 ships measurement
infrastructure, a strict-whitelist cosmetic-change classifier, relocation
context enrichment, and a canonical 13-scenario regression matrix —
**without touching any destructive write path**. V2 (separate effort,
design captured in `docs/v2-desync-optimization-guide.md` with nine rounds of Codex
review) tackles the destructive-path overhaul: atomic rebind, baseline
advancement with full CAS, schema migration v6, append-only verdict
history.

V1 introduces zero new mutating capabilities. Every change is one of:
read-only measurement, additive contract field, pure function, test
coverage, or a surgical bug fix to an already-shipped path. The plan,
phase breakdown, V2 deferred items, and Codex review parking lot live in
`docs/v2-desync-optimization-guide.md`.

### Added — `tests/bench_drift.py` (A1)

Drift benchmark harness. Seeds 100 decisions across 25 files via
tree-sitter `extract_symbols` (no BM25 index build required), times
`handle_search_decisions`, `handle_detect_drift`, `handle_link_commit`
under a `memory://` ledger, writes
`test-results/bench/drift_baseline.json` plus a stdout summary.
Marked `@pytest.mark.bench` so default test runs skip it; run via
`pytest tests/bench_drift.py -v -m bench -s`.

Baseline on Apple Silicon (post-rebase, surrealdb 2.0.0):

| handler            | p50 (ms) | p95 (ms) | max (ms) |
|--------------------|---------:|---------:|---------:|
| search_decisions   |      9.2 |     10.4 |     11.0 |
| detect_drift       |     14.2 |     15.5 |     16.4 |
| link_commit (warm) |      7.3 |      8.0 |      8.3 |

All 50–185× under the V2 perf targets in `PLAN.md:83`
(`search_decisions < 2s`, `detect_drift < 1s`).

### Added — `handlers/sync_middleware.repo_write_barrier(ctx)` (A2-light)

Per-repo `asyncio.Lock` async context manager backed by a module-level
`dict[repo_path, asyncio.Lock]`. `handle_bind` wraps its body via a thin
`_do_bind` inner function. Different repos run concurrently; same repo
serializes. Lazy guard-lock construction avoids the "bound to wrong
loop" pitfall across test event loops. Yields a mutable `BarrierTiming`
holder whose `held_ms` is populated on exit (including on exceptions).

Deliberately narrow scope: does NOT protect `resolve_compliance` or
cross-process writers — both are V2 scope (V1 plan §5.2, §5.5).

### Added — `contracts.SyncMetrics` (A3)

```python
class SyncMetrics(BaseModel):
    sync_catchup_ms: float | None = None
    barrier_held_ms: float | None = None
```

Attached as `sync_metrics: SyncMetrics | None = None` to
`SearchDecisionsResponse`, `PreflightResponse`, `HistoryResponse`,
`BindResponse`. Purely additive, non-breaking. Each handler times its
own sync call locally so nested calls (e.g. preflight chaining to
search_decisions) don't step on each other's metrics.

### Added — `ledger/ast_diff.is_cosmetic_change(before, after, lang)` (B1)

Strict-whitelist tree-sitter classifier returning `True` only when two
snippets differ by inter-token whitespace alone. Compares a recursive
`(node.type, child_sigs | leaf_bytes)` signature; identifier renames,
comment edits (incl. `# type: ignore` / `# noqa` / `// @ts-ignore` /
build tags / lint pragmas), docstring edits, trailing-comma changes,
string-literal changes, import reorders, and any AST shape change all
return `False`. Reuses `code_locator.indexing.symbol_extractor._get_parser`
so the cosmetic detector and symbol indexer can never silently disagree
on supported languages: python, javascript, typescript, java, go, rust,
c_sharp (plus jsx → javascript and tsx → typescript via
`LANGUAGE_FALLBACK`). Unsupported langs, parse failures, and trees with
`has_error` all fail safe to `False`.

False negatives (real cosmetic changes routed unbiased to L3 in V2) are
cheap; false positives (semantics-affecting changes mislabeled cosmetic)
bias future L3 prompts toward "looks fine" — exactly the failure mode
the strict whitelist prevents.

### Added — `DriftEntry.cosmetic_hint: bool = False` (B2)

Populated by `handlers.detect_drift._enrich_with_cosmetic_hints` after
the pure `raw_decisions_to_drift_entries` mapping (IO encapsulated
outside the pure function). Read-path advisory ONLY — never mutates
`content_hash`, never gates drift surfacing or status, never advances
baseline. Five fail-safe paths leave the hint `False`: non-drifted
entry, equal HEAD/working-tree bytes, unsupported file extension,
invalid line range, exception during classifier.

Source comparison: HEAD bytes (via `ledger.status.get_git_content` ref
`"HEAD"`) vs working-tree bytes (ref `"working_tree"`), sliced to the
region's `(start_line, end_line)`. Language resolved from file extension
via `code_locator.indexing.symbol_extractor.EXTENSION_LANGUAGE`.

### Added — `pending_grounding_checks[].original_lines` (D1)

For `reason='symbol_disappeared'` entries, the payload now carries
`original_lines: [start_line, end_line]` so the caller LLM can run
`git show <prev_ref>:<file_path>` to inspect the symbol's prior
position when locating its new home. Strictly informational — no
actionable workflow. Single-line addition in `ledger/adapter.py`.

### Added — `tests/test_desync_scenarios.py` (F1)

Canonical regression matrix for the 13 desync scenarios from the Notion
"Auto-Grounding Problem" catalog, routed through the real handler
layer per the Apr 8 PR #84 lesson (tests bypassing handlers miss
post-ingest hooks). Self-contained tmp git-repo fixture per test.

**Scorecard**: 12 PASS, 1 XFAIL.

| # | Scenario | V1 outcome |
|---|---|---|
| 1 | New decision, matching code exists | ✅ ungrounded → caller binds |
| 2 | Code changed after grounded | ✅ pending + `pending_compliance_check` |
| 3 | Code deleted after grounded | ✅ symbol_disappeared |
| 4 | Symbol renamed in file | ✅ symbol_disappeared with `original_lines` |
| 5 | Symbol moved cross-file | ✅ symbol_disappeared |
| 6 | Code added later | ✅ caller binds explicitly |
| 7 | Cold start, no matching code | ✅ stays ungrounded |
| 8 | Drifted intent → atomic re-ground | ⏸ XFAIL (V2 §8 D2 — `bicameral_rebind` with old-binding CAS) |
| 9 | Intent description supersession | ✅ re-ingest succeeds |
| 10 | N decisions share a symbol | ✅ both surface |
| 11 | No server-side BM25 grounding (post-v0.6.0) | ✅ stays ungrounded |
| 12 | Line-shift edit | ✅ no spurious drift (`resolve_symbol_lines` self-heals) |
| 13 | `[Open Question]` prefix | ✅ ingested as gap |

### Changed — `handlers/link_commit._build_verification_instruction()`

Splits the v0.6.4 monolithic `_VERIFICATION_INSTRUCTION` into three
composable parts so the response text is conditional on which
`pending_*` payloads actually fired:

- `pending_compliance_checks` present → resolve_compliance CTA.
- `pending_grounding_checks` with `reason='ungrounded'` →
  `Grep/Read → validate_symbols / extract_symbols → bicameral.bind` CTA
  (safe — no prior binding to retire, no duplicate-binding risk).
- `pending_grounding_checks` with `reason='symbol_disappeared'` →
  **explicit "INFORMATIONAL ONLY — do NOT call bicameral.bind on the
  new location" warning** citing the duplicate-binding hazard under
  the N:N `binds_to` relation. Atomic rebind ships in V2.

Addresses Codex pass-10 #2 + pass-12 #2: the v0.6.4 monolithic CTA
inadvertently routed relocation cases through the unsafe bind path.
The V1 split removes that without reducing the safe CTA for ungrounded.

### Fixed — `ledger/adapter.py` ungrounded grounding-check `decision_id`

`pending_grounding_checks` for ungrounded decisions emitted empty
`decision_id` because the consumer read `d.get("id", "")` from
`get_all_decisions(filter="ungrounded")`, but that query aliases the
field to `decision_id`. Callers had no handle to bind against.
Surfaced by V1 F1 regression coverage; existing
`test_pending_grounding_checks_for_ungrounded_decisions` regression
only asserted `len > 0`, missing the empty-ID bug. Read `decision_id`
first, fall back to `id` for forward compatibility.

### Tests

75 passed, 1 xfailed in 7.11s after V1. Zero regressions on the
v0.6.3/v0.6.4/0.6.4-bump rebase, and the SDK-2.0 idempotency-catch
issue I had originally pinned around (`surrealdb<2.0.0`) is fixed
properly upstream by `66796ef`, so V1 ships against
`surrealdb>=2.0.0` directly.

### Deferred to V2

Captured in full in `docs/v2-desync-optimization-guide.md` (design target with
nine rounds of Codex review) and summarized in
`docs/v2-desync-optimization-guide.md` §4–§5:

- A0 — atomic SurrealQL block primitive (Python SDK doesn't support
  `begin_transaction()` in embedded mode).
- A2a — full sync barrier (sync-token CAS + region fingerprint at
  commit time).
- C0 / C0a / C1 — schema migration v5→v6 (per-binding baseline
  ownership, tombstone fields, append-only `compliance_verdict_history`,
  full-CAS cache key, traversal filtering).
- C2 — `bicameral_judge_drift` + `record_compliance_verdict` with
  five-field CAS (incl. binding-state token).
- C3 — `pending_compliance_checks` from `detect_drift` (cache-aware).
- B3 — `bicameral_advance_baseline` (only after fresh L3 `compliant`
  verdict matching full CAS).
- D2 — `bicameral_rebind` with old-binding CAS; closes scenario 8.
- Migration of the `handlers/resolve_compliance.py` hard-delete +
  `handlers/ingest.py` auto-chained `handle_judge_gaps` to the
  tombstone + CAS contract — hard prerequisite before V2 destructive
  work ships.

## 0.7.0 — 2026-04-24 — Accountable North Star: proposal state + signoff schema

Every decision now has provenance: who proposed it, who ratified it, in which session.

### Changed

- **`product_signoff` → `signoff`** — field rename across the full stack (schema,
  queries, contracts, handlers). New tagged shape: `{state:'proposed'|'ratified', ...}`.
- **Default-to-proposed**: `bicameral_ingest` writes
  `signoff = {state:'proposed', session_id, created_at}` by default.
  Proposed decisions are **drift-exempt** — they never enter `drifted` or `reflected`
  until explicitly ratified.
- **`bicameral_ratify`** now promotes `proposed → ratified`, capturing `signer`,
  `session_id`, `ratified_at`, and optional `note`.
- **Schema v5 → v6**: migration copies historical `product_signoff` → `signoff`
  with `{state:'ratified'}` for records with bindings; new ingests default to `proposed`.
- **Session-start banner** extended: surfaces stale proposals (>14 days) alongside
  drifted decisions. `SessionStartBanner` gains `proposal_count` and
  `stale_proposal_count`.
- **New status `'proposal'`** added to the `status` ASSERT constraint and all
  `Literal[...]` annotations in contracts.
- **`context.BicameralContext`** gains `session_id` (UUID generated once per
  server process) for audit trails on signoff objects.
- **Jacob regression suite** (`tests/test_alpha_flow.py`) — 5 invariants + v0.7
  proposal test gate the Wednesday ship.

### Gating note
The drift-exemption for proposals is gated on a drift precision eval harness
(≥90% precision on `drifted` verdicts). See `thoughts/shared/plans/2026-04-24-beta-north-star-wednesday.md`.

## 0.6.4 — 2026-04-23 — nuke `search_code`, caller-LLM owns all code retrieval

**Architecture shift**: the MCP server no longer performs any code search.
The `search_code` tool and its BM25 + vector + RRF fusion stack are deleted.
Callers (Claude Code, Cursor, any MCP client) resolve code regions using
their native tools (Grep, Read, Glob) and hand file paths or symbols back
to the server via `bicameral.bind` and the new `file_paths` arg on
`bicameral.preflight`.

Division of labor, made legible:
- **Server owns**: ledger, graph, drift math, gating. Deterministic facts only.
- **Caller owns**: deciding what files matter for a task. Probabilistic scoping.

### Removed

- `search_code` MCP tool + handler dispatch in `server.py`.
- `code_locator/tools/search_code.py`, `code_locator/fusion/rrf.py`
  and the `fusion/` package.
- `code_locator/retrieval/bm25_protocol.py`, `bm25s_client.py`,
  `sqlite_vec_client.py`, and the `retrieval/` package.
- BM25 index build step in `code_locator_runtime.rebuild_index` — startup
  is faster and the `.bicameral/` footprint smaller.
- `bm25s` and `sqlite-vec` runtime/extra deps from `pyproject.toml`.
- Config knobs: `bm25_backend`, `bm25_k1`, `bm25_b`, `rrf_k`,
  `max_retrieval_results`, `channel_weights`, `vector_enabled`, `vector_model`.
- `search_hint` field on `IngestMapping` + `IngestDecision` — it only existed
  to widen BM25 queries; with BM25 gone it's dead weight.
- `tests/eval_code_locator.py` — the eval harness was exclusively for
  `search_code` recall.

### Changed — `bicameral.preflight` accepts `file_paths`

```
bicameral.preflight(
  topic="<1-line topic>",
  file_paths=["<repo-relative path>", ...],   # NEW — optional
  participants=[...],
)
```

When the caller supplies `file_paths`, the server returns decisions pinned
to exactly those files (region-anchored, high precision). When `file_paths`
is omitted, preflight falls back to the ledger keyword search — existing
callers that only pass `topic` keep working and still surface drifted /
ungrounded decisions whose descriptions match the topic.

### Changed — `CodeIntelligencePort` reduced to deterministic primitives

```python
class CodeIntelligencePort(Protocol):
    def validate_symbols(self, candidates: list[str]) -> list[dict]: ...
    async def extract_symbols(self, file_path: str) -> list[dict]: ...
    def get_neighbors(self, symbol_id: int) -> list[dict]: ...
```

No `search_code` method. `RealCodeLocatorAdapter` no longer instantiates
`Bm25sClient` or `SqliteVecClient` — it only loads the tree-sitter symbol
index and the structural graph.

### Changed — skills

- `bicameral-ingest` SKILL.md rewrite: grounding procedure is now "use
  Grep/Read/Glob + validate_symbols, then pass explicit `code_regions`".
  All BM25/RRF/search_hint guidance removed.
- `bicameral-preflight` SKILL.md: teaches the new `file_paths` argument.
- `test_v0417_jargon_hygiene`: BM25/RRF exception for bicameral-ingest
  removed — no skill should mention backend retrieval jargon now.

### Migration notes

No breaking change for existing `bicameral.preflight(topic=...)` callers.
`IngestMapping.search_hint` is removed — callers that were passing it
get a Pydantic `extra fields` silent drop (tolerant by default) or an
error if `extra="forbid"` is configured. Real fix: stop passing it.

---

## 0.6.1 — 2026-04-23 — session-start drift banner + ledger catch-up middleware

**Reliability fix**: two persistent desync gaps (G1 — hook unreliability, G3 — cross-session surfacing) are closed.

### Added — `handlers/sync_middleware.py`

New middleware module with two entry points:

- `ensure_ledger_synced(ctx)` — called by `preflight` and `history`. Compares live HEAD SHA against `_sync_state.last_sync_sha`; if diverged, runs `link_commit(HEAD)` before returning the session-start banner. Guarantees ledger is caught up on every call regardless of whether the PostToolUse hook fired.

- `get_session_start_banner(ctx)` — called by `search_decisions` (which already runs `link_commit` itself). Queries all drifted decisions once per MCP server session and returns a `SessionStartBanner`. Fires exactly once: the first MCP call of each session. Subsequent calls return `None` without touching the DB.

Both functions swallow all exceptions (fail-open: bicameral is never the reason a tool call fails).

### Added — `SessionStartBanner` contract

New Pydantic model in `contracts.py`:
```python
class SessionStartBanner(BaseModel):
    drifted_count: int
    items: list[dict]   # {decision_id, description, source_ref}
    message: str
```

Added as `session_start_banner: SessionStartBanner | None = None` to `SearchDecisionsResponse`, `PreflightResponse`, and `HistoryResponse`.

### Added — `ledger/adapter.py`: `get_decisions_by_status`

New adapter method that queries all decisions matching a list of status values. Used by the session-start banner to surface drifted decisions at session open.

### Changed — preflight sync path simplified

Replaced inline try/except HEAD catch-up block in `preflight.py` with a single `ensure_ledger_synced(ctx)` call. Identical behavior, shared with `history.py`.

### Changed — skill rendering (`bicameral-preflight/SKILL.md`)

Section 2.5 added: when `response.session_start_banner` is non-null, render the drifted-decision list unconditionally — even when `fired=false`. The session-start banner is not gated by the preflight topic gate.

### Tests

9 new unit tests in `tests/test_sync_middleware.py` covering banner once-per-session semantics, exception swallowing, dedup, and ledger catch-up logic.

## 0.6.0 — 2026-04-23 — caller-LLM binding flow (`bicameral_bind`)

**Architecture shift**: server-side BM25 auto-grounding is removed. The
caller LLM now discovers code regions and writes bindings explicitly via
the new `bicameral_bind` tool. This eliminates the hallucinated-grounding
problem: every `binds_to` edge is authored by the same LLM that verified
semantic fit, not by a keyword search.

### Added — `bicameral_bind` tool

New MCP tool: `bicameral_bind(bindings: list[{decision_id, file_path, symbol_name, start_line?, end_line?, purpose?}])`.

- Resolves symbol line range via tree-sitter when `start_line`/`end_line` not supplied
- Upserts `code_region` + `binds_to` edge in the ledger
- Transitions decision status `ungrounded → pending`
- Returns `BindResponse` with per-binding results (region_id, content_hash, error)
- Idempotent: re-binding the same (decision, symbol) is a no-op

### Removed — server-side auto-grounding pipeline

`ground_mappings()` and the vocab-cache layer (~375 LOC) are deleted.
Removed tests: `test_coverage_loop`, `test_vocab_cache`,
`test_fc1_bm25_degeneracy`, `test_fc3_vocab_cache_similarity`,
`test_v0423_search_hint`, `test_fc2_multi_region_grounding`.

Net diff: –2317 LOC (197 added, 2514 removed).

### Changed — `IngestResponse` and `LinkCommitResponse`

- `IngestResponse.ungrounded_decisions: list[str]` →
  `pending_grounding_decisions: list[dict]` (each entry: `{decision_id, description}`)
- `LinkCommitResponse` now carries `pending_grounding_checks: list[dict]`
  (ungrounded decisions + regions where symbol disappeared at current ref)
- `ingest_commit()` no longer calls `_reground_ungrounded()` — grounding
  is the caller's responsibility via `bicameral_bind`

### Changed — schema v4 → v5 (migration)

Migration v4→v5 cleans up stale `source_span`/`intent` edges from the
`yields` table and applies `UNIQUE(in, out)` index. Fixes a startup error
on DBs that went through v3→v4 with residual edges.

## 0.5.0 — 2026-04-20 — decision tier refactor + stop-and-ask primitives

**BREAKING — atomic clean-break migration.** Schema v3 → v4. Legacy tables
(`intent`, `source_span`, `maps_to`, `implements`) are dropped on upgrade.
Re-ingest from sources via `bicameral.ingest`; run `bicameral.reset` to
see the source_cursor replay plan. Pre-release has zero external
integrators; dev-DB content is replayable from source transcripts.

### Changed — decision tier rename (intent → decision)

Every caller-facing `intent` / `intent_id` is renamed to `decision` /
`decision_id`. Field names, handler parameters, Pydantic contracts,
SKILL.md prose, and tool docstrings all use "decision" consistently.
No translation cost for new readers.

- `intent` table → `decision` (with new `product_signoff` field)
- `source_span` table → `input_span` (verbatim text required, no DEFAULT)
- `maps_to` + `implements` edges removed
- New edges: `yields (input_span → decision)`, `binds_to (decision → code_region)`,
  `locates (symbol → code_region)` — retrieval tier
- `compliance_check.intent_id` → `decision_id`; `compliant: bool` →
  `verdict: string` (three-way enum), plus new `pruned` flag

### Changed — three-way compliance verdicts + holistic status projection

`ComplianceVerdict.compliant: bool` is replaced by
`verdict: Literal["compliant", "drifted", "not_relevant"]`.

- `compliant` — keep `binds_to` edge, write cache row
- `drifted` — keep `binds_to` edge, write cache row (persistent drift signal)
- `not_relevant` — DELETE the `binds_to` edge (retrieval mistake, not drift);
  write cache row with `pruned=true` for audit trail

Decision status is now projected holistically via
`project_decision_status(decision_id)` after every batch — aggregates
verdicts across all bound regions. DRIFTED always wins; REFLECTED requires
every relevant region to be `compliant`. Closes the v0.4.x last-verdict-wins
caveat.

### Added — `bicameral.ratify` tool (double-entry ledger)

New one-shot idempotent MCP tool: `bicameral.ratify(decision_id, signer, note)`.
Sets `decision.product_signoff = {signer, timestamp, source_commit_ref, note}`.
Supports the double-entry model: `product_signoff` is stored (PM axis);
`eng_reflected` is derived from `compliance_check` aggregation (engineering
axis). Rescinding signoff requires a new decision that supersedes the
previous one — keeps the audit trail clean.

### Added — stop-and-ask primitives (skill-side)

Three skills now classify findings as `mechanical` (auto-resolve silently)
or `ask` (emit one question). Per-skill caps keep the user from being
buried:

- **bicameral-preflight** — sequential per-category (drift → divergence →
  uningested_corrections → open questions → ungrounded), max 1 question
  per category, hard cap 4. Preflight now also scans the last ~10 user
  turns for uningested corrections (regex pre-filter → LLM classify →
  ledger cross-check), auto-ingesting mechanical clarifications and
  surfacing load-bearing ones for user approval.
- **bicameral-ingest** — premise gate on `IngestResponse.supersession_candidates`
  (new field surfaced by server BM25 overlap against existing decisions);
  max 3 questions, remainder → batched final approval gate
- **bicameral-judge-gaps** — ambiguity gate, max 3 questions, remainder →
  batched final approval gate

Advisory-mode override: with `BICAMERAL_GUIDED_MODE=0`, questions render
as informational notes (non-blocking).

**Deferred to v0.5.1:** SessionEnd hook for terminal-correction case
(user corrects then exits without another code verb). Preflight covers
~80% of cases; closing the tail needs cost-optimized `claude -p`
headless invocation which we want dogfood data on first.

### Migration notes

- Run `init_schema` (idempotent). The `_migrate_v3_to_v4` step drops legacy
  v3 tables, drops `compliance_check` (had `intent_id`), and recreates v4
  tables with `decision_id` + `verdict` + `pruned`.
- `source_cursor` rows survive the cutover — users can re-run their
  original ingests against the new schema.
- Any external caller code referencing `intent_id`, `ungrounded_intents`,
  or `ComplianceVerdict.compliant: bool` must be updated.

## 0.4.23 — 2026-04-21 — caller-LLM-driven retrieval + search_hint recall booster

Addresses the BM25 vocab-mismatch problem that surfaced after v0.4.20
made grounding status honest. Decisions whose natural-language
description doesn't lexically overlap with the real code identifier
vocabulary were getting bound to whatever file incidentally shared a
keyword — "email dispatch" binding to a React toast reducer's
`dispatch`, "active subscriber" binding to an unrelated `AcquisitionFunnel.tsx`
`ActiveUser` component. Under v0.4.19's silent auto-promotion nobody
saw this; under v0.4.20's honest PENDING projection users saw garbage
bindings and had nothing to do about them.

Two changes, both within the existing deterministic-retrieval moat:

### Changed — caller-LLM retrieval is now the default (Lever 1)

- `skills/bicameral-ingest/SKILL.md` restructured. Step 2 is now
  *"Resolve code regions via the MCP retrieval tools"* — caller LLM is
  instructed to use `validate_symbols` + `search_code` + `get_neighbors`
  to build explicit `code_regions` from codebase evidence *before*
  ingesting. Step 3 now leads with the internal format (with explicit
  regions) as the preferred shape. Natural format remains supported
  as the fallback for abstract decisions with no resolvable code surface.
- No server-side code changes. The server already accepted internal-
  format ingest payloads; this flips the skill's default guidance from
  *"use natural format, let BM25 handle it"* to *"resolve explicitly,
  fall back to BM25 only when necessary."*

### Added — `search_hint` recall booster (Lever 2)

- `IngestMapping.search_hint: str` and `IngestDecision.search_hint: str`
  — optional caller-supplied field carrying synonyms / domain vocab /
  likely identifier names that the decision's description wouldn't
  contain literally. Used only when the mapping falls through to
  server-side auto-grounding.
- `adapters.code_locator.ground_mappings` concatenates
  `description + " " + search_hint` as the BM25 query when the hint is
  non-empty. Strictly additive: omitted hint = pre-v0.4.23 behavior.
- `search_hint` is query-only metadata. It is never stored on
  `intent.description` and never surfaces in briefs, status responses,
  or the gap-judge context pack. Humans see the clean decision text;
  BM25 sees the widened query.

### Guarantee preserved — no server-side LLM

Retrieval remains deterministic at runtime. The caller LLM does the
expensive lookup at ingest time (when it has your full codebase
context), writes explicit `code_regions`, and the server's BM25 fallback
is only consulted for truly abstract decisions. This keeps the tech
moat (*deterministic, provider-agnostic retrieval*) intact while
fixing the quality complaint.

### Upgrade notes

- **Existing bindings from pre-v0.4.23 ingests are unchanged.** If you
  have false-positive bindings from BM25 auto-grounding (e.g., dispatch
  intents bound to `use-toast.ts`), they persist in the graph. To clean
  them up today: `bicameral.reset` → re-ingest under the new skill
  defaults. A targeted edge-pruning path is tracked for a future release.
- **No schema change**, no migration, no behavior shift for running
  callers — the skill update only changes the default path the caller
  LLM takes when the bicameral-ingest skill is invoked.

## 0.4.22 — 2026-04-20 — hotfix: init_schema idempotent against existing persistent DB

**Hotfix for v0.4.20 regression on persistent DBs.** Phase 1b made
`LedgerClient.execute()` raise on SurrealDB error strings instead of
silently discarding them. That surfaced three latent bugs we fixed in
v0.4.20 (`_migrate_v1_to_v2` field redefine, edge-helper UNIQUE
violations, the `UPDATE $rid` SQL error) but missed `init_schema()`
itself, which runs `DEFINE ANALYZER` / `DEFINE TABLE` / etc. on every
MCP server connect.

On `memory://` (test) DBs this never triggered because each connection
starts fresh. On `surrealkv://` (the default persistent DB under
`~/.bicameral/ledger.db`) the second-and-subsequent connect raised
`LedgerError: "The analyzer 'biz_analyzer' already exists"`, which
broke every MCP tool call after the server's first start — including
`bicameral.reset`, the very tool users would reach for to recover.

### Fixed

- `init_schema()` now tolerates "already exists" rejections on every
  DDL statement via a new `_execute_define_idempotent` helper. Other
  error classes (malformed DDL, permission failures) still surface as
  `LedgerError` so real bugs don't get masked. Matches the pattern
  we already use for `_migrate_v1_to_v2` and the edge helpers.

### Added

- `tests/test_compliance_check_schema.py::test_init_schema_is_idempotent_against_existing_db`
  — regression test: runs `init_schema()` three times in a row and
  verifies schema still works. Would have caught the v0.4.20 issue
  if it had been there.

### Users upgrading from 0.4.20/0.4.21

- **Recommended.** The only user-visible change is that the MCP server
  stops crashing on startup against a persistent DB. No schema change,
  no data migration, no behavior shift. Verification semantics
  (PENDING-by-default, caller-LLM resolves via `bicameral.resolve_compliance`)
  are unchanged from 0.4.21.

## 0.4.21 — 2026-04-20 — bicameral.resolve_compliance (caller-LLM verdict write-back)

Closes the end-to-end verification loop v0.4.20 opened. The drift sweep
emits `pending_compliance_checks`; the caller LLM evaluates them; this
new tool writes the verdicts back. Status flips from PENDING to
REFLECTED (or DRIFTED) on the next read.

### Added — `bicameral.resolve_compliance` MCP tool

- `bicameral.resolve_compliance(phase, verdicts[], commit_hash?)` —
  the single caller-LLM verification write-back tool. One tool for
  every verification flow (ingest, drift, regrounding, supersession,
  divergence) — phase is a routing label, not a tool discriminator.
- Verdict shape: `{intent_id, region_id, content_hash, compliant,
  confidence, explanation}`. `content_hash` is echoed verbatim from the
  `PendingComplianceCheck` the caller is resolving, so the cache row
  lands keyed on the exact code shape that was evaluated.
- Idempotent via the UNIQUE cache-key index — replaying the same batch
  is a silent no-op.
- Structured rejections for unknown intent / region IDs (returned, not
  raised), so callers can retry the accepted subset without losing work.

### Behavior — status becomes achievable again

Post-v0.4.20 everything projected as PENDING because the cache was
empty and there was no way to populate it. With `resolve_compliance`
shipped, the caller-LLM → server → cache loop is closed. Status
transitions users now see:

- PENDING → REFLECTED — compliant verdict for the current code shape.
- PENDING → DRIFTED — non-compliant verdict (the caller rejected the
  candidate).
- DRIFTED → PENDING — code changed; cache miss re-emits a pending check.
- PENDING → PENDING — verdict unavailable (caller hasn't responded yet).

### Known caveat — multi-region aggregation

When an intent has multiple regions, `resolve_compliance` writes
`intent.status` with last-verdict-wins within a batch. Correct
aggregation (any-uncompliant-drifts-the-intent) still requires the
drift-sweep loop across all regions, which only runs when HEAD advances.
Tracked as a follow-up — most current decisions are single-region, so
this is rarely user-visible.

## 0.4.20 — 2026-04-20 — unified compliance verification (cache-aware status, pending checks)

The grounding pipeline used to silently promote BM25 candidates to
REFLECTED whenever the content hash matched. That confused retrieval
with verification — a high-keyword-overlap match against divergent
code looked indistinguishable from a verified one, and `doctor` was
unable to call out the difference.

This release introduces the foundation of the unified compliance
verification plan
([thoughts/shared/plans/2026-04-20-ingest-time-verification.md](thoughts/shared/plans/2026-04-20-ingest-time-verification.md)).
REFLECTED is now earned by a caller-LLM verdict cached in the new
`compliance_check` table, keyed on `(intent_id, region_id, content_hash)`.
The drift sweep emits a batched `pending_compliance_checks` payload for
every unverified shape; the caller LLM evaluates and (in 0.4.21+) writes
back via `bicameral.resolve_compliance`.

### Behavior change — visible to existing users

After upgrading, decisions that previously projected as REFLECTED
(via hash-only inference) now project as PENDING. There is no data
loss — every `maps_to` edge stays — but status reflects honest state:
"grounded but not verified." Once `bicameral.resolve_compliance` ships
in 0.4.21, the caller LLM resolves the pending batch and verified
decisions return to REFLECTED. Until then, expect a one-time
"everything is pending" baseline. This is the intended migration.

### Added — `compliance_check` cache

- New table `compliance_check` (schema v3) with UNIQUE
  `(intent_id, region_id, content_hash)` cache key. Reads project
  status from this cache without LLM calls. Phase enum reserves
  `ingest`, `drift`, `regrounding`, `supersession`, `divergence`
  upfront so future flows don't need a schema migration.
- `ledger.queries.get_compliance_verdict()` — the cache lookup.
- `ledger.status.derive_status(stored_hash, actual_hash, cached_verdict)`
  — extended signature; verdict-aware projection.
- `LinkCommitResponse.pending_compliance_checks` — batched verification
  jobs from the drift sweep, propagated through every consumer of the
  auto-chain (search, doctor, scan_branch, ingest).
- `IngestResponse.sync_status` — the post-ingest LinkCommitResponse,
  including pending_compliance_checks.

### Added — honest ledger client

- `ledger.client.LedgerError` — raised by `LedgerClient.execute()`
  and `.query()` when SurrealDB returns an error string. Replaces
  the silent-discard behavior that previously masked UNIQUE
  violations, ASSERT failures, and malformed queries.
- `ledger.queries._execute_idempotent_edge` — explicit helper that
  catches "already contains" UNIQUE-violation errors as success for
  edge-creation paths (`relate_maps_to`, `relate_implements`,
  `relate_yields`). Idempotency for team-mode event replay is now
  visible at the call site instead of hidden in the client.
- `_migrate_v1_to_v2` now tolerates the redundant `DEFINE FIELD`
  rejection on fresh DBs.

### Fixed

- `adapter.py` line-number update after tree-sitter symbol re-resolution
  was running `UPDATE $rid SET ...` with `$rid` bound to a string —
  SurrealDB v2 rejects this. The error was silently discarded under the
  old client behavior, so symbol renames and line-shifts never actually
  persisted to the ledger. Switched to inline `UPDATE {region_id} SET ...`
  matching the existing edge-helper pattern.

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
  walkthrough (see `thoughts/shared/plans/2026-04-14-beta-drift-demo.md`).

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
