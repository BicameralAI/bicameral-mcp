"""Handler for /ingest MCP tool.

Thin orchestration: validate payload, resolve symbols, ingest into ledger, then sync.
Auto-grounding removed in caller-LLM binding flow (v0.5.1+).

Limitations — aggregate-rate worst case (#230 Finding 2):
  The token-bucket rate gate slows BURST consumption per session but does
  not bound aggregate throughput across time. Default config (burst=10,
  refill=1/s, size cap=1 MiB) admits ~70 ingests in any 60-second window
  and 1 MiB/s sustained, which works out to ~86 GiB/day in the worst case
  (runaway agent loop, model regression producing infinite tool calls,
  prompt-injection-hijacked re-ingest cycle, dev-time infinite-loop bug).
  Not a security crisis — the size cap bounds per-payload damage and the
  in-process registry is reset on server restart — but it IS an operator-
  side disaster (ledger writer churn + disk pressure). Stricter aggregate
  enforcement (sliding-window cross-session bound) is deferred to the
  team-server-activation track.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import UTC
from typing import Literal

import preflight_telemetry

# #232 Finding 1: cross-module use of context.py's private truthy frozenset
# is intentional — it's the canonical vocabulary for BICAMERAL_* env-var
# toggles (1/true/yes/on, case-insensitive). Renaming to a public alias is
# out of scope here; #232 acceptance only requires vocabulary parity across
# the existing toggle reads.
from context import _GUIDED_MODE_TRUTHY
from contracts import (
    BriefEnvelope,
    BriefGap,
    ContextForCandidate,
    CreatedDecision,
    IngestPayload,
    IngestResponse,
    IngestStats,
    SourceCursorSummary,
)

logger = logging.getLogger(__name__)


class _IngestRefused(Exception):
    """Raised when an ingest is rejected by an entry-time guardrail.

    Carries a structured ``reason`` string for the MCP-boundary response
    translation and an optional human-readable ``detail`` describing the
    specific cause (byte counts, bucket state, etc.). Caught at the
    ``server.call_tool`` boundary; never bubbles to the agent as a raw
    exception.
    """

    def __init__(self, reason: str, *, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


# #418 Phase 0a: gate classification. Hard gates raise unconditionally in
# both active and passive ingest modes — operator MUST intervene (rotate
# credential, handle regulated data out-of-band, fix caller serialization
# bug). Soft gates fail-fast in active mode (today's behavior) but
# WARN+DLQ+continue in passive mode (poller resilience).
#
# Classification is principled, not arbitrary:
#   - ``sensitive_data:*`` — credential/PHI/PAN leak; skipping the item is
#     not safe (the data is already in the agent's context window).
#   - ``malformed_payload`` — caller bug; the payload isn't even JSON-
#     serializable, so we couldn't write the DLQ sidecar even if we wanted to.
#   - ``size_limit_exceeded``, ``rate_limit_exceeded``,
#     ``injection_canary_match`` — recoverable by skipping the item;
#     operator reviews the DLQ.
_HARD_GATE_PREFIXES = ("sensitive_data:",)
_HARD_GATE_REASONS = frozenset({"malformed_payload"})


def _is_hard_gate(reason: str) -> bool:
    """Return True when a refusal must fail-fast in both ingest modes."""
    if reason in _HARD_GATE_REASONS:
        return True
    return any(reason.startswith(p) for p in _HARD_GATE_PREFIXES)


def _emit_ingest_refusal_telemetry(
    reason: str,
    session_id: str,
    *,
    disposition: str = "rejected",
) -> None:
    """Dual-write the refusal event to JSONL telemetry + audit log.

    Side-effect-only helper invoked by ``handle_ingest`` after a guard
    raises ``_IngestRefused`` and before the exception re-propagates to
    the MCP boundary. Kept out of the gate helpers themselves so those
    stay pure (raise on fail; reusable in non-ingest contexts).

    Each write is exception-isolated; failure of either surface MUST NOT
    block the other so the original ``_IngestRefused`` propagates cleanly
    via the caller's ``raise``. JSONL writes are nominally trusted not
    to raise, but the explicit ``try/except`` formalizes that trust at
    the helper level and is required for the bidirectional-independence
    test contract (#227 Phase 2).

    ``disposition`` (#418 Phase 0a): one of ``"rejected"`` (default,
    hard-gate OR active-mode soft-gate refusal) or ``"warned_and_dlqd"``
    (passive-mode soft-gate refusal — item routed to the DLQ store and
    the caller continues). Additive on the audit event; existing
    consumers see the field appear without schema migration.
    """
    from audit_log import AuditEventType
    from audit_log import emit as audit_emit

    try:
        preflight_telemetry.write_ingest_refusal_event(reason=reason, session_id=session_id)
    except Exception:  # noqa: BLE001 — audit-log surface must not be blocked
        pass
    try:
        audit_emit(
            AuditEventType.INGEST_REFUSAL,
            session_id=session_id,
            reason=reason,
            disposition=disposition,
        )
    except Exception:  # noqa: BLE001 — refusal flow must not be broken by emit
        pass


def _check_payload_size(payload: dict, max_bytes: int) -> None:
    """Raise ``_IngestRefused`` if the serialized payload exceeds ``max_bytes``.

    Measurement is ``len(json.dumps(payload).encode("utf-8"))`` — captures
    every field the agent might supply, language-agnostic, single
    comparison. Pure: no telemetry side-effect; the wrapping try/except
    in ``handle_ingest`` records the refusal event before re-raising.

    #232 Finding 2: a payload that is not JSON-serializable (circular ref,
    deeply nested object, opaque type whose ``__str__`` raises) would
    previously leak ``ValueError`` / ``TypeError`` / ``RecursionError``
    past the gate to the MCP boundary's generic exception handler.
    Translate to ``_IngestRefused('malformed_payload', ...)`` at the same
    boundary as the other refusals — closes the fail-open path.
    """
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


class _TokenBucket:
    """Lazy-refill token bucket — single counter, single timestamp.

    ``take()`` returns True when a token is available (and consumes it),
    False when the bucket is empty. Refill is computed on access, no
    background timer. Thread-safe via internal lock for concurrent
    handler dispatches in the same process.
    """

    def __init__(self, burst: int, refill_per_sec: float) -> None:
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


# Module-level registry: one bucket per session_id. Reset on server
# restart by design — rate-limit state is in-process only (LLM-08
# threat model is agent loops, not malicious restart-loops).
_RATE_LIMIT_REGISTRY: dict[str, _TokenBucket] = {}
_RATE_LIMIT_REGISTRY_LOCK = threading.Lock()


def _check_rate_limit(session_id: str, burst: int, refill_per_sec: float) -> None:
    """Raise ``_IngestRefused('rate_limit_exceeded', ...)`` when the bucket
    for ``session_id`` has no tokens. Disabled entirely by setting
    ``BICAMERAL_INGEST_RATE_LIMIT_DISABLE`` to a truthy value (1/true/yes/on,
    case-insensitive — see ``context._GUIDED_MODE_TRUTHY``).

    #230 Finding 1: the refusal detail does NOT include ``session_id``. The
    raw session UUID is process-fingerprinting state that surrounding
    telemetry writers hash via per-install salt; emitting it raw at the
    MCP boundary (which the agent then relays into operator-visible context)
    is inconsistent with that posture. Operators get the bucket params
    they need to tune ``.bicameral/config.yaml``; the session UUID is not
    action-relevant here.
    """
    env_val = os.getenv("BICAMERAL_INGEST_RATE_LIMIT_DISABLE", "").strip().lower()
    if env_val in _GUIDED_MODE_TRUTHY:
        return
    with _RATE_LIMIT_REGISTRY_LOCK:
        bucket = _RATE_LIMIT_REGISTRY.get(session_id)
        if bucket is None:
            bucket = _TokenBucket(burst, refill_per_sec)
            _RATE_LIMIT_REGISTRY[session_id] = bucket
    if not bucket.take():
        raise _IngestRefused(
            "rate_limit_exceeded",
            detail=f"bucket empty (burst={burst}, refill={refill_per_sec}/s)",
        )


def _check_canary(payload: dict) -> None:
    """Raise ``_IngestRefused('injection_canary_match', ...)`` if the
    serialized payload contains any catalog-pattern hit (#212 LLM-01).

    Detector dispatches via the module-level pointer
    ``handlers.canary_patterns._canary_detect`` so a v2 classifier-backed
    implementation can take effect with a single-line module-level swap.

    Disabled by setting ``BICAMERAL_INGEST_CANARY_DISABLE=1`` — operator
    escape for known-false-positive workflows or controlled tests. The
    env-disable shortcuts the detector cost (does not even serialize the
    payload).
    """
    if os.getenv("BICAMERAL_INGEST_CANARY_DISABLE", "").strip().lower() in _GUIDED_MODE_TRUTHY:
        return
    from handlers import canary_patterns

    serialized = json.dumps(payload, default=str)
    hits = canary_patterns._canary_detect(serialized)
    if not hits:
        return
    first = hits[0]
    raise _IngestRefused(
        "injection_canary_match",
        detail=(
            f"category={first.category}; "
            f"pattern_id={first.pattern_id}; "
            f"excerpt={first.match_excerpt!r}; "
            f"catalog={canary_patterns._CANARY_CATALOG_VERSION}; "
            f"total_hits={len(hits)}"
        ),
    )


def _check_sensitive(payload: dict) -> None:
    """Raise ``_IngestRefused('sensitive_data:<cls>', ...)`` when the
    serialized payload contains any secret / PHI / PAN hit (#213
    LLM-04 + HIPAA-01 + PCI-01 fold).

    Refusal class is the FIRST hit's class; ``detail`` carries the
    full ``by_class`` count so the operator sees the full picture
    even when multiple classes triggered. Detector dispatches via
    the module-level pointer
    ``handlers.sensitive_patterns._sensitive_detect`` for v2 swap.

    Disabled by ``BICAMERAL_INGEST_SECRET_DISABLE=1`` (single master
    env disable covers all three classes; YAGNI on per-class
    disables in v1). The env disable shortcuts the detector cost
    (does not even serialize the payload).
    """
    if os.getenv("BICAMERAL_INGEST_SECRET_DISABLE", "").strip().lower() in _GUIDED_MODE_TRUTHY:
        return
    from handlers import sensitive_patterns

    serialized = json.dumps(payload, default=str)
    hits = sensitive_patterns._sensitive_detect(serialized)
    if not hits:
        return
    first = hits[0]
    counts: dict[str, int] = {}
    for h in hits:
        counts[h.cls] = counts.get(h.cls, 0) + 1
    raise _IngestRefused(
        f"sensitive_data:{first.cls}",
        detail=(
            f"class={first.cls}; "
            f"pattern_id={first.pattern_id}; "
            f"excerpt={first.match_excerpt!r}; "
            f"catalog={sensitive_patterns._SENSITIVE_CATALOG_VERSION}; "
            f"total_hits={len(hits)}; "
            f"by_class={counts}"
        ),
    )


def _normalize_payload(payload: dict) -> dict:
    """Validate and normalize ingest payload using Pydantic contracts.

    1. Validates the raw dict against IngestPayload (fails fast on bad types)
    2. If ``mappings`` is already present, returns as-is (internal format)
    3. If ``decisions``/``action_items``/``open_questions`` present, converts to mappings
    """
    validated = IngestPayload.model_validate(payload)

    # Already has mappings — convert back to dict and return
    if validated.mappings:
        return validated.model_dump()

    mappings: list[dict] = []
    source_meta = {
        "source_type": validated.source,
        "source_ref": validated.title,
        "speakers": validated.participants,
        "meeting_date": validated.date,
    }

    for d in validated.decisions:
        text = d.description or d.title or d.text
        if not text:
            continue
        span_text = d.source_excerpt or text
        mapping: dict = {
            "intent": text,
            "span": {
                **source_meta,
                "text": span_text,
                "source_ref": d.id or source_meta["source_ref"],
                "speakers": d.participants or source_meta["speakers"],
            },
            "symbols": [],
            "code_regions": [],
        }
        if d.signoff is not None:
            mapping["signoff"] = d.signoff
        if d.feature_group is not None:
            mapping["feature_group"] = d.feature_group
        # #340 — thread decision_level from IngestDecision to the mapping.
        if d.decision_level is not None:
            mapping["decision_level"] = d.decision_level
        # #109 — thread optional governance metadata from IngestDecision
        # to the per-mapping payload so the ledger write picks it up.
        if d.governance is not None:
            mapping["governance"] = d.governance
        mappings.append(mapping)

    # Action items are task assignments, not product decisions — they belong in a
    # ticket tracker, not the decision ledger.  We accept them in the payload for
    # backwards compat but do not write them to the ledger.

    for q in validated.open_questions:
        # Open questions are AI-surfaced requirement gaps: no human explicitly
        # committed to them, no code implements them. signoff.discovered=true
        # marks them as AI-discovered so consumers can distinguish them from
        # explicitly ingested decisions without a description prefix hack.
        mappings.append(
            {
                "intent": q,
                "span": {**source_meta, "text": ""},
                "symbols": [],
                "code_regions": [],
                "signoff": {"state": "proposed", "discovered": True},
            }
        )

    if not mappings:
        logger.warning(
            "[ingest] payload validated but produced 0 mappings: %s",
            list(payload.keys()),
        )
        return validated.model_dump()

    result = validated.model_dump()
    result["mappings"] = mappings
    return result


def _derive_last_source_ref(payload: dict) -> str:
    mappings = payload.get("mappings") or []
    refs = [str((m.get("span") or {}).get("source_ref", "")).strip() for m in mappings]
    refs = [ref for ref in refs if ref]
    return refs[-1] if refs else str(payload.get("query", "")).strip()


_TOPIC_MAX = 200


def _word_truncate(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars on a word boundary."""
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    if " " in clipped:
        return clipped.rsplit(" ", 1)[0]
    return clipped


def _derive_topics(payload: dict) -> list[str]:
    """Extract topics for the judge_gaps auto-chain.

    Primary: distinct feature_group values from mappings (one topic per segment).
    Fallback: payload.query → longest decision description → payload.title.
    Returns empty list when nothing useful is found (skips chain).
    """
    mappings = payload.get("mappings") or []
    topics: list[str] = []
    seen: set[str] = set()
    for m in mappings:
        fg = str(m.get("feature_group") or "").strip()
        if fg and fg not in seen:
            seen.add(fg)
            topics.append(_word_truncate(fg, _TOPIC_MAX))
    if topics:
        return topics

    # Fallback: single topic from query/description/title
    query = str(payload.get("query") or "").strip()
    if query:
        return [_word_truncate(query, _TOPIC_MAX)]

    decisions = payload.get("decisions") or []
    decision_texts = [
        str(d.get("description") or d.get("title") or "").strip()
        for d in decisions
        if isinstance(d, dict)
    ]
    decision_texts = [t for t in decision_texts if t]
    if decision_texts:
        return [_word_truncate(max(decision_texts, key=len), _TOPIC_MAX)]

    title = str(payload.get("title") or "").strip()
    if title:
        return [_word_truncate(title, _TOPIC_MAX)]

    return []


async def _find_context_for_candidates(
    mappings: list[dict],
    ledger,
    top_k: int = 5,
) -> list[ContextForCandidate]:
    """After ingest writes spans, find context_pending decisions that may be answered.

    Runs BM25 search per span text and filters to decisions with
    signoff.state='context_pending'. Returns up to top_k candidates total
    (deduped by (span_id, decision_id) pair). Never raises — returns [] on error.
    """
    from ledger.queries import get_input_span_id, search_context_pending_by_text

    inner = getattr(ledger, "_inner", ledger)
    client = inner._client

    seen_pairs: set[tuple[str, str]] = set()
    candidates: list[ContextForCandidate] = []

    for mapping in mappings:
        span = mapping.get("span") or {}
        span_text = span.get("text", "")
        source_type = span.get("source_type", "manual")
        source_ref = span.get("source_ref", "")
        if not span_text:
            continue
        try:
            span_id = await get_input_span_id(client, source_type, source_ref, span_text)
            if not span_id:
                continue
            matches = await search_context_pending_by_text(client, span_text, top_k=top_k)
            for m in matches:
                decision_id = m.get("decision_id", "")
                if not decision_id:
                    continue
                pair = (span_id, decision_id)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                candidates.append(
                    ContextForCandidate(
                        span_id=span_id,
                        decision_id=decision_id,
                        decision_description=m.get("description", ""),
                        overlap_score=float(m.get("overlap_score", 0.0)),
                    )
                )
                if len(candidates) >= top_k:
                    return candidates
        except Exception as exc:
            logger.debug("[ingest] context_for scan failed: %s", exc)

    return candidates


async def handle_ingest(
    ctx,
    payload: dict,
    source_scope: str = "",
    cursor: str = "",
    *,
    ingest_mode: Literal["active", "passive"] = "active",
) -> IngestResponse:
    # #216: enforce entry-time guardrails BEFORE any ledger work, including
    # the SurrealDB connection handshake. A refused payload should cost zero
    # downstream resources. LLM-02 size check first (cheaper short-circuit
    # on oversized payloads); LLM-08 rate check second. Both raise
    # ``_IngestRefused`` with distinct ``reason`` strings; telemetry-emit
    # on refusal then re-raise so the MCP boundary translates to a
    # structured TextContent error.
    #
    # Per-session bucket scoping note: ``ctx.session_id`` is resolved by
    # ``context._resolve_agent_identity`` (#231 v1) to a per-developer
    # salted email-hash (16-char hex) when ``git config user.email`` is
    # available — gives per-developer rate-limit bucket isolation in
    # team-server installs. Falls back to the process-wide ``_SESSION_ID``
    # UUID when git config is unreadable (test/CI runs, no email set);
    # in that mode the rate gate is per-server-process and concurrent
    # callers share a bucket — acceptable for the test shape, not for
    # production. Option (β) per-MCP-session granularity is the v2 upgrade
    # path gated on team-server protocol activation; documented in plan-231.
    # Cheapest-first ordering: size (O(1) byte count) → rate (O(1) bucket
    # take) → canary (O(n) regex) → sensitive (O(n) regex + Luhn).
    # Canary first because injection is upstream of leakage — block the
    # manipulation attempt before scanning for leaks. #212 LLM-01,
    # #213 LLM-04 + HIPAA-01 + PCI-01 fold.
    #
    # #418 Phase 0a: ``ingest_mode`` splits soft-gate posture by transport.
    # ``ingest_mode="active"`` (default, MCP tool surface) keeps fail-fast
    # behavior — the caller-LLM sees the refusal, fixes the payload,
    # retries. ``ingest_mode="passive"`` (pollers, webhook receivers, the
    # sync-and-brief CLI) WARNs + DLQs + continues for soft gates so a
    # single bad item can't halt the whole poller. Hard gates
    # (``sensitive_data:*``, ``malformed_payload``) fail-fast in BOTH modes —
    # the failure is not safely recoverable by skipping.
    dlqd_count: dict[str, int] = {}
    try:
        _check_payload_size(payload, ctx.ingest_max_bytes)
        _check_rate_limit(
            getattr(ctx, "session_id", ""),
            ctx.ingest_rate_limit_burst,
            ctx.ingest_rate_limit_refill_per_sec,
        )
        _check_canary(payload)
        _check_sensitive(payload)
    except _IngestRefused as exc:
        session_id_for_emit = getattr(ctx, "session_id", "")
        if ingest_mode == "passive" and not _is_hard_gate(exc.reason):
            # Soft gate + passive caller: route to DLQ, emit refusal with
            # warned_and_dlqd disposition, return an empty stats-only
            # IngestResponse so the poller continues with the next item.
            try:
                from dlq.store import write_dlq_entry

                try:
                    serialized = json.dumps(payload, default=str).encode("utf-8")
                except Exception:  # noqa: BLE001 — should be impossible (malformed is hard-gate)
                    serialized = repr(payload).encode("utf-8", errors="replace")
                content_hash = "sha256:" + hashlib.sha256(serialized).hexdigest()
                derived_source_ref = _derive_last_source_ref(payload) or str(
                    payload.get("title") or ""
                )
                write_dlq_entry(
                    source_id=source_scope or "unknown",
                    source_ref=derived_source_ref or "<unknown>",
                    reason=exc.reason,
                    byte_size=len(serialized),
                    content_hash=content_hash,
                    raw_content=serialized,
                )
            except Exception as dlq_exc:  # noqa: BLE001 — audit emit must still fire
                logger.warning(
                    "[ingest] DLQ write failed for reason=%s: %s",
                    exc.reason,
                    dlq_exc,
                )
            _emit_ingest_refusal_telemetry(
                exc.reason,
                session_id_for_emit,
                disposition="warned_and_dlqd",
            )
            dlqd_count[exc.reason] = dlqd_count.get(exc.reason, 0) + 1
            return IngestResponse(
                ingested=False,
                repo=str(payload.get("repo") or getattr(ctx, "repo_path", "")),
                query=str(payload.get("query", "")),
                source_refs=[],
                stats=IngestStats(
                    intents_created=0,
                    symbols_mapped=0,
                    regions_linked=0,
                    ungrounded=0,
                    grounded=0,
                    grounded_pct=0.0,
                    grounding_deferred=0,
                    dlqd_count=dlqd_count,
                ),
                created_decisions=[],
            )
        # Hard gate OR active-mode soft gate: today's fail-fast path.
        _emit_ingest_refusal_telemetry(
            exc.reason,
            session_id_for_emit,
            disposition="rejected",
        )
        raise

    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    payload = _normalize_payload(payload)
    repo = str(payload.get("repo") or ctx.repo_path)
    # Issue #67: ``ledger.ingest_payload`` reads ``payload.get("repo", "")``
    # internally and falls back to subprocess.run(cwd=Path("").resolve()).
    # On Linux that picks up the test runner's CWD (often a git repo, so
    # the call appears to "work" with the wrong SHA). On Windows it
    # produces a path the OS rejects with WinError 267. Inject the
    # resolved repo path so the adapter never sees an empty value.
    if not payload.get("repo"):
        payload = {**payload, "repo": repo}

    # For agent_session / manual ingests (gap answers, inline resolutions),
    # backfill the git user email as the speaker when speakers is empty.
    # Transcript/slack/document spans carry their own speaker lists; only
    # session-originated spans lack an author and need this backfill.
    _SESSION_SOURCE_TYPES = {"agent_session", "manual"}
    _git_email_cache: str | None = None
    _fallback_mode = getattr(ctx, "signer_email_fallback", "local-part-only")
    for mapping in payload.get("mappings") or []:
        span = mapping.get("span") or {}
        if span.get("source_type") in _SESSION_SOURCE_TYPES and not span.get("speakers"):
            if _git_email_cache is None:
                from events.writer import _get_git_email, _resolve_signer_email

                _raw_email = _get_git_email(ctx.repo_path)
                # #200 Phase 2: apply signer-email fallback policy from
                # `.bicameral/config.yaml: signer_email_fallback`. Privacy-
                # positive default (`local-part-only`) strips the email
                # host before the value lands in the ledger / team-mode
                # JSONL substrate.
                _git_email_cache = _resolve_signer_email(_raw_email, mode=_fallback_mode)
            if _git_email_cache and _git_email_cache != "unknown":
                span["speakers"] = [_git_email_cache]

    payload = ctx.code_graph.resolve_symbols(payload)

    from datetime import datetime

    _now_iso = datetime.now(UTC).isoformat()
    _session_id = getattr(ctx, "session_id", None) or ""

    # v0.7.0: every new ingest enters as 'proposed' by default.
    # v0.9.3: supersession detection removed from server — caller-LLM checks
    # bicameral.history after ingest and calls bicameral_resolve_collision for conflicts.
    mappings = payload.get("mappings") or []
    _proposed_signoff = {"state": "proposed", "session_id": _session_id, "created_at": _now_iso}
    for m in mappings:
        if m.get("signoff") is None:
            m["signoff"] = _proposed_signoff
    payload = {**payload, "mappings": mappings}

    # Pollution guard (v0.4.6, Bug 3): warn the user if they're ingesting
    # from a non-authoritative ref. The ingest still proceeds — baselines
    # will be stamped against the authoritative ref via ingest_payload(ctx=ctx)
    # below, so no data is corrupted. The warning is informational only.
    authoritative_ref = getattr(ctx, "authoritative_ref", "")
    authoritative_sha = getattr(ctx, "authoritative_sha", "")
    head_sha = getattr(ctx, "head_sha", "")
    if authoritative_sha and head_sha and authoritative_sha != head_sha:
        logger.warning(
            "[ingest] checked out on a ref that differs from authoritative %s "
            "(HEAD=%s); baseline hashes will be stamped against %s so the "
            "ledger stays branch-independent. Switch to %s if you want "
            "baselines pinned to the current working tree.",
            authoritative_ref,
            head_sha[:8],
            authoritative_ref,
            authoritative_ref,
        )

    # v0.4.8: writes always invalidate the within-call sync cache. In the
    # top-level ingest path this is a no-op (no cache exists yet this call),
    # but the invariant "mutations clear cache" must hold symmetrically —
    # otherwise a future chain that runs a read handler *before* ingest and
    # then writes would leave a stale cache covering post-write reads.
    try:
        from handlers.link_commit import handle_link_commit, invalidate_sync_cache

        invalidate_sync_cache(ctx)
    except Exception:
        pass

    result = await ledger.ingest_payload(payload, ctx=ctx)

    # v0.8.0: context_for candidate detection.
    # After spans are written, BM25-search for context_pending decisions that
    # the new spans may answer. Returns up to 5 candidates across all mappings.
    context_for_candidates: list = []
    try:
        context_for_candidates = await _find_context_for_candidates(
            payload.get("mappings") or [], ledger, top_k=5
        )
    except Exception as exc:
        logger.debug("[ingest] context_for detection failed: %s", exc)

    # Sync ledger to HEAD and re-ground any previously ungrounded intents.
    # The LinkCommitResponse carries ``pending_compliance_checks`` from the
    # drift sweep — the caller LLM resolves them via bicameral.resolve_compliance.
    sync_status = None
    try:
        sync_status = await handle_link_commit(ctx, "HEAD")
    except Exception as exc:
        logger.warning("[ingest] post-ingest link_commit failed: %s", exc)

    # Auto-chain: fire judge_gaps per feature_group topic so the caller gets
    # one structured gap-judgment payload per segment. Failures are swallowed.
    # #187: collected payloads are folded into the unified `brief` envelope
    # below; the legacy flat `judgment_payload[s]` fields were removed.
    judgment_payloads: list = []
    try:
        topics = _derive_topics(payload)
        if topics:
            from handlers.gap_judge import handle_judge_gaps

            for topic in topics:
                jp = await handle_judge_gaps(ctx, topic=topic)
                if jp is not None:
                    judgment_payloads.append(jp)
    except Exception as exc:
        logger.warning("[ingest] post-ingest gap-judge chain failed: %s", exc)

    cursor_summary = None
    source_type = str(
        ((payload.get("mappings") or [{}])[0].get("span") or {}).get("source_type", "manual")
    )
    last_source_ref = _derive_last_source_ref(payload)
    if hasattr(ledger, "upsert_source_cursor"):
        cursor_row = await ledger.upsert_source_cursor(
            repo=repo,
            source_type=source_type,
            source_scope=source_scope or "default",
            cursor=cursor or last_source_ref,
            last_source_ref=last_source_ref,
        )
        cursor_summary = SourceCursorSummary(**cursor_row)

    source_refs = []
    for mapping in payload.get("mappings", []):
        span = mapping.get("span") or {}
        ref = str(span.get("source_ref", "")).strip()
        if ref and ref not in source_refs:
            source_refs.append(ref)

    stats = result.get("stats", {})
    intents_created = int(stats.get("intents_created", 0))
    ungrounded_count = int(stats.get("ungrounded", 0))
    grounded_count = max(intents_created - ungrounded_count, 0)
    grounded_pct = (grounded_count / intents_created) if intents_created > 0 else 0.0

    logger.info(
        "[ingest] complete: %d/%d grounded (%.0f%%) | source_refs=%s",
        grounded_count,
        intents_created,
        grounded_pct * 100.0,
        source_refs,
    )

    # #187: build the unified brief envelope from the gap-judge findings.
    # Future PR may also surface drift_candidates/divergences here once
    # those are computed in the ingest path; today only gaps + rubric are
    # populated server-side. brief stays None when there's nothing to render
    # (silent-on-no-signal — matches PreflightResponse contract).
    brief: BriefEnvelope | None = None
    if judgment_payloads:
        gaps: list[BriefGap] = []
        for jp in judgment_payloads:
            gaps.extend(jp.phrasing_gaps)
        brief = BriefEnvelope(
            gaps=gaps,
            rubric=judgment_payloads[0].rubric,
        )

    ingest_response = IngestResponse(
        ingested=bool(result.get("ingested", False)),
        repo=str(result.get("repo", repo)),
        query=str(payload.get("query", "")),
        source_refs=source_refs,
        stats=IngestStats(
            intents_created=intents_created,
            symbols_mapped=int(stats.get("symbols_mapped", 0)),
            regions_linked=int(stats.get("regions_linked", 0)),
            ungrounded=ungrounded_count,
            grounded=grounded_count,
            grounded_pct=grounded_pct,
            grounding_deferred=0,
            dlqd_count=dlqd_count,
        ),
        created_decisions=[
            CreatedDecision(
                decision_id=d["decision_id"],
                description=d["description"],
                decision_level=d.get("decision_level"),
            )
            for d in result.get("created_decisions", [])
        ],
        pending_grounding_decisions=list(result.get("ungrounded_decisions", [])),
        context_for_candidates=context_for_candidates,
        source_cursor=cursor_summary,
        brief=brief,
        sync_status=sync_status,
    )

    return ingest_response
