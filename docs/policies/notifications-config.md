# Notifications config — `~/.bicameral/notifications.yml`

Operator config for the notifications layer (#330 + #335). Per-operator,
not per-team — lives in your home directory so secrets and channel
preferences stay private.

## Default location

`~/.bicameral/notifications.yml` (override with the explicit-path arg
to `NotificationsConfig.load()` in test or programmatic contexts).

## Config shape

```yaml
notification_policy:
  channels:
    - type: slack
      webhook_url_env: SLACK_WEBHOOK_URL
      events: [decision_ratified]
```

Top-level key: `notification_policy.channels` is a list of channel entries.

## Channel types

### `slack` (shipped in Phase 2a)

POSTs a plain-text message to a Slack incoming webhook URL.

| Key | Required | Default | Description |
|---|---|---|---|
| `type` | yes | — | Must be `slack` |
| `webhook_url_env` | no | `SLACK_WEBHOOK_URL` | Name of the env var holding the webhook URL |
| `events` | no | `[]` (= all event types) | List of event types this channel subscribes to |

**Setup:**

1. Create a Slack incoming webhook for your channel (see Slack docs).
2. Export the URL as an env var in your shell or `.mcp.json`:
   ```bash
   export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/T.../B.../..."
   ```
3. Add the channel block to `~/.bicameral/notifications.yml`.

**Message format:** plain text, one line per notification:
`[bicameral][<event_type>] <feature_area>: <summary>`

### `stderr` (smoke-test, Phase 1)

Emits one JSON line per event to stderr. Useful for local-dev / CI smoke
testing. No config keys required.

### Future channels (not shipped yet)

- `email` — Phase 3 of the roadmap
- `webhook` — Phase 4+
- `linear` / `jira` — Phase 4+
- `dashboard` — Phase 4+

See [`notifications-roadmap.md`](notifications-roadmap.md) for the
multi-cycle plan.

## Event types

| Event | Fires when | Wired in |
|---|---|---|
| `decision_ratified` | `bicameral.ratify` succeeds with `action=ratify` | Phase 2a (this cycle) |
| `proposal_captured` | `bicameral.ingest` records a new proposal | Phase 2b |
| `decision_rejected` | `bicameral.ratify` with `action=reject` | Phase 2b |
| `decision_superseded` | `bicameral.resolve_collision` with `action=supersede` | Phase 2b |
| `drift_detected` | `bicameral.detect_drift` reports drift | Phase 2b |
| `compliance_recorded` | `bicameral.resolve_compliance` records a verdict | Phase 2b |
| `gap_judgment` | `bicameral.judge_gaps` emits a gap | Phase 2b |
| `health_digest` | Scheduled #335 digest run | Phase 3 |

Only events listed under "Wired in: Phase 2a" actually fire today.
Others are reserved in the `EventType` Literal so Phase 2b lands as
config-compatible additions, not breaking changes.

## Operator responsibilities

### PII / secret content in notifications

The notification path emits `NotificationEvent` — a structural-fact
contract with no PII fields by construction (per the #221 design
directive). The `summary` field carries operator-supplied content
(currently the `note` parameter on `bicameral.ratify`) **verbatim**,
subject to a 200-char cap.

**Bicameral does NOT scrub `note` content.** If you put a customer's
name, email, or any other PII into the `note` arg, that content lands
in Slack verbatim (within the 200-char cap). Treat `note` the same way
you'd treat any content you type into Slack directly.

If your team needs server-side PII scrubbing on notification payloads,
that's tracked as a Phase 3+ extension.

### Failure modes

| Scenario | Behavior |
|---|---|
| `notifications.yml` missing | Empty config; no notifications fire; no warning |
| `notifications.yml` malformed YAML | Empty config; stderr warning printed once at hub init |
| `notification_policy` key missing | Empty config; no notifications fire |
| `channels[]` entry has unknown `type` | Channel skipped; stderr warning |
| `webhook_url_env` env var unset / empty | Per-call `ChannelDeliveryError` raised; hub catches; logs to stderr; ratify call returns normally |
| Slack endpoint returns 5xx / 4xx / network error | Same — channel-level failure; ratify call returns normally |

In no case does a notification failure block the underlying handler
(ratify, ingest, etc.). Notifications are best-effort by contract.

### Audit trail

Notification failures emit to stderr via the standard `print(file=sys.stderr)`
path. They do **not** currently land in the audit log; if your deployment
requires per-notification audit records, treat that as a follow-up
request.

## See also

- [`notifications-roadmap.md`](notifications-roadmap.md) — multi-cycle plan
- [`gdpr-art-17-erasure-roadmap.md`](gdpr-art-17-erasure-roadmap.md) — PII boundary directive
- [`acceptable-use.md`](acceptable-use.md) — operator content responsibility on ingest
