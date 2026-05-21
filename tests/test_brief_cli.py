"""Sociable tests for the ``bicameral-mcp brief`` CLI (#437 Phase 2).

Per CLAUDE.md's mandatory sociable-testing rule for anything that reads the
ledger: every test instantiates a **real** ``SurrealDBLedgerAdapter`` over
``memory://`` and seeds decision rows with the production schema (the
``_fresh_adapter`` pattern from ``test_codegenome_continuity_service.py``).
No ``MagicMock`` ledger — observable CLI output is asserted, not call shapes.

The CLI's ``main`` wraps ``_run`` in ``asyncio.run``; pytest-asyncio already
owns an event loop, so the async tests below drive the real ``_run`` coroutine
directly (the same coroutine ``main`` runs) with ``BicameralContext.from_env``
patched to a context carrying the real seeded adapter. ``main``'s thin
exit-code mapping (``SinceParseError`` → 2, success → 0) is covered by the
non-async tests at the bottom.
"""

from __future__ import annotations

import argparse
import io
import itertools
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import cli.brief_cli as brief_cli
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate
from pulse.summary import SinceParseError


async def _fresh_adapter(suffix: str) -> tuple[SurrealDBLedgerAdapter, LedgerClient]:
    """Return a real ``SurrealDBLedgerAdapter`` over an isolated ``memory://`` ledger."""
    client = LedgerClient(url="memory://", ns=f"briefcli_{suffix}", db="ledger_test")
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)
    adapter = SurrealDBLedgerAdapter(url="memory://")
    adapter._client = client
    adapter._connected = True
    return adapter, client


_canonical_counter = itertools.count()


async def _seed_decision(
    client: LedgerClient,
    *,
    description: str,
    status: str = "ungrounded",
    source_type: str = "manual",
    source_ref: str = "",
    feature_hint: str = "",
    signoff: dict | None = None,
    created_at: str | None = None,
) -> str:
    """Create one decision row with the production schema; return its id string."""
    set_clause = (
        "description = $d, status = $s, source_type = $st, source_ref = $sr, "
        "feature_hint = $fh, signoff = $sig, canonical_id = $cid"
    )
    vars_: dict = {
        "d": description,
        "s": status,
        "st": source_type,
        "sr": source_ref,
        "fh": feature_hint,
        "sig": signoff,
        "cid": f"briefcli-test-{next(_canonical_counter)}",
    }
    if created_at is not None:
        set_clause += ", created_at = <datetime>$ca"
        vars_["ca"] = created_at
    rows = await client.query(f"CREATE decision SET {set_clause}", vars_)
    return str(rows[0]["id"])


def _make_args(
    *,
    json_out: bool = False,
    since: str | None = None,
    feature: str | None = None,
    recent_limit: int = 8,
) -> argparse.Namespace:
    return argparse.Namespace(
        command="brief",
        json=json_out,
        since=since,
        feature=feature,
        recent_limit=recent_limit,
    )


async def _run_brief(adapter: SurrealDBLedgerAdapter, args: argparse.Namespace) -> tuple[int, str]:
    """Drive ``brief_cli._run`` with ``from_env`` patched to the real adapter.

    Returns ``(exit_code, stdout)``. ``_run`` is the exact coroutine ``main``
    executes via ``asyncio.run`` — driving it directly avoids nesting an
    ``asyncio.run`` inside pytest-asyncio's running loop.
    """
    ctx = SimpleNamespace(repo_path=".", ledger=adapter)
    buf = io.StringIO()
    with patch("context.BicameralContext.from_env", return_value=ctx):
        with redirect_stdout(buf):
            rc = await brief_cli._run(args)
    return rc, buf.getvalue()


# ── argparse wiring ──────────────────────────────────────────────────────────


def test_argparser_accepts_json_since_feature_and_limit() -> None:
    """The subcommand's argparse accepts every documented flag."""
    parser = argparse.ArgumentParser()
    brief_cli._build_argparser(parser)
    args = parser.parse_args(
        ["--json", "--since", "yesterday", "--feature", "checkout", "--recent-limit", "3"]
    )
    assert args.json is True
    assert args.since == "yesterday"
    assert args.feature == "checkout"
    assert args.recent_limit == 3


def test_argparser_max_decisions_aliases_recent_limit() -> None:
    """``--max-decisions`` is an accepted alias for ``--recent-limit``."""
    parser = argparse.ArgumentParser()
    brief_cli._build_argparser(parser)
    args = parser.parse_args(["--max-decisions", "5"])
    assert args.recent_limit == 5


# ── plain-text brief over a real ledger ──────────────────────────────────────


async def test_brief_prints_project_pulse_summary() -> None:
    """``brief`` prints the Project Pulse summary built from real ledger rows."""
    adapter, client = await _fresh_adapter("plain")
    await _seed_decision(
        client,
        description="Adopt feature flags",
        signoff={"state": "proposed", "signer": "silong"},
    )

    rc, out = await _run_brief(adapter, _make_args())

    assert rc == 0
    assert "Bicameral Brief" in out
    assert "Needs Attention" in out
    assert "Adopt feature flags" in out
    assert "read-only data" in out  # data-framing line preserved


async def test_brief_all_clear_is_explicit_and_friendly() -> None:
    """An all-clear ledger renders the explicit friendly message."""
    adapter, client = await _fresh_adapter("allclear")
    await _seed_decision(
        client,
        description="Already ratified",
        status="reflected",
        signoff={"state": "ratified", "signer": "jin"},
    )

    rc, out = await _run_brief(adapter, _make_args())

    assert rc == 0
    assert "Bicameral checked project memory." in out
    assert "No drift, no pending signoffs, memory is current." in out


# ── --json over a real ledger ────────────────────────────────────────────────


async def test_brief_json_emits_valid_structured_summary() -> None:
    """``brief --json`` emits JSON parseable back to the summary shape."""
    adapter, client = await _fresh_adapter("json")
    await _seed_decision(
        client,
        description="Cache vocab lookups",
        signoff={"state": "proposed", "signer": "silong"},
    )

    rc, out = await _run_brief(adapter, _make_args(json_out=True))

    assert rc == 0
    payload = json.loads(out)
    assert payload["is_all_clear"] is False
    assert isinstance(payload["health"], dict)
    assert isinstance(payload["needs_attention"], list)
    assert isinstance(payload["recently_learned"], list)
    assert payload["needs_attention"][0]["kind"] == "awaiting_ratification"
    assert "suggested_next_move" in payload


# ── --since filtering ────────────────────────────────────────────────────────


async def test_brief_since_bounds_recently_learned() -> None:
    """``--since`` drops decisions older than the cutoff from recently-learned."""
    adapter, client = await _fresh_adapter("since")
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await _seed_decision(
        client,
        description="ancient decision",
        status="reflected",
        created_at=old,
    )
    await _seed_decision(
        client,
        description="fresh decision",
        status="reflected",
        created_at=recent,
    )

    rc, out = await _run_brief(adapter, _make_args(json_out=True, since="2d"))

    assert rc == 0
    payload = json.loads(out)
    learned = [item["summary"] for item in payload["recently_learned"]]
    assert "fresh decision" in learned
    assert "ancient decision" not in learned


async def test_brief_bad_since_raises_since_parse_error() -> None:
    """An unparseable ``--since`` value raises ``SinceParseError`` from ``_run``
    — ``main`` maps that to exit 2 (see ``test_main_maps_bad_since_to_exit_2``)."""
    adapter, _client = await _fresh_adapter("badsince")
    with pytest.raises(SinceParseError):
        await _run_brief(adapter, _make_args(since="next-tuesday-ish"))


# ── --feature filtering ──────────────────────────────────────────────────────


async def test_brief_feature_filters_by_feature_hint() -> None:
    """``--feature`` keeps only decisions whose feature_hint matches."""
    adapter, client = await _fresh_adapter("feature")
    await _seed_decision(
        client,
        description="checkout flow change",
        signoff={"state": "proposed", "signer": "silong"},
        feature_hint="checkout",
    )
    await _seed_decision(
        client,
        description="search ranking change",
        signoff={"state": "proposed", "signer": "jin"},
        feature_hint="search",
    )

    rc, out = await _run_brief(adapter, _make_args(json_out=True, feature="checkout"))

    assert rc == 0
    payload = json.loads(out)
    attention = [item["summary"] for item in payload["needs_attention"]]
    assert "checkout flow change" in attention
    assert "search ranking change" not in attention


# ── main() exit-code mapping (thin wrapper over _run) ────────────────────────


def test_main_maps_bad_since_to_exit_2(capsys) -> None:
    """``main`` catches ``SinceParseError`` from ``_run`` and exits 2 with a
    clear stderr message."""
    with patch.object(brief_cli, "_run", side_effect=SinceParseError("unrecognized --since value")):
        rc = brief_cli.main(_make_args(since="garbage"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid --since" in err


def test_main_returns_0_on_success() -> None:
    """``main`` returns the exit code ``_run`` produced on the happy path."""

    async def _ok(_args: argparse.Namespace) -> int:
        return 0

    with patch.object(brief_cli, "_run", new=_ok):
        rc = brief_cli.main(_make_args())
    assert rc == 0


def test_main_maps_unexpected_error_to_exit_1(capsys) -> None:
    """An unexpected exception from ``_run`` is caught, logged, exit 1."""
    with patch.object(brief_cli, "_run", side_effect=RuntimeError("ledger down")):
        rc = brief_cli.main(_make_args())
    assert rc == 1
    err = capsys.readouterr().err
    assert "unexpected error" in err
    assert "ledger down" in err
