# Ephemeral / Authoritative — Branch-Aware Compliance Verdicts

_Introduced in v0.10.7; V2 behaviour shipped in v0.10.8._

---

## Problem

Bicameral compliance verdicts (`compliant` / `drifted`) are stored per
`(decision_id, region_id, content_hash)`.  Without branch awareness, two
failure modes emerge:

1. **Branch pollution** — a verdict written on a feature branch mutates
   `code_region.content_hash`, so switching back to `main` sees stale
   data that was never verified on `main`.
2. **Duplicate compliance work** — after a feature branch merges, the same
   content hash is already verified, but the system re-runs compliance from
   scratch because the previous verdict was branch-local.

---

## Core Design

Every `compliance_check` row carries an `ephemeral` boolean flag:

| Value | Meaning |
|---|---|
| `ephemeral = False` | Verdict written on the authoritative branch (e.g. `main`). Trusted as ground truth. |
| `ephemeral = True` | Verdict written on a feature branch. Provisional — valid for that branch's content hash, not yet promoted to authoritative. |

**Promotion**: when `resolve_compliance` is called on the authoritative
branch with a content hash that already has an `ephemeral=True` row, that
row is promoted to `ephemeral=False` automatically — no duplicate LLM call,
no duplicate compliance work.

**Pollution guard**: feature-branch calls to `link_commit` derive status
by comparing `actual_hash` vs `stored_hash` *locally*, without ever writing
back to `code_region.content_hash`.  The authoritative hash is never
overwritten by a feature-branch sweep.

---

## Lifecycle

```
feature branch                       authoritative branch (main)
──────────────────────────────────   ────────────────────────────────────
link_commit (feature)
  → sweep: branch-delta (all commits)
  → pending_compliance_checks emitted
  → is_ephemeral = True

resolve_compliance(verdicts)
  → compliance_check written
    ephemeral = True
  → decision status projected locally
    (actual_hash comparison, no mutation)

                     ── merge ──▶

                                     link_commit (main / post-merge)
                                       → sweep: latest commit only
                                       → same content_hash seen

                                     resolve_compliance(verdicts)
                                       → promote_ephemeral_verdict()
                                         sets ephemeral = False
                                       → no new LLM call needed
                                       → decision status = reflected
```

---

## Branch-Delta Sweep

On the first `link_commit` call for a feature branch, the sweep scope is
`"branch_delta"` rather than `"commit"`:

```
git diff <authoritative_ref>...HEAD --name-only
```

This covers *all* files touched across the entire feature branch, not just
the latest commit.  Earlier commits in a long-running branch are no longer
missed.

After the first sweep, subsequent calls on the same branch revert to
`"commit"` scope (incremental).  `sweep_scope` is returned in the
`link_commit` response so callers can tell which mode fired.

---

## `flow_id` Coupling

`link_commit` emits a `flow_id` that must be passed back in the subsequent
`resolve_compliance` call.  This ties verdicts to the specific sweep that
generated the pending checks:

- **Match** → `is_ephemeral` is inherited from the `link_commit` context.
- **Mismatch or missing** → `is_ephemeral = False` (safe default), warning
  logged.  Status is still computed correctly; only the ephemeral flag may
  be wrong.

Once MCP sampling lands, `link_commit` will fire `sampling/createMessage`
internally and receive verdicts inline, making `flow_id` an internal
implementation detail rather than a caller concern.

---

## Scenario Matrix (v0.10.8 — 17 cases)

| ID | Scenario | Result |
|---|---|---|
| E1 | Authoritative branch full cycle | `reflected`, `ephemeral=False` |
| E2 | Feature branch full cycle | `reflected`, `ephemeral=True` |
| E3 | Fast-forward merge → same hash | verdict survives |
| E4 | Squash merge → same content hash | `reflected` |
| E5 | Content change (prior compliant verdict exists) | `drifted` |
| E6 | Branch switch A → diverged B | status `drifted` |
| E7 | Feature → main after merge | ephemeral promoted to `False` |
| E8 | Detached HEAD | `ephemeral=False` (safe default) |
| E9 | Process restart (flag lost) | status still correct |
| E10 | Idempotent `resolve_compliance` | no duplicate rows (UNIQUE upsert) |
| E11 | `flow_id` mismatch | `ephemeral=False`, status correct |
| E12 | Branch-delta sweep catches earlier feature commits | drift flagged |
| E13 | Rebase onto main: same content, new SHA | verdict carries over |
| E14 | Deleted branch | verdict survives (hash-keyed) |
| E15 | `authoritative_ref=""` | degraded safe mode, `ephemeral=False` |
| E16 | `resolve_compliance` without prior `link_commit` | `reflected` |
| E17 | Ephemeral first-write-wins guard | promoted by `resolve_compliance` |

All 17 pass in v0.10.8.  See `tests/test_ephemeral_authoritative.py` for
the full fixture-driven regression suite.

---

## Key Invariants

1. `code_region.content_hash` is **never mutated** by a feature-branch
   sweep.  Only authoritative-branch calls may update it.
2. Verdicts are keyed by `(decision_id, region_id, content_hash)`.  The
   hash key means a verdict survives rebases, squash merges, branch
   deletions, and process restarts — as long as the content is unchanged.
3. `ephemeral=False` is ground truth.  `ephemeral=True` is provisional.
   Promotion is one-way and irreversible.
4. A missing or mismatched `flow_id` degrades gracefully to
   `ephemeral=False` — it never silently marks an authoritative verdict
   as ephemeral.

---

## Schema Fields

`compliance_check` table additions (v0.10.7+):

| Field | Type | Description |
|---|---|---|
| `ephemeral` | `bool` | `True` if written on a feature branch |
| `content_hash` | `string` | Hash at verdict time; used for promotion lookup |

`link_commit` response additions:

| Field | Description |
|---|---|
| `flow_id` | Opaque token; pass to `resolve_compliance` |
| `sweep_scope` | `"commit"` or `"branch_delta"` |
| `pending_ephemeral` | `True` if this sweep is on a feature branch |
