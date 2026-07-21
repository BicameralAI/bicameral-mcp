#!/usr/bin/env python3
"""Fail closed unless a bounded manual grant authorizes the MCP #736 topology."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROFILE = "mcp-alpha-host-promotion-v1"
SOURCE_ISSUE = "BicameralAI/bicameral-mcp#736"
RUNNER = "scripts/run_mcp_alpha_host_promotion_topology.py"
EXECUTOR = "devin"
MAXIMUM_ATTEMPTS = 4
MAXIMUM_TIMEOUT_MINUTES = 45
GRANT_ID = re.compile(r"^bmtg_[A-Za-z0-9_-]{8,96}$")
REQUIRED_PROHIBITIONS = {
    "create_retry_or_terminate_implementation_session",
    "export_or_reveal_credentials",
    "mutate_customer_or_team_data",
    "approve_product_acceptance",
    "merge_release_or_deploy",
    "widen_scope_budget_or_expiry",
}


class GrantError(ValueError):
    """The authorization grant does not admit this exact topology."""


def _timestamp(value: Any, *, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise GrantError(f"{name} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GrantError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise GrantError(f"{name} must include a timezone")
    return parsed.astimezone(UTC)


def _string(document: dict[str, Any], name: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value:
        raise GrantError(f"{name} must be a non-empty string")
    return value


def _strings(document: dict[str, Any], name: str) -> list[str]:
    value = document.get(name)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
        or len(value) != len(set(value))
    ):
        raise GrantError(f"{name} must be a non-empty array of unique strings")
    return value


def _positive_integer(document: dict[str, Any], name: str) -> int:
    value = document.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GrantError(f"{name} must be a positive integer")
    return value


def load_and_validate_grant(
    path: Path,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GrantError(f"cannot read authorization grant {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise GrantError("authorization grant must be an object")
    if document.get("version") != 1:
        raise GrantError("authorization grant version must be 1")

    grant_id = _string(document, "grant_id")
    if GRANT_ID.fullmatch(grant_id) is None:
        raise GrantError("grant_id is invalid")
    if _string(document, "status") != "active":
        raise GrantError("authorization grant is not active")
    if document.get("execution_surface") != "existing-human-created":
        raise GrantError("execution_surface must be existing-human-created")
    if document.get("human_presence_required") is not True:
        raise GrantError("human_presence_required must be true")
    exact_fields = {
        "source_issue": SOURCE_ISSUE,
        "profile": PROFILE,
        "runner": RUNNER,
        "privilege_class": "privileged-external",
        "executor": EXECUTOR,
    }
    for name, expected in exact_fields.items():
        if _string(document, name) != expected:
            raise GrantError(f"{name} must be {expected}")
    for name in ("issued_by", "human_operator", "approval_record_url"):
        _string(document, name)

    issued_at = _timestamp(document.get("issued_at"), name="issued_at")
    expires_at = _timestamp(document.get("expires_at"), name="expires_at")
    if expires_at <= issued_at:
        raise GrantError("expires_at must be later than issued_at")
    effective_now = (now or datetime.now(UTC)).astimezone(UTC)
    if effective_now >= expires_at:
        raise GrantError("authorization grant is expired")

    for name in (
        "allowed_actions",
        "credential_classes",
        "resource_scope",
        "required_receipt",
        "prohibited_actions",
    ):
        _strings(document, name)
    missing = REQUIRED_PROHIBITIONS - set(document["prohibited_actions"])
    if missing:
        raise GrantError("prohibited_actions missing: " + ", ".join(sorted(missing)))

    budget = document.get("run_budget")
    if not isinstance(budget, dict):
        raise GrantError("run_budget must be an object")
    _positive_integer(budget, "successful_runs_required")
    attempts = _positive_integer(budget, "maximum_attempts")
    timeout = _positive_integer(budget, "timeout_minutes_per_attempt")
    if attempts > MAXIMUM_ATTEMPTS:
        raise GrantError(f"maximum_attempts exceeds {MAXIMUM_ATTEMPTS}")
    if timeout > MAXIMUM_TIMEOUT_MINUTES:
        raise GrantError(f"timeout_minutes_per_attempt exceeds {MAXIMUM_TIMEOUT_MINUTES}")
    if budget.get("maximum_concurrent_executors") != 1:
        raise GrantError("maximum_concurrent_executors must be 1")
    if budget.get("maximum_external_spend_usd") != 0:
        raise GrantError("maximum_external_spend_usd must be 0 for MCP #736")

    summary = {
        "mode": "bounded-manual-grant",
        "grant_id": grant_id,
        "issued_by": document["issued_by"],
        "approval_record_url": document["approval_record_url"],
        "executor": document["executor"],
        "human_operator": document["human_operator"],
        "expires_at": document["expires_at"],
        "maximum_attempts": attempts,
        "timeout_minutes_per_attempt": timeout,
        "maximum_external_spend_usd": 0,
        "provider_session_authority": False,
        "product_acceptance_authority": False,
    }
    return document, summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("grant", type=Path)
    parser.add_argument("--now", help="Override current time for deterministic validation.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        now = _timestamp(args.now, name="now") if args.now else None
        _, summary = load_and_validate_grant(args.grant, now=now)
    except GrantError as exc:
        print(json.dumps({"decision": "deny", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"decision": "allow", **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
