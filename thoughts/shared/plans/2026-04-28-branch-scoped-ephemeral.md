# Plan: Branch-Scoped Ephemeral Bind

**Date**: 2026-04-28  
**Status**: Implemented

---

## Problem

`handlers/bind.py` always used `authoritative_sha` (main HEAD) as the git ref
for file validation and content hash computation, regardless of which branch
the session was on. This caused two distinct failure modes on feature branches:

1. **Branch-local files** — a file added on a feature branch doesn't exist at
   `authoritative_sha`. The `get_git_content` guard returned `None` → bind
   rejected the file with a spurious error.

2. **Hash mismatch → phantom "drifted"** — for files that exist on both
   branches but with different content:
   - `bind` stored `H_main` (hash of main's content)
   - `link_commit(HEAD)` on the feature branch computed `H_branch` (hash of
     branch content)
   - `actual_hash ≠ stored_hash` + prior compliant verdict → `"drifted"` even
     though the LLM just bound to the current branch content seconds ago

   After `resolve_compliance(H_branch)` the compliance_check row exists for
   `H_branch`. On the second `link_commit`: `stored_hash = H_main`,
   `actual_hash = H_branch` → mismatch → `has_prior_compliant_verdict = True`
   → `"drifted"` — the decision could *never* reach `"reflected"` on the
   branch.

---

## Fix

In `_do_bind` (handlers/bind.py):

```python
effective_ref = authoritative_sha
if head_sha and head_sha not in ("HEAD", ""):
    from handlers.link_commit import _is_ephemeral_commit
    if _is_ephemeral_commit(head_sha, repo, authoritative_ref):
        effective_ref = head_sha
```

When `_is_ephemeral_commit` is `True` (current HEAD is not reachable from the
authoritative branch), all file-existence checks and hash computations use
`head_sha` instead of `authoritative_sha`. On non-ephemeral branches (main,
detached HEAD) the behavior is unchanged.

---

## Tests Added

| ID  | Name | Invariant |
|-----|------|-----------|
| E18 | `test_e18_bind_branch_local_file` | Bind to a file that only exists on the feature branch succeeds (no error, non-empty hash) |
| E19 | `test_e19_bind_modified_function_uses_branch_hash` | `bind_result.content_hash` equals the hash of the branch content, not main's content |
| E20 | `test_e20_bind_link_commit_hash_consistency_no_phantom_drift` | After bind on feature branch → resolve_compliance → second link_commit → status is "reflected", not "drifted" |
| E21 | `test_e21_ungrounded_feature_bind_reflected_ephemeral` | Full flow: ungrounded decision → feature branch bind → resolve_compliance → status is "reflected" and compliance_check.ephemeral=True |
| E22 | `test_e22_switch_to_main_no_stale_reflected` | After switching back to main without merging, status is NOT "reflected" — the implementation hasn't landed on main yet |

---

## Invariants

- `bind_result.content_hash` always reflects the content at `effective_ref`
  (branch HEAD when ephemeral, authoritative SHA when not)
- `link_commit` on the same branch computes `actual_hash` at HEAD → equals
  `stored_hash` → verdict lookup uses the correct hash → status transitions work
- On non-ephemeral branches, behavior is identical to pre-fix (no regression)
- Detached HEAD is non-ephemeral (safe default) — unaffected
- When returning to main after feature branch work, `already_synced` early-return
  now repairs stale ephemeral hashes: regions where `code_region.content_hash = H_branch`
  get updated to `H_main`, and decisions that were "reflected" via ephemeral verdicts
  become "drifted" (correctly — the implementation isn't on main yet)

---

## Bug B10: `already_synced` shortcut left stale ephemeral "reflected" on main

### Root cause

`ingest_commit` checks `state.last_synced_commit == commit_hash` → early return.
After returning to main (same `commit_hash` as last sync), the shortcut fired before
recomputing region hashes — leaving `code_region.content_hash = H_branch` and
`decision.status = "reflected"` from the feature branch verdict.

### Fix

In the `already_synced` path, when `is_authoritative=True`:
1. Fast-check for any `compliance_check.ephemeral = true` rows (no-op if none exist)
2. For each bound region, recompute `actual_hash` at `commit_hash`
3. If `actual_hash != stored_hash`: `update_region_hash`, `project_decision_status`,
   `update_decision_status` — same pipeline as the normal authoritative sweep

This restores the correct "drifted" status: `actual_hash=H_main` has no verdict,
but `has_prior_compliant_verdict=True` (the ephemeral H_branch verdict counts as
prior signal) → "drifted".

**Files changed**: `ledger/queries.py` (added `get_all_bound_regions`),
`ledger/adapter.py` (stale repair in `already_synced` branch).

---

## Related tests already passing

- E02: feature branch full cycle (uses code_regions in ingest, not stand-alone bind)
- E06: branch switch → stale verdict cleared
- E07: ephemeral promotion after FF-merge
- E15: custom authoritative ref
