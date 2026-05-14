"""bicameral-mcp sync-and-brief — pull-based session-magic CLI (#279 Phase 1).

Pulls from configured sources in ``.bicameral/config.yaml`` under the
``sources:`` key, auto-chains through ``handle_ingest``, calls
``handle_preflight`` for drift, and prints a markdown brief to stdout.

Designed to be invoked by the SessionStart hook (or by the operator
manually). Returns exit code 0 on success — even when there's nothing
new to brief. The SessionStart hook wrapper additionally appends
``exit 0`` so the hook can NEVER block session start.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _build_argparser(subparser: argparse.ArgumentParser) -> None:
    """Wire the subcommand's args. Called from ``server.py``'s argparse."""
    subparser.add_argument(
        "--max-decisions",
        type=int,
        default=20,
        help="Cap on the number of decisions in the brief (default: 20).",
    )
    subparser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout output; useful for hook-driven invocation.",
    )


def main(args: argparse.Namespace) -> int:
    """Entry point invoked from ``server.py``'s ``_dispatch``.

    Always returns 0 on success. On unexpected exception logs to stderr
    + ``~/.bicameral/cli-errors.log`` and returns 1.
    """
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 — operator-visible CLI; never re-raise
        _log_to_errors_file(exc)
        print(f"[sync-and-brief] unexpected error: {exc}", file=sys.stderr)
        return 1


async def _run(args: argparse.Namespace) -> int:
    from context import BicameralContext

    ctx = BicameralContext.from_env()
    config = _read_config(ctx)
    sources = (config or {}).get("sources") or []

    if not sources:
        if not args.quiet:
            print(
                "No sources configured. Add a `sources:` block to "
                "`.bicameral/config.yaml` or run `bicameral-mcp setup` "
                "to bootstrap one. Nothing to do."
            )
        return 0

    watermark_dir = Path.home() / ".bicameral" / "source-watermarks"
    for source in sources:
        await _run_source(ctx, source, watermark_dir=watermark_dir)

    brief = await _synthesize_brief(ctx, max_decisions=args.max_decisions)
    if not args.quiet:
        print(brief)
    return 0


async def _run_source(ctx: Any, source: dict, *, watermark_dir: Path) -> None:
    """Per-source pull → ingest → confirm-watermark two-phase commit.

    Catches MissingApiKeyError and logs a friendly message; never raises
    to the caller (other sources should still run).
    """
    from events.sources import ADAPTERS, MissingApiKeyError
    from handlers.ingest import handle_ingest

    source_type = str(source.get("type") or "")
    adapter_cls = ADAPTERS.get(source_type)
    if adapter_cls is None:
        print(
            f"[sync-and-brief] unknown source type {source_type!r}; skipping.",
            file=sys.stderr,
        )
        return

    adapter = adapter_cls()
    try:
        payloads = adapter.pull(watermark_dir=watermark_dir, config=source)
    except MissingApiKeyError as exc:
        print(f"[sync-and-brief] {exc}", file=sys.stderr)
        return
    except Exception as exc:  # noqa: BLE001
        print(
            f"[sync-and-brief] {source_type} source pull failed: {exc}",
            file=sys.stderr,
        )
        return

    if not payloads:
        return

    try:
        for payload in payloads:
            await handle_ingest(
                ctx, payload, source_scope=source_type, cursor=adapter.name
            )
    except Exception as exc:  # noqa: BLE001
        # Ingest failure: do NOT advance watermark.
        print(
            f"[sync-and-brief] {source_type} ingest failed (watermark "
            f"NOT advanced): {exc}",
            file=sys.stderr,
        )
        return

    adapter.confirm_watermark()


async def _synthesize_brief(ctx: Any, *, max_decisions: int) -> str:
    """Compute drift findings, fetch recent decisions, render the brief."""
    from cli.brief_renderer import render_brief
    from handlers.preflight import handle_preflight

    drift_findings: list[dict] = []
    try:
        preflight_resp = await handle_preflight(ctx)
        # handle_preflight's response shape varies; pull findings defensively.
        findings = getattr(preflight_resp, "findings", None)
        if findings is None and isinstance(preflight_resp, dict):
            findings = preflight_resp.get("findings")
        drift_findings = [_finding_to_dict(f) for f in (findings or [])]
    except Exception as exc:  # noqa: BLE001 — drift is best-effort
        logger.warning("[sync-and-brief] preflight failed: %s", exc)

    decisions: list = []
    try:
        ledger = ctx.ledger
        if hasattr(ledger, "connect"):
            await ledger.connect()
        all_decisions = await ledger.get_all_decisions(filter="all")
        # Sort newest-first then cap.
        decisions = sorted(
            all_decisions,
            key=lambda d: _get_decision_sort_key(d),
            reverse=True,
        )[:max_decisions]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sync-and-brief] decision fetch failed: %s", exc)

    signer_mode = _resolve_signer_fallback_mode(ctx)
    return render_brief(
        decisions,
        drift_findings,
        max_decisions=max_decisions,
        signer_fallback_mode=signer_mode,
    )


# ── helpers ────────────────────────────────────────────────────────────────


def _read_config(ctx: Any) -> dict:
    repo_path = Path(getattr(ctx, "repo_path", "."))
    config_path = repo_path / ".bicameral" / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sync-and-brief] config unreadable at %s: %s", config_path, exc)
        return {}


def _resolve_signer_fallback_mode(ctx: Any) -> str:
    """Read the config's signer_email_fallback policy; default local-part-only."""
    config = _read_config(ctx)
    mode = str((config or {}).get("signer_email_fallback") or "local-part-only")
    if mode not in ("redact", "local-part-only", "full"):
        mode = "local-part-only"
    return mode


def _finding_to_dict(f: Any) -> dict:
    if isinstance(f, dict):
        return f
    if hasattr(f, "model_dump"):
        return f.model_dump()
    return {"value": str(f)}


def _get_decision_sort_key(d: Any) -> str:
    if isinstance(d, dict):
        return str(d.get("created_at") or "")
    return str(getattr(d, "created_at", "") or "")


def _log_to_errors_file(exc: BaseException) -> None:
    try:
        log_path = Path.home() / ".bicameral" / "cli-errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "tool": "sync-and-brief",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                + "\n"
            )
    except Exception:  # noqa: BLE001
        pass  # logging failure must not propagate
