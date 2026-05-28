# Bicameral MCP

**Bicameral MCP** is the public, agent-facing tool surface for Bicameral.

It lets local coding agents ingest candidate decisions, run preflight, request local binding/grounding, query review state, and emit review commands. It does not own source-specific integrations, canonical storage, or the hosted code graph.

## Key Features

- **Agent lifecycle tools** – ingest, preflight, bind, review, query, and explain commands.
- **Protocol-shaped output** – emits objects compatible with `bicameral-bot/protocol/`.
- **Local-first operation** – talks to the local bot runtime and workspace context.
- **Review-command discipline** – agents suggest or emit allowed commands; governance decides materialization.

## High-level Architecture

```text
Coding agent / MCP host
        │
        ▼
bicameral-mcp tools
        │ protocol-shaped evidence + ReviewCommand
        ▼
bicameral-bot local daemon/gateway
        │
        ├── local grounding + review state
        └── optional Bicameral Cloud advisory query
```

## Repository Layout

```text
├── docs/adr/                # MCP-specific architecture decisions
├── CONTEXT.md               # Project glossary and resolved terms
└── README.md                # You are here
```

## Related Repositories

- [`bicameral-bot`](https://github.com/BicameralAI/bicameral-bot) – local daemon/gateway and embedded protocol contracts.
- [`bicameral-integrations`](https://github.com/BicameralAI/bicameral-integrations) – source adapters and EM-safe mods.
- [`bicameral-cloud`](https://github.com/BicameralAI/bicameral-cloud) – hosted code graph/oracle.

## Boundary Rule

MCP is the agent's hands, not Bicameral's authority. It can surface evidence and emit commands. The bot's governance policy and storage adapters decide what becomes canonical.

## Testing

```bash
pytest -v tests/
```
