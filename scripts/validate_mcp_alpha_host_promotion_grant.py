#!/usr/bin/env python3
"""Validate either authorization mode accepted by the MCP #736 topology."""

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
REQUIRED_HOSTS = {"claude", "codex"}
REQUIRED_CREDENTIAL_ALIASES = {"anthropic-api", "openai-api"}
MAXIMUM_ATTEMPTS = 4
MAXIMUM_TIMEOUT_MINUTES = 45
BMTG_ID = re.compile(r"^bmtg_[A-Za-z0-9_-]{8,96}$")
TEG_ID = re.compile(r"^teg_[A-Za-z0-9_-]{8,96}$")
REVISION = re.compile(r"^[a-f0-9]{40}$")
ORGANIZATION_ID = re.compile(r"^org-[A-Za-z0-9_-]+$")
ORGANIZATION_SECRET_REFERENCE = re.compile(r"^secret:org:[A-Z][A-Z0-9_]*$")
SECRET_LIKE = re.compile(
    r"(?:cog_[A-Za-z0-9_-]{16,}|apk_(?:user_)?[A-Za-z0-9_-]{16,}|"
    r"sk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|"
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----)"
)
BMTG_REQUIRED_PROHIBITIONS = {
    "create_retry_or_terminate_implementation_session",
    "export_or_reveal_credentials",
    "mutate_customer_or_team_data",
    "approve_product_acceptance",
    "merge_release_or_deploy",
    "widen_scope_budget_or_expiry",
}
# Backward-compatible public constant used by existing profile fixtures.
REQUIRED_PROHIBITIONS = BMTG_REQUIRED_PROHIBITIONS
TEG_REQUIRED_PROHIBITIONS = {
    "blind_retry_or_replacement",
    "export_or_reveal_credentials",
    "mutate_customer_or_team_data",
    "approve_product_acceptance",
    "merge_release_or_deploy",
    "widen_scope_budget_or_expiry",
}
TEG_REQUIRED_RECEIPTS = {
    "provider_session_identity",
    "credential_aliases",
    "actor_mode",
    "spend_disposition",
    "deterministic_teardown",
}


class GrantError(ValueError):
    """The authorization evidence does not admit this exact topology."""


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


def _object(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise GrantError(f"{name} must be an object")
    return value


def _reject_secret_values(value: Any) -> None:
    if isinstance(value, str):
        if SECRET_LIKE.search(value):
            raise GrantError("authorization evidence contains a secret-like value")
    elif isinstance(value, list):
        for item in value:
            _reject_secret_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            _reject_secret_values(item)


def _validate_budget(budget: dict[str, Any], *, v2: bool) -> tuple[int, int]:
    successful_runs = _positive_integer(budget, "successful_runs_required")
    attempts = _positive_integer(budget, "maximum_attempts")
    timeout_name = "timeout_minutes_per_attempt"
    timeout = _positive_integer(budget, timeout_name)
    if successful_runs != len(REQUIRED_HOSTS):
        raise GrantError("successful_runs_required must cover both required hosts")
    if attempts > MAXIMUM_ATTEMPTS:
        raise GrantError(f"maximum_attempts exceeds {MAXIMUM_ATTEMPTS}")
    if timeout > MAXIMUM_TIMEOUT_MINUTES:
        raise GrantError(f"{timeout_name} exceeds {MAXIMUM_TIMEOUT_MINUTES}")
    if not v2:
        if budget.get("maximum_concurrent_executors") != 1:
            raise GrantError("maximum_concurrent_executors must be 1")
        if budget.get("maximum_external_spend_usd") != 0:
            raise GrantError("maximum_external_spend_usd must be 0 for MCP #736")
    return attempts, timeout


def _validate_bmtg(
    document: dict[str, Any], *, now: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    grant_id = _string(document, "grant_id")
    if BMTG_ID.fullmatch(grant_id) is None:
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
    if now >= expires_at:
        raise GrantError("authorization grant is expired")
    for name in (
        "allowed_actions",
        "credential_classes",
        "resource_scope",
        "required_receipt",
        "prohibited_actions",
    ):
        _strings(document, name)
    missing = BMTG_REQUIRED_PROHIBITIONS - set(document["prohibited_actions"])
    if missing:
        raise GrantError("prohibited_actions missing: " + ", ".join(sorted(missing)))
    attempts, timeout = _validate_budget(_object(document, "run_budget"), v2=False)

    return document, {
        "mode": "bounded-manual-grant",
        "grant_id": grant_id,
        "execution_surface": "existing-human-created",
        "issued_by": document["issued_by"],
        "approval_record_url": document["approval_record_url"],
        "executor": document["executor"],
        "human_operator": document["human_operator"],
        "expires_at": document["expires_at"],
        "maximum_attempts": attempts,
        "timeout_minutes_per_attempt": timeout,
        "maximum_external_spend_usd": 0,
        "provider_session_authority": False,
        "topology_action_authority": True,
        "product_acceptance_authority": False,
    }


def _validate_teg_v2(
    document: dict[str, Any], *, now: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    _reject_secret_values(document)
    grant_id = _string(document, "grant_id")
    if TEG_ID.fullmatch(grant_id) is None:
        raise GrantError("grant_id is invalid")
    if _string(document, "status") != "active":
        raise GrantError("authorization grant is not active")
    if document.get("execution_surface") != "provider-created":
        raise GrantError("execution_surface must be provider-created")
    for name in ("issued_by", "approval_record_url"):
        _string(document, name)

    issued_at = _timestamp(document.get("issued_at"), name="issued_at")
    expires_at = _timestamp(document.get("expires_at"), name="expires_at")
    if expires_at <= issued_at:
        raise GrantError("expires_at must be later than issued_at")
    if now >= expires_at:
        raise GrantError("authorization grant is expired")

    actors = _object(document, "actors")
    for name in (
        "topology_owner",
        "provider_session_executor",
        "credential_custodian",
        "confirmation_actor",
        "evidence_acceptor",
    ):
        _string(actors, name)
    topology = _object(document, "topology")
    exact_topology = {
        "source_issue": SOURCE_ISSUE,
        "profile": PROFILE,
        "runner": RUNNER,
        "privilege_class": "privileged-external",
    }
    for name, expected in exact_topology.items():
        if _string(topology, name) != expected:
            raise GrantError(f"topology.{name} must be {expected}")
    if set(_strings(topology, "required_hosts")) != REQUIRED_HOSTS:
        raise GrantError("topology.required_hosts must be claude and codex")
    revisions = _object(topology, "revisions")
    if set(revisions) != {"mcp", "bot"} or not all(
        isinstance(value, str) and REVISION.fullmatch(value) for value in revisions.values()
    ):
        raise GrantError("topology.revisions must bind exact mcp and bot revisions")

    provider = _object(document, "provider_policy")
    if _string(provider, "provider") != "devin":
        raise GrantError("provider_policy.provider must be devin")
    if _string(provider, "adapter") != "devin-tool-native":
        raise GrantError("provider_policy.adapter must be devin-tool-native")
    if ORGANIZATION_ID.fullmatch(_string(provider, "organization_id")) is None:
        raise GrantError("provider_policy.organization_id is invalid")
    work_key = _string(provider, "canonical_work_key")
    tags = set(_strings(provider, "tags"))
    required_tags = {
        f"topology_work_key:{work_key}",
        f"topology_grant_id:{grant_id}",
    }
    if not required_tags.issubset(tags):
        raise GrantError("provider_policy.tags do not bind work key and grant id")
    if set(_strings(provider, "allowed_actions")) != {"create", "observe", "terminate"}:
        raise GrantError("provider_policy.allowed_actions must be create, observe, terminate")
    if _positive_integer(provider, "maximum_launches") != 1:
        raise GrantError("provider_policy.maximum_launches must be 1")
    if _positive_integer(provider, "maximum_concurrent_sessions") != 1:
        raise GrantError("provider_policy.maximum_concurrent_sessions must be 1")
    if _string(provider, "replacement_policy") != "none":
        raise GrantError("provider_policy.replacement_policy must be none")

    session = _object(document, "session_spec")
    for name in ("title", "prompt", "playbook_id"):
        _string(session, name)
    if set(_strings(session, "repositories")) != {
        "BicameralAI/bicameral-mcp",
        "BicameralAI/bicameral-bot",
    }:
        raise GrantError("session_spec.repositories must bind MCP and bot")
    if session.get("mode") != "normal" or session.get("platform") != "inherit":
        raise GrantError("session_spec must use normal mode and inherit platform")

    credentials = _object(document, "credential_policy")
    if credentials.get("availability_boundary") != "devin-organization":
        raise GrantError("credential_policy must use the Devin organization boundary")
    bindings = credentials.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        raise GrantError("credential_policy.bindings must be a non-empty array")
    aliases: list[str] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            raise GrantError("credential bindings must be objects")
        alias = _string(binding, "class")
        reference = _string(binding, "organization_secret_reference")
        if ORGANIZATION_SECRET_REFERENCE.fullmatch(reference) is None:
            raise GrantError("credential bindings require organization secret references")
        aliases.append(alias)
    if len(aliases) != len(set(aliases)):
        raise GrantError("credential binding classes must be unique")
    if set(aliases) != REQUIRED_CREDENTIAL_ALIASES:
        raise GrantError("credential bindings must cover Anthropic and OpenAI")

    spend = _object(document, "spend_policy")
    if spend.get("mode") != "organization-limits":
        raise GrantError("spend_policy.mode must be organization-limits")
    _string(spend, "policy_reference")
    if spend.get("owner_acknowledged") is not True:
        raise GrantError("organization limits require owner acknowledgement")
    attempts, timeout = _validate_budget(_object(document, "run_budget"), v2=True)

    confirmation = _object(document, "confirmation_policy")
    actor_mode = _string(confirmation, "mode")
    if actor_mode not in {"real-human", "synthetic-proxy", "not-applicable"}:
        raise GrantError("confirmation_policy.mode is invalid")
    if _string(confirmation, "actor") != actors["confirmation_actor"]:
        raise GrantError("confirmation actor does not match actor binding")
    if _string(confirmation, "maximum_evidence_claim") != actor_mode:
        raise GrantError("confirmation evidence claim contradicts actor mode")
    missing_receipts = TEG_REQUIRED_RECEIPTS - set(_strings(document, "required_receipt"))
    if missing_receipts:
        raise GrantError("required_receipt missing: " + ", ".join(sorted(missing_receipts)))
    missing_prohibitions = TEG_REQUIRED_PROHIBITIONS - set(_strings(document, "prohibited_actions"))
    if missing_prohibitions:
        raise GrantError("prohibited_actions missing: " + ", ".join(sorted(missing_prohibitions)))

    sunset = _object(document, "sunset")
    invalid_after = _timestamp(
        sunset.get("provider_created_invalid_after"),
        name="sunset.provider_created_invalid_after",
    )
    if invalid_after < expires_at:
        raise GrantError("sunset must not precede grant expiry")
    if sunset.get("controller_activation_invalidates") is not True:
        raise GrantError("controller activation must invalidate provider-created authority")
    if now >= invalid_after:
        raise GrantError("provider-created grant is past its sunset")

    return document, {
        "mode": "topology-execution-grant-v2",
        "grant_id": grant_id,
        "execution_surface": "provider-created",
        "issued_by": document["issued_by"],
        "approval_record_url": document["approval_record_url"],
        "provider_session_executor": actors["provider_session_executor"],
        "evidence_acceptor": actors["evidence_acceptor"],
        "expires_at": document["expires_at"],
        "canonical_work_key": work_key,
        "provider": "devin",
        "execution_path": "devin-tool-native",
        "credential_aliases": sorted(aliases),
        "actor_mode": actor_mode,
        "spend_disposition": {
            "mode": "organization-limits",
            "owner_acknowledged": True,
        },
        "component_revisions": dict(revisions),
        "maximum_attempts": attempts,
        "timeout_minutes_per_attempt": timeout,
        "provider_session_authority": True,
        "topology_action_authority": True,
        "product_acceptance_authority": False,
        "dispatch_receipt": False,
    }


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
    effective_now = (now or datetime.now(UTC)).astimezone(UTC)
    if document.get("version") == 1:
        return _validate_bmtg(document, now=effective_now)
    if document.get("version") == 2:
        return _validate_teg_v2(document, now=effective_now)
    raise GrantError("authorization grant version must be 1 or 2")


def load_and_validate_launch_receipt(
    path: Path,
    *,
    grant: dict[str, Any],
) -> dict[str, Any]:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GrantError(f"cannot read provider launch receipt {path}: {exc}") from exc
    if not isinstance(receipt, dict):
        raise GrantError("provider launch receipt must be an object")
    _reject_secret_values(receipt)
    provider = grant["provider_policy"]
    topology = grant["topology"]
    confirmation = grant["confirmation_policy"]
    exact = {
        "version": 1,
        "grant_id": grant["grant_id"],
        "canonical_work_key": provider["canonical_work_key"],
        "launch_ordinal": 1,
        "launch_ordinal_consumed": True,
        "provider": "devin",
        "execution_path": "devin-tool-native",
        "outcome": "created",
        "actor_mode": confirmation["mode"],
        "maximum_evidence_claim": confirmation["maximum_evidence_claim"],
        "sanitized": True,
    }
    for name, expected in exact.items():
        if receipt.get(name) != expected:
            raise GrantError(f"provider launch receipt {name} must be {expected}")
    for name in (
        "provider_session_id",
        "provider_session_url",
        "deterministic_teardown",
    ):
        _string(receipt, name)
    if set(_strings(receipt, "provider_tags")) != set(provider["tags"]):
        raise GrantError("provider launch receipt tags do not match the grant")
    aliases = sorted(binding["class"] for binding in grant["credential_policy"]["bindings"])
    if sorted(_strings(receipt, "credential_aliases")) != aliases:
        raise GrantError("provider launch receipt credential aliases do not match the grant")
    if receipt.get("component_revisions") != topology["revisions"]:
        raise GrantError("provider launch receipt revisions do not match the grant")
    if receipt.get("spend_disposition") != {
        "mode": "organization-limits",
        "owner_acknowledged": True,
    }:
        raise GrantError("provider launch receipt spend disposition does not match the grant")
    retry = _string(receipt, "retry_or_replacement")
    if not retry.startswith("none"):
        raise GrantError("provider launch receipt must record no retry or replacement")
    return {
        "mode": "topology-execution-grant-v2",
        "grant_id": grant["grant_id"],
        "canonical_work_key": provider["canonical_work_key"],
        "provider": "devin",
        "execution_path": "devin-tool-native",
        "provider_session_id": receipt["provider_session_id"],
        "provider_session_url": receipt["provider_session_url"],
        "launch_ordinal": 1,
        "launch_ordinal_consumed": True,
        "credential_aliases": aliases,
        "actor_mode": confirmation["mode"],
        "spend_disposition": receipt["spend_disposition"],
        "deterministic_teardown": receipt["deterministic_teardown"],
        "dispatch_receipt": False,
        "product_acceptance_authority": False,
    }


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
