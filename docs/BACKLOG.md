# Project Backlog

## Blockers (Must Fix Before Progress)

### Security Blockers
<!-- Format: - [ ] [S#] Description -->
- [ ] [S1] No `SECURITY.md` in repo root — gold-standard incomplete.
      Recommended next step: `/qor-repo-scaffold` for SECURITY.md +
      vulnerability disclosure channel.

### Development Blockers
<!-- Format: - [ ] [D#] Description -->
- [ ] [D1] `SCHEMA_COMPATIBILITY` map in `ledger/schema.py` is missing
      an entry for v10 (jumps from `9: "0.9.3"` to nothing). Out of scope
      for #59 PR; flag for upstream maintainers.

## Backlog (Planned Work)
<!-- Format: - [ ] [B#] Description -->
- [ ] [B1] Split `ledger/queries.py` (1310 LOC) by concern
      (read / write / sync). Existing `queries_read.py` /
      `queries_write.py` / `queries_sync.py` indicate prior work; status
      of the split-vs-monolith strategy is unclear and should be
      reconciled.
- [x] [B2] Issue #60 — CodeGenome Phase 3 continuity evaluation in
      `link_commit` completed in PR #73 (stacked on #71, retargeted to dev).
- [ ] [B3] Issue #61 — CodeGenome Phase 4 semantic drift evaluation in
      `resolve_compliance`. Depends on #59; recommended after #60.

- [ ] [B4] M5 benchmark fixture corpus for Phase 3 continuity
      (`tests/fixtures/codegenome_m5/{moved,renamed,logic_removed,class_extracted}/`).
      Plan deferred from #60 PR — unit + integration tests in
      `test_codegenome_continuity*.py` cover the scenarios via stubs and
      provide adequate behavioral coverage; the real-fixture corpus
      enables the false-positive-rate benchmark called for in #60's exit
      criteria. Add as a follow-up PR before #61 starts.

- [ ] [B5] Event-sourced ledger RFC — append-only event log with
      SurrealDB/SQLite as a rebuildable projection. Tracked as Issue #97.
      v1.0.0 candidate; load-bearing iff multi-machine/team sync enters
      the roadmap. We already get partial event-sourcing today via the
      META_LEDGER chain and the `compliance_check` CHANGEFEED (Phase 4 /
      #61); the RFC asks whether to extend that pattern to all
      mutation-bearing tables. Cheap v0.14.0 wedge proposed in the issue:
      extend `CHANGEFEED 30d INCLUDE ORIGINAL` to `code_subject`,
      `subject_identity`, `binds_to`, `code_region` without committing
      to the full rewrite. Decision blocked on Jin's call about team
      sync as a v1.0.0 goal.

## Wishlist (Nice to Have)
<!-- Format: - [ ] [W#] Description -->
- [ ] [W1] Section-4 razor enforcement on legacy oversized files
      (`ledger/queries.py`, `ledger/adapter.py`, `contracts.py`). Tracked
      as backlog (B1); not blocking new feature work.
- [ ] [W2] CodeGenome Phase 5+ — evidence packets, chamber evaluations,
      benchmark-guided promotion. See `Bicameral-Arc.md` (architecture
      plan).

---
_Updated by /qor-* commands automatically_
