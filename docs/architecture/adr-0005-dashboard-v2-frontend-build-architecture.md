# ADR-0005: Dashboard v2 Frontend Build Architecture

**Status:** Accepted for planning
**Date:** 2026-05-21
**Related:** `docs/design/dashboard-v2-comprehensive-design.md`, `docs/architecture/adr-0001-dashboard-v2-information-architecture.md`

> Renumbered from ADR-0004 → ADR-0005 to resolve a numbering collision with
> `adr-0004-three-layer-pm-em-dev-conceptual-model.md` (the design author's
> deliberately-numbered conceptual-model ADR, pulled from the design fork).

## Context

The current dashboard is a single hand-maintained file, `assets/dashboard.html`
(~1,538 lines of HTML, CSS, and JavaScript), served verbatim by
`dashboard/server.py`. There is no build step: the file *is* the source and the
artifact.

Dashboard v2 specifies an eight-view application — Pulse, Ledger, Ratification,
Drift, Sources, Audit, Integrations, Settings — decomposed into roughly fifty
components, with TypeScript data contracts (`IdentityContext`,
`IntegrationConfigSummary`, `AccessibilityPreferences`, and others), five
themes, and WCAG 2.2 AA compliance.

A single hand-maintained file cannot carry that surface without becoming
unmaintainable, and the design's TypeScript data contracts cannot be enforced
without a TypeScript compiler.

## Decision

Adopt a real frontend build toolchain for Dashboard v2.

- **Build tool:** Vite.
- **Language:** TypeScript — the design's data contracts become compiler-
  enforced types rather than documentation.
- **Component framework:** a JSX-based, React-compatible framework (React or
  Preact). The final pick is deferred to the Milestone 1 plan; the component
  model is JSX and matches the design's component tree.
- **Output contract:** Vite produces a static bundle. `dashboard/server.py`
  continues to serve a built artifact — the server's HTTP contract (`GET /`,
  `/history`, `/events`, `/pulse`) is unchanged; only the *source form* of the
  bundle changes.
- **Shipped artifact:** the built bundle is shipped, so end users never need a
  Node toolchain. The zero-config "just run the server" path is preserved.
- **On-demand, not a daemon:** the dashboard is launched when the user wants it
  and is not required to run continuously. Agent and CLI output remain the
  primary delivery channel (design principle 2.7); the build architecture must
  not assume an always-on server.
- **Ledger preservation:** the existing `assets/dashboard.html` behaviour is
  ported into the new architecture as `LedgerView` (and `PulseView`, from
  #437), preserving current functionality as ADR-0001 mandates.

## Rationale

Roughly fifty components in a single vanilla file is untenable. The design's
TypeScript contracts need a compiler to be real. Vite is the standard
low-configuration choice for a TypeScript SPA. Serving a pre-built static
bundle keeps `dashboard/server.py`'s existing "serve a bundle" contract intact,
so the migration is contained to the frontend source tree and does not touch
the MCP server, the ledger, or the HTTP endpoints.

## Consequences

Positive:

- A real component model, scalable to the eight-view surface.
- Type-safe, compiler-enforced data contracts.
- Theming and accessibility become tractable instead of hand-rolled.
- The server contract and end-user run path are unchanged.

Tradeoffs:

- Introduces a Node toolchain into a Python-first repository.
- CI must build the frontend; the build must be reproducible.
- Contributors who modify the dashboard need Node installed.
- The existing `assets/dashboard.html` must be ported, not merely extended.

## Acceptance Criteria

- A Vite + TypeScript project exists for the dashboard frontend.
- `dashboard/server.py` serves the built bundle; `GET /`, `/history`,
  `/events`, and `/pulse` behave exactly as before.
- The existing Ledger view's behaviour is preserved after the port.
- The Project Pulse view (#437) is preserved after the port.
- CI builds the frontend and the build is reproducible.
- End users do not need a Node toolchain to run the dashboard — the built
  artifact is shipped.
