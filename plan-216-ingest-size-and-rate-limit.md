# Plan: #216 — ingest boundary guardrails (LLM-02 size limit + LLM-08 rate limit)

**change_class**: feature

**doc_tier**: standard

**terms_introduced**:
- term: ingest_max_bytes
  home: context.py
- term: ingest_rate_limit_burst
  home: context.py
- term: ingest_rate_limit_refill_per_sec
  home: context.py
- term: token-bucket rate limiter
  home: handlers/ingest.py

**boundaries**:
- limitations: rate-limit state is in-process (per-server-restart); a malicious agent restart-looping has bigger problems than this gate. Token-bucket-burst defaults are tuned for single-user developer-tool workflow shape; team-server activation may want stricter sliding-window enforcement (revisit then). **Per-developer isolation update (post-#231)**: bucket scoping is now per-developer (salted-email-hash key) when `git config user.email` is available; falls back to process-wide single bucket only in test/CI mode. Two developers on the same install get distinct buckets — runaway loop on developer-A doesn't affect developer-B.
- non_goals: do not implement LLM-01 (prompt-injection canary scan — already filed as #212), LLM-04 (PII/secret/PHI/PAN detect-and-refuse — already filed as #213), or any other epic #216 sub-task. Do not extend rate-limit beyond `bicameral.ingest`. Do not add adversarial-human threat-model coverage (out of scope per LLM-08's deployment trigger).
- exclusions: not modifying `IngestPayload` Pydantic schema. Not changing `_normalize_payload` semantics. Not adding telemetry counters to the outbound `telemetry.py` relay (size + rate counters land in local `~/.bicameral/preflight_events.jsonl` only).

## Open Questions

All resolved during /qor-plan dialogue 2026-05-06:

- **Size-limit measurement**: serialized-JSON byte size, 1 MiB default, single config knob `ingest_max_bytes`. Per option α.
- **Rate-limit shape**: token bucket per `session_id`, 10 tokens initial burst, 1 token/sec refill. Two config knobs (`ingest_rate_limit_burst`, `ingest_rate_limit_refill_per_sec`). Per option (i).
- **Rate-limit override env**: `BICAMERAL_INGEST_RATE_LIMIT_DISABLE=1` for local debugging.
- **Test surface**: functionality tests on every helper + integration tests via `handle_ingest` + boundary tests via `server.call_tool`. Per § 6.2 control-acceptance template (positive / negative / bypass-override / fail-closed / telemetry / docs).
- **Refusal-mechanism shape (resolved post-audit, 2026-05-06)**: the v1 plan proposed `return IngestResponse(ok=False, ingested_count=0, reason=..., detail=...)`, but `IngestResponse` (`contracts.py:566-577`) has neither field — `ingested: bool` is the actual flag and four other fields are required. The audit (verdict VETO, category `infrastructure-mismatch`) surfaced three candidate remediations: (A) amend the contract, (B) add a separate refusal model, (C) raise to MCP boundary. **Path (C) selected**: `_IngestRefused` propagates from `handle_ingest`; `server.call_tool` catches it and translates to a `TextContent` error response, exactly the same shape as the existing `DestructiveMigrationRequired`/`SchemaVersionTooNew` precedent at `server.py:1250-1261`. No `IngestResponse` schema change. Smallest contract surface; existing pattern reused.

## Phase 1: LLM-02 — payload size limit

### Affected Files

- `tests/test_ingest_size_limit.py` — **new** functionality tests for size-limit gate
- `tests/test_server_ingest_refusal.py` — **new** functionality tests for the MCP-boundary translation of `_IngestRefused`
- `context.py` — add `_read_ingest_max_bytes` reader; new `ingest_max_bytes: int` field on `BicameralContext`; wire into `from_env`
- `handlers/ingest.py` — add `_IngestRefused` exception class; add `_check_payload_size(payload, max_bytes)` helper; call from `handle_ingest` before `_normalize_payload`; emit refusal telemetry then re-raise on `_IngestRefused`
- `server.py` — extend `call_tool`'s existing exception-translation `except` block (precedent at server.py:1250-1261 for `DestructiveMigrationRequired`/`SchemaVersionTooNew`) to also catch `_IngestRefused` and translate to a `TextContent` carrying structured fields (`error`, `detail`, `action`). **No `contracts.py` change** — `IngestResponse` schema is unchanged; the refusal path uses exception propagation rather than a dual-shape return.

### Changes

**`context.py`** — add module-level constants and reader following the existing string-field precedent:

```python
_DEFAULT_INGEST_MAX_BYTES = 1024 * 1024  # 1 MiB; bounds DoS, looser than any legitimate transcript
_INGEST_MAX_BYTES_MIN = 1024  # 1 KiB; below this is meaningless / config error
_INGEST_MAX_BYTES_MAX = 64 * 1024 * 1024  # 64 MiB; above this is operator footgun

def _read_ingest_max_bytes(repo_path: str) -> int:
    """Resolve `ingest_max_bytes` from `.bicameral/config.yaml` (example).
    
    Default 1 MiB. Clamped to [1 KiB, 64 MiB]; out-of-range values
    (negative, non-integer, beyond clamp) fall back to default with
    no silent acceptance. Read by `handlers.ingest._check_payload_size`."""
```

Add field on `BicameralContext`:
```python
ingest_max_bytes: int = _DEFAULT_INGEST_MAX_BYTES
```

Wire into `from_env`:
```python
ingest_max_bytes=_read_ingest_max_bytes(repo_path),
```

**`handlers/ingest.py`** — add helper (top of file, after imports):

```python
import json

class _IngestRefused(Exception):
    """Raised when an ingest is rejected by an entry-time guardrail.
    Carries a structured `reason` string for the response."""
    def __init__(self, reason: str, *, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


def _check_payload_size(payload: dict, max_bytes: int) -> None:
    """Raise `_IngestRefused` if the serialized payload exceeds `max_bytes`.
    Measurement is `len(json.dumps(payload).encode())` — captures every
    field the agent might supply, language-agnostic, single comparison."""
    size = len(json.dumps(payload, default=str).encode("utf-8"))
    if size > max_bytes:
        raise _IngestRefused(
            "size_limit_exceeded",
            detail=f"{size} bytes > {max_bytes} cap",
        )
```

Call at top of `handle_ingest` (before `_normalize_payload` line 229):

```python
try:
    _check_payload_size(payload, ctx.ingest_max_bytes)
except _IngestRefused as e:
    _emit_ingest_refusal_telemetry(e.reason, ctx.session_id)
    raise  # propagate to MCP boundary; server.call_tool translates to TextContent
```

**No `contracts.py` change.** `IngestResponse` schema (`contracts.py:566-577`) is unchanged. The refusal path uses **exception propagation rather than a dual-shape return** — selected via path (C) of the audit-cycle remediation menu. Helpers stay pure (gate logic, raise on fail); `handle_ingest` adds the telemetry concern as a thin try/except/emit/re-raise wrapper; `server.call_tool` adds the MCP-error translation. Three layers of separation; no Union return type, no `IngestResponse` field rename, no required-field gymnastics on the refusal path.

**`server.py` MCP-boundary translation** — extend the existing `except (DestructiveMigrationRequired, SchemaVersionTooNew)` block at `server.py:1250-1261` (or add an adjacent `except _IngestRefused` block before it) to handle ingest refusals:

```python
except _IngestRefused as exc:
    return [
        TextContent(
            type="text",
            text=json.dumps({
                "error": exc.reason,
                "detail": exc.detail,
                "action": _ACTION_FOR_REFUSAL_REASON.get(
                    exc.reason,
                    "review .bicameral/config.yaml limits and retry",
                ),
            }, indent=2),
        )
    ]
```

`_ACTION_FOR_REFUSAL_REASON` is a small dict mapping each refusal reason to operator-actionable guidance (e.g. `"size_limit_exceeded"` → `"split the payload into smaller ingests or raise ingest_max_bytes in .bicameral/config.yaml"`; `"rate_limit_exceeded"` → `"slow ingest cadence or raise ingest_rate_limit_burst / ingest_rate_limit_refill_per_sec, or set BICAMERAL_INGEST_RATE_LIMIT_DISABLE=1 for local debugging"`). Lives in `server.py` next to the existing `except` blocks.

**Import discipline**: `_IngestRefused` is imported into `server.py` from `handlers.ingest` alongside the existing `from handlers.ingest import handle_ingest` at `server.py:46`.

**Telemetry helper** — add `_emit_ingest_refusal_telemetry(reason, session_id)` in `handlers/ingest.py` that appends a refusal event to `~/.bicameral/preflight_events.jsonl` via `preflight_telemetry.write_ingest_refusal_event` (new public function in `preflight_telemetry.py`). The helper is invoked from `handle_ingest`'s except wrapper (NOT from inside `_check_payload_size` itself — keeps the gate helper pure of side-effects so it's reusable in non-ingest contexts).

### Unit Tests

- `tests/test_ingest_size_limit.py` (**new**):
  - `test_check_payload_size_passes_when_under_cap` — invokes `_check_payload_size({"k": "v"}, 1024)`; asserts no exception raised. Functionality.
  - `test_check_payload_size_raises_at_exact_excess` — payload serializes to `cap + 1` bytes; assert `_IngestRefused` raised with `reason == "size_limit_exceeded"`. Functionality on the boundary condition.
  - `test_check_payload_size_uses_serialized_byte_count` — payload with unicode chars whose UTF-8 byte size differs from char count; assert refusal triggers on the BYTE size, not the char size. Locks the measurement semantic.
  - `test_check_payload_size_includes_schema_overhead` — payload where the `text` content is small but nested-object overhead pushes serialized size past cap; assert refusal triggers. Locks that the gate measures the full serialized form, not just text fields.
  - `test_handle_ingest_raises_ingest_refused_on_size_excess` — call `handle_ingest(ctx, oversized_payload, ...)` with `ctx.ingest_max_bytes=1024`; assert `_IngestRefused` is raised with `exc.reason == "size_limit_exceeded"` and `exc.detail` containing the actual byte count and cap. Assert no ledger write occurred (mock the ledger; assert no `connect`/`ingest_payload` calls). Functionality test on integration boundary; exception propagation is the contract.
  - `test_handle_ingest_emits_refusal_telemetry_before_reraise_on_size_excess` — same call; assert `preflight_telemetry.write_ingest_refusal_event` was invoked with the right reason + session_id BEFORE the exception propagated out of `handle_ingest` (mock the writer; assert call ordering). Locks the telemetry-emission contract — operator audit-trail must fire even though the response shape is exception-based.
- `tests/test_server_ingest_refusal.py` (**new**):
  - `test_call_tool_translates_size_limit_refusal_to_text_content_error` — invoke `server.call_tool("bicameral.ingest", {"payload": oversized_payload})` (or the analogous test entrypoint that triggers the same `try`/`except` translation); assert returned value is a `list[TextContent]` with one entry, `json.loads(entry.text) == {"error": "size_limit_exceeded", "detail": <byte-count detail>, "action": <action-string>}`. Functionality test on the MCP-boundary translation.
  - `test_call_tool_action_string_for_size_limit_directs_operator_to_config_knob` — same call shape; assert the `action` field mentions both `ingest_max_bytes` (config remedy) and "split the payload" (operator-side remedy). Locks operator-actionable guidance discipline.
- `tests/test_context_ingest_max_bytes.py` (**new**):
  - `test_read_ingest_max_bytes_defaults_when_config_missing` — `repo_path` with no `.bicameral/config.yaml` (example); assert `_read_ingest_max_bytes(...)` returns `1048576`. Functionality.
  - `test_read_ingest_max_bytes_honors_valid_yaml_value` — config with `ingest_max_bytes: 524288`; assert returns `524288`.
  - `test_read_ingest_max_bytes_clamps_below_minimum` — config with `ingest_max_bytes: 100`; assert returns default (1 MiB), not `100`. Locks fail-closed-on-config-error.
  - `test_read_ingest_max_bytes_clamps_above_maximum` — config with `ingest_max_bytes: 999999999`; assert returns default. Locks operator-footgun protection.
  - `test_read_ingest_max_bytes_falls_back_on_non_integer` — config with `ingest_max_bytes: "not-an-int"`; assert returns default. Locks fail-soft on malformed yaml.

## Phase 2: LLM-08 — token-bucket rate limit per session_id

### Affected Files

- `tests/test_ingest_rate_limit.py` — **new** functionality tests for token-bucket helper + integration
- `context.py` — add `_read_ingest_rate_limit_burst` and `_read_ingest_rate_limit_refill_per_sec` readers; new `ingest_rate_limit_burst: int` and `ingest_rate_limit_refill_per_sec: float` fields on `BicameralContext`; wire into `from_env`
- `handlers/ingest.py` — add `_TokenBucket` class and module-level `_RATE_LIMIT_REGISTRY: dict[str, _TokenBucket]`; add `_check_rate_limit(session_id, burst, refill)` helper; call after size-limit check in `handle_ingest`'s try/except wrapper (`_IngestRefused` propagates; same exception type as Phase 1, different `reason`)
- `server.py` — extend the Phase-1 `_ACTION_FOR_REFUSAL_REASON` dict to map `"rate_limit_exceeded"` to operator-actionable guidance. **No new `except` block needed** — the Phase 1 `except _IngestRefused` already catches both reasons.
- `preflight_telemetry.py` — extend `write_ingest_refusal_event` (added in Phase 1) to handle the new `rate_limit_exceeded` reason

### Changes

**`context.py`** — same precedent as Phase 1:

```python
_DEFAULT_INGEST_RATE_LIMIT_BURST = 10
_DEFAULT_INGEST_RATE_LIMIT_REFILL_PER_SEC = 1.0
_INGEST_RATE_LIMIT_BURST_RANGE = (1, 1000)
_INGEST_RATE_LIMIT_REFILL_RANGE = (0.01, 100.0)

def _read_ingest_rate_limit_burst(repo_path: str) -> int: ...
def _read_ingest_rate_limit_refill_per_sec(repo_path: str) -> float: ...
```

Add fields + wire into `from_env`.

**`handlers/ingest.py`** — token-bucket implementation:

```python
import time
import threading

class _TokenBucket:
    """Lazy-refill token-bucket. Single-counter, single-timestamp state.
    
    `take()` returns True if a token is available (and consumes it),
    False if the bucket is empty. Refill is computed on access — no
    background timer. Thread-safe via internal lock for concurrent
    handler dispatches in the same process."""
    def __init__(self, burst: int, refill_per_sec: float):
        self._burst = float(burst)
        self._refill = refill_per_sec
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()
    
    def take(self) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self._burst, self._tokens + elapsed * self._refill)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


# Module-level registry; one bucket per session_id. Reset on server restart.
_RATE_LIMIT_REGISTRY: dict[str, _TokenBucket] = {}
_RATE_LIMIT_REGISTRY_LOCK = threading.Lock()


def _check_rate_limit(session_id: str, burst: int, refill_per_sec: float) -> None:
    """Raise `_IngestRefused('rate_limit_exceeded', ...)` if the bucket
    for `session_id` has no tokens. Disabled by `BICAMERAL_INGEST_RATE_LIMIT_DISABLE=1`."""
    import os
    if os.getenv("BICAMERAL_INGEST_RATE_LIMIT_DISABLE", "").strip() == "1":
        return
    with _RATE_LIMIT_REGISTRY_LOCK:
        bucket = _RATE_LIMIT_REGISTRY.get(session_id)
        if bucket is None:
            bucket = _TokenBucket(burst, refill_per_sec)
            _RATE_LIMIT_REGISTRY[session_id] = bucket
    if not bucket.take():
        raise _IngestRefused(
            "rate_limit_exceeded",
            detail=f"session {session_id} bucket empty (burst={burst}, refill={refill_per_sec}/s)",
        )
```

Extend the Phase-1 try/except wrapper in `handle_ingest` to also call `_check_rate_limit`:

```python
try:
    _check_payload_size(payload, ctx.ingest_max_bytes)
    _check_rate_limit(
        ctx.session_id,
        ctx.ingest_rate_limit_burst,
        ctx.ingest_rate_limit_refill_per_sec,
    )
except _IngestRefused as e:
    _emit_ingest_refusal_telemetry(e.reason, ctx.session_id)
    raise  # propagate to MCP boundary; server.call_tool translates
```

`_IngestRefused` is the same exception type for both gates; only the `reason` field differs (`"size_limit_exceeded"` vs `"rate_limit_exceeded"`). The server-boundary `except _IngestRefused` block from Phase 1 catches both; only the `_ACTION_FOR_REFUSAL_REASON` dict needs a new entry for `"rate_limit_exceeded"`. Ordering invariant: size-check runs before rate-check (cheaper short-circuit; the size-excess test in Phase 1 locks this).

### Unit Tests

- `tests/test_ingest_rate_limit.py` (**new**):
  - `test_token_bucket_take_consumes_one_when_full` — `_TokenBucket(burst=10, refill=1.0)`; assert `take()` returns True, internal `_tokens` reduces from 10 to 9. Functionality.
  - `test_token_bucket_returns_false_when_empty` — exhaust bucket via 10 sequential `take()` calls; 11th call returns False. Functionality.
  - `test_token_bucket_refills_over_time` — exhaust bucket, mock `time.monotonic` to advance by 5 seconds, assert next 5 `take()` calls return True. Locks the lazy-refill semantic on observable behavior.
  - `test_token_bucket_caps_at_burst` — quiet for 100 seconds (mock time), assert bucket has at most `burst` tokens (not `burst + 100 * refill`). Locks the cap semantic.
  - `test_check_rate_limit_passes_when_disabled_via_env` — `monkeypatch.setenv("BICAMERAL_INGEST_RATE_LIMIT_DISABLE", "1")`; call `_check_rate_limit("sid", 1, 1.0)` 100 times; assert no exception. Locks env override.
  - `test_check_rate_limit_raises_when_session_bucket_empty` — call `_check_rate_limit("sid", 1, 0.0)` twice; first call passes, second raises `_IngestRefused` with `reason == "rate_limit_exceeded"`. (Refill 0 means the bucket never refills, so the second call is guaranteed empty.)
  - `test_check_rate_limit_isolates_sessions` — exhaust bucket for `session_a`; assert `session_b` still has full bucket. Locks per-session state isolation.
  - `test_handle_ingest_raises_ingest_refused_on_rate_limit` — drive bucket to empty via repeated `handle_ingest` calls; assert next call raises `_IngestRefused` with `exc.reason == "rate_limit_exceeded"` and `exc.detail` mentioning the empty bucket. Functionality test on integration boundary.
  - `test_handle_ingest_emits_refusal_telemetry_before_reraise_on_rate_limit` — same drive-to-empty + next call; assert `preflight_telemetry.write_ingest_refusal_event` invoked with `rate_limit_exceeded` BEFORE the exception propagated out of `handle_ingest`. Locks the telemetry-emission contract.
  - `test_handle_ingest_size_check_runs_before_rate_check` — call with oversized payload while bucket is also empty; assert raised `_IngestRefused.reason == "size_limit_exceeded"` (size check runs first; the rate-check is unreached and bucket state is unchanged). Locks the ordering invariant.
  - `test_call_tool_translates_rate_limit_refusal_to_text_content_error` — drive `server.call_tool("bicameral.ingest", ...)` calls until the bucket empties; assert next invocation returns `list[TextContent]` with `json.loads(entry.text) == {"error": "rate_limit_exceeded", "detail": ..., "action": <action-string-mentioning-config-knobs-and-disable-env>}`. Functionality test on the MCP-boundary translation for the rate-limit reason.
- `tests/test_context_ingest_rate_limit.py` (**new**):
  - `test_read_ingest_rate_limit_burst_defaults_when_config_missing` — assert returns 10. Functionality.
  - `test_read_ingest_rate_limit_burst_honors_valid_yaml_value` — config with `ingest_rate_limit_burst: 25`; assert returns 25.
  - `test_read_ingest_rate_limit_burst_clamps_out_of_range` — config with `ingest_rate_limit_burst: 0` and `ingest_rate_limit_burst: 99999`; both fall back to default.
  - `test_read_ingest_rate_limit_refill_defaults_when_config_missing` — assert returns 1.0. Functionality.
  - `test_read_ingest_rate_limit_refill_honors_valid_yaml_value` — config with `ingest_rate_limit_refill_per_sec: 0.5`; assert returns 0.5.
  - `test_read_ingest_rate_limit_refill_clamps_out_of_range` — `0.0` and `1000.0` both fall back. (Refill of exactly 0 would lock the bucket forever after first burst — treat as malformed.)

## CI Commands

- `pytest tests/test_ingest_size_limit.py tests/test_context_ingest_max_bytes.py tests/test_server_ingest_refusal.py -v` — Phase 1 functionality + config-reader + boundary-translation tests.
- `pytest tests/test_ingest_rate_limit.py tests/test_context_ingest_rate_limit.py -v` — Phase 2 functionality + config-reader tests (boundary-translation for rate-limit lives in `tests/test_server_ingest_refusal.py` (**new**) per the cross-phase test file).
- `pytest tests/test_ingest*.py tests/test_context*.py tests/test_server_ingest_refusal.py -v` — full regression sweep over the ingest + context + server-boundary surface (catches signer-email + config integration regressions).
- `pytest tests/ -k ingest -v` — broader regression across any test file referencing ingest behavior.
- `ruff check handlers/ingest.py context.py server.py tests/test_ingest_size_limit.py tests/test_ingest_rate_limit.py tests/test_context_ingest_max_bytes.py tests/test_context_ingest_rate_limit.py tests/test_server_ingest_refusal.py` — lint clean on every touched + new file.
- `ruff format --check handlers/ingest.py context.py server.py tests/test_ingest_size_limit.py tests/test_ingest_rate_limit.py tests/test_context_ingest_max_bytes.py tests/test_context_ingest_rate_limit.py tests/test_server_ingest_refusal.py` — format clean (avoids the #199 ruff-format CI miss).
