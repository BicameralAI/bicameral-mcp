# Availability stance — operator-run-only

**Status**: active
**Closes gap**: SOC2-02 (SOC 2 Type II Availability TSC) per `docs/research-brief-compliance-audit-2026-05-06.md` § 2.2, § 5
**Doctrine**: #205 deterministic-governance hard rule

## Active commitment

bicameral-mcp is **operator-run-only**. The operator installs and runs bicameral-mcp on their own infrastructure (CLI host, IDE, local OS). There is no hosted offering.

Because there is no hosted service, bicameral-mcp declares:

- **No target uptime percentage** — runtime availability is the operator's domain.
- **No MTTR (mean time to recovery) target** — incidents are operator-side; recovery is operator-side.
- **No support response time target** — community support via GitHub issues; no contractual response SLA.
- **No incident notification SLA** — operators monitor their own installs.

## What this means in practice

| Concern | Owner |
|---|---|
| Install platform choice (host, IDE, OS) | Operator |
| Server-process lifecycle (start, restart, monitor) | Operator |
| Upgrades to new wheel versions | Operator |
| Process-health monitoring | Operator |
| Incident response | Operator |
| Data retention / backup | Operator |
| Network reachability of dependencies (Sigstore, GitHub, PyPI) | Operator + upstream service availability |

What bicameral-mcp's CI guarantees: the published wheel works against the declared dependency surface and passes the documented test gates (regression, e2e, lint, type-check, secret scan, schema persistence). The server's runtime availability after install is the operator's domain.

## Activation requirements for a future hosted tier

If a hosted tier ever ships (e.g., `bicameral.cloud`, a managed team-server offering, or any "we run this for you" pricing tier), THIS document changes shape: the operator-run section moves to "Self-hosted (always available)" with no SLA, and a new "Hosted tier (active)" section declares concrete numbers.

Specifically, a future hosted tier MUST declare BEFORE shipping:

- **Target uptime percentage** (e.g., 99.5%, 99.9%, 99.99%)
- **MTTR target** for unplanned incidents (e.g., < 4 hours for sev-1, < 24 hours for sev-2)
- **Support response time target** (e.g., business-hours best-effort, 24x7 for paid tiers)
- **Incident notification SLA** — how operators are notified of service-affecting incidents (e.g., status page within 15 minutes of detection)
- **Security incident-disclosure SLA** — disclosure timeline for security-affecting incidents (e.g., 72-hour disclosure for confirmed breach)
- **Data residency commitments** — geographic boundary on where customer data is stored and processed
- **Change-notification SLA** — advance notice of breaking changes, deprecations, or maintenance windows

Each declaration becomes operator-readable and auditor-readable. Without these, a hosted tier cannot pass SOC 2 Type II audit covering the Availability Trust Service Criterion. **A hosted tier MUST NOT ship without this section being filled in.**

## What changes when a hosted tier ships

When a future hosted tier ships, this document updates to the parallel-stance shape:

```
## Self-hosted (always available)

[current operator-run-only content moves here, unchanged]

## Hosted tier (active)

[new section with the declared numbers from the activation requirements above]
```

The current Active commitment section remains the hosted-tier "Self-hosted" alternative — operators on hosted tiers can still elect to self-host with no SLA.

## Cross-references

- Research brief: § 2.2 gap SOC2-02; § 5 deployment-trigger column (hosted)
- Related: #215 (SOC2-01 — declare MCP transport trust boundary)
- Doctrine: #205 (deterministic-governance hard rule)
