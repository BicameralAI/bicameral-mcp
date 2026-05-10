# Legacy ledger fixtures

Frozen DB states that reproduce historical corruption patterns. Each
file builds the bad state in a fresh `memory://` ledger using raw
`LedgerClient.execute` calls — never the real `init_schema` / `migrate`
path, which would refuse to apply the broken state.

The CI suite at `tests/test_legacy_ledger_fixtures.py` parametrizes
over every fixture:

1. Build the bad state.
2. Run `init_schema` + `migrate` (the production code path).
3. Assert the cleanup ran, the schema reaches `SCHEMA_VERSION`, no
   row violates the current type/UNIQUE constraints, and a second
   `init_schema` + `migrate` is a no-op (idempotent).

## Fixture index

| Fixture | Reproduces | First seen | Cleaned by |
|---|---|---|---|
| `v3_yields_source_span.py` | v3-era `yields.in = source_span:*` rows surviving past v5 cleanup | 2026-05-09 dogfood (#296 root cause) | v4→v5 + v16→v17 |

## Adding a fixture

Each fixture is a Python module exporting an async `build(client)`
coroutine that mutates the client's state. Keep them tiny — one bad
row per fixture is enough to assert the cleanup contract.

```python
# tests/fixtures/legacy_ledgers/<name>.py
async def build(client):
    await client.execute("…raw SurrealQL that produces the bad state…")
```

Then register it in `tests/test_legacy_ledger_fixtures.py::FIXTURES`.
