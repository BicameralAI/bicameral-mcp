# Feedback on Bicameral Spec Governance — L1/L2 Claim/Identity Model

**Author:** Kevin Knapp (@Knapp-Kevin)
**Reviewers (upstream):** Jin Kuan
**Source spec:** Notion — *"Bicameral Spec Governance: L1/L2 Claim/Identity"* (Apr 26, 2026)
**Status:** initial response, written while resolving PR #71 review feedback and rebasing PR #73
**Related work:** #59 (Phase 1+2), #60 (Phase 3 continuity), #71, #73

---

## TL;DR

The L1/L2/L3 hierarchy is a real improvement over the implicit "everything is a decision" model the codegenome work assumed. Three concrete responses below, plus one schema deferral and a list of follow-ups we should track separately.

| Q | Answer | Rationale |
|---|--------|-----------|
| Q1 — Add `claim_evaluator` table now? | **Defer** | Worth modeling, but the data shape isn't stable yet; we'll over-fit. Add when the first evaluator implementation needs persistence. |
| Q2 — How to treat unclassified (`NULL`) `decision_level`? | **Tolerant — treat as L3 (skip codegenome write), and invest effort in classification later** | Safe-by-default; lets old rows coexist with new policy without backfill blocking the change. |
| Q3 — Handler-level vs ledger-internal L1 exemption guard? | **Handler-level** | Keeps `ledger/` mechanically dumb; composition belongs in `handlers/`. Makes the rule visible at the call site where engineers actually look. |

The L1 exemption guard for Q3 has shipped on `claude/codegenome-phase-1-2-qor` (commit `9a8c6ee`) with five regression tests covering all four level cases (L1/L2/L3/None) plus response-shape invariance.

---

## §1 — Why this model is right

The codegenome Phase 1+2 work treated *every* decision binding as a candidate for identity-graph entry. That conflated three very different artefacts:

1. **Behavioural claims** (e.g. "the system MUST emit a compliance verdict within 200ms of bind"). These don't have a fingerprintable code region — they're satisfied by emergent properties of many regions.
2. **Implementation identities** (e.g. "function `evaluate_continuity_for_drift` at `codegenome/continuity_service.py:42-89`"). These have crisp boundaries, a content hash, and a useful continuity story across renames/moves.
3. **Glue / infrastructure** (e.g. "we use SurrealDB v2"). Stable for the project's lifetime; fingerprinting them adds noise without signal.

Without a level discriminator, every L1 claim that happened to be bound to *any* region produced an `subject_identity` row, polluting the graph with high-churn fingerprints that drift on every refactor. The guard fixes this.

## §2 — Q1: Defer the `claim_evaluator` schema

**Recommendation:** don't land the table yet. Land the *concept* (PMs evaluate L1 via evidence/probes rather than fingerprints) in docs and the L1 exemption guard, but wait on persistence.

Reasons:

- The shape of an evaluator is genuinely uncertain. Is it (a) a stored procedure ID, (b) a probe URL + threshold, (c) a fixture path, or (d) a pointer to an external test suite? The proposal alludes to all four.
- Adding the table now bakes in one shape and makes it expensive to change. The codegenome migration ladder (`SCHEMA_VERSION 12`) is already long; we don't need a v13 we'll have to revisit.
- Nothing in PR #71 / #73 *needs* it. The L1 exemption guard works without persistence — it just doesn't write a row.

**What to do instead, now:**
- Document the intent in `docs/SHADOW_GENOME.md` under a new "L1 evaluation" subsection (one paragraph: PMs verify behaviour, not fingerprints).
- Open a tracking issue: *"Design `claim_evaluator` persistence shape"* — gated on having one real evaluator to model.

## §3 — Q2: Tolerant policy for `NULL` `decision_level`

**Recommendation:** treat `NULL` (a.k.a. unclassified) as **L3 — skip the codegenome write — but invest categorization effort going forward.**

Why tolerant rather than strict:

- A strict policy ("`NULL` is an error, refuse to bind") forces a backfill before this change can ship. The decision corpus has hundreds of rows from before the level concept existed; there's no automatic way to classify them.
- A liberal policy ("`NULL` defaults to L2, fingerprint everything") reproduces the original problem and silently re-pollutes the graph.
- A tolerant policy ("`NULL` defaults to L3, skip the fingerprint, log at debug level") is reversible, doesn't lose data (the row still exists), and makes classification a soft migration rather than a hard one.

The shipped guard (`handlers/bind.py`) implements this:

```python
if level == "L2":
    # write codegenome identity
else:
    logger.debug(
        "[bind] L1 exemption — skipping codegenome write for %s "
        "(decision_level=%r)", decision_id, level,
    )
```

`level` is `None` when:
- The lookup raises (rare; logged as warning).
- The row's `decision_level` field is missing or empty.

In both cases, we skip cleanly. The bind response shape is unchanged — that's covered by `test_bind_response_shape_unchanged_for_l1`.

**Categorization effort going forward:**

- Decisions surfaced through the dashboard should display their level, with an "unclassified" badge driving PM attention.
- A one-time bulk-classify utility (read-only, suggests level based on description regex + commit-graph density) could reduce the `NULL` count without forcing it.
- `decision_level` should become required on new rows once the badge surfaces unclassified ones (soft → hard).

## §4 — Q3: Handler-level guard, not ledger-internal

**Recommendation:** the L1 exemption check belongs in `handlers/bind.py`, not `ledger/queries.py`.

Reasons (concrete):

1. **Composition vs storage.** `ledger/` is already pure CRUD against SurrealDB tables. Embedding policy ("don't write if level == L1") inside `upsert_subject_identity` would mix policy with storage and force every other caller (continuity service, future tools) to re-discover the rule.
2. **Visibility at the call site.** When an engineer reads `bind.py` and sees the codegenome hook, the guard is right there; they don't need to chase three layers of indirection to learn "ah, this is no-op'd for L1."
3. **Testability.** The five new tests in `tests/test_codegenome_l1_exemption.py` mock `ledger.get_decision_level` and verify handler dispatch — much simpler than mocking inside the ledger layer.
4. **Backwards compatibility.** Tools that bypass `bind` (none today, but future ones) get default "fingerprint everything" semantics until explicitly opted in. That's the conservative choice for a graph DB you can't easily back out of.

The only ledger-side addition is `get_decision_level(decision_id) → str | None`, a single read. That's mechanical, not policy.

## §5 — Schema impact

The L1 exemption guard required **zero** schema changes. The `decision_level` field already exists on the `decision` table (added in v0.10.x for the dashboard); we just started reading it.

If `claim_evaluator` lands later (post-Q1 deferral), it would be a v13 migration with:
- New table `claim_evaluator`.
- New edge `claim_evaluator -> evaluates -> decision`.
- Optional field `decision.evaluator_strategy` (string enum).

Don't pre-allocate any of that today.

## §6 — Open follow-ups and bugs to file separately

While resolving #71 review feedback and rebasing #73, these surfaced as legitimate but out-of-scope:

| Item | Type | Where |
|------|------|-------|
| `binds_to.provenance` declared `TYPE object` (not `FLEXIBLE`) silently strips nested keys | bug | already filed as #72 |
| `events/writer.py:16` does top-level `import fcntl` (Unix-only) — breaks 17 ephemeral_authoritative tests on Windows | portability bug | already filed as #74 |
| 81 pre-existing Windows test failures (non-codegenome) | platform | already filed as #67–70 |
| Document `decision_level` field on `decision` table in `ARCHITECTURE_PLAN.md` | docs gap | new — file as docs issue |
| `INFO FOR TABLE` returns empty in v2 embedded — schema introspection tooling needs to use `schema.py` | already documented in CLAUDE.md | no action |
| `count() AS n` requires `GROUP ALL` in v2 embedded — caught during continuity_ledger tests | already documented (added to v2 quirks list) | no action |
| Dashboard should surface `decision_level` and an "unclassified" badge | feature | new — file once Q2 ships |
| Bulk-classify utility for legacy `NULL` rows | feature | new — gated on dashboard surfacing |

## §7 — Summary

- The hierarchy is well-motivated; the L1 exemption is the minimal correct change to honour it.
- Defer `claim_evaluator` until there's a concrete evaluator to model.
- Treat `NULL` as L3 by default, but make classification visible and progressively required.
- Keep policy in handlers, mechanism in ledger.

The guard is shipped on `claude/codegenome-phase-1-2-qor` and covered by five regression tests. PR #73 has been rebased on top of it and is ready for review.
