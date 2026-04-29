# Plan: CodeGenome Phase 4 — Semantic Drift Evaluation in `resolve_compliance` (M3)

**Issue:** [BicameralAI/bicameral-mcp#61](https://github.com/BicameralAI/bicameral-mcp/issues/61)
**Branch:** `claude/codegenome-phase-4-qor` (rebased onto `dev` after PR #73 merged)
**Merge target:** `BicameralAI/dev` (NOT main — per the new dev-integration workflow; Jin batches `dev → main` separately)
**Risk Grade:** L2 (modifies existing tool surface, adds schema fields, adds handler logic)
**Revision:** v3 — refresh after Phase 3 merged. Phase 1 of Phase 4 is **DONE** (commit `2afd52d`); Phases 2-5 remain. Carries forward all v2 design decisions (F1-F5 + O1-O5 sealed in v2 audit PASS, META_LEDGER Entry #12, chain hash `332c72b2`).

## What's changed since v2 plan

- **PR #71 (Phase 1+2) merged to upstream `main`** at 2026-04-28T19:55:40Z.
- **PR #73 (Phase 3) merged to `dev`** at 2026-04-28T20:59:56Z with all 17 CodeRabbit + Devin review items addressed in commit `f9049fa`.
- **`dev` integration branch live** on both `BicameralAI/bicameral-mcp` and the `Knapp-Kevin/bicameral-mcp` fork. CI workflows (`MCP Regression Tests`, `Preflight Failure-Mode Eval`, `Schema Persistence Tests`) updated to trigger on `pull_request: branches: [main, dev]` (commit `169722f` on dev).
- **Phase 4 branch rebased** onto current `dev` (single-base; the previous 3-deep stack `Phase 1+2 → Phase 3 → Phase 4` is collapsed).
- **Phase 1 sealed** — schema v13 migration, contracts (`PreClassificationHint`, extended `ComplianceVerdict` / `PendingComplianceCheck` / `LinkCommitResponse` / `ResolveComplianceAccepted`), and 9 persistence tests all green at commit `2afd52d`. Local regression: 146/146 pass.
- **Obs-V2-1 resolved positively** — `SHOW CHANGES FOR TABLE compliance_check SINCE 1` works in SurrealDB v2 embedded; F1 changefeed regression tests pass without xfail. The fallback caveat from v2 plan is removed.
- **Obs-V2-2 still pending** — F3 parity test must still guard `_USE_LEGACY` mode where `_LANG_PACKAGE_MAP` isn't defined; carry into Phase 2 implementation.

## Risk Assessment

- [x] Modifies existing APIs → **L2** (`PendingComplianceCheck` gains an optional field; `resolve_compliance` accepts new optional params; `LinkCommitResponse` gains an optional list)
- [x] Adds schema fields → **L2** (`compliance_check.semantic_status`, `compliance_check.evidence_refs`)
- [ ] No security/auth surface → not L3
- [ ] No UI-only changes → not L1

L2 routes through `/qor-audit` before implementation.

## Open Questions

1. **Per-language signal degradation.** Q2 selected multi-language (B). Languages without a stable docstring convention (Go, Rust comments-only; Java/C# Javadoc) need different rules for the `diff_lines` signal. Plan: each language has its own `LineCategorizer` (single class, dispatched via the `language` argument). Languages with weak docstring/comment AST distinction (Go: only line/block comments; no first-class docstrings) treat doc-comments as `comment` lines for the cosmetic signal — same weight, no special docstring branch. Acceptable because the Jaccard + signature signals carry most of the weight.

2. **Caller-LLM override semantics.** Issue says "caller LLM always wins". When a row already has an auto-resolved `verdict="compliant" + semantic_status="semantically_preserved"` and the caller submits a contradicting `verdict="drifted"`, do we (a) overwrite the row in place, or (b) write a new row and mark the auto-resolved one superseded? The current `compliance_check` UNIQUE index is on `(decision_id, region_id, content_hash)` so option (a) is the natural behaviour — the caller's verdict replaces the auto one for the same content_hash. Plan assumes (a).

3. **Evidence representation.** Per Q3, `evidence_refs` is `list[string]` of free-form descriptors like `"signature_hash:matched"`, `"neighbors_jaccard:0.97"`, `"diff_lines:docstring=12,blank=3"`. Not RecordIDs. Sufficient for audit; queryable enough for spot-checking. (Phase 5/6 may promote to a `drift_evidence` table.)

## Composition Principles

- **Sibling pass, not fused** — `_run_drift_classification_pass` runs after `_run_continuity_pass`, on the surviving pending list. Continuity = "where did this go?", classification = "did the meaning change?". Two concerns, two passes.
- **Handler composes, ledger is dumb** — `_run_drift_classification_pass` writes the auto-resolve `compliance_check` row directly via the existing `upsert_compliance_check` query. No new ledger method.
- **Failure-isolated** — any exception in classification falls through to the existing `PendingComplianceCheck` flow. Response shape never changes shape on error.
- **Caller-LLM verdict always wins** — auto-resolution writes the `compliance_check` row but does NOT prune the binding. If the caller later submits a contradicting verdict, the row is overwritten (UNIQUE index handles this).

## CI commands to validate the plan

Match upstream `.github/workflows/test-mcp-regression.yml`:

- `python -m pytest tests/test_codegenome_drift_classifier.py -v`
- `python -m pytest tests/test_codegenome_drift_service.py -v`
- `python -m pytest tests/test_codegenome_resolve_compliance_persistence.py -v`
- `python -m pytest tests/test_m3_benchmark.py -v`
- `python -m pytest tests/test_phase2_ledger.py -q` (regression — schema bump check)
- `python -m pytest tests/test_codegenome_*.py -q` (full codegenome regression)

---

## Phase 1: Schema + contracts ✅ **DONE** (commit `2afd52d`)

**Status:** sealed, 9/9 tests passing, 146/146 broader regression clean. Artifacts:
- `ledger/schema.py` v12 → v13 — `compliance_check` redefined with `CHANGEFEED 30d INCLUDE ORIGINAL`; `semantic_status` (option<string>, ASSERT enum `['semantically_preserved', 'semantic_change']`) and `evidence_refs` (array<string>) added. `_migrate_v12_to_v13` registered.
- `ledger/queries.py::upsert_compliance_check` extended with optional `semantic_status` + `evidence_refs` kwargs.
- `contracts.py` — new `PreClassificationHint`; extended `ComplianceVerdict`, `ResolveComplianceAccepted`, `PendingComplianceCheck` (with `pre_classification: PreClassificationHint | None = None`), `LinkCommitResponse` (`auto_resolved_count: int = 0` per O1).
- `tests/test_codegenome_resolve_compliance_persistence.py` — 9 tests covering migration additivity, CHANGEFEED retrofit, changefeed records overwritten rows, F2 dropped enum value rejection, persistence round-trip, legacy-caller backward compat.

The test plan summary, razor pre-check, and risk table sections below are kept intact for chain integrity. Phases 2-5 are the remaining implementation queue.

### Affected Files

- `tests/test_codegenome_resolve_compliance_persistence.py` — **new**, ~85 lines (was 60; +25 LOC for the changefeed regression test, F1)
- `ledger/schema.py` — redefine `compliance_check` with CHANGEFEED, add 2 fields, bump v12→v13 (~18 lines added)
- `contracts.py` — extend `ComplianceVerdict`, `ResolveComplianceAccepted`, `PendingComplianceCheck`, `LinkCommitResponse`, add `PreClassificationHint` (~35 lines added; +5 LOC for `auto_resolved_count` per O1)

### Changes

**`ledger/schema.py`** — table redefinition (CHANGEFEED added per F1) + 2 new fields:

```python
# In _TABLES, replace the compliance_check table line so the table itself
# carries CHANGEFEED 30d INCLUDE ORIGINAL — required for F1 forensic recovery
# of caller-LLM-overwritten auto-resolved rows.
"DEFINE TABLE compliance_check SCHEMAFULL CHANGEFEED 30d INCLUDE ORIGINAL",

# ... existing compliance_check fields unchanged ...

# Two new fields appended to the compliance_check section (F2: enum value
# pre_classification_hint dropped — only written values are listed):
"DEFINE FIELD semantic_status ON compliance_check TYPE option<string> DEFAULT NONE "
"ASSERT $value = NONE OR $value IN ['semantically_preserved', 'semantic_change']",
"DEFINE FIELD evidence_refs   ON compliance_check TYPE array<string> DEFAULT []",
```

```python
# Bump version + register migration:
SCHEMA_VERSION = 13
SCHEMA_COMPATIBILITY[13] = "0.13.0"

async def _migrate_v12_to_v13(client):
    """v12 → v13: add CHANGEFEED on compliance_check, plus
    semantic_status + evidence_refs fields (#61).

    The CHANGEFEED is required for F1 forensic recovery — when a
    caller-LLM verdict overwrites an auto-resolved row (UNIQUE on
    decision_id, region_id, content_hash), the original row must
    remain inspectable via the changefeed for 30 days.
    """
    # CHANGEFEED can be retrofitted on an existing table via
    # DEFINE TABLE ... OVERWRITE. The OVERWRITE keeps the existing rows.
    await _execute_define_idempotent(
        client,
        "DEFINE TABLE OVERWRITE compliance_check SCHEMAFULL CHANGEFEED 30d INCLUDE ORIGINAL",
    )
    await _execute_define_idempotent(
        client,
        "DEFINE FIELD OVERWRITE semantic_status ON compliance_check "
        "TYPE option<string> DEFAULT NONE "
        "ASSERT $value = NONE OR $value IN ['semantically_preserved', 'semantic_change']",
    )
    await _execute_define_idempotent(
        client,
        "DEFINE FIELD OVERWRITE evidence_refs ON compliance_check "
        "TYPE array<string> DEFAULT []",
    )

_MIGRATIONS[13] = _migrate_v12_to_v13
```

Note: `init_schema` already injects OVERWRITE into every DEFINE during connect (verified in `ledger/schema.py::_with_overwrite` and the existing v8→v9 migration pattern), so the migration body is defensive but the canonical _TABLES change is what makes init_schema apply the CHANGEFEED on every fresh connect too.

**`contracts.py`** — additive fields (F2: enum tightened to written-only values; O1: `auto_resolved_count` consolidated here):

```python
class PreClassificationHint(BaseModel):
    """Server-computed evidence that the caller LLM may use as a hint."""
    verdict: Literal["cosmetic", "semantic", "uncertain"]
    confidence: float                # in [0, 1]
    signals: dict[str, float]        # {"signature": 0.30, "neighbors": 0.95, ...}
    evidence_refs: list[str] = []

class PendingComplianceCheck(BaseModel):
    # ... existing fields ...
    pre_classification: PreClassificationHint | None = None  # Phase 4 (#61)

class ComplianceVerdict(BaseModel):
    # ... existing fields ...
    semantic_status: Literal["semantically_preserved", "semantic_change"] | None = None
    evidence_refs: list[str] = []

class ResolveComplianceAccepted(BaseModel):
    # ... existing fields ...
    semantic_status: Literal["semantically_preserved", "semantic_change"] | None = None

class LinkCommitResponse(BaseModel):
    # ... existing fields ...
    auto_resolved_count: int = 0  # Phase 4 (#61) — observability for cosmetic auto-resolve
```

### Unit Tests

- **`tests/test_codegenome_resolve_compliance_persistence.py`** — covers:
  - `test_v13_migration_is_additive` — apply v12→v13, verify existing rows still readable + the two new fields read back as `None` / `[]`.
  - `test_v13_migration_adds_changefeed_on_compliance_check` — F1 regression: after migration, `INFO FOR TABLE compliance_check` (or equivalent v2 introspection) reports CHANGEFEED enabled. (Note: `INFO FOR TABLE` is unreliable in v2 embedded per CLAUDE.md; fallback is to write a row, overwrite it, and assert the original is recoverable via `SHOW CHANGES FOR TABLE compliance_check SINCE <ts>`.)
  - `test_compliance_check_changefeed_records_overwritten_row` — F1 regression: write a row with `semantic_status="semantically_preserved"`, overwrite it via the UNIQUE-key collision path with a caller verdict carrying `verdict="drifted", semantic_status="semantic_change"`, then assert the ORIGINAL row is recoverable via the changefeed.
  - `test_compliance_verdict_accepts_semantic_status` — Pydantic accepts both written values.
  - `test_compliance_verdict_rejects_pre_classification_hint_value` — F2 regression: the dropped value is no longer accepted by Pydantic OR the schema ASSERT.
  - `test_resolve_compliance_persists_semantic_status_and_evidence` — end-to-end through `upsert_compliance_check`.
  - `test_resolve_compliance_omits_optional_fields_for_legacy_callers` — payload without the new fields still accepted, persists `semantic_status=NONE, evidence_refs=[]`.

---

## Phase 2: Drift classifier (pure function, deterministic, multi-language)

### Affected Files

- `tests/test_codegenome_drift_classifier.py` — **new**, ~240 lines (was 220; +20 for the language-name parity test, F3)
- `tests/test_extract_call_sites.py` — **new**, ~120 lines (F4: per-language call-site extraction tests)
- `codegenome/drift_classifier.py` — **new**, ≤ 250 lines (target ~210)
- `codegenome/diff_categorizer.py` — **new**, ≤ 200 lines (target ~150 — public API + dispatcher only, per O3)
- `codegenome/_diff_dispatch.py` — **new**, ≤ 200 lines (target ~120 — per-line slot computation + tree-sitter integration, extracted from diff_categorizer per O3)
- `code_locator/indexing/call_site_extractor.py` — **new** module, ≤ 200 lines (target ~150). Hosts the new public function `extract_call_sites(content, language) -> set[str]` plus per-language tree-sitter queries. Lives as a sibling of `symbol_extractor.py` rather than extending it because `symbol_extractor.py` is already 459 LOC (pre-existing razor exception); piling 80 more LOC on top would make the violation worse. The new module reuses `symbol_extractor._get_parser` for parser caching so there's no duplicate language-loading code.
- `codegenome/_line_categorizers/` — **new** package, one tiny module per language (uses canonical `c_sharp` per F3):
  - `python.py` (~80 lines) — docstring/comment/import via tree-sitter
  - `javascript.py` (~70 lines) — JSDoc/line-comment/import
  - `typescript.py` (~30 lines) — extends javascript.py with type-annotation-only rule
  - `go.py` (~60 lines) — line/block comment, `import (...)` block
  - `rust.py` (~60 lines) — `//` and `///` doc-comments, `use` lines
  - `java.py` (~70 lines) — Javadoc, `//`, `import` lines
  - `c_sharp.py` (~70 lines) — XML doc, `//`, `using` lines (filename matches the `code_locator` language ID exactly per F3)
  - `__init__.py` (~30 lines) — registry + `categorize(language, line, in_function_first_stmt) -> LineCategory`

### Changes

**`codegenome/drift_classifier.py`** — entry point:

```python
from dataclasses import dataclass, field
from typing import Literal, Iterable

@dataclass(frozen=True)
class DriftClassification:
    verdict: Literal["cosmetic", "semantic", "uncertain"]
    confidence: float                          # weighted score in [0, 1]
    signals: dict[str, float]                  # per-signal contribution
    evidence_refs: list[str] = field(default_factory=list)

# Weights pinned by issue #61
_W_SIGNATURE_UNCHANGED   = 0.30
_W_NEIGHBORS_JACCARD     = 0.25
_W_DIFF_LINES_COSMETIC   = 0.30
_W_NO_NEW_CALLS          = 0.15

# Thresholds pinned by issue #61
_T_COSMETIC = 0.80
_T_SEMANTIC = 0.30

_SUPPORTED_LANGUAGES = frozenset({
    "python", "javascript", "typescript", "go", "rust", "java", "c_sharp",
})
# Canonical: matches ``code_locator.indexing.symbol_extractor._LANG_PACKAGE_MAP``
# keys exactly. The C# identifier is ``c_sharp`` (with underscore) — see F3
# in AUDIT_REPORT.md for the integration-mismatch failure mode this prevents.

def classify_drift(
    old_body: str,
    new_body: str,
    *,
    old_signature_hash: str | None,
    new_signature_hash: str | None,
    old_neighbors: Iterable[str] | None,
    new_neighbors: Iterable[str] | None,
    language: str,
) -> DriftClassification:
    """Deterministic structural drift classifier. ≤40 lines.

    ``language`` is one of the languages supported by
    ``code_locator.indexing.symbol_extractor``. Unsupported languages
    return ``verdict='uncertain'`` and pass through to the caller LLM
    unchanged.
    """
    if language not in _SUPPORTED_LANGUAGES:
        return DriftClassification(verdict="uncertain", confidence=0.0,
                                    signals={}, evidence_refs=[f"language:unsupported:{language}"])
    signals = {
        "signature": _signal_signature(old_signature_hash, new_signature_hash),
        "neighbors": _signal_neighbors(old_neighbors, new_neighbors),
        "diff_lines": _signal_diff_lines(old_body, new_body),
        "no_new_calls": _signal_no_new_calls(old_body, new_body),
    }
    score = (
        signals["signature"]    * _W_SIGNATURE_UNCHANGED +
        signals["neighbors"]    * _W_NEIGHBORS_JACCARD +
        signals["diff_lines"]   * _W_DIFF_LINES_COSMETIC +
        signals["no_new_calls"] * _W_NO_NEW_CALLS
    )
    verdict = _verdict_from_score(score)
    evidence_refs = _build_evidence_refs(signals, score)
    return DriftClassification(
        verdict=verdict, confidence=score,
        signals=signals, evidence_refs=evidence_refs,
    )
```

Each `_signal_*` helper is its own ≤30-line function:

- `_signal_signature(old, new)` → 1.0 if both non-None and equal, 0.5 if either None, 0.0 if differ.
- `_signal_neighbors(old, new)` → Jaccard via `codegenome.continuity._jaccard` (reused — DRY). 0.0 if either neighbor set is None.
- `_signal_diff_lines(old, new, language)` → delegates to `diff_categorizer.categorize_diff(old, new, language)`. Returns ratio of `comment + docstring + blank` lines to total changed lines (per-language line categorizer dispatch).
- `_signal_no_new_calls(old, new, language)` → calls `code_locator.indexing.call_site_extractor.extract_call_sites(old, language)` and `extract_call_sites(new, language)` to obtain `set[str]` of called callable names. Returns 1.0 if `new_calls ⊆ old_calls`, else 0.0. If the extractor raises (parser unavailable for the language at runtime), returns 0.5 (unknown — graceful degradation; classifier downgrades to `uncertain` rather than asserting cosmetic).
- `_verdict_from_score(score)` → ≥0.80 cosmetic, ≤0.30 semantic, else uncertain.
- `_build_evidence_refs(signals, score)` → list of `f"signature:{v:.2f}"` etc. + score.

**`codegenome/diff_categorizer.py`** — multi-language diff line categorization:

```python
from typing import Literal

LineCategory = Literal["comment", "docstring", "blank", "import", "logic", "signature"]

@dataclass(frozen=True)
class DiffStats:
    total: int
    comment: int
    docstring: int
    blank: int
    import_: int
    logic: int
    signature: int

def categorize_diff(
    old_body: str, new_body: str, language: str,
) -> DiffStats:
    """Categorize each changed line per-language. Public API.

    Internally:
    - Uses ``difflib`` for line-level diff.
    - Calls ``codegenome._diff_dispatch.compute_slot_flags(...)`` to get
      tree-sitter-derived ``in_function_signature`` and
      ``in_docstring_slot`` flags per line. (O3: the slot computation
      and tree-sitter integration live in the sibling module so this
      file stays a thin public-API + dispatcher.)
    - Dispatches each changed line to
      ``codegenome._line_categorizers.<language>.categorize_line(...)``.

    Caller must pre-validate ``language``; unsupported langs are a
    programming error here (the ``classify_drift`` entry-point already
    short-circuits unsupported languages to ``uncertain``).
    """
```

`codegenome/_diff_dispatch.py` (O3: extracted helper module) exposes:

```python
def compute_slot_flags(
    body: str, language: str,
) -> dict[int, tuple[bool, bool]]:
    """Map line-number → (in_function_signature, in_docstring_slot).

    Tree-sitter integration lives here so ``diff_categorizer.py`` stays
    a thin public-API layer. ~120 LOC; covers all 7 supported languages.
    """
```

Each per-language categorizer (`_line_categorizers/<lang>.py`) exposes a single function:

```python
def categorize_line(
    line: str, *, in_function_signature: bool, in_docstring_slot: bool,
) -> LineCategory:
    """Classify one source line in this language."""
```

`in_function_signature` and `in_docstring_slot` are pre-computed by the dispatcher using the tree-sitter AST so each language module stays small (~30–80 lines, well under razor).

### Unit Tests

- **`tests/test_codegenome_drift_classifier.py`**:
  - `test_classify_docstring_addition_is_cosmetic` — issue exit criterion 1.
  - `test_classify_import_reordering_is_cosmetic` — issue exit criterion 2.
  - `test_classify_logic_removal_is_semantic` — issue exit criterion 3.
  - `test_classify_signature_change_is_semantic` — issue exit criterion 4.
  - `test_classify_blank_lines_only_is_cosmetic`
  - `test_classify_comment_only_is_cosmetic`
  - `test_classify_uncertain_when_signals_mixed` — score in [0.30, 0.80).
  - `test_classify_unsupported_language_returns_uncertain` — language fallback (e.g. `language="ruby"`).
  - `test_classify_javascript_jsdoc_addition_is_cosmetic` — multi-lang exit criterion.
  - `test_classify_typescript_type_annotation_only_is_cosmetic` — TS-specific rule.
  - `test_classify_go_block_comment_addition_is_cosmetic`.
  - `test_classify_rust_doc_comment_addition_is_cosmetic`.
  - `test_classify_c_sharp_xml_doc_addition_is_cosmetic` — F3: explicit `c_sharp` (underscore) input flows end-to-end.
  - `test_classify_java_javadoc_addition_is_cosmetic` — Java symmetry case.
  - `test_supported_languages_match_code_locator` — F3 regression: asserts `_SUPPORTED_LANGUAGES == set(code_locator.indexing.symbol_extractor._LANG_PACKAGE_MAP.keys())`. A future divergence (e.g. someone re-introducing `csharp`) fails this test loud.
  - `test_signal_signature_handles_none_inputs` — returns 0.5 (uncertain weight).
  - `test_signal_neighbors_uses_jaccard_threshold` — 0.94 Jaccard → not cosmetic, 0.96 → cosmetic.
  - `test_signal_no_new_calls_detects_added_call` — `f()` body adds `bar()` → returns 0.0.
  - `test_signal_no_new_calls_returns_unknown_on_extractor_failure` — F4 graceful degradation: extractor raises → signal returns 0.5, classifier never auto-resolves.
  - `test_evidence_refs_include_score_and_signals` — round-trip through `evidence_refs`.
  - `test_classify_drift_function_under_40_lines` — Section 4 razor enforcement (static count).
  - `test_diff_categorizer_recognizes_python_docstring` — triple-quoted string at function start.
  - `test_diff_categorizer_recognizes_import_lines` — `import x` and `from x import y`.

- **`tests/test_extract_call_sites.py`** (F4: new public extractor):
  - `test_extract_call_sites_python` — `f(); g.h(); A().b()` → `{"f", "h", "b"}` (or fully-qualified per design).
  - `test_extract_call_sites_javascript` — handles `obj.method()`, `fn()`, `new Foo()`.
  - `test_extract_call_sites_typescript` — TS-specific generic-call syntax.
  - `test_extract_call_sites_go` — package-qualified calls (`pkg.Func()`), method receivers.
  - `test_extract_call_sites_rust` — turbofish-decorated calls (`fn::<T>()`).
  - `test_extract_call_sites_java` — `obj.method()`, static calls, constructor calls.
  - `test_extract_call_sites_c_sharp` — F3 + F4: explicit `c_sharp` input; LINQ/extension-method patterns.
  - `test_extract_call_sites_returns_empty_for_unparseable_input` — graceful failure.
  - `test_extract_call_sites_returns_empty_for_unsupported_language` — passes empty rather than raising; aligns with `_signal_no_new_calls` 0.5-on-error contract.

---

## Phase 3: Drift classification service (orchestration)

### Affected Files

- `tests/test_codegenome_drift_service.py` — **new**, ~150 lines
- `codegenome/drift_service.py` — **new**, ≤ 250 lines (target ~190)

### Changes

**`codegenome/drift_service.py`** — wires the classifier into the ledger I/O layer:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class DriftClassificationContext:
    decision_id: str
    region_id: str
    content_hash: str
    old_body: str
    new_body: str
    file_path: str
    repo_path: str
    repo_ref: str
    commit_hash: str

@dataclass(frozen=True)
class DriftClassificationOutcome:
    classification: DriftClassification
    auto_resolved: bool                    # True when written as compliance_check
    pre_classification_hint: PreClassificationHint | None  # set when uncertain

async def evaluate_drift_classification(
    *, ledger, codegenome, ctx: DriftClassificationContext,
) -> DriftClassificationOutcome:
    """≤40 lines: load identity, classify, write or hint."""
```

Steps inside `evaluate_drift_classification`:

1. Load `subject_identity` for the binding (via `codegenome.find_subject_identities_for_decision` — already exists Phase 1+2).
2. If no identity (decision has no codegenome row), return `auto_resolved=False, pre_classification_hint=None` — fall through.
3. Call `classify_drift(...)`.
4. If `verdict == "cosmetic"` and `confidence >= 0.80`:
   - `await ledger.upsert_compliance_check(...)` with `verdict="compliant"`, `semantic_status="semantically_preserved"`, `evidence_refs=...`, `confidence="high"`, `phase="drift"`.
   - Return `auto_resolved=True, pre_classification_hint=None`.
5. If `verdict == "uncertain"` (score in [0.30, 0.80)):
   - Return `auto_resolved=False, pre_classification_hint=PreClassificationHint(...)`.
6. Otherwise (`verdict == "semantic"`):
   - Return `auto_resolved=False, pre_classification_hint=None`.

Helpers extracted to keep entry function ≤40 lines (O5: the verdict-write branches go in their own helper so the entry stays well under 40):

- `_load_old_and_new_bodies(ctx)` — uses `ledger.status.get_git_content` for old, current file content for new.
- `_get_current_neighbors(codegenome, code_locator, ctx)` — calls existing `code_graph.get_neighbors(symbol_name)`.
- `_write_auto_resolution(ledger, ctx, classification)` — single ledger write.
- `_write_or_hint(ledger, ctx, classification) -> DriftClassificationOutcome` — O5: encapsulates the 3-branch decision (cosmetic→write+auto_resolved, uncertain→hint, semantic→pass-through). Keeps `evaluate_drift_classification` body to a 3-statement happy path: load, classify, dispatch.

### Unit Tests

- **`tests/test_codegenome_drift_service.py`**:
  - `test_cosmetic_drift_writes_compliance_check_and_returns_auto_resolved`.
  - `test_cosmetic_drift_writes_semantic_status_semantically_preserved`.
  - `test_cosmetic_drift_writes_evidence_refs`.
  - `test_semantic_drift_returns_no_hint_no_auto_resolve`.
  - `test_uncertain_drift_returns_pre_classification_hint`.
  - `test_no_subject_identity_falls_through_cleanly` — decision without Phase 1+2 identity is a no-op (returns `auto_resolved=False`).
  - `test_evaluate_function_under_40_lines` — razor.
  - `test_failure_isolated_returns_no_auto_resolve_on_exception` — classifier raises → outcome is `auto_resolved=False, pre_classification_hint=None`.

---

## Phase 4: Handler integration (`link_commit` + `resolve_compliance`)

### Affected Files

- `tests/test_codegenome_phase4_link_commit.py` — **new**, ~140 lines
- `tests/test_codegenome_phase4_resolve_compliance.py` — **new**, ~100 lines
- `handlers/link_commit.py` — add `_run_drift_classification_pass` (~50 lines added)
- `handlers/resolve_compliance.py` — accept + persist new optional fields (~25 lines modified)
- `ledger/queries.py` — extend `upsert_compliance_check` signature (~15 lines modified)
- `ledger/adapter.py` — pass-through wrapper update (~5 lines modified)

### Changes

**`handlers/link_commit.py`** — new sibling pass:

```python
async def _run_drift_classification_pass(
    ctx, pending: list[PendingComplianceCheck], commit_hash: str,
) -> tuple[list[PendingComplianceCheck], list[str]]:
    """Phase 4 (#61): per-region cosmetic-vs-semantic classification.

    Returns (surviving_pending, auto_resolved_region_ids).
    Each surviving pending check carries a `pre_classification` hint
    when the classifier was uncertain. Auto-resolved checks are
    stripped from the output AND written to compliance_check directly.

    Gated on the SAME ``cg_config.enhance_drift`` flag that gates
    ``_run_continuity_pass`` (Phase 3). One flag, one feature: when
    a user enables "enhanced drift handling", BOTH the continuity
    matcher and the cosmetic-classifier run. There is no separate
    Phase-4-only flag (O2).

    Failure-isolated: any exception falls through to the original
    pending list with no hint and no auto-resolve. Response shape
    is preserved.
    """
```

Wired in `handle_link_commit` after `_run_continuity_pass` strips moved/renamed regions:

```python
# Continuity pass first (Phase 3) — strips moved/renamed.
continuity_resolutions = await _run_continuity_pass(ctx, pending)
if continuity_resolutions: ...

# Drift classification pass second (Phase 4 / #61) — strips cosmetic;
# attaches pre_classification hint to uncertain ones.
# Same enhance_drift flag as the continuity pass (see O2).
pending, auto_resolved_ids = await _run_drift_classification_pass(
    ctx, pending, commit_hash=result["commit_hash"],
)
```

(O1: `auto_resolved_count` field on `LinkCommitResponse` is added in §Phase 1's `contracts.py` change list — not re-listed here.)

**`handlers/resolve_compliance.py`** — accept the new optional fields per verdict:

```python
# Around line 151, the upsert call:
await upsert_compliance_check(
    self._client,
    decision_id=v.decision_id, region_id=v.region_id,
    content_hash=v.content_hash, commit_hash=commit_hash,
    verdict=v.verdict, confidence=v.confidence, explanation=v.explanation,
    phase=phase, ephemeral=ephemeral,
    semantic_status=v.semantic_status,        # NEW
    evidence_refs=v.evidence_refs,            # NEW
)
```

**`ledger/queries.py::upsert_compliance_check`** — extend signature with two optional params, default-noop for legacy callers:

```python
async def upsert_compliance_check(
    client, *, ..., 
    semantic_status: str | None = None,
    evidence_refs: list[str] | None = None,
) -> None:
```

### Unit Tests

- **`tests/test_codegenome_phase4_link_commit.py`**:
  - `test_run_drift_classification_pass_off_when_flag_disabled` — no-op when `enhance_drift=False`.
  - `test_run_drift_classification_pass_strips_cosmetic_pendings`.
  - `test_run_drift_classification_pass_keeps_semantic_pendings_unchanged`.
  - `test_run_drift_classification_pass_attaches_hint_to_uncertain` — surviving pending has `pre_classification` populated.
  - `test_run_drift_classification_pass_writes_compliance_check_for_auto_resolved`.
  - `test_run_drift_classification_pass_failure_isolated` — classifier raises → unchanged pending list, no hints.
  - `test_link_commit_response_shape_unchanged_when_pass_disabled`.
  - `test_continuity_then_classification_order` — moved+cosmetic → continuity strips first, classification doesn't see the region.
  - `test_link_commit_response_includes_auto_resolved_count`.

- **`tests/test_codegenome_phase4_resolve_compliance.py`**:
  - `test_caller_verdict_with_semantic_status_persists`.
  - `test_caller_verdict_without_semantic_status_persists_as_null`.
  - `test_caller_verdict_overwrites_auto_resolution` — after auto-resolve writes a row, caller submits a different verdict for the same `(decision, region, content_hash)`; row is overwritten (UNIQUE index).
  - `test_caller_verdict_ignores_invalid_semantic_status_value` — Pydantic catches before reaching ledger.
  - `test_evidence_refs_round_trip_through_caller_verdict`.

---

## Phase 5: M3 benchmark fixture + integration test

### Affected Files

- `tests/fixtures/m3_benchmark/` — **new** directory with **30 paired fixtures** (F5: full multi-language coverage including uncertain band):
  - **Python (12 fixtures, 4 cosmetic / 4 semantic / 4 uncertain):**
    - `py_01_docstring_added.{old,new}.py` — cosmetic
    - `py_02_imports_reordered.{old,new}.py` — cosmetic
    - `py_03_blank_lines_added.{old,new}.py` — cosmetic
    - `py_04_comments_added.{old,new}.py` — cosmetic
    - `py_05_logic_removed.{old,new}.py` — semantic
    - `py_06_signature_changed.{old,new}.py` — semantic
    - `py_07_new_function_call.{old,new}.py` — semantic
    - `py_08_branching_added.{old,new}.py` — semantic
    - `py_09_typing_annotation_added.{old,new}.py` — uncertain (cosmetic-leaning)
    - `py_10_variable_rename_only.{old,new}.py` — uncertain
    - `py_11_assertion_text_changed.{old,new}.py` — uncertain
    - `py_12_constant_value_tuned.{old,new}.py` — uncertain
  - **JavaScript (3 fixtures):**
    - `js_01_jsdoc_added.{old,new}.js` — cosmetic
    - `js_02_logic_removed.{old,new}.js` — semantic
    - `js_03_default_arg_changed.{old,new}.js` — uncertain (F5: non-Python uncertain)
  - **TypeScript (3 fixtures):**
    - `ts_01_type_annotation_only.{old,new}.ts` — cosmetic
    - `ts_02_signature_changed.{old,new}.ts` — semantic
    - `ts_03_generic_constraint_added.{old,new}.ts` — uncertain (F5: non-Python uncertain)
  - **Go (3 fixtures):**
    - `go_01_block_comment_added.{old,new}.go` — cosmetic
    - `go_02_logic_removed.{old,new}.go` — semantic
    - `go_03_error_string_reworded.{old,new}.go` — uncertain (F5: non-Python uncertain)
  - **Rust (3 fixtures):**
    - `rs_01_doc_comment_added.{old,new}.rs` — cosmetic
    - `rs_02_signature_changed.{old,new}.rs` — semantic
    - `rs_03_lifetime_annotation_added.{old,new}.rs` — uncertain (F5: non-Python uncertain)
  - **Java (3 fixtures, F5: language was missing entirely from v1 plan):**
    - `java_01_javadoc_added.{old,new}.java` — cosmetic
    - `java_02_logic_removed.{old,new}.java` — semantic
    - `java_03_throws_clause_added.{old,new}.java` — uncertain
  - **C# (3 fixtures, F5: language was missing; F3: uses `c_sharp` language ID and `cs_*` filenames per the codebase convention):**
    - `cs_01_xml_doc_added.{old,new}.cs` — cosmetic
    - `cs_02_signature_changed.{old,new}.cs` — semantic
    - `cs_03_async_modifier_added.{old,new}.cs` — uncertain
- `tests/fixtures/m3_benchmark/expected.json` — expected `verdict` per fixture, used by the benchmark runner
- `tests/test_m3_benchmark.py` — **new**, ~80 lines

### Changes

**`tests/test_m3_benchmark.py`** — runs every fixture pair through `classify_drift` and verifies:

```python
def test_m3_precision_at_least_90_percent():
    """Issue #61 exit criterion: M3 precision ≥ 90% on benchmark corpus."""
    results = run_corpus()
    cosmetic_correct = sum(1 for r in results if r.expected == "cosmetic" and r.actual == "cosmetic")
    cosmetic_total   = sum(1 for r in results if r.expected == "cosmetic")
    semantic_correct = sum(1 for r in results if r.expected == "semantic" and r.actual == "semantic")
    semantic_total   = sum(1 for r in results if r.expected == "semantic")
    # Precision: of all "drifted" verdicts (i.e. NOT cosmetic), how many are real?
    auto_resolved_count = sum(1 for r in results if r.actual == "cosmetic")
    real_semantic_count = sum(1 for r in results if r.actual == "semantic" and r.expected == "semantic")
    false_positive_count = sum(1 for r in results if r.actual == "cosmetic" and r.expected == "semantic")
    assert false_positive_count / max(auto_resolved_count, 1) < 0.05, "False-positive rate must be < 5%"
    # Plus per-fixture asserts for the 4 mandatory exit criteria.

def test_docstring_addition_auto_resolved():
    # exit criterion: docstring addition → auto-resolved as semantically_preserved
def test_import_reordering_auto_resolved():
def test_logic_removal_not_auto_resolved():
def test_signature_change_not_auto_resolved():
```

### Unit Tests

Listed above — `test_m3_benchmark.py` itself is the test file.

---

## Test plan summary

| Phase | New test files | New unit tests | Integration tests |
|------:|----------------|---------------:|------------------:|
| 1 | `test_codegenome_resolve_compliance_persistence.py` | 7 (was 5; +2 for F1 changefeed regression) | 0 |
| 2 | `test_codegenome_drift_classifier.py` + `test_extract_call_sites.py` | 23 + 9 = 32 (was 19; +4 multi-lang completion, +9 call-site extractor per F4) | 0 |
| 3 | `test_codegenome_drift_service.py` | 8 | 0 |
| 4 | `test_codegenome_phase4_link_commit.py` + `_resolve_compliance.py` | 14 | 0 |
| 5 | `test_m3_benchmark.py` | 5 (4 exit-criterion + 1 corpus precision) | 1 |
| **Total** | **7** | **66** | **1** |

Plus regression: full `test_phase2_ledger.py`, `test_codegenome_*.py`, `test_alpha_flow.py` must stay green.

## Section 4 razor pre-check

| New file | Estimated LOC | Cap | Margin |
|---|---:|---:|---:|
| `code_locator/indexing/call_site_extractor.py` | ~150 | 250 | OK (new per F4) |
| `codegenome/drift_classifier.py` | ~210 | 250 | OK |
| `codegenome/diff_categorizer.py` | ~150 | 250 | OK (was ~220; split per O3) |
| `codegenome/_diff_dispatch.py` | ~120 | 250 | OK (new per O3) |
| `codegenome/_line_categorizers/__init__.py` | ~30 | 250 | OK |
| `codegenome/_line_categorizers/python.py` | ~80 | 250 | OK |
| `codegenome/_line_categorizers/javascript.py` | ~70 | 250 | OK |
| `codegenome/_line_categorizers/typescript.py` | ~30 | 250 | OK |
| `codegenome/_line_categorizers/go.py` | ~60 | 250 | OK |
| `codegenome/_line_categorizers/rust.py` | ~60 | 250 | OK |
| `codegenome/_line_categorizers/java.py` | ~70 | 250 | OK |
| `codegenome/_line_categorizers/c_sharp.py` | ~70 | 250 | OK (renamed from `csharp.py` per F3) |
| `codegenome/drift_service.py` | ~190 | 250 | OK |
| `tests/test_codegenome_drift_classifier.py` | ~240 | 250 | OK (was ~220; +20 for F3 parity test) |
| `tests/test_extract_call_sites.py` | ~120 | 250 | OK (new per F4) |
| `tests/test_codegenome_drift_service.py` | ~150 | 250 | OK |
| `tests/test_codegenome_phase4_link_commit.py` | ~140 | 250 | OK |
| `tests/test_codegenome_phase4_resolve_compliance.py` | ~100 | 250 | OK |
| `tests/test_codegenome_resolve_compliance_persistence.py` | ~85 | 250 | OK (was ~60; +25 for F1 changefeed regression test) |
| `tests/test_m3_benchmark.py` | ~80 | 250 | OK |

**New file (F4):** `code_locator/indexing/call_site_extractor.py` (~150 LOC). Sibling of `symbol_extractor.py`; reuses parser caching, exposes `extract_call_sites(content, language) -> set[str]`. Lives separately because `symbol_extractor.py` is already 459 LOC (pre-existing exception); a new file is the razor-compliant home.

Every new function targeted ≤ 40 lines; entry points (`classify_drift`, `evaluate_drift_classification`, `_run_drift_classification_pass`) explicitly tested for line count.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| AST extractor binary mismatch on Windows breaks `_signal_no_new_calls` | High — silent false positives | Reuse `code_locator.indexing.symbol_extractor`; test with the existing test fixtures that already pass on Linux. Fail closed: AST extraction error → signal returns 0.0 (treated as "new calls present" → not cosmetic). |
| Per-language line categorizer divergence (e.g. Go has no docstrings, Rust uses `///` for doc-comments) creates inconsistent `diff_lines` weight across languages | Medium | One categorizer per language; each one tested independently against language-specific fixtures (4 multi-lang test cases in Phase 2 + JS/TS/Go/Rust fixtures in Phase 5). The weight model still works because each language returns the same shape (`DiffStats`), and the cosmetic ratio is computed identically downstream. |
| Language detection at the call site (`handlers/link_commit.py`) needs to derive language from file extension | Low | `code_locator.indexing.symbol_extractor.lang_map` already does this — reuse via a one-line helper rather than duplicating the table. |
| M3 corpus is too small to validate < 5% false-positive rate | Medium | Start with 20 fixtures across 5 languages (Python: 8, JS/TS: 4, Go: 2, Rust: 2, Uncertain: 4). Issue allows promotion to LLM-based evaluation in Phase 7 if structural signals plateau. |
| Schema migration v12→v13 fails on a long-running embedded DB | Medium | Migration is purely additive (`DEFINE FIELD ... DEFAULT NONE`). Test via `test_codegenome_resolve_compliance_persistence.py::test_v13_migration_is_additive` against a v12-seeded DB. |
| Caller-LLM verdict overwrites auto-resolution silently | Low | This is intentional per the issue ("caller LLM always wins"). F1 remediation: §Phase 1 schema change adds `CHANGEFEED 30d INCLUDE ORIGINAL` on `compliance_check` (previously absent), so overwrites preserve the original auto-resolved row in the changefeed for 30 days. Regression test `test_compliance_check_changefeed_records_overwritten_row` pins the contract. |
| `pre_classification` hint inflates `LinkCommitResponse` payload | Low | Field is `None` for pendings outside the [0.30, 0.80) band. Worst case: ~150 bytes per uncertain pending. Acceptable. |

## Dependencies

- **Phase 1+2 (#71)** — required: `subject_identity.signature_hash` and `compliance_check` table. **Now in `dev`** (squash-merged via #71 → main → dev).
- **Phase 3 (#73)** — required for full M3 precision: `subject_identity.neighbors_at_bind` + the continuity-resolved auto-redirect path. **Now in `dev`** (merged 2026-04-28T20:59:56Z, commit `f9049fa` includes the 17 review fixes).
- **Section 4 razor** — every function ≤ 40 lines, every file ≤ 250 lines (per `CLAUDE.md`).
- **CLAUDE.md "Tool Changes Require Skill Changes" rule** — Phase 4 changes the `LinkCommitResponse` shape (new `auto_resolved_count` field — DONE in Phase 1; optional `pre_classification` on each pending — also DONE in Phase 1) AND `resolve_compliance` contract (new optional verdict fields — DONE in Phase 1 contracts). Skill files in `skills/bicameral-resolve-compliance/SKILL.md` and `skills/bicameral-sync/SKILL.md` (the active link-commit skill, per Phase 3 review) must be updated **when Phase 4 wires the actual handler logic** in Phase 4 of the plan (next implementation chunk).
- **Phase 1 of Phase 4** — schema v13 + contracts, sealed at commit `2afd52d`. Phases 2-5 build on this foundation.

## QOR audit gates this plan will pass through

1. **`/qor-audit`** — adversarial review of this plan before any code is written. Expected V1/V2/V3 checks: orphan macro-arch (does every new file have a clear caller?), residual unresolved-grounding markers, Section 4 razor estimates, contract additivity, schema migration safety.
2. **`/qor-implement`** — phase-by-phase implementation with TDD: tests in each phase land before the implementation files they exercise.
3. **`/qor-substantiate`** — full regression run after every phase. Hard gate before opening the PR.
4. **`/qor-document`** — update `docs/SYSTEM_STATE.md`, `docs/META_LEDGER.md`, and the two SKILL.md files (`pilot/mcp/skills/bicameral-link-commit/SKILL.md`, `pilot/mcp/skills/bicameral-resolve-compliance/SKILL.md`). The new test files DO introduce `MagicMock`/`AsyncMock` of `ledger`, `codegenome`, and `code_locator` adapters, but per O4 the `mocks/README.md` auto-tick rule applies only to first-class mock IMPLEMENTATIONS being replaced by real ones. Test-only mocks scoped to a single `tests/test_*.py` file do NOT need a `mocks/README.md` entry; they're pytest fixtures, not standalone mock packages. State this explicitly in the documentation pass.

## Stacking + merge strategy (refreshed v3)

The 3-deep stack from v2 is collapsed. Current state:

- `claude/codegenome-phase-4-qor` is rebased onto `BicameralAI/dev` directly. Single base, no intermediate stacking.
- `dev` already contains both Phase 1+2 (squash-merged via #71) and Phase 3 (squash-merged via #73, including the 17-item review hardening from `f9049fa`).
- Phase 4's PR will target **`BicameralAI/dev`**, NOT `main`. The `dev → main` aggregate PR is downstream and is Jin's call when the batch is ready for upstream main.

The user previously held PR #81 (provenance FLEXIBLE) due to schema-version conflict with PR #73; now that #73 has merged claiming v12, **#81 needs a rebase** to pick the next available version (v13 if Phase 4 Phase 1 hasn't merged yet, v14 otherwise). That rebase is independent of this plan but worth noting because the schema version a Phase 4 caller observes depends on which migration sequence executes.

## Implementation queue (Phases 2-5)

| Phase | Files | Tests | LOC | Status |
|------:|------:|------:|----:|---|
| 2 — Drift classifier (multi-language) + line categorizers + call_site_extractor | 14 | 32 | ~1100 | pending |
| 3 — Drift classification service | 2 | 8 | ~340 | pending |
| 4 — Handler integration (`link_commit` + `resolve_compliance`) | 6 | 14 | ~330 | pending |
| 5 — M3 benchmark fixture corpus (30 fixtures across 7 languages) + integration test | 31 (30 fixture pairs + 1 test runner) | 5 | ~80 + ~600 fixture | pending |
| **Total remaining** | **~53** | **59** | **~2450** | |

Phase 1 (already done, commit `2afd52d`) added 3 modified + 1 new file with 9 tests, ~145 LOC.

After Phase 4 ships and the `dev → main` PR is opened by Jin's call, this issue (#61) closes the assigned codegenome trilogy (#59 / #60 / #61).
