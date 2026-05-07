# Security & Privacy

## Privacy posture

Bicameral runs **entirely on your laptop**. Code, decisions, transcripts, and search queries never leave the machine unless you explicitly opt into team mode (which only shares an append-only event file via your existing git remote).

- **No telemetry of content** — only tool name, version, call duration, error flag, and integer counts. Decision text, transcripts, file paths, repo names, and any user-supplied string are never collected.
- **Opt out of telemetry**: `export BICAMERAL_TELEMETRY=0` or set it in your `.mcp.json` `env` block.
- **Full compliance posture** — host-trust model, acceptable use, install-trust model, audit log, diagnose output, availability stance — lives in [`docs/policies/`](docs/policies/).

## Software supply chain

Each release ships signed artifacts on the [Releases page](https://github.com/BicameralAI/bicameral-mcp/releases):

| Artifact | What it is |
|---|---|
| `bicameral-mcp.sbom.json` | CycloneDX SBOM of the wheel's dependency closure |
| `bicameral-mcp.sbom.intoto.jsonl` | Sigstore Rekor attestation over the SBOM |
| `hooks-manifest.json{,.sig,.crt}` | Signed manifest of the post-install hooks |
| `skills-manifest.toml{,.sig,.crt}` | Signed manifest of bundled skills |
| `release-tag-commit.txt{,.sig,.crt}` | Cosign keyless signature of the release-tag commit |

Verification procedure: [`docs/RELEASE_EVIDENCE_PROCEDURE.md`](docs/RELEASE_EVIDENCE_PROCEDURE.md).

GitHub's auto-generated dependency graph SBOM (SPDX format) is also available under **Insights → Dependency graph → Export SBOM**.

## Supported versions

Only the **latest minor** is actively maintained. Critical fixes get backported to the prior minor for ~30 days after a new minor ships; older releases are best-effort.

Check the recommended version your install will upgrade to:

```bash
cat $(python -c 'import bicameral_mcp; print(bicameral_mcp.__file__)' | xargs dirname)/RECOMMENDED_VERSION
# or
bicameral-mcp update
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
- The bundled skill files installed by `bicameral-mcp setup`
- The post-install hooks (`bicameral-mcp-preflight-reminder`, etc.)
- The release supply chain (signed manifests, SBOM emitter, publish workflow)

Out of scope:
- Issues in third-party dependencies — file those upstream first; we'll patch our pin if a fix lands
- Issues in MCP hosts (Claude Code, Cursor, Codex) — file those with the host vendor
- Vulnerabilities reachable only by an attacker with write access to your `.bicameral/` directory or `~/.claude/settings.json` (local-attack assumption already covered by host-trust model)
