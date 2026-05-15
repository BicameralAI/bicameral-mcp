# Notifications layer — implementation roadmap

**Status: Phase 1 of N shipped (2026-05-15). Neither #330 (FC-1) nor #335 (FC-4) is closed by this cycle.**

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

## Phase 2 — Slack adapter + event-hub wiring (next cycle)

**Will ship:**

- `notifications/slack.py` (`SlackChannelAdapter`) — webhook-shaped or bot-token-shaped, TBD by Phase 2's plan cycle.
- `.bicameral/notifications.yml` config schema + parser (operator config flows from `notification_policy.channels` block in #330's body).
- Event-hub trigger wiring — handler-level emits + audit-log integration. When a ledger event fires (`proposal_captured`, `decision_ratified`, `drift_detected`, `compliance_recorded`, `decision_superseded`), the hub constructs a `NotificationEvent` and fans out to every registered channel that opted in via config.
- Fan-out loop owns the catch-and-log discipline declared in `ChannelDeliveryError`'s docstring: one channel's failure NEVER blocks delivery to other channels.
- Filtering: `feature_areas`, `min_severity`, `events` selectors per #330's config example.

**Gap-closure status after Phase 2:** #330 substantively closes for Slack delivery; #335 still open.

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
