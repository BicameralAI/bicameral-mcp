---
name: bicameral-admin-surrealql
description: Raw SurrealQL execution surface in the dashboard for operator debugging and emergency-correction tasks. Off-by-default; requires BICAMERAL_ENABLE_ADMIN_PANEL=1 at MCP server start. Read-only by default; mutations require BICAMERAL_ENABLE_ADMIN_PANEL_WRITES=1 PLUS in-UI typed confirmation PLUS a non-empty signer. Every query is audit-logged.
---

# Bicameral Admin SurrealQL Panel

Raw SurrealQL panel embedded in the dashboard for operator debugging without leaving the dashboard. This is the bottom-of-the-escape-hatch surface — the last resort when the structured tools (`bicameral.history`, `bicameral.remove_decision`, `bicameral.remove_source`, `bicameral.reset`) don't cover the situation.

## When to use

- Investigating a stale ledger entry that the dashboard renders incorrectly and you want to inspect the raw row before deciding how to act.
- Verifying that an event log replay produced the expected DB state after a `bicameral.reset --replay-from-events`.
- Spot-checking schema migrations during development.
- Reading the `_admin.jsonl` audit log (or its team-mode counterpart) via a `SELECT … FROM …` against a derived table.

## When NOT to use

- For routine corrections that the structured tools handle. Removing a decision: use `bicameral.remove_decision`. Removing a source: use `bicameral.remove_source`. Wiping the ledger: use `bicameral.reset`. The structured tools enforce idempotency, attribution, and event emission with semantics that match the rest of the system.
- For data exfiltration. Read mode is intentionally not authenticated beyond same-origin + env-flag — if the panel can run, anything on the same machine that knows the dashboard port can read all decisions. Treat it like a local debug pry-bar, not a production query surface.
- For ad-hoc DELETE/UPDATE without a backup. Write mode mutations bypass the normal handler validation; if you wreck the schema, the only recovery is `bicameral.reset` or a manual restore.
- In team mode without coordinating with co-authors. Writes from the admin panel emit `admin_query.executed` events into the shared event log, but a write that races with another author's `bicameral.ingest` can leave the local DBs out of sync until the next replay.

## Mandatory verification

1. **Verify both env flags before relying on write mode.** Reachability requires `BICAMERAL_ENABLE_ADMIN_PANEL=1` at MCP server start. Without it, the route returns 404. Mutations additionally require `BICAMERAL_ENABLE_ADMIN_PANEL_WRITES=1`. If the second flag is missing, the panel will reject `mode: "write"` requests with HTTP 403.

2. **Always start in read mode.** Read mode wraps the SQL in `BEGIN TRANSACTION ... CANCEL TRANSACTION` so even `DELETE` queries leave the DB unchanged. Use read mode to PROVE the SQL does what you expect before flipping write mode.

3. **Type the confirmation phrase verbatim.** Write mode in the dashboard requires typing the literal phrase `I accept the risk` into the confirmation modal. The modal pins this phrase against the JS check; misspellings won't toggle write mode.

4. **Provide a non-empty signer for every write.** The handler rejects write-mode queries with empty/whitespace `signer` field with HTTP 400. Use your email or agent id — this string is permanent in the audit log.

5. **Inspect the audit log after each write.** In team mode the events flow through `.bicameral/events/<author>.jsonl`. In local-only mode the panel writes to `.bicameral/events/_admin.jsonl`. The event carries `sql`, `mode`, `signer`, `elapsed_ms`, `error`, and `ts`. Confirm the entry you expect is there.

## Format

Direct HTTP (same-origin from the dashboard UI):

```http
POST /admin/query HTTP/1.1
Host: localhost:<dashboard_port>
Origin: http://localhost:<dashboard_port>
Content-Type: application/json

{"sql": "SELECT * FROM decision LIMIT 10", "mode": "read", "signer": ""}
```

Response:

```json
{
  "mode": "read-only",
  "rows": [...],
  "elapsed_ms": 4.23,
  "error": null
}
```

## Handler-side enforcement

- `BICAMERAL_ENABLE_ADMIN_PANEL` unset → 404 (the route is not even routable).
- `Origin` header missing or not `http://localhost:<dashboard_port>` → 403.
- `mode: "write"` without `BICAMERAL_ENABLE_ADMIN_PANEL_WRITES=1` → 403.
- `mode: "write"` with empty/whitespace `signer` → 400.
- Read mode wraps SQL in `BEGIN TRANSACTION; <sql>; CANCEL TRANSACTION;` (mutations roll back).
- Every executed query, success or failure, emits one `admin_query.executed` event:
  - Team mode: through the attached ledger writer (`<author>.jsonl`).
  - Local-only mode: appended to `.bicameral/events/_admin.jsonl`.
- The response payload's `mode` field is `"read-only"` or `"write"` and is the canonical operator-facing label (the SurrealDB result set may contain rows from a `DELETE` query even though the transaction rolled back; trust the `mode` field, not the row content).

## Audit trail

Every query writes one event:

```jsonl
{"schema_version":2,"event_type":"admin_query.executed","author":"<author>","timestamp":"...","payload":{"sql":"...","mode":"read-only"|"write","elapsed_ms":4.23,"error":null,"signer":"...","ts":"..."}}
```

In team mode the events replicate via the shared event-log backend (same path as `decision_ratified.completed`, `decision_removed.completed`, `source_removed.completed`).

## After execution

- Read mode: the operator sees the result rows in the dashboard, and the audit event records what was inspected. No DB state change.
- Write mode: the DB row state reflects the query, the audit event captures the full SQL + signer, and any downstream `bicameral.preflight` / `bicameral.history` calls render the new state. Note that admin writes do NOT participate in the normal handler-level event types (e.g., a direct `UPDATE decision:abc SET signoff.state = 'ratified'` will not emit `decision_ratified.completed`); the `admin_query.executed` event is the only record.

## Anti-patterns — REJECT these

| Anti-pattern | Why it fails |
|---|---|
| Running write-mode queries without dry-running them in read mode first | Read mode is the safety net; skip it and you've signed up for the consequences. |
| Using admin queries instead of `bicameral.remove_decision` / `bicameral.remove_source` | The structured tools emit canonical events (`decision_removed.completed`, `source_removed.completed`) that downstream agents key on. Admin writes only emit `admin_query.executed`, which is generic. |
| Running write mode without `BICAMERAL_ENABLE_ADMIN_PANEL_WRITES=1` at the server | The handler rejects with 403; no work is done. This is the gate working as designed. |
| Submitting an empty `signer` for a write | The handler rejects with 400 before any DB call. Provide your email or agent id. |
| Exposing the dashboard port to other machines on the LAN | The dashboard binds to 127.0.0.1 by default but is otherwise un-authenticated; the admin panel inherits that posture. Treat it like a local-only debug surface. |
