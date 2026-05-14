# Threat model and MCP-transport trust boundary

This document is the canonical scope statement for bicameral-mcp's
trust posture. It closes **Track 1** of #215 (compliance audit gap
**SOC2-01**, P0/H), per
[`docs/research-brief-compliance-audit-2026-05-06.md § 2.2`](../research-brief-compliance-audit-2026-05-06.md).

Track 2 — the auth-shim design — is deferred to a follow-up cycle
gated on team-mode evolution; see [Track 2 section below](#track-2--the-future-auth-shim-deferred).

## Scope statement (load-bearing)

**bicameral-mcp is a local-install developer tool. The trust
boundary is the OS user account. Multi-user, hosted, or
shared-machine deployments are out of scope; team-mode requires the
Track 2 auth shim before such activation.**

Every other statement in this document supports that sentence. If a
B2B compliance reviewer reads only one paragraph, this is it.

## What this means in practice

| Deployment shape | In scope? | Why |
|---|---|---|
| One operator on one laptop | ✅ In scope | OS user account *is* the trust boundary; stdio transport terminates inside one user session. |
| One operator + team-mode via a *private* Google Drive folder (or local-folder backend over a syncthing/Dropbox volume) | ✅ In scope | Filesystem-ACL trust on the shared backend is layered *under* the local MCP transport; each operator's MCP server is still single-tenant. |
| One operator + team-mode via a *shared* folder where peers can also `ingest` to that folder | ✅ In scope | Each operator runs their own MCP server in their own user account; the shared backend is a peer-author event log, not a multi-tenant MCP transport. |
| Shared dev VM with multiple SSH users running one MCP server | ❌ Out of scope | One MCP transport serves multiple user identities with no auth shim — directly the SOC2-01 gap. |
| Shared CI runner where multiple operators or agents invoke the MCP without per-user isolation | ❌ Out of scope | Same as above; no per-user authentication on stdio. |
| Hosted bicameral-mcp instance behind a reverse proxy serving multiple teams | ❌ Out of scope | Requires Track 2. The transport-level trust elevation is the auth-shim work. |
| Team-server-tier deployment (the runtime that was removed in #242 for v0; future revival path) | ❌ Out of scope until Track 2 | The pluggable BackendAdapter from #279 Phase 2 is the wire substrate; Track 2 is the auth layer on top. |

## The MCP stdio transport surface

The server in [`server.py`](../../server.py) accepts MCP requests on
stdio and runs every handler without an authentication check. This
is correct for the in-scope deployments above: the stdio pipe is
*inside* one OS user session, and the OS-level user account is
what authenticates the caller.

For the out-of-scope deployments above, the missing auth check is
the substantive gap that Track 2 closes.

`SurrealKV://~/.bicameral/ledger.db` is protected by filesystem ACLs
on the operator's `$HOME`; same OS-user boundary. No network
listener; no inbound port; no separate daemon.

## Team-mode posture (v0, post-#242)

The old self-hosted server runtime (an HTTP `/events` API plus
Slack/Notion OAuth workers) was removed in **#242** because its
shape did not match the v0 productization commitment to *pull-based
event-log adapters*. What ships today and is in-scope under this
trust boundary:

- **`events/backends/local_folder.py`** — append-only `<email>.jsonl`
  files in a shared filesystem path (NFS, Dropbox, syncthing, etc.).
  Trust terminates at the filesystem ACLs of the shared volume.
- **`events/backends/google_drive.py`** — same wire format hosted on
  Google Drive. Trust terminates at Google Drive's share-permission
  layer and the operator's Google account.

Both backends are configured under `team:` in `.bicameral/config.yaml`
(see [`docs/policies/sources-config.md`](sources-config.md)). Neither
elevates the MCP-transport trust boundary — they let two operators
share a common append-only event log, but each operator runs their
own single-tenant MCP server. If you wouldn't trust your peer with
shell access to the shared folder, you shouldn't trust them as a
team-mode peer; the trust topology is "filesystem ACL ⇒ event-log
write" and nothing more.

## Why Track 1 ships now

This is the gap that **shows up immediately in any B2B compliance
review**. Track 1 closes the *perception* gap: a SOC 2 reviewer
looking at the codebase without an explicit scope statement sees
"unauthenticated MCP transport" and writes a finding. Shipping this
document hands them the scope statement; the reviewer sees
"in-scope deployments are single-tenant; out-of-scope deployments
are documented as such and gated on Track 2."

Track 1 does **not** close the *substantive* gap for the
out-of-scope deployments. Track 2 does.

## Track 2 — the future auth shim (deferred)

Track 2 lands when team-mode evolves beyond peer-shared event-log
files into a server-mediated tier (per the team-server-priority
operator directive 2026-05-14 and the future revival of the runtime
removed in #242). Design options span:

- Per-developer JWT signing keys carried in the MCP envelope.
- mTLS over a stdio-tunneling transport for hosted deployments.
- Operator-side OS-keychain-backed credentials with a server-side
  verification handshake.

Selecting between these is Track 2's job; this document does **not**
make that selection. The activation gate is *team-mode evolution
into a server-mediated tier*, not a calendar date.

Track 2's plan will also be the cycle that adds the corresponding
entry to [`governance-gates.yaml`](../../governance-gates.yaml):
under #205 doctrine, governance is enforced by deterministic code,
not by doctrine text. Adding a gate entry in Track 1 would point at
no enforcement code, inverting the doctrine.

## Operator checklist

Before deploying bicameral-mcp on anything other than a single
operator's laptop, walk through this checklist:

- [ ] Are all MCP-transport callers running under the same OS user?
- [ ] If team-mode is configured, is the `team:` backend a
      filesystem path or Google Drive folder where every peer
      operator already has trust to write?
- [ ] If the answer to either question is "no", **stop**. The
      deployment is out-of-scope for the current trust boundary
      and requires Track 2 (not yet shipped).

If you're unsure, file a question in
[the security-reporting channel](../../SECURITY.md#reporting-a-vulnerability)
rather than assuming.

## References

- Compliance brief: [`docs/research-brief-compliance-audit-2026-05-06.md`](../research-brief-compliance-audit-2026-05-06.md) § 2.2 (SOC2-01)
- Doctrine: [#205](https://github.com/BicameralAI/bicameral-mcp/issues/205) — deterministic governance
- Removed self-hosted server: [#242](https://github.com/BicameralAI/bicameral-mcp/issues/242)
- v0 team-mode wire substrate: [#279 Phase 2](https://github.com/BicameralAI/bicameral-mcp/pull/321) (BackendAdapter)
- Related boundary statements:
  - [`docs/policies/acceptable-use.md`](acceptable-use.md) § 3 (multi-tenant deployment)
  - [`docs/policies/host-trust-model.md`](host-trust-model.md) (host UX trust dependency)
- This issue: [#215](https://github.com/BicameralAI/bicameral-mcp/issues/215) — Track 1 = this document; Track 2 = future auth shim.
