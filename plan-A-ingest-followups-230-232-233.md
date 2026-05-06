# Plan A: Ingest middleware devil's-advocate followups (#230 + #232 + #233)

**change_class**: feature

**doc_tier**: minimal

**high_risk_target**: false

**terms_introduced**:
- term: malformed_payload
  home: handlers/ingest.py (new `_IngestRefused.reason` literal) + server.py `_ACTION_FOR_REFUSAL_REASON` entry

**boundaries**:
- limitations:
  - Aggregate-rate worst case (~86 GiB/day on default config) is documented but not enforced — sliding-window aggregate enforcement is deferred to the team-server-activation track.
  - `BICAMERAL_INGEST_*_DISABLE` truthy vocabulary unification covers the rate-limit env var only in this PR; broader `BICAMERAL_*` env-read audit is logged as a substrate observation but not wired (other env vars use distinct semantics like `!= "0"` that need per-site review).
- non_goals:
  - Rewriting the bucket implementation (stays token-bucket per #229's design dialogue choice).
  - Adding cross-session aggregate enforcement.
  - Telemetry-side privacy review of OTHER `_IngestRefused.detail` strings (canary, sensitive-data, size — those don't leak session UUIDs and are out of scope here).
- exclusions:
  - #209 regex refinement (separate plan).
  - #199 install repro (deferred, /qor-debug surface).

## Open Questions

None at plan time. Three issues are well-scoped with explicit acceptance criteria; this plan bundles them because they all touch `handlers/ingest.py` middleware + adjacent surfaces.

## Phase 1: Source-fix all three findings + doc updates

### Affected Files

- `tests/test_ingest_rate_limit.py` — update `test_check_rate_limit_raises_when_session_bucket_empty` assertion to verify scrubbed-detail shape; new test `test_check_rate_limit_disabled_via_truthy_variants` covering `=true`/`=yes`/`=on`; remove the file-local `_reset_rate_limit_registry` fixture (hoisted to conftest)
- `tests/test_ingest_size_limit.py` — new test `test_check_payload_size_handles_unserializable_payload` (circular-ref dict raises `_IngestRefused('malformed_payload')` not `ValueError`)
- `tests/conftest.py` — host the autouse `_reset_rate_limit_registry` fixture so any ingest-touching test in any file gets registry-clear behavior automatically (#233 fix)
- `handlers/ingest.py` —
  - `_check_rate_limit`: replace `f"session {session_id} bucket empty (...)"` with `f"bucket empty (burst=N, refill=R/s)"` (no UUID in detail) — #230 Finding 1 fix
  - `_check_rate_limit`: replace `os.getenv("BICAMERAL_INGEST_RATE_LIMIT_DISABLE", "").strip() == "1"` with `os.getenv("BICAMERAL_INGEST_RATE_LIMIT_DISABLE", "").strip().lower() in _GUIDED_MODE_TRUTHY` — #232 Finding 1 fix (import `_GUIDED_MODE_TRUTHY` from `context`)
  - `_check_payload_size`: wrap `json.dumps` in `try/except (ValueError, TypeError, RecursionError)` and translate to `_IngestRefused("malformed_payload", detail="payload is not JSON-serializable")` — #232 Finding 2 fix
  - Module-level docstring: add a "Limitations: aggregate-rate worst case" subsection per #230 Finding 2 (worst-case math, runaway-agent scenario, cross-link to team-server-activation track for the stricter-bound future work)
- `server.py` — `_ACTION_FOR_REFUSAL_REASON` adds `"malformed_payload"` → operator-actionable guidance pointing at the MCP request shape (#232 Finding 2 surface)
- `docs/research-brief-compliance-audit-2026-05-06.md` — § 2.4 LLM-08 walk gets a "Limitations" subsection documenting the ~86 GiB/day worst case (#230 Finding 2 doctrine update)
- `plan-216-ingest-size-and-rate-limit.md` — boundaries section gets a new bullet under `limitations` documenting the aggregate-rate worst case (#230 Finding 2 plan amendment for traceability)

### Changes

#### #230 Finding 1 — scrub session_id from `_IngestRefused.detail`

Source-fix in `handlers/ingest.py:_check_rate_limit`. Replace the f-string body:

```python
# Before:
detail=(
    f"session {session_id} bucket empty (burst={burst}, refill={refill_per_sec}/s)"
),

# After:
detail=f"bucket empty (burst={burst}, refill={refill_per_sec}/s)",
```

Drops the `session {uuid}` segment entirely. Operators get the bucket params (which they need to tune `.bicameral/config.yaml`); the session UUID is not action-relevant at the MCP boundary.

`server.py:_ACTION_FOR_REFUSAL_REASON["rate_limit_exceeded"]` already names the env var bypass — no change needed there.

#### #230 Finding 2 — document aggregate-rate worst case

Doctrine + plan + handler-docstring cross-references. No code change.

Worst-case math (default config burst=10, refill=1/s, size cap=1 MiB):
- 60-second window: 10 (burst) + 60 (refill) = 70 ingests × 1 MiB = 70 MiB
- Sustained: 1 MiB/s = ~86 GiB/day

Runaway agent scenarios that hit this: model regression producing infinite-loop tool calls, prompt-injection-hijacked agent in a re-ingest cycle, dev-time infinite-loop bug. Not a security crisis (size cap bounds per-payload damage), but a real operator-side disaster (ledger writer churn + disk pressure).

Each of the three target docs gets the math + scenario list + a forward-pointer to the team-server-activation track (which gates by sliding-window aggregate, not single-session token-bucket).

#### #232 Finding 1 — `_GUIDED_MODE_TRUTHY` vocabulary in rate-limit disable

Import `_GUIDED_MODE_TRUTHY` from `context` at the top of `handlers/ingest.py`. Replace:

```python
# Before:
if os.getenv("BICAMERAL_INGEST_RATE_LIMIT_DISABLE", "").strip() == "1":
    return

# After:
if os.getenv("BICAMERAL_INGEST_RATE_LIMIT_DISABLE", "").strip().lower() in _GUIDED_MODE_TRUTHY:
    return
```

`_GUIDED_MODE_TRUTHY = frozenset({"1", "true", "yes", "on"})` — already canonical at `context.py:14`. After this change, operators setting `=true`, `=yes`, `=on`, or `=1` all disable consistently.

The action-string at `server.py:_ACTION_FOR_REFUSAL_REASON["rate_limit_exceeded"]` mentions `=1` specifically; that's still valid (the canonical example) and doesn't need updating — the wider vocabulary is operator-discoverable via the truthy frozenset, not a documented contract change.

#### #232 Finding 2 — circular-ref edge case in `_check_payload_size`

Wrap the `json.dumps` call:

```python
def _check_payload_size(payload: dict, max_bytes: int) -> None:
    """..."""
    try:
        size = len(json.dumps(payload, default=str).encode("utf-8"))
    except (ValueError, TypeError, RecursionError) as exc:
        raise _IngestRefused(
            "malformed_payload",
            detail=f"payload is not JSON-serializable: {type(exc).__name__}",
        ) from exc
    if size > max_bytes:
        raise _IngestRefused(
            "size_limit_exceeded",
            detail=f"{size} bytes > {max_bytes} cap",
        )
```

Translates the unhandled-exception fail-open path to a structured refusal at the same MCP boundary as the other gates. The catch list is the documented set of exceptions `json.dumps` can raise; explicit `RecursionError` covers circular-reference shapes (which raise `ValueError` on most Pythons but `RecursionError` under deep nesting).

`server.py:_ACTION_FOR_REFUSAL_REASON` adds:

```python
"malformed_payload": (
    "the payload could not be JSON-serialized. Verify the request body "
    "is a plain dict of JSON-compatible primitives — no circular refs, "
    "no opaque objects, no non-serializable types."
),
```

#### #233 — hoist `_reset_rate_limit_registry` to `conftest.py`

Move the autouse fixture from `tests/test_ingest_rate_limit.py:29-34` to `tests/conftest.py`. Same body, same scope (function-level autouse). After hoist, any test file that imports `handlers.ingest` (directly or transitively) gets registry-clear behavior automatically.

Removes the implicit dependency in `test_handle_ingest_size_check_runs_before_rate_check` and prevents future test-isolation flakes from registry leakage.

### Unit Tests

- `tests/test_ingest_rate_limit.py::test_check_rate_limit_raises_when_session_bucket_empty` — UPDATED: invokes `_check_rate_limit` after exhausting the bucket; asserts `exc.detail == "bucket empty (burst=1, refill=0.01/s)"` (no UUID present); negative-substring check `"session " not in exc.detail` confirms the scrub.

- `tests/test_ingest_rate_limit.py::test_check_rate_limit_disabled_via_truthy_variants` — NEW: parametrized over `["1", "true", "yes", "on", "TRUE", "Yes"]`; for each, sets the env var via `monkeypatch.setenv`, invokes `_check_rate_limit("sid", burst=0, refill_per_sec=0.0)`, asserts no exception (the `burst=0, refill=0` config would normally refuse immediately; the env-var bypass short-circuits before bucket allocation, so success proves the truthy-set vocabulary works).

- `tests/test_ingest_size_limit.py::test_check_payload_size_handles_unserializable_payload` — NEW: constructs a circular dict (`d = {}; d["self"] = d`); invokes `_check_payload_size(d, max_bytes=1024)`; asserts `_IngestRefused` raised with `reason == "malformed_payload"` and detail mentions the exception type. Companion test with a `RecursionError`-raising mock (deep-nested object) asserts the same translation path.

- `tests/conftest.py` — NEW autouse fixture `_reset_rate_limit_registry`: clears `handlers.ingest._RATE_LIMIT_REGISTRY` before and after each test. Same body as the file-local version being removed.

- `tests/test_ingest_rate_limit.py::test_handle_ingest_size_check_runs_before_rate_check` — UNCHANGED: still asserts `"sid-order" not in _RATE_LIMIT_REGISTRY` to prove the rate-check never ran (size-check refused first). Now relies on the conftest fixture instead of the file-local one — same contract, just hoisted.

## CI Commands

- `python -m pytest tests/test_ingest_rate_limit.py tests/test_ingest_size_limit.py -v` — runs the new + updated tests
- `python -m pytest -v` — full regression (138+ ingest tests + the broader suite — the conftest hoist must not break any test outside ingest)
- `ruff check .` — lint gate
- `ruff format --check .` — format gate
- `python -c "from handlers.ingest import _check_rate_limit, _GUIDED_MODE_TRUTHY; print('imports ok')"` — smoke test that the new `_GUIDED_MODE_TRUTHY` import resolves
