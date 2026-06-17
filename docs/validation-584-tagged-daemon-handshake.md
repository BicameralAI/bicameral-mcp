# #584 — PROTOCOL_VERSION capability handshake vs. tagged daemon (validation)

## Summary

The #584 gate requires validating the MCP capability handshake **against the
tagged daemon binary, not bot source HEAD**. This document records that
validation and the limitation it surfaces for #585.

The MCP-side handshake logic is correct (protocol match, mismatch, and
unsupported-command paths all behave as specified). **However, no released
`bicameral-bot` daemon binary actually serves the v2 ToolRequest handshake
surface the MCP requires.** Running the real MCP client against the real tagged
daemon binary therefore fails fast with `daemon_unavailable` — the handshake
cannot be completed end-to-end against a tag today.

## What was tested

- **MCP under test:** `bicameral-mcp` commit `a5e15cc`, version `0.17.0`,
  `TOOLREQUEST_PROTOCOL_VERSION = v2`.
- **Tagged daemon:** `bicameral-bot` `v0.1.4` (commit `bc2dec6`, latest release),
  built with `cargo build --bin bicameral` and run via `bicameral gateway start`.
- **MCP-expected daemon contract:** `GET /v2/capabilities`, `POST /v2/tool-requests`,
  default URL `http://127.0.0.1:37373`.

## Observed results

| Scenario | Setup | Result |
|---|---|---|
| Real tagged daemon v0.1.4 | MCP → `http://127.0.0.1:7525/v2/capabilities` | **HTTP 404** → fail-fast `DaemonConnectionError` (`daemon_unavailable`) |
| Real daemon at MCP default URL | MCP → `http://127.0.0.1:37373` | connection refused → `daemon_unavailable` (daemon binds `7525`, MCP defaults `37373`) |
| Protocol match (conformant v2 daemon) | daemon serves `protocol=v2` + 11 cmds | handshake OK; `CapabilityReport` lists `preflight.run` + 10 others |
| Protocol mismatch (daemon advertises v1) | daemon serves `protocol=v1` | fail-fast `DaemonProtocolError` (`daemon_protocol_mismatch`), structured #583 recovery payload, `retryable=false` |
| Unsupported/deferred command | daemon returns `unsupported_command` | typed `DaemonCapabilityError` (`daemon_capability_error`) |

The match / mismatch / unsupported rows confirm the MCP behaves correctly when a
conformant daemon exists. The first two rows are the gate-blocking finding: the
released daemon does not speak the contract.

## Concrete contract mismatches with the released daemon

1. **HTTP surface.** The released daemon (`bicameral gateway start`) serves only
   `/api/v1/*` (health, ingest, review, status, dashboard, projection, setup,
   query, bind). It has **no `/v2/capabilities` and no `/v2/tool-requests`**.
   Observed: `GET /health` → 200 `{"status":"ok","version":"0.1.4"}`,
   `GET /api/v1/status` → 200, `GET /v2/capabilities` → 404.
2. **Port.** `gateway start` default bind is `127.0.0.1:7525`; the MCP default
   daemon URL is `127.0.0.1:37373`.
3. **No v2 surface anywhere in bot.** Searching for `v2/capabilities`,
   `tool-requests`, `37373`, and an HTTP-served `capability_report` returns
   nothing across all bot tags (`v0.1.0`–`v0.1.4`) and on both `origin/main`
   and `origin/dev`. The ToolRequest dispatcher exists only as a Rust library
   function (`dispatch_with_workspace`); it is not wired to any HTTP endpoint.

## Limitation that should block #585

The MCP↔daemon ToolRequest capability handshake **cannot be exercised
end-to-end against any released bot daemon binary today**, because the daemon
does not expose the `/v2/capabilities` + `/v2/tool-requests` surface (on the
MCP-expected port). The #585 dogfood "protocol match → CapabilityReport from a
real daemon" item depends on this surface existing in a tagged daemon. Until
`bicameral-bot` ships the v2 ToolRequest HTTP endpoints (and aligns the port) in
a release tag, that gate item is unmet.

## Regression coverage

`tests/test_tagged_daemon_handshake_regression.py` locks this in by driving the
production `DaemonClient` HTTP transport against a loopback daemon that
reproduces the released v0.1.4 surface (no `/v2/capabilities`). It asserts the
handshake fails fast with `daemon_unavailable` and that `call_tool` returns a
typed recovery payload with no staged/result fallback. A conformant-daemon
positive control in the same module records the contract a tagged daemon must
satisfy to flip the released-surface cases green.

> Note: the prior #584 PR (#605) validated only against in-process stub daemons
> (`monkeypatch.setattr(server, "_client", ...)`); it never drove the real HTTP
> client or reproduced the released daemon surface, which is why it reported
> "no limitations block #585." This document and the regression test correct
> that record.

## Recommendation

- Track a bot-side issue to expose the v2 ToolRequest HTTP surface
  (`/v2/capabilities`, `/v2/tool-requests`) and align the daemon port, then cut
  a release tag.
- Mark #585 blocked on that bot work until a tagged daemon serves the handshake.
