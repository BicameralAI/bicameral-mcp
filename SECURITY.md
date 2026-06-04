# Security & Privacy

## Privacy posture

Bicameral MCP is a local MCP client for the `bicameral-bot` daemon. MCP forwards
ToolRequest payloads to the configured local daemon endpoint and does not own
ledger, graph, dashboard, source adapter, telemetry, or governance storage.

- **No MCP-local telemetry** — the cutover removes MCP-local usage and feedback telemetry.
- **Daemon boundary** — content privacy and storage posture are owned by the configured `bicameral-bot` daemon and its selected substrate.
- **Boundary docs** — the current MCP authority boundary is documented in
  [`docs/specs/bicameral-mcp-idealized-spec.md`](docs/specs/bicameral-mcp-idealized-spec.md)
  and [`docs/plans/toolrequest-refactor-plan.md`](docs/plans/toolrequest-refactor-plan.md).

## Threat model and trust boundary

bicameral-mcp is a local-install developer tool. **The trust boundary is the OS user account.** Multi-user, hosted, or shared-machine deployments are out of scope; team-mode requires a future auth shim before such activation.

See [`docs/specs/bicameral-mcp-idealized-spec.md`](docs/specs/bicameral-mcp-idealized-spec.md)
for the canonical MCP cutover scope.

## Software supply chain

Each release ships signed artifacts on the [Releases page](https://github.com/BicameralAI/bicameral-mcp/releases):

| Artifact | What it is |
|---|---|
| `bicameral-mcp.sbom.json` | CycloneDX SBOM of the wheel's dependency closure |
| `bicameral-mcp.sbom.intoto.jsonl` | Sigstore Rekor attestation over the SBOM |
| `release-tag-commit.txt{,.sig,.crt}` | Cosign keyless signature of the release-tag commit |

Release verification procedure will be rebuilt around the thin-client package
before the next release promotion.

GitHub's auto-generated dependency graph SBOM (SPDX format) is also available under **Insights → Dependency graph → Export SBOM**.

## Supported versions

Only the **latest minor** is actively maintained. Critical fixes get backported to the prior minor for ~30 days after a new minor ships; older releases are best-effort.

Check the recommended version your install will upgrade to:

```bash
bicameral-mcp tools
```

## Reporting a vulnerability

**Please do not file public issues for security reports.**

Use one of:

1. **[GitHub Security Advisories](https://github.com/BicameralAI/bicameral-mcp/security/advisories/new)** — preferred. Private channel, enables coordinated disclosure.
2. Email **jin@bicameral-ai.com** with the subject prefix `[security]`.

Include:
- Affected version(s)
- Repro steps or proof-of-concept
- Impact assessment as you see it
- Whether you've shared the finding elsewhere

We will acknowledge within 3 business days, and aim for a patch + advisory within 30 days for critical issues.

## Scope

In scope for security reports:
- The MCP server itself (`bicameral_mcp` Python package)
- The release supply chain (signed manifests, SBOM emitter, publish workflow)

Out of scope:
- Issues in third-party dependencies — file those upstream first; we'll patch our pin if a fix lands
- Issues in MCP hosts (Claude Code, Cursor, Codex) — file those with the host vendor
- Vulnerabilities reachable only by an attacker with write access to your `.bicameral/` directory or `~/.claude/settings.json` (local-attack assumption already covered by host-trust model)
