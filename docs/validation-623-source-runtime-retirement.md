# MCP Source Runtime Retirement Validation (#623)

Date: 2026-06-24

## MCP Status

`bicameral-mcp` no longer contains the source-acquisition runtime paths named in
#623:

- `cli/sync_and_brief_cli.py`
- `cli/drive_renew_cli.py`
- `sources/`
- `events/sources/`
- `webhooks/`

The packaged runtime remains the thin ToolRequest client modules listed in
`pyproject.toml`. Local MCP ingest maps only to daemon command
`ingest.submit_local`; MCP does not accept or route `ExternalIngestEnvelope`.

## Guard Added

`tests/test_source_runtime_retired_623.py` locks this boundary by asserting:

- source-acquisition and Drive/webhook runtime paths stay absent;
- package metadata cannot include source runtime paths;
- production Python does not call the old in-process ingest/runtime surfaces;
- MCP keeps only the local `ingest.submit_local` ToolRequest mapping.

## Cross-Repo Blocker

Final #623 closure still depends on the integration-side prerequisite recorded
in the issue:

`bicameral-integrations/runtime/gateway_mapping.py` must emit
`ExternalIngestEnvelope` to bot `POST /api/v1/external-ingest`.

As of this validation pass, the integrations `main` branch still maps
`AdapterEmission` to the legacy v1 `IngestRequest` shape. MCP should not add any
compatibility shim for that contract. Integrations must acquire and emit the
external envelope; bot materializes; MCP remains routing-only.

## Recovery Pointer

Removed MCP-owned behavior remains recoverable from pre-refactor commit
`0827444c80d45fe3474f68002166e1fc35708eda`.
