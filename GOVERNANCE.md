# Project Governance

## Project

Bicameral MCP — agent-facing MCP tools for ingest, preflight, binding, and review commands.

## Maintainers

BicameralAI maintainers steward repository direction, review contributions, and preserve
the authority boundaries documented in `README.md` and `docs/`.

## Decision making

Source of truth for behavior is the protocol contract: the MCP tools emit candidates,
evidence, hints, signals, and advisories through protocol-compatible paths; they must not
write canonical state directly. Blocking and enforcement remain governance decisions, not
tool side effects.

## Process governance (three layers)

Repository **process** governance is layered:

- **Shared process (bic-logic)** — factory-owned doctrine, owned upstream in
  `bicameral-factory` and consumed here. This is the **one mandatory layer**: the contract
  every PR must satisfy.
- **Sibling tools (registry)** — any local process, governance, or AI tooling a contributor
  uses is a *registered sibling*: leak-guarded, never tracked, never referenced. The registry
  is [`docs/governance/SIBLINGS.md`](docs/governance/SIBLINGS.md). The maintainer's own tooling
  is itself a registered sibling, not a requirement on contributors.

Contributors are free to bring their own tooling — see `CONTRIBUTING.md` → *Bring your own
tools*. This is repo/process governance only; it never produces product Decisions, gates, or
compliance outcomes.

## Branch protection plan

The default branch must be protected with the repository's SOC2 compensating
control for limited team size:

- Changes to `main` land by pull request; direct pushes are blocked.
- At least one approving review from a named human reviewer is required before merge.
- `CODEOWNERS` designates the required human reviewer set. AI agents and automation
  identities are untrusted contributors for approval purposes and have no standing
  self-approval.
- Required status checks must pass before merge, including deterministic tests,
  lint/typecheck, secret scanning, security scanning, dependency review, governance
  boundary, factory attestation, and CodeQL where enabled.
- Branch protection applies to administrators; admin bypass of the human-review gate
  requires an explicit documented exception.
- Force pushes and branch deletion are restricted on `main` and release branches.

As of the #675 control review, GitHub reports `main` as protected. Detailed branch
protection settings are maintained in GitHub repository settings and require an
administrator to verify or change.

## Contribution guidelines

See `CONTRIBUTING.md`.

## Code of conduct

See `CODE_OF_CONDUCT.md`.

## Security policy

See `SECURITY.md`.
