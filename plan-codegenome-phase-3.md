# Plan: CodeGenome Phase 3 (Issue #60) — Continuity Evaluation in `link_commit`

Closes the **M5 (Status Trustworthiness)** gap: when a function moves
or is renamed, `link_commit` consults the stored `subject_identity`
records (written by Phase 1+2) before emitting a `PendingComplianceCheck`,
auto-resolves to `identity_moved` / `identity_renamed` when the
match confidence is high, and updates the `binds_to` edge to point at
the new `code_region`.

**Branch**: `claude/codegenome-phase-3-qor` off
`claude/codegenome-phase-1-2-qor` (`6865b4c`).
**PR target**: `BicameralAI/bicameral-mcp`. Stacked on PR #71; retarget to
`main` after #71 merges.
**Schema**: `SCHEMA_VERSION` 11 → 12 (additive — one new edge,
one new field on `subject_identity`).
**Default behavior**: zero change unless `BICAMERAL_CODEGENOME_ENABLED=1`
*and* `BICAMERAL_CODEGENOME_ENHANCE_DRIFT=1`. Both flags already declared
in #59's `CodeGenomeConfig`.

---

## Open Questions

1. **`SCHEMA_COMPATIBILITY[12]` value** — `0.12.0` placeholder shipped by
   the plan. Release-engineering pins the final value at PR merge.
   (Same convention as #59's `[11]` entry.)
2. **M5 fixture corpus location** — issue mandates fixtures for
   function-move, function-rename, logic-removal, class-extracted-to-two-modules.
   Plan creates `tests/fixtures/codegenome_m5/` with synthesized
   before/after pairs. If upstream prefers a different location (e.g.
   `tests/fixtures/m5/` to match an existing benchmark corpus convention),
   PR-review will move them.
3. **Performance budget** — issue states "<200ms added to typical
   `link_commit`". Plan caps continuity evaluation at **top-20 candidates
   per drifted region**, with same-symbol-kind pre-filter applied first.
   If empirical p95 exceeds budget on the benchmark corpus, the cap drops
   to 10 in a follow-up; not a #60 blocker.

---

## Architecture Decisions (locked)

| Decision | Choice |
|---|---|
| Module placement | `codegenome/continuity.py` matches Phase-1+2 flat layout. |
| Composition | Handler-orchestrated. `handlers/link_commit.py` calls `codegenome.continuity.find_continuity_match`; matcher is a pure function over (identity, code_locator, repo_ref). |
| `binds_to` mutation | Delete-and-create (single active bind per `(decision, code_region)`); audit trail comes from the new `identity_supersedes` edge between old and new `subject_identity` rows + the `subject_version` row written for the new location. |
| Neighbor signal source | Extend `subject_identity` with `neighbors_at_bind: option<array<string>>`; `compute_identity_with_neighbors(...)` wraps `compute_identity(...)` and adds the neighbors. Existing rows have `None`; matcher gracefully degrades (zero contribution from the Jaccard signal). |
| Threshold semantics | ≥ 0.75 → auto-resolve as `identity_moved` / `identity_renamed`; 0.50–0.75 → `needs_review` (caller LLM picks); < 0.50 → fall through to existing `PendingComplianceCheck`. |
| Failure mode | Continuity-evaluation exceptions are caught and logged; the bind handler falls through to existing PendingComplianceCheck behavior. The `LinkCommitResponse` contract is unchanged. |
| Fuzzy matching | `rapidfuzz.fuzz.ratio` (existing dep, used by `code_locator/tools/validate_symbols.py`). Threshold ≥ 0.80 per issue spec. |
| Anti-goal Q2 | **Strict #60 only.** No groundwork for #61 (`semantic_status`/`evidence_refs` on `compliance_check` are #61-owned). |

---

## CI / validation commands

```bash
# Phase-3-only fast loop (target while iterating)
python -m pytest tests/test_codegenome_continuity.py tests/test_codegenome_link_commit_integration.py -v

# Phase 2/3 markers (touches existing link_commit + bind tests)
python -m pytest -m phase2 -v

# M5 benchmark suite (fixtures + thresholds)
python -m pytest tests/test_m5_benchmark.py -v

# Full suite (regression check before commit)
python -m pytest tests/ -v
```

`pytest.ini` markers already declared in upstream; no new markers introduced.

---

## Phase 1 — `compute_identity_with_neighbors` + schema v11 → v12

Extends Phase-1+2's identity record with neighbor data so the matcher
has both pre- and post-rebase neighbor sets. Schema migration is
additive only.

### Unit tests (TDD — written first)

- `tests/test_codegenome_adapter.py` (extension):
  - `compute_identity_with_neighbors` returns identity with `neighbors_at_bind` populated when `code_locator` supplies neighbors.
  - `compute_identity_with_neighbors` falls back to `neighbors_at_bind = ()` when `code_locator` is `None`.
  - Existing `compute_identity` signature unchanged (back-compat via wrapper).
- `tests/test_codegenome_bind_integration.py` (extension):
  - Bind with `enabled=True, write_identity_records=True` writes `neighbors_at_bind` non-empty (when stub locator returns neighbors).
  - `find_subject_identities_for_decision` returns `neighbors_at_bind` field.

### Affected files

- `codegenome/adapter.py` — add `neighbors_at_bind: tuple[str, ...] | None = None` to `SubjectIdentity` dataclass (frozen — wrap with tuple, not list, for hashability).
- `codegenome/deterministic_adapter.py` — add `compute_identity_with_neighbors(file_path, start_line, end_line, *, code_locator, repo_ref="HEAD")` method. Calls existing `compute_identity` then queries `code_locator.get_neighbors(symbol_id)` for the resolved symbol and stores their addresses. `compute_identity` (original) unchanged.
- `codegenome/bind_service.py` — `write_codegenome_identity` accepts an optional `code_locator` arg; when present, calls `compute_identity_with_neighbors` instead of `compute_identity`. Default `None` keeps Phase-1+2 callers working.
- `handlers/bind.py` — pass `ctx.code_graph` (the `RealCodeLocatorAdapter`) to `write_codegenome_identity`.
- `ledger/schema.py` — `SCHEMA_VERSION = 12`; new entry `12: "0.12.0"` in `SCHEMA_COMPATIBILITY`; add `neighbors_at_bind` field to `subject_identity` table; add `_migrate_v11_to_v12`.
- `ledger/queries.py` — extend `upsert_subject_identity` `**kwargs` to accept and persist `neighbors_at_bind` (validate as `array<string>`); extend `find_subject_identities_for_decision` SELECT clause to return the field.

### Schema additions (deterministic_v2 — neighbor-aware)

```text
subject_identity.neighbors_at_bind  option<array<string>>   # symbol addresses, sorted
```

Migration writes nothing to existing rows; `neighbors_at_bind` stays
`None` for Phase-1+2-era identities. The matcher in Phase 2 of *this*
plan treats `None` as "no signal" (Jaccard contribution = 0; remaining
weights still sum to a defensible total via the `weighted_average`
helper from #59).

---

## Phase 2 — Continuity matcher + ledger writes

Pure-function matcher and the ledger queries that record relocation
outcomes. No `link_commit` integration yet.

### Unit tests (TDD — written first)

- `tests/test_codegenome_continuity.py`:
  - Exact-name match in different file → confidence ≥ 0.75, `change_type = "moved"`.
  - Renamed in same file (fuzz ≥ 0.80) → confidence ≥ 0.50, < 0.75, `change_type = "renamed"`.
  - Renamed *and* moved (exact-name fail, fuzz ≥ 0.80, kind match, neighbor Jaccard ≥ 0.5) → confidence ≥ 0.75, `change_type = "moved_and_renamed"`.
  - No candidate above threshold → `find_continuity_match` returns `None`.
  - Threshold edge: confidence exactly 0.50 returns `None` (strict greater-than for "needs_review" floor); 0.75 returns `change_type` (auto-resolve).
  - **#60 constraint**: candidate cap honored — passing 50 candidates results in only top-20 scored.
  - Empty `neighbors_at_bind` (Phase-1+2 row) — matcher computes without the Jaccard signal; weights renormalize.
  - `score_continuity` is a pure function (no side effects, deterministic).
- `tests/test_codegenome_continuity_ledger.py`:
  - `update_binds_to_region(decision_id, old_region_id, new_region_id)` deletes the old `binds_to` edge and creates a new one with `provenance.method = "continuity_resolved"`.
  - `write_identity_supersedes(old_id, new_id, change_type, confidence, evidence_refs)` creates an `identity_supersedes` edge. Idempotent.
  - `write_subject_version(code_subject_id, repo_ref, file_path, start_line, end_line, ...)` upserts a `subject_version` row keyed on `(repo_ref, file_path, start_line, end_line)`. Returns the row id.
  - `relate_has_version(code_subject_id, subject_version_id, confidence=0.9)` creates a `has_version` edge from `code_subject` to `subject_version`. Idempotent (UNIQUE(in, out)). Mirrors `relate_has_identity` from #59.

### Affected files

- `codegenome/continuity.py` — new module:
  - `ContinuityMatch` frozen dataclass with `new_file_path`, `new_start_line`, `new_end_line`, `new_symbol_name`, `new_symbol_kind`, `confidence`, `change_type` (`Literal["moved", "renamed", "moved_and_renamed"]`).
  - `_normalize_name(s: str) -> str` — lowercase + strip surrounding underscores.
  - `_jaccard(a: Iterable[str], b: Iterable[str]) -> float` — pure, returns 0.0 for both-empty.
  - `score_continuity(old_identity, candidate, *, fuzzy_threshold=0.80) -> tuple[float, str]` — returns (confidence, change_type). Uses `weighted_average` from `codegenome.confidence` with weights `{exact_name: 0.40, fuzzy_name: 0.20, kind: 0.20, neighbors: 0.20}`. `change_type` derived from which signals fired (exact_name fail + fuzzy pass → renamed; file changed → moved; both → moved_and_renamed).
  - `find_continuity_match(identity, code_locator, *, candidate_cap=20, threshold=0.75) -> ContinuityMatch | None` — orchestrates: code_locator narrowing (symbol kind + fuzzy name) → top-N → score each → pick max → threshold gate.
- `ledger/schema.py` — add `identity_supersedes` edge table:
  - `RELATION IN subject_identity OUT subject_identity`
  - `change_type: string` (`"moved" | "renamed" | "moved_and_renamed"`)
  - `confidence: float [0,1]`
  - `evidence_refs: array<string> DEFAULT []`
  - `created_at: datetime DEFAULT time::now()`
  - `UNIQUE(in, out)`
  - Migration entry in `_migrate_v11_to_v12`.
- `ledger/queries.py` — four new functions, all using `_validated_record_id`:
  - `update_binds_to_region(client, decision_id, old_region_id, new_region_id, *, confidence=0.85)` — delete old edge, create new with `provenance.method = "continuity_resolved"`.
  - `write_identity_supersedes(client, old_identity_id, new_identity_id, change_type, confidence, evidence_refs=())` — idempotent RELATE on `identity_supersedes`.
  - `write_subject_version(client, code_subject_id, repo_ref, file_path, start_line, end_line, *, symbol_name=None, symbol_kind=None, content_hash=None, signature_hash=None) -> str` — upsert keyed on `(repo_ref, file_path, start_line, end_line)`.
  - `relate_has_version(client, code_subject_id, subject_version_id, confidence=0.9)` — idempotent RELATE on `has_version` (the edge defined-but-unused in #59 schema; Phase 3 wires it). Mirrors `relate_has_identity` exactly.
- `ledger/adapter.py` — four thin wrapper methods on `SurrealDBLedgerAdapter` mirroring the queries.

---

## Phase 3 — `link_commit` integration + `LinkCommitResponse` extension

Wires the matcher into the drift-detection seam in `handlers/link_commit.py`.
Behavior gated by `enhance_drift=True`.

### Unit + integration tests (TDD — written first)

- `tests/test_codegenome_link_commit_integration.py`:
  - **flag off**: `LinkCommitResponse` shape unchanged; no calls to `find_continuity_match`. Existing `PendingComplianceCheck` flow runs.
  - **flag on, exact-name match in new file**: `continuity_resolutions` non-empty with `semantic_status="identity_moved"`, `confidence ≥ 0.75`. After resolution, the ledger contains: (a) two `code_region` rows (old + new); (b) two `subject_identity` rows linked by `identity_supersedes`; (c) a new `subject_version` row reachable from the parent `code_subject` via `has_version`; (d) one active `binds_to` edge pointing at the new region (old edge deleted). No `PendingComplianceCheck` for this region.
  - **flag on, renamed in same file**: same prerequisite-row assertions as above; `continuity_resolutions` with `semantic_status="identity_renamed"`.
  - **flag on, candidate confidence 0.50–0.75**: `semantic_status="needs_review"`, `new_code_region_id is None`, `new_location is None`, `PendingComplianceCheck` still emitted with `pre_classification` hint.
  - **flag on, no candidate above 0.50**: existing `PendingComplianceCheck` flow, `continuity_resolutions` empty.
  - **flag on, identity-supersedes idempotency**: re-running `link_commit` on the same drift produces no duplicate edges (UNIQUE indexes enforce).
  - **failure isolation**: `find_continuity_match` raising → fall through to existing PendingComplianceCheck, response unchanged.
  - **performance budget**: synthetic 10-region drift, p95 added latency ≤ 200ms (CI-skip-marked unless `BENCH=1`).
- `tests/test_m5_benchmark.py`:
  - **moved**: `tests/fixtures/codegenome_m5/moved/` — function moved to new file → `identity_moved` → no `PendingComplianceCheck`.
  - **renamed**: `tests/fixtures/codegenome_m5/renamed/` → `identity_renamed`.
  - **logic-removal-same-path**: `tests/fixtures/codegenome_m5/logic_removed/` → still drifted, no false continuity.
  - **class-extracted-to-two-modules**: `tests/fixtures/codegenome_m5/class_extracted/` → `needs_review` (ambiguous split).
  - False-positive rate < 10% across the corpus.

### Affected files

- `contracts.py` — add `ContinuityResolution` Pydantic model + `continuity_resolutions: list[ContinuityResolution] = []` on `LinkCommitResponse`. Existing fields untouched.

```python
class ContinuityResolution(BaseModel):
    decision_id: str
    old_code_region_id: str
    new_code_region_id: str | None = None
    semantic_status: Literal["identity_moved", "identity_renamed", "needs_review"]
    confidence: float = Field(ge=0.0, le=1.0)
    old_location: CodeRegionSummary
    new_location: CodeRegionSummary | None = None
    rationale: str
```

- `codegenome/continuity_service.py` — new module orchestrating the per-drifted-region resolution flow:
  - `evaluate_continuity_for_drift(*, ledger, code_locator, decision_id, region_id, repo_ref, repo_path) -> ContinuityResolution | None`
  - Loads identities via `find_subject_identities_for_decision`; picks the highest-confidence one as `old_identity_id` and resolves the parent `code_subject_id`.
  - Calls `find_continuity_match(old_identity, code_locator)` → `ContinuityMatch | None`.
  - **On match.confidence ≥ 0.75 — full 7-step auto-resolve sequence** (each step's prerequisite is the previous step's return value):
    1. `new_identity = codegenome.compute_identity_with_neighbors(match.new_file_path, match.new_start_line, match.new_end_line, code_locator=code_locator, repo_ref=repo_ref)` — produces the new subject_identity values.
    2. `new_region_id = await ledger.upsert_code_region(file_path=match.new_file_path, symbol_name=match.new_symbol_name, start_line=match.new_start_line, end_line=match.new_end_line, repo=repo_path, content_hash=new_identity.content_hash)` — creates the row that step 7's RELATE will target.
    3. `new_identity_id = await ledger.upsert_subject_identity(new_identity)` — creates the row that step 6's RELATE will target.
    4. `new_version_id = await ledger.write_subject_version(code_subject_id=subject_id, repo_ref=repo_ref, file_path=match.new_file_path, start_line=match.new_start_line, end_line=match.new_end_line, symbol_name=match.new_symbol_name, symbol_kind=match.new_symbol_kind, content_hash=new_identity.content_hash, signature_hash=new_identity.signature_hash)` — records the new location as a version of the existing code_subject.
    5. `await ledger.relate_has_version(subject_id, new_version_id)` — wires the new subject_version into the graph (without this edge the row is unreachable).
    6. `await ledger.write_identity_supersedes(old_identity_id, new_identity_id, change_type=match.change_type, confidence=match.confidence)` — records the identity transition.
    7. `await ledger.update_binds_to_region(decision_id, old_region_id=region_id, new_region_id=new_region_id)` — flips the active binding.
    Returns `ContinuityResolution(semantic_status="identity_moved" or "identity_renamed", new_code_region_id=new_region_id, ..., rationale=f"continuity match @ {match.confidence:.2f}, change_type={match.change_type}")`.
  - On 0.50 ≤ confidence < 0.75: no ledger writes; returns `ContinuityResolution(semantic_status="needs_review", new_code_region_id=None, new_location=None, ..., rationale="ambiguous continuity candidate; awaiting caller decision")` for the caller LLM to act on.
  - On confidence < 0.50: returns `None` (handler falls through to existing PendingComplianceCheck).
- `handlers/link_commit.py` — new helper `_run_continuity_pass(ctx, drifted_regions, base_response)`:
  - Pre-condition: `ctx.codegenome_config.enabled and ctx.codegenome_config.enhance_drift`.
  - For each region in `drifted_regions`: call `evaluate_continuity_for_drift`.
  - Resolutions with `semantic_status in ("identity_moved", "identity_renamed")` → suppress the corresponding `PendingComplianceCheck`.
  - All resolutions are appended to `response.continuity_resolutions`.
  - All exceptions caught and logged; baseline response shape preserved.

---

## Schema specifics (v11 → v12)

```text
subject_identity                 (existing — additive field only)
  + neighbors_at_bind  option<array<string>>

identity_supersedes              (new)
  RELATION IN subject_identity OUT subject_identity
  change_type    string  (moved | renamed | moved_and_renamed)
  confidence     float [0,1]
  evidence_refs  array<string> DEFAULT []
  created_at     datetime DEFAULT time::now()
  INDEX idx_identity_supersedes_unique ON in, out UNIQUE

has_version                      (existing — defined in #59, wired here)
  RELATION IN code_subject OUT subject_version
  No schema change. Phase 3 is the first caller (`relate_has_version`),
  closing the orphan-edge condition flagged by the first audit (V1).
```

`supersedes` (decision → decision, exists since v6) is **not** changed.
The new edge is `identity_supersedes` (subject_identity →
subject_identity) — separate concern, separate name.

---

## Success criteria (audit checklist)

- [ ] `SCHEMA_VERSION = 12`; migration registered; `init_schema` idempotent.
- [ ] All Phase 1, 2, 3 tests pass under `python -m pytest tests/test_codegenome_*.py tests/test_m5_benchmark.py -v`.
- [ ] `python -m pytest -m phase2 -v` passes (no regression on existing bind/link_commit/drift tests).
- [ ] With both flags off, `LinkCommitResponse` shape and behavior identical to today; no calls to `find_continuity_match`.
- [ ] With both flags on and a function-move fixture, `continuity_resolutions[0].semantic_status == "identity_moved"`, the `PendingComplianceCheck` for that region is **suppressed**, and the `binds_to` edge points at the new `code_region`.
- [ ] `find_continuity_match` returns `None` for the logic-removal fixture (no false continuity).
- [ ] `class-extracted-to-two-modules` fixture returns `needs_review` (split ambiguity).
- [ ] Continuity evaluation exceptions are caught; `LinkCommitResponse` unchanged on failure.
- [ ] Ledger module **does not** import from `codegenome` (one-way dep preserved).
- [ ] No new MCP tools registered; `EXPECTED_TOOL_NAMES` in `server.py` unchanged.
- [ ] No `BindResponse` / `BindResult` field changes (Phase-1+2 contract intact).
- [ ] Section 4 razor: every new function ≤ 40 lines, every new file ≤ 250 lines.
- [ ] Performance: continuity pass adds ≤ 200ms p95 over 10-region synthetic drift.
- [ ] False-positive rate on M5 benchmark corpus < 10%.
