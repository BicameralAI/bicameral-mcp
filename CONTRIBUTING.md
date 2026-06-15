# Contributing to Bicameral MCP

Thank you for considering a contribution. This repo provides agent-facing Bicameral MCP
tools for ingest, preflight, binding, and review commands. The tools emit protocol-shaped
objects and route through governance; they never own canonical state.

## How to contribute

1. Fork the repository (or branch, if you have access).
2. Create a topic branch for your change.
3. Keep tool behavior aligned with the authority boundaries in `README.md` and `docs/`.
4. Add or update tests when behavior changes.
5. Run the local checks below before opening a pull request.
6. Open a pull request against the default branch using the repository's conventions.

## Local checks

These mirror the CI gates (`lint-and-typecheck`, tests, `secret-scan`):

```bash
pip install -e ".[test]"
ruff check .
ruff format --check .
mypy .
pytest
```

If you have [`pre-commit`](https://pre-commit.com/) installed, hooks run on commit:

```bash
pip install pre-commit && pre-commit install
pre-commit run --all-files
```

## Bring your own tools — the sibling registry

You are **not** required to adopt the maintainer's process tooling. The only thing your PR
must satisfy is the shared **bic-logic** contract plus the local checks above. Everything
you run *locally* — a governance system, an AI assistant, an IDE plugin, or a homegrown
framework — is welcome as a **registered sibling**: leak-guarded, never tracked, never
referenced.

To use your own tool: add a row to [`docs/governance/SIBLINGS.md`](docs/governance/SIBLINGS.md),
add its scratch root to `.gitignore`, and keep its artifacts out of tracked files.

## Issue reports

Use the issue templates for bugs, feature requests, and documentation problems. Include
reproduction steps, expected behavior, and any relevant protocol contracts.

## License

By contributing, you agree that your contributions are licensed under the license that
governs this repository.
