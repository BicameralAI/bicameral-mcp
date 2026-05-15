# Notifications layer — implementation roadmap

**Status: Phase 1 + Phase 2a shipped (2026-05-15). Neither #330 (FC-1) nor #335 (FC-4) is closed by this cycle.**

This document is the operator-facing roadmap for the outbound-notification layer. Two open feature epics share the same channel-routing infrastructure:

- **#330 (FC-1)** — multi-channel event delivery hub (Slack / email / dashboard / webhook / Linear / IDE).
- **#335 (FC-4)** — decision health monitor with persistent alignment dashboard + scheduled digest delivery.

Per the cycle pairing rationale: "Building FC-1's channel adapter layer first gives FC-4's digest/email delivery at near-zero marginal cost." Both build on the abstraction this cycle ships.

## Phase 1 — channel-adapter foundation (this cycle, 2026-05-15)

**Shipped:**

- `notifications/` Python package:
  - `ChannelAdapter` protocol (`@runtime_checkable`, async `deliver(NotificationEvent) -> None`).
  - `CHANNELS` registry (`dict[str, type]`) — same pattern as `events/sources/__init__.py::ADAPTERS`.
  - `NotificationEvent` frozen dataclass — structural fact only; `summary` truncated to 200 chars at construction; **no PII fields** (per #221 design directive).
  - `Severity` and `EventType` closed `Literal` aliases.
  - `ChannelDeliveryError` (subclass of `RuntimeError`).
  - `StderrChannelAdapter` — smoke-test channel emitting one JSON line per event.
- 13 sociable unit tests pinning the contract, the PII boundary, JSON shape, async-coroutine conformance, fail-fast on stderr write failure, and per-`EventType` parametrized round-trip.
- This roadmap doc.

**Explicitly NOT shipped in Phase 1:**

- No event-hub trigger wiring. Ledger event emits (audit_log, handler emits) are unchanged.
- No #335 metrics computation. No alignment-score / drift-count / staleness pipeline.
- No Slack / email / webhook / Linear / dashboard SSE adapters.
- No `.bicameral/notifications.yml` config schema or parser.
- No retry / backoff / dead-letter queue. Adapters are best-effort; resilience is per-channel concern.

**Gap-closure status after Phase 1:** #330 and #335 remain OPEN.

## Phase 2a — Slack adapter + hub + `decision_ratified` wiring (this cycle, 2026-05-15)

**Shipped:**

- `notifications/slack.py` (`SlackChannelAdapter`) — webhook-based. Reads URL from `os.environ[webhook_url_env]` at delivery time; env-var-only-secret pattern mirroring `events/sources/granola.py`.
- `notifications/config.py` (`NotificationsConfig` parser) — reads `~/.bicameral/notifications.yml`; fail-closed on missing/malformed/unknown-channel-type.
- `notifications/hub.py` (`NotificationHub`) — fan-out orchestrator with per-channel fail-isolation; iterates subscribed channels; returns success count; never raises.
- `get_hub()` process-singleton accessor + `reset_hub_for_testing()` (mirrors `adapters/ledger.py::get_ledger` pattern).
- Wiring at `handlers/ratify.py:122-145` — `await get_hub().notify(...)` after `apply_ratify()` succeeds, guarded by `if action == "ratify"`. Belt-and-suspenders try/except: hub-construction failures never block the ratify return.
- 41 sociable tests (10 Slack + 8 config + 10 hub + 5 ratify-integration + 8 PII-and-feature-area pins).
- `docs/policies/notifications-config.md` — operator-facing config doc + responsibilities.

**Explicitly NOT shipped in Phase 2a:**

- Only `decision_ratified` event wired; other event types (`proposal_captured`, `drift_detected`, `decision_rejected`, `decision_superseded`, `compliance_recorded`, `gap_judgment`) remain un-wired. Phase 2b lands them with handler-level emits.
- No `feature_area` resolution; Phase 2a wires empty-string by design (BicameralContext has no such field today). Phase 2b adds decision-row lookup.
- No filtering by `feature_areas` / `min_severity` / role-defaults.
- No fire-and-forget; `notify()` is awaited inline.

**Gap-closure status after Phase 2a:** #330 partially closes (one channel + one event type); #335 still entirely open (metrics + digest pending Phase 3).

## Phase 2b — remaining event types + filtering (next cycle)

**Will ship:**

- Handler-level `get_hub().notify(...)` emits at: `handlers/ingest.py` (proposal_captured), `handlers/resolve_compliance.py` (compliance_recorded), `handlers/resolve_collision.py` (decision_superseded), `handlers/detect_drift.py` or `sync_middleware.py` (drift_detected), `handlers/judge_gaps.py` (gap_judgment), and `handlers/ratify.py` reject branch (decision_rejected).
- `feature_area` resolution: read from `decision.feature_group` at notify time so the channel payload carries the real feature area.
- `feature_areas` / `min_severity` / role-defaults filtering per #330's config example.
- (Maybe) `governance-gates.yaml` entry once the event-hub becomes load-bearing for the configured channel set.

**Gap-closure status after Phase 2b:** #330 substantively closes for the Slack-only deployment shape; #335 still open (metrics + digest).

## Phase 3 — Email adapter + #335 metrics + digest delivery (cycle after)

**Will ship:**

- `notifications/email.py` — SMTP-shaped or transactional-provider-shaped (Postmark / Resend / Sendgrid).
- Metrics computation for #335: alignment score, drift count, proposal staleness, grounding coverage, resolution velocity, protected-component coverage. Derived from the existing ledger surface; no schema change.
- Scheduled-digest emit — config-driven cadence (daily / weekly / per-sprint); produces a `NotificationEvent` of `event_type: "health_digest"` carrying summary metrics; routes through the same channel layer.

**Gap-closure status after Phase 3:** #335 substantively closes; #330 closes for email delivery.

## Phase 4+ — additional adapters

In dependency order:

- `notifications/webhook.py` — raw JSON to operator-supplied endpoint. Enables Datadog / Grafana / PagerDuty / custom dashboards.
- `notifications/linear.py` — Linear comment on a feature-linked ticket. Requires the same OAuth flow shape as `events/sources/granola.py`.
- `notifications/jira.py` — Jira issue comment. Parallel to Linear.
- Dashboard SSE bridge — server-sent-events stream consumed by the existing dashboard UI (`pilot/dashboard/`). Persistent web view with auto-refresh.

## Out of scope for the entire roadmap

- **Per-recipient delivery state tracking.** No "Alice received digest at 10:00 UTC; Bob didn't" log. Channels are fire-and-forget at the framework layer; per-channel adapters log their own successes for diagnostic purposes.
- **At-least-once delivery guarantees.** Each channel adapter is best-effort by contract. Channels that need at-least-once layer their own queue + retry (e.g., a future webhook adapter using an outbox table). The framework's contract is **best-effort**; documented in this doc + tested in Phase 1.
- **Cross-recipient deduplication.** If two channel adapters both reach the same operator (e.g., Slack DM + email), they each fire independently. Dedup is the operator's config job, not the framework's.
- **Encryption at the channel layer.** TLS is the channel-implementation's responsibility (Slack does it, email STARTTLS handles it, webhook URL scheme `https://` enforces it). The framework neither adds nor checks encryption.

## PII boundary (locked-in invariant)

`NotificationEvent` carries **structural fact only**: `decision_id`, `event_type`, `feature_area`, `summary` (≤200 chars), `severity`, `source_ref`, `occurred_at`. Tests `test_notification_event_no_pii_fields_present` and `test_notification_event_carries_only_structural_fields` lock the dataclass shape; future Phase-2+ field additions that violate this contract fail at test-time.

Adapters that want raw decision content (full description, transcript text, speakers, rationale) MUST dereference `decision_id` against the ledger downstream of the event. That crosses the same data-segregation boundary documented in [`docs/policies/gdpr-art-17-erasure-roadmap.md`](gdpr-art-17-erasure-roadmap.md) and is subject to the same operator-erasable discipline.

## Refs

- Pairing rationale: cycle assignment 2026-05-15 (operator-supplied)
- Issues: [#330 (FC-1)](https://github.com/BicameralAI/bicameral-mcp/issues/330), [#335 (FC-4)](https://github.com/BicameralAI/bicameral-mcp/issues/335), [#221](https://github.com/BicameralAI/bicameral-mcp/issues/221) (PII boundary directive), [#205](https://github.com/BicameralAI/bicameral-mcp/issues/205) (deterministic governance doctrine)
- Plan artifact: `plan-330-335-channel-adapter-foundation.md` at repo root
- Precedent: `events/sources/__init__.py` (`SourceAdapter` pattern), `pii_archive/` (foundation-only #221 Phase A audit precedent)
