# Plan: CodeGenome Phase 1+2 (Issue #59)

Adapter boundary + bind-time identity records. Foundational PR for the
Phase 3 (continuity, #60) and Phase 4 (semantic drift, #61) sequence.

**Branch**: `claude/codegenome-phase-1-2-qor` off `upstream/main` (`6bdff24`).
**PR target**: `BicameralAI/bicameral-mcp`.
**Schema**: `SCHEMA_VERSION` 10 → 11 (additive).
**Default behavior**: zero change unless `BICAMERAL_CODEGENOME_ENABLED=1` *and*
`BICAMERAL_CODEGENOME_WRITE_IDENTITY_RECORDS=1`.

---

## Open Questions

1. **`SCHEMA_COMPATIBILITY[11]` value** — upstream is at 0.10.2 and the
   conventional next bump is `"0.11.0"`. The plan ships `"0.11.0"` as a
   sourced placeholder; the final value is pinned during PR review by
   upstream release-engineering. (Owner: release-eng. Resolution path:
   PR-review comment, not in-plan edit.)
2. **`SCHEMA_COMPATIBILITY[10]` is missing on upstream** (jumps 9 → next).
   Out of scope; documented in PR description, not patched here.
3. **`canonical_name` collisions across files** — `code_subject` is keyed
   `(kind, canonical_name)` UNIQUE. Two functions named `parse` in different
   files would collide. Acceptable for deterministic_location_v1 because
   `subject_identity.address` (file:lines blake2b) disambiguates downstream;
   re-evaluate if Phase 3 needs disambiguation at the subject level.

---

## Architecture Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Module location | `codegenome/` at repo root | Matches upstream's flat layout (`handlers/`, `ledger/`, `code_locator/`) |
| Composition pattern | Handler-orchestrated | Codegenome → ledger dependency points one way; bind handler is the single coordination point; no `bind_decision` complecting |
| Adapter factory | `adapters/codegenome.py::get_codegenome()` | Mirrors `get_ledger`/`get_code_locator`/`get_drift_analyzer` |
| Identity content hash | Reuses `ledger.status.hash_lines` (sha256, ws-normalized) | Required equality with `code_region.content_hash` per #59 exit criterion overrides the issue body's literal `blake2b` |
| Identity signature hash | `blake2b(structural_signature, 32)` | Per issue spec; distinct signal from content |
| Schema migration style | Additive `_migrate_v10_to_v11` + new entries in `_TABLES`/`_EDGES` | Matches v6→v7 and v8→v9 patterns |
| Failure mode | Identity-write exceptions are logged and swallowed | Bind response contract unchanged; identity records are best-effort enrichment |

---

## CI / validation commands

```bash
# Codegenome unit + integration only (fast, target while iterating)
python -m pytest tests/test_codegenome_adapter.py tests/test_codegenome_confidence.py \
                 tests/test_codegenome_config.py tests/test_codegenome_bind_integration.py -v

# Phase 2 marker (touches our new bind integration + existing bind tests)
python -m pytest -m phase2 -v

# Full suite (regression check before commit)
python -m pytest tests/ -v
```

`pytest.ini` already declares the markers; no new markers introduced.

---

## Phase 1 — Adapter skeleton

Stable interface, dataclasses, Pydantic models, confidence helpers, config.
**No handler or schema changes in this phase.** Importing the new modules
must be a no-op for existing flows.

### Unit tests (TDD — written first)

- `tests/test_codegenome_confidence.py` — `noisy_or` and `weighted_average`.
  - `noisy_or([0.7, 0.7]) ≈ 0.91`
  - `noisy_or([])` returns `0.0` (empty fuse)
  - clamps negatives → 0, clamps >1 → 1
  - matches `1 − ∏(1 − cᵢ)` for n=3
  - `weighted_average` drops keys with no matching weight
  - `weighted_average` with zero total weight → `0.0` (not NaN)
- `tests/test_codegenome_config.py` — env-loaded feature flags.
  - default-from-env: every flag `False`
  - parametrized truthy values (`1`, `true`, `True`, `yes`, `on`)
  - parametrized falsy / unknown values (`0`, `false`, `garbage`, empty)
  - per-flag isolation (one env var ≠ cross-talk)
  - `identity_writes_active()` requires both `enabled` and `write_identity_records`
- `tests/test_codegenome_adapter.py` — adapter ABC + dataclasses.
  - all four ABC methods raise `NotImplementedError`
  - `SubjectIdentity` is frozen (mutation raises)
  - `EvidencePacket` round-trip (constructor accepts populated lists)

### Affected files

- `codegenome/__init__.py` — package marker, brief docstring.
- `codegenome/adapter.py` — `CodeGenomeAdapter` ABC + dataclasses
  (`SubjectCandidate`, `SubjectIdentity`, `EvidenceRecord`, `DriftEvaluation`,
  `EvidencePacket`); literals `EvidenceType` and `DriftStatus`.
- `codegenome/contracts.py` — three Pydantic models for the MCP-boundary
  contract per upstream issue #59 deliverables list:
  `SubjectCandidateModel`, `EvidenceRecordModel`, `EvidencePacketModel`.
  Fields use `Field(ge=0, le=1)` for confidences. The dataclass
  `SubjectIdentity` is **not** mirrored as a Pydantic model: it has no
  caller in #59 and no entry in the issue's Phase-1 deliverables list;
  any future caller (Phase 3+) can introduce its mirror at point-of-need.
- `codegenome/confidence.py` — `noisy_or(Iterable[float])`,
  `weighted_average(Mapping, Mapping)`. Pure functions, no I/O.
- `codegenome/config.py` — `CodeGenomeConfig` Pydantic model with all flags
  defaulting `False`, plus `from_env()` reading `BICAMERAL_CODEGENOME_*`,
  plus `identity_writes_active()` predicate used by the bind handler.

### Changes

ABC methods raise `NotImplementedError`. Phase 1 ships **only**
`compute_identity` as a usable concrete method (in `deterministic_adapter.py`,
introduced in Phase 2). The other three (`resolve_subjects`,
`evaluate_drift`, `build_evidence_packet`) remain abstract — no stubs, no
empty returns, per anti-goal Q2.

Confidence weights table (referenced by Phase 3+4, not used here):

```python
DEFAULT_CONFIDENCE_WEIGHTS = {
    "subject_resolution":    0.25,
    "structural_identity":   0.20,
    "content_similarity":    0.15,
    "call_graph_similarity": 0.15,
    "test_support":          0.15,
    "runtime_support":       0.10,
}
```

The constants live in `codegenome/confidence.py` so Phase 3 can import them
without restructuring.

---

## Phase 2 — Identity record write path

Schema v10 → v11 migration, ledger queries, deterministic adapter,
adapter factory, context wiring, and the side-effect-only bind hook.

### Unit + integration tests (TDD — written first)

- `tests/test_codegenome_adapter.py` extension — `DeterministicCodeGenomeAdapter.compute_identity`:
  - same `(file, span)` → same `address` and `signature_hash`
  - different span → different address
  - different file → different address
  - `signature_hash == blake2b(structural_signature, digest_size=32).hexdigest()`
  - **#59 exit criterion**: `identity.content_hash == ledger.status.hash_lines(body, s, e)`
    (the test itself is the verification mechanism for the equality
    assertion; sha256-with-whitespace-normalization is the existing ledger
    hash function, reused so the values are byte-identical by construction)
  - constants: `identity_type == "deterministic_location_v1"`,
    `model_version == "deterministic-location-v1"`, `confidence == 0.65`
  - missing file (git returns `None`) → `content_hash is None`,
    `signature_hash` still computed
  - invalid range (`end < start`) → `content_hash is None`
- `tests/test_codegenome_bind_integration.py` — full handler path:
  - **flag off**: `find_subject_identities_for_decision(decision_id) == []`,
    no rows in `code_subject` or `subject_identity`
  - **flag on**: identity row queryable, `identity_type == "deterministic_location_v1"`,
    `address.startswith("cg:")`, `subject_identity.content_hash == BindResult.content_hash`
  - **idempotency**: bind twice → exactly one `code_subject` row, one
    `subject_identity` row, one `has_identity` edge, one `about` edge
  - **failure isolation**: when `compute_identity` raises, bind response
    is unchanged (no `error` field set, identity records absent)

### Affected files

- `ledger/schema.py`
  - `SCHEMA_VERSION = 11` (was 10)
  - `SCHEMA_COMPATIBILITY[11] = "0.11.0"` (placeholder, see Open Q1)
  - new `_TABLES` entries: `code_subject`, `subject_identity`, `subject_version`
    (full DEFINE FIELDs + UNIQUE indexes per architecture spec)
  - new `_EDGES` entries: `has_identity` (code_subject→subject_identity),
    `has_version` (code_subject→subject_version), `about` (decision→code_subject)
  - new `_migrate_v10_to_v11(client)` — additive only, mirrors
    `_migrate_v6_to_v7` style; registered in `_MIGRATIONS`
- `ledger/queries.py` — append five functions:
  - `upsert_code_subject(client, kind, canonical_name, current_confidence, repo_ref=None) -> str`
  - `upsert_subject_identity(client, *, address, identity_type, structural_signature, behavioral_signature, signature_hash, content_hash, confidence, model_version) -> str` (read-then-create on UNIQUE address)
  - `relate_has_identity(client, code_subject_id, subject_identity_id, confidence=0.9)` (idempotent edge)
  - `link_decision_to_subject(client, decision_id, code_subject_id, confidence=0.8)` (idempotent edge)
  - `find_subject_identities_for_decision(client, decision_id) -> list[dict]` (two-hop traversal `decision→about→code_subject→has_identity→subject_identity`)
- `ledger/adapter.py` — add five thin async wrappers (`upsert_code_subject`,
  `upsert_subject_identity`, `relate_has_identity`, `link_decision_to_subject`,
  `find_subject_identities_for_decision`). Each calls `_ensure_connected()`
  then delegates. The `upsert_subject_identity` wrapper duck-types the
  argument as `codegenome.adapter.SubjectIdentity` to keep the dependency
  one-way (ledger does **not** import codegenome).
- `codegenome/deterministic_adapter.py` — `DeterministicCodeGenomeAdapter`:
  - `__init__(repo_path)`
  - `compute_identity(file_path, start_line, end_line, repo_ref="HEAD")`:
    1. `structural_signature = f"{file_path}:{start_line}:{end_line}"`
    2. `signature_hash = blake2b(... , 32).hexdigest()`
    3. `address = f"cg:{signature_hash}"`
    4. body via `ledger.status.get_git_content` (lazy import; empty path
       returns `None` content_hash but address still computed)
    5. `content_hash = ledger.status.hash_lines(body, start_line, end_line)`
       on success
  - module constants: `IDENTITY_TYPE_V1`, `MODEL_VERSION_V1`, `DEFAULT_CONFIDENCE_V1`
- `codegenome/bind_service.py` — `write_codegenome_identity(*, ledger, codegenome,
  decision_id, file_path, symbol_name, symbol_kind, start_line, end_line,
  repo_ref, code_region_content_hash) -> SubjectIdentity | None`
  - calls `codegenome.compute_identity(...)`
  - logs warning if `identity.content_hash != code_region_content_hash`
    (does not abort)
  - upsert subject → upsert identity → `relate_has_identity` →
    `link_decision_to_subject`
- `adapters/codegenome.py` — `get_codegenome()` returning a per-call
  `DeterministicCodeGenomeAdapter(repo_path=os.getenv("REPO_PATH", "."))`.
  Mirrors `adapters/code_locator.get_code_locator()`.
- `context.py` — extend `BicameralContext`:
  - new fields: `codegenome: object | None = None`,
    `codegenome_config: object | None = None`
  - `from_env()` populates both via `get_codegenome()` and
    `CodeGenomeConfig.from_env()`
- `handlers/bind.py` — inside `_do_bind`'s per-binding loop, after
  `bind_decision` succeeds and before building `pending_check`:
  - read `cg_config = getattr(ctx, "codegenome_config", None)`,
    `cg_adapter = getattr(ctx, "codegenome", None)`
  - if both present and `cg_config.identity_writes_active()` is `True`,
    call `write_codegenome_identity(...)` inside `try/except` (log warning
    on failure)
  - **no change to `BindResponse`/`BindResult` shape**

### Schema specifics (deterministic_location_v1 contract)

```text
code_subject       (kind, canonical_name) UNIQUE
                   current_confidence ∈ [0,1]
subject_identity   (address) UNIQUE
                   confidence ∈ [0,1]
subject_version    (repo_ref, file_path, start_line, end_line) — non-unique index
                   defined now per anti-goal Q2 (foundation for #60); not written by Phase 2
has_identity       code_subject → subject_identity, UNIQUE(in,out)
has_version        code_subject → subject_version, UNIQUE(in,out)
                   defined but unused in Phase 2
about              decision → code_subject, UNIQUE(in,out)
```

`supersedes` (subject_identity → subject_identity) is **explicitly not**
included — Phase 3 (#60) owns that design.

---

## Success Criteria (audit checklist)

- [ ] `SCHEMA_VERSION = 11`; migration registered; `init_schema` idempotent.
- [ ] All Phase 1 + Phase 2 unit/integration tests pass under
      `python -m pytest tests/test_codegenome_*.py -v`.
- [ ] `python -m pytest -m phase2 -v` passes (no regression on existing bind tests).
- [ ] With both flags off, `find_subject_identities_for_decision` returns `[]`
      after bind; no rows in `code_subject` / `subject_identity`.
- [ ] With both flags on, `find_subject_identities_for_decision` returns ≥ 1
      row whose `content_hash` matches the bound region's `content_hash`.
- [ ] Bind handler raises no new exception classes; failures inside
      `write_codegenome_identity` are caught and logged.
- [ ] Ledger module **does not** import from `codegenome` (one-way dep).
- [ ] No new MCP tools registered; `EXPECTED_TOOL_NAMES` in `server.py` unchanged.
- [ ] No `BindResponse` / `BindResult` field changes.
