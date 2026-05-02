# Security Policy

This document describes how to report security vulnerabilities affecting
the bicameral-mcp server, what is in scope, and what response timeline to
expect.

## Supported Versions

| Version | Status | Receives security fixes |
|---------|--------|-------------------------|
| `0.11.x` | Current development line | Yes |
| `0.10.x` | Previous stable | Security fixes only |
| `< 0.10` | End of life | No |

The recommended version at any moment is recorded in
[`RECOMMENDED_VERSION`](RECOMMENDED_VERSION) at the repository root.

## Reporting a Vulnerability

**Do not open a public GitHub issue for suspected security problems.**

Preferred channel: GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository (Security tab → Report a vulnerability).

Fallback: email the maintainers via the address listed on the repository
profile. Include:

- A description of the vulnerability and its impact.
- Steps to reproduce, or a minimal proof of concept.
- The version (`git rev-parse HEAD` or release tag) you tested against.
- Your preferred contact method for follow-up.

Please do not include exploit details in the initial public-facing
contact if you must use a fallback channel; we will move the conversation
to a private channel before discussing specifics.

## Threat Model Summary

The bicameral-mcp server is an MCP (Model Context Protocol) tool surface
that runs locally and exposes a SurrealDB-backed decision ledger plus a
code-symbol index to the host AI agent.

**What this server stores:**

- Decision records, code regions, content hashes, symbol indices, and
  bind-time identity metadata in an embedded SurrealDB database
  (`~/.bicameral/ledger.db` by default).
- Local file paths and source-text excerpts referenced by indexed
  symbols.
- Process-governance artifacts (META_LEDGER, SHADOW_GENOME, gate chain)
  in plain text under `docs/` and `.qor/`.

**What this server does NOT store:**

- Authentication credentials, API keys, OAuth tokens, or session secrets.
- End-user personally identifiable information (PII) — names, emails,
  postal addresses, phone numbers, government IDs, payment data.
- Encrypted blobs whose decryption keys are accessible to this process.

**Trust boundary:**

The server runs in-process under the host AI tool (Claude Code, etc.) and
inherits that host's trust posture. The server itself does not perform
network authentication, accept inbound network connections by default, or
delegate authority on behalf of users to remote services. The trust
assumption is that the host process and the local filesystem are not
adversarial.

**Out of scope for this policy:**

- Vulnerabilities in dependencies (SurrealDB, tree-sitter, Python stdlib)
  unless they are exploitable through this server's documented API.
- Issues in development tooling (`tests/`, `scripts/`, fixtures) that
  are not part of a release artifact.
- Behavior under hosts that violate the trust boundary above.

## Response SLA

| Stage | Target |
|-------|--------|
| Acknowledgement of report | within 7 calendar days |
| Initial triage and severity assessment | within 14 days |
| Fix or coordinated disclosure plan | within 30 days |

The 30-day fix-or-coordinated-disclosure target follows the established
norm for vulnerability handling. For complex issues that require
upstream coordination, we will agree a longer timeline with the reporter
in writing before the 30-day mark.

We will credit reporters in the release notes and CHANGELOG unless
anonymity is requested.

## Safe Harbor

We will not pursue legal action against researchers who:

- Report findings through the channels described above.
- Avoid privacy violations, data destruction, and service degradation in
  the course of testing.
- Do not access, modify, or exfiltrate data they are not authorized to
  access.
- Provide reasonable time for remediation before public disclosure.
