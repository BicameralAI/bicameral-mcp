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
- [ ] [B2] Issue #60 — CodeGenome Phase 3 continuity evaluation in
      `link_commit`. Depends on #59. Plan due after #59 merges.
- [ ] [B3] Issue #61 — CodeGenome Phase 4 semantic drift evaluation in
      `resolve_compliance`. Depends on #59; recommended after #60.

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
