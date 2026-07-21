# MCP Alpha Host Promotion Receipts

This workflow assembles and validates the terminal evidence required by MCP
issue `#736`. It does not run the topology, grant admission, or turn supporting
component evidence into terminal User Journey evidence.

Do not start a Claude Code or Codex host attempt until a schema-valid, active
Bounded Manual Topology Grant authorizes the exact
`mcp-alpha-host-promotion-v1` profile. Each host must be captured independently
from a clean home by the genuine production host.

The grant applies only inside an already human-created execution surface. It
does not authorize Devin/provider session creation, retry, continuation, or
termination. A human operator retains authentication, installation consent,
explicit product confirmation, and final acceptance authority. The alpha grant
is defined by Factory #298 / PR #299 and expires at the July 29 alpha cut.

Validate the grant before any host process starts:

```bash
python3 scripts/validate_mcp_alpha_host_promotion_grant.py \
  /path/to/bounded-manual-grant.json
```

Expiry, revocation, profile/runner/executor mismatch, non-zero external spend,
parallel executors, a widened attempt/timeout budget, or removal of a prohibited
action fails closed.

## Evidence inputs

Prepare one sanitized host summary for each host. These summaries must be
produced from the real host run, not copied from this example:

```json
{
  "profile": "mcp-alpha-host-promotion-v1",
  "evidence_level": "real_process_integration",
  "capture_kind": "production_host_session",
  "host": "claude",
  "host_run": {
    "host_version": "<exact version>",
    "documented_mechanism": "<production mechanism>",
    "clean_host_configuration": {
      "status": "passed",
      "host_home": "<clean host home>",
      "config_root": "<clean config root>"
    },
    "bounded_context_sanitization": {
      "status": "passed",
      "raw_transcript_collected": false,
      "secrets_collected": false
    },
    "preflight_invocations": 1,
    "candidate_rendered": true,
    "confirmation_required_rendered": true,
    "explicit_human_confirmation": true,
    "agent_or_hook_self_confirm_possible": false,
    "challenge_resubmitted": true,
    "daemon_materialized_decision": true,
    "ledger_visible_after_restart": true,
    "factory_runtime_dependency_absent": true
  },
  "negative_path_receipts": {
    "automatic_hook_unavailable_manual_fallback": "passed"
  },
  "lifecycle_receipt_files": {
    "install": "claude-install.json",
    "status": "claude-status.json",
    "disable": "claude-disable.json",
    "update": "claude-update.json",
    "uninstall": "claude-uninstall.json"
  }
}
```

The actual `negative_path_receipts` object must contain every path declared in
`REQUIRED_NEGATIVE_PATHS` in the merged validator. Lifecycle paths are resolved
relative to the host summary. The assembler reads and hashes those files and
derives lifecycle pass/fail status from their machine-readable content.

Prepare one sanitized shared topology summary with
`capture_kind: real_process_topology`. It owns only the shared process-health,
isolation, candidate/canonical-result correlation, restart/replay, teardown, and
sanitization sections. It must not contain challenge values or credentials.

## Assemble and validate

```bash
python3 scripts/assemble_mcp_alpha_host_promotion_receipt.py \
  --mcp-root /path/to/bicameral-mcp \
  --bot-root /path/to/bicameral-bot \
  --artifact mcp_wheel=/path/to/bicameral_mcp.whl \
  --artifact bot_binary=/path/to/bicameral \
  --contract topology_contract=scripts/run_mcp_alpha_host_promotion_topology.py \
  --authorization-grant /path/to/bounded-manual-grant.json \
  --host-evidence claude=/path/to/claude-evidence.json \
  --host-evidence codex=/path/to/codex-evidence.json \
  --shared-evidence /path/to/shared-evidence.json \
  --output /path/to/combined-terminal-receipt.json

python3 scripts/run_mcp_alpha_host_promotion_topology.py \
  --mcp-root /path/to/bicameral-mcp \
  --bot-root /path/to/bicameral-bot \
  --receipt-input /path/to/combined-terminal-receipt.json \
  --release-artifact /path/to/bicameral_mcp.whl \
  --release-artifact /path/to/bicameral \
  --json
```

The assembler exits `0` only when the existing merged validator accepts the
combined receipt and the same grant remains active and exact. Missing or expired
authorization, missing host evidence, direct `prework-run` evidence,
synthetic capture kinds, missing lifecycle receipts, incomplete negative paths,
and missing release artifacts remain product failures.

The combined JSON is a sanitized review artifact. Named human product review is
still required before `#736` can satisfy the alpha release gate.
The authorization summary in that receipt proves only that execution was
bounded; it is not terminal evidence, product acceptance, or release authority.
