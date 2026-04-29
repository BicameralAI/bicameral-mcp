# `decision_level` — reference

**Field:** `decision.decision_level`
**Type:** `option<string>` (nullable)
**Constraint:** `$value = NONE OR $value IN ['L1', 'L2', 'L3']`
**Defined in:** `ledger/schema.py` (initial DEFINE) and `_migrate_v8_to_v9` (migration path)
**Read by:** `handlers/bind.py` (L1 exemption guard), dashboard (planned — see #76)

This reference exists because the field controls a behavioural switch in the codegenome write path that isn't obvious from the schema alone. The full rationale is in `docs/spec-governance-feedback.md`; this doc is the quick-lookup version.

---

## Values

| Value | Meaning | CodeGenome write? | Examples |
|-------|---------|-------------------|----------|
| `"L1"` | **Behavioural / product claim.** A statement about what the system MUST do, observable from outside. Verified by PMs via evidence/probes, not by code-region fingerprinting. | **No** — skip silently. | "MUST emit a compliance verdict within 200ms." "MUST persist drift events for 30 days." |
| `"L2"` | **Implementation identity.** A specific function/class/region with crisp boundaries, a content hash, and a useful continuity story across renames/moves. | **Yes** — write `subject_identity` row. | "Function `evaluate_continuity_for_drift` at `codegenome/continuity_service.py:42-89`." |
| `"L3"` | **Glue / infrastructure detail.** Stable for the project's lifetime; fingerprinting it adds noise without signal. | **No** — skip silently. | "We use SurrealDB v2 embedded." "We run on Python 3.13." |
| `NULL` (NONE) | **Unclassified.** Legacy row from before the level concept existed, or a freshly-created row whose level hasn't been set yet. | **No** — treated as L3 by tolerant policy. | Most decisions created before v0.9.3. |

The tolerant NULL policy is described in `docs/spec-governance-feedback.md` §3 (Q2). It's reversible — adding a `decision_level` later just changes future behaviour, not stored data — so legacy rows can stay unclassified until they surface in the dashboard ("unclassified" badge, planned in #76).

## Why the L1 exemption matters

Without this guard, every L1 claim that happens to be `bicameral.bind`-ed to *any* region produces a `subject_identity` fingerprint in the codegenome graph. L1 claims drift on every refactor — the underlying code that satisfies the claim changes constantly even when the claim itself is stable. The fingerprint then drifts too, generating noise that obscures real implementation drift on L2 rows.

The guard fixes this by reading `decision_level` before invoking `write_codegenome_identity`. Only `level == "L2"` proceeds. Everything else (`L1`, `L3`, `NULL`, lookup error) skips and logs at debug.

## Where the value comes from

- **New decisions** — set by the caller-LLM via `bicameral.ingest` when the decision is first recorded. The classification is best-effort; PMs can correct via the dashboard once #76 lands.
- **Legacy decisions** (pre-v0.9.3) — `NULL`. They will stay `NULL` until either:
  1. A PM edits them via the dashboard inline edit (#76 stretch goal).
  2. The bulk-classify utility (#77) proposes a level and a PM accepts.
- **Schema migration** — `_migrate_v8_to_v9` adds the field with `DEFAULT NONE`; no backfill is performed.

## Reading the value

From Python:

```python
from ledger.queries import get_decision_level

level = await get_decision_level(client, decision_id)
# level is one of: "L1", "L2", "L3", or None
```

From the adapter:

```python
level = await ledger.get_decision_level(decision_id)
```

The ledger-internal query is read-only and mechanical — policy lives at the handler layer (per `docs/spec-governance-feedback.md` §4 / Q3).

## Cross-references

- `docs/spec-governance-feedback.md` — the L1/L2 spec-governance proposal and the response that produced this field's enforcement model.
- `handlers/bind.py` — site of the L1 exemption guard.
- `ledger/queries.py::get_decision_level` — the read query.
- `tests/test_codegenome_l1_exemption.py` — the regression suite covering all four level cases plus response-shape invariance.
- Issue #75 — this documentation.
- Issue #76 — dashboard surfacing (planned).
- Issue #77 — bulk-classify utility (planned).
