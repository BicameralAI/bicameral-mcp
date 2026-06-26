# #585 — Devin-through-MCP stress test vs. tagged daemon `bicameral-bot v0.1.5`

## Summary

#585 requires running the real Devin hand-off loop **through the MCP, against the
tagged daemon binary, not bot source HEAD**. The #584 re-validation (docs/validation-584-…,
PR #609) blocked this on a missing v2 ToolRequest HTTP surface. That blocker is
now cleared: `bicameral-bot v0.1.5` (tag `161e174`, release PR bicameral-bot#513)
ships `/v2/capabilities` + `/v2/tool-requests` (#427/#430) on the MCP-aligned
default port `127.0.0.1:37373`.

**Result: PASS for pilot, with one high-severity bot/runtime follow-up.** Every
in-scope stress item passed end-to-end through the production MCP code paths
against the tagged daemon. Separately, the stress run uncovered a daemon-side
ledger-integrity bug (a review command against an unknown target id is accepted
and then permanently corrupts ledger replay) — filed as a bot/runtime follow-up,
not an MCP change.

## What was tested

- **MCP under test:** `bicameral-mcp` `0.17.0`, `TOOLREQUEST_PROTOCOL_VERSION = v2`,
  driven through `server.list_tools`, `server._ensure_protocol_compatible`, and
  `server.call_tool` → `DaemonClient` urllib transport (no stubs; real HTTP).
- **Tagged daemon:** `bicameral-bot v0.1.5` (tag `161e174`), run via
  `bicameral gateway start`. The published linux tarball targets `GLIBC_2.39`
  (ubuntu-24.04); this host is glibc-2.35, so the daemon was built from the
  identical `v0.1.5` source tag (`cargo build --release --bin bicameral`).
- **Endpoint:** default `http://127.0.0.1:37373` (no env override).

## Observed results (live, against the tagged daemon)

| # | Scenario | Result |
|---|---|---|
| 1 | `GET /health` | **200** `{"status":"ok","version":"0.1.5"}` |
| 2 | MCP handshake `GET /v2/capabilities` | **`CapabilityReport`**, `protocol_version=v2`, 16 supported commands (incl. `preflight.run`), deferred set reported |
| 3 | `list_tools` (re-runs handshake) | OK, full tool list returned |
| 4 | Read command `history.list` via `call_tool` | `status=ok`, `result.decisions=[]`, correlated `trace` |
| 5 | Read command `search.query` via `call_tool` | `status=ok`, `result.results=[]`, `code_context.graph_readiness=ready` |
| 6 | Staged `preflight.run` via `call_tool` | `status=ok`, stages rendered; daemon emits no `staged` envelope yet (see limitation) |
| 7 | Deferred `review.resolve_compliance` | `status=rejected`, daemon-authored `unsupported_capability: …deferred…` message + `trace.failure_reason`; MCP forwards verbatim |
| 8 | Unsupported MCP tool name (`bicameral.graph.status`) | typed `error_code=unsupported_tool`, **no daemon round-trip** |
| 9 | Agent retry (5× identical `history.list`) | all `ok`, consistent |
| 10 | Concurrent `asyncio.gather` of 5 mixed tool calls | all `ok`/`warn` |
| 11 | No MCP-local fallback for graph/locator/dashboard/install/upgrade/migration/lifecycle | none resolve to a handler; all return `unsupported_tool` |
| 12 | Full hand-off loop: `ingest.submit_local` → candidate projected → `review.*` | ingest `ok` (candidate projected); review commands routed to daemon |

### Handshake (item 2) — captured `CapabilityReport`

- `daemon_protocol_version = v2`, `mcp_protocol_version = v2`
- `supported_commands` (16): `ingest.submit_local`, `ingest.submit_managed`,
  `history.list`, `search.query`, `review.accept_candidate`,
  `review.reject_candidate`, `review.approve_signoff`, `review.reject_signoff`,
  `binding.create`, `binding.inspect`, `evidence.refresh`, `preflight.run`,
  `code.locate`, `graph.status`, `graph.refresh_snapshot`,
  `decision.find_code_impact`
- `deferred_commands` (3): `review.resolve_compliance`, `tracking.untrack_source`,
  `tracking.refresh_query`

### Deferred command (item 7) — typed, daemon-authored

```
status:  rejected
message: unsupported_capability: review.resolve_compliance is deferred until V1
         compliance enforcement, correctness assertion, and signoff decoupling
         are specified
trace.failure_reason: unsupported_capability: review.resolve_compliance is deferred …
```

The rejection text and `trace` are authored by the daemon. The MCP forwards the
payload verbatim — it synthesizes no success, runs no local handler, and adds no
fallback fields.

## No-fallback boundary (items 8, 11)

The MCP exposes **no** tool that maps to graph, locator, ledger-mutation,
dashboard, install, upgrade, migration, or daemon-lifecycle behavior
(`MCP_TOOL_COMMANDS` contains only ingest/preflight/bind/review/history/search
ToolRequest commands). Out-of-scope tool names short-circuit to a typed
`unsupported_tool` error before any daemon call. Daemon failures surface as typed
recovery payloads (mcp#583) and never fall back to a local handler.

## Limitations / observations

1. **Staged preflight not yet emitted by the daemon (low).** `/v2/capabilities`
   advertises `preflight_stages` (`lookup=supported`, others `not_configured`),
   but the `preflight.run` ToolResponse carries advisory locator/readiness data
   with **no `staged` envelope**. The MCP staged renderer therefore reports every
   stage as `unsupported` (and correctly never promotes `not_configured`
   enforcement to warn/pause/block). The staged-preflight rendering contract
   (bot#323/#324) is exercised on the MCP side but not yet populated by the
   tagged daemon. Cosmetic for pilot; tracked as a bot follow-up.

2. **Ledger-poisoning on review of an unknown target (high) — bot/runtime
   follow-up.** Any of `review.accept_candidate`, `review.reject_candidate`,
   `review.approve_signoff`, `review.reject_signoff` invoked with a target id
   that does not exist returns `status=ok` (no typed rejection) and persists an
   event the replay deserializer then rejects:
   `daemon_unavailable: replay failed: serialization error: {Accept,Reject}Candidate
   missing candidate_id` (and the signoff analogues). After one such event,
   **every** replay-backed command (`history.list`, `search.query`,
   `preflight.run`, …) fails until the offending event file is manually removed
   from `.bicameral/events/`. Review of a **valid** target works correctly.

   This is exactly the failure mode #585 cares about: an agent (Devin) retrying
   review/deferred commands against stale or unknown ids can brick the workspace
   ledger. It is a daemon-side data-integrity defect — the MCP is a thin
   transport faithfully forwarding the daemon's `ok`. Per #585, this is filed as
   a bot/runtime follow-up rather than fixed by expanding MCP authority.

## Regression coverage

`tests/test_tagged_daemon_stress_585.py` locks the v0.1.5 contract in CI by
standing up a loopback HTTP daemon whose responses are modeled on the live
transcript above, then driving the production MCP code paths over real HTTP. It
covers the handshake/`CapabilityReport`, a supported read command, staged
preflight rendering, the typed daemon-authored deferred rejection (forwarded
verbatim, no fallback), agent retry, concurrent calls, and the no-local-fallback
boundary. It complements the #584 released-surface regression
(`tests/test_tagged_daemon_handshake_regression.py`), which still asserts the
fail-fast behavior against a pre-v2 daemon.

## Recommendation

- **Pilot:** the Devin-through-MCP loop passes end-to-end against tagged
  `bicameral-bot v0.1.5`. #585 can close as **pass for pilot**.
- File a bot/runtime follow-up for the review-of-unknown-target ledger poisoning
  (validate target existence before appending review/signoff events, and/or make
  the event store reject malformed events at write time so replay cannot be
  corrupted). Also note the daemon does not yet emit the staged-preflight
  envelope.
