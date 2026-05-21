# Bicameral Dashboard v2 — Frontend

Vite + TypeScript + Preact source for the operator dashboard. The build
produces a single self-contained HTML artifact that the dashboard server
serves at `/`.

## Regenerating the committed artifact

End users do **not** need a Node toolchain — the build output
(`assets/dashboard.html`, repo root) is committed. Contributors who change
anything under `dashboard/frontend/` must regenerate it:

```
cd dashboard/frontend
npm ci
npm run build
```

`npm run build` runs `tsc --noEmit` (type-check) + `vite build` (emits one
self-contained `dist/index.html` via `vite-plugin-singlefile`) + a Node
post-build copy (`scripts/copy-artifact.mjs`) that lands the file at
`../../assets/dashboard.html`.

The build is byte-deterministic: no timestamp/banner injection, all JS/CSS
inlined (no content-hashed filenames). CI rebuilds and runs
`git diff --exit-code assets/dashboard.html` as a freshness gate, so a stale
committed artifact fails the build.

## Tests

```
npm test
```

Vitest + `@testing-library/preact`. Covers the sidebar nav, hash router,
PulseView states, and the Team Sync pill.

## Pinned toolchain

Every dependency is exact-pinned in `package.json` (no `^`/`~`) and
`package-lock.json` is committed. CI installs with `npm ci` on Node 20 —
the same major used to generate the committed artifact.
