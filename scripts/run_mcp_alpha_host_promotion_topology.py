#!/usr/bin/env python3
"""Validate #736 terminal topology receipts for MCP host promotion.

This runner is intentionally fail-closed. It can validate a sanitized
real-process receipt, but it does not fabricate terminal User Journey evidence
from component tests, fixtures, or an incomplete local environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

PROFILE = "mcp-alpha-host-promotion-v1"
REQUIRED_HOSTS = ("claude", "codex")
TERMINAL_EVIDENCE_LEVEL = "real_process_integration"

REQUIRED_RECEIPT_FIELDS = (
    "component_commits",
    "product_artifact_and_contract_digests",
    "host_runs",
    "production_process_health",
    "disposable_event_store_and_workspace_isolation",
    "candidate_challenge_result_and_ledger_correlation",
    "restart_replay",
    "negative_path_receipts",
    "release_package_factory_artifact_absence",
    "deterministic_teardown",
    "sanitization",
)

HOST_REQUIRED_FIELDS = (
    "host_version",
    "documented_mechanism",
    "clean_host_configuration",
    "consented_adapter_lifecycle_receipts",
    "bounded_context_sanitization",
    "preflight_invocations",
    "candidate_rendered",
    "confirmation_required_rendered",
    "explicit_human_confirmation",
    "agent_or_hook_self_confirm_possible",
    "challenge_resubmitted",
    "daemon_materialized_decision",
    "ledger_visible_after_restart",
    "factory_runtime_dependency_absent",
)

REQUIRED_ADAPTER_LIFECYCLE_STEPS = (
    "install",
    "status",
    "disable",
    "update",
    "uninstall",
)

REQUIRED_NEGATIVE_PATHS = (
    "automatic_hook_unavailable_manual_fallback",
    "daemon_unavailable",
    "protocol_envelope_mismatch",
    "expired_challenge",
    "daemon_restart_invalidates_pending_challenge",
    "stale_packet",
    "actor_workspace_mismatch",
    "challenge_replay",
    "lost_response_after_commit_idempotent",
    "cancellation_no_canonical_transition",
    "no_result_bounded_to_scope",
)

PASS_VALUES = ("ok", "pass", "passed")

COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")

SECRET_KEY_RE = re.compile(
    r"(secret|token|password|credential|api[_-]?key|challenge[_-]?(value|secret|token))",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,})")

FORBIDDEN_PACKAGE_PATTERNS = (
    ".bicameral/factory",
    ".bicameral/factory-attestation",
    ".bicameral/factory-context",
    ".agents/skills",
    ".codex/skills",
    "bicameral-factory",
    "worker-receipt",
    "roadmap",
)


def git_commit(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, inner in value.items():
            if SECRET_KEY_RE.search(str(key)):
                sanitized[key] = (
                    inner
                    if inner is False or inner is None or inner == "[REDACTED]"
                    else "[REDACTED]"
                )
            else:
                sanitized[key] = sanitize(inner)
        return sanitized
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE_RE.sub("[REDACTED]", value)
    return value


def contains_secret(value: Any) -> bool:
    return sanitize(value) != value


def _is_passed(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value in PASS_VALUES)


def _require_passed_status(
    value: Any,
    field: str,
    failures: list[str],
) -> None:
    if not isinstance(value, dict) or not _is_passed(value.get("status")):
        failures.append(f"{field}.status must be passed")


def _artifact_members(path: Path) -> list[str]:
    if path.is_dir():
        return [str(child.relative_to(path)) for child in path.rglob("*")]
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            return archive.getnames()
    return [path.name]


def scan_release_artifacts(paths: list[Path]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in paths:
        if not path.exists():
            findings.append({"artifact": str(path), "finding": "missing_artifact"})
            continue
        for member in _artifact_members(path):
            normalized = member.replace("\\", "/").lower()
            for pattern in FORBIDDEN_PACKAGE_PATTERNS:
                if pattern in normalized:
                    findings.append(
                        {
                            "artifact": str(path),
                            "member": member,
                            "finding": "forbidden_factory_runtime_artifact",
                            "pattern": pattern,
                        }
                    )
    return findings


def validate_receipt(receipt: dict[str, Any]) -> tuple[str, list[str]]:
    failures: list[str] = []
    if receipt.get("profile") != PROFILE:
        failures.append(f"profile must be {PROFILE}")
    if receipt.get("evidence_level") != TERMINAL_EVIDENCE_LEVEL:
        failures.append(f"evidence_level must be {TERMINAL_EVIDENCE_LEVEL}")

    for field in REQUIRED_RECEIPT_FIELDS:
        if field not in receipt:
            failures.append(f"missing required receipt field: {field}")

    component_commits = receipt.get("component_commits")
    if not isinstance(component_commits, dict):
        failures.append("component_commits must be an object")
    else:
        for component in ("mcp", "bot"):
            commit = component_commits.get(component)
            if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None:
                failures.append(f"component_commits.{component} must be a full git commit")

    digests = receipt.get("product_artifact_and_contract_digests")
    if not isinstance(digests, dict) or not digests:
        failures.append("product_artifact_and_contract_digests must be a non-empty object")
    else:
        for artifact, digest in digests.items():
            if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
                failures.append(
                    f"product_artifact_and_contract_digests.{artifact} must be a sha256 digest"
                )

    host_runs = receipt.get("host_runs", {})
    if not isinstance(host_runs, dict):
        failures.append("host_runs must be an object keyed by host")
        host_runs = {}
    for host in REQUIRED_HOSTS:
        run = host_runs.get(host)
        if not isinstance(run, dict):
            failures.append(f"missing host run: {host}")
            continue
        for field in HOST_REQUIRED_FIELDS:
            if field not in run:
                failures.append(f"{host}: missing field {field}")
        for text_field in ("host_version", "documented_mechanism"):
            if not isinstance(run.get(text_field), str) or not run[text_field].strip():
                failures.append(f"{host}: {text_field} must be a non-empty string")
        if run.get("preflight_invocations") != 1:
            failures.append(f"{host}: preflight_invocations must equal 1")
        for bool_field in (
            "candidate_rendered",
            "confirmation_required_rendered",
            "explicit_human_confirmation",
            "challenge_resubmitted",
            "daemon_materialized_decision",
            "ledger_visible_after_restart",
            "factory_runtime_dependency_absent",
        ):
            if run.get(bool_field) is not True:
                failures.append(f"{host}: {bool_field} must be true")
        if run.get("agent_or_hook_self_confirm_possible") is not False:
            failures.append(f"{host}: agent_or_hook_self_confirm_possible must be false")

        lifecycle = run.get("consented_adapter_lifecycle_receipts")
        if not isinstance(lifecycle, dict):
            failures.append(f"{host}: consented_adapter_lifecycle_receipts must be an object")
        else:
            for step in REQUIRED_ADAPTER_LIFECYCLE_STEPS:
                if not _is_passed(lifecycle.get(step)):
                    failures.append(
                        f"{host}: consented_adapter_lifecycle_receipts.{step} must be passed"
                    )

        clean_config = run.get("clean_host_configuration")
        if not isinstance(clean_config, dict):
            failures.append(f"{host}: clean_host_configuration must be an object")
        else:
            if not _is_passed(clean_config.get("status")):
                failures.append(f"{host}: clean_host_configuration.status must be passed")
            for path_field in ("host_home", "config_root"):
                path = clean_config.get(path_field)
                if not isinstance(path, str) or not path.strip():
                    failures.append(
                        f"{host}: clean_host_configuration.{path_field} must be a non-empty path"
                    )

        bounded_context = run.get("bounded_context_sanitization")
        if not isinstance(bounded_context, dict):
            failures.append(f"{host}: bounded_context_sanitization must be an object")
        else:
            if not _is_passed(bounded_context.get("status")):
                failures.append(f"{host}: bounded_context_sanitization.status must be passed")
            for collection_field in ("raw_transcript_collected", "secrets_collected"):
                if bounded_context.get(collection_field) is not False:
                    failures.append(
                        f"{host}: bounded_context_sanitization.{collection_field} must be false"
                    )

    process_health = receipt.get("production_process_health")
    if not isinstance(process_health, dict):
        failures.append("production_process_health must be an object")
    else:
        for process in ("mcp", "daemon"):
            process_receipt = process_health.get(process)
            if not isinstance(process_receipt, dict):
                failures.append(f"production_process_health.{process} must be an object")
                continue
            if not _is_passed(process_receipt.get("status")):
                failures.append(f"production_process_health.{process}.status must be passed")
            identity = process_receipt.get("identity")
            if not isinstance(identity, str) or not identity.strip():
                failures.append(
                    f"production_process_health.{process}.identity must be a non-empty string"
                )

    for status_field in (
        "disposable_event_store_and_workspace_isolation",
        "candidate_challenge_result_and_ledger_correlation",
        "restart_replay",
        "sanitization",
    ):
        _require_passed_status(receipt.get(status_field), status_field, failures)

    negative_paths = receipt.get("negative_path_receipts")
    if not isinstance(negative_paths, dict):
        failures.append("negative_path_receipts must be an object keyed by host")
    else:
        for host in REQUIRED_HOSTS:
            host_paths = negative_paths.get(host)
            if not isinstance(host_paths, dict):
                failures.append(f"negative_path_receipts.{host} must be an object")
                continue
            for path in REQUIRED_NEGATIVE_PATHS:
                if not _is_passed(host_paths.get(path)):
                    failures.append(f"negative_path_receipts.{host}.{path} must be passed")

    if contains_secret(receipt):
        failures.append("receipt contains unredacted secret-like keys or values")

    release_absence = receipt.get("release_package_factory_artifact_absence", {})
    _require_passed_status(release_absence, "release_package_factory_artifact_absence", failures)
    if isinstance(release_absence, dict) and release_absence.get("findings"):
        failures.append("release package contains Factory runtime artifacts")

    _require_passed_status(
        receipt.get("deterministic_teardown"), "deterministic_teardown", failures
    )

    if failures:
        return "product_failure", failures
    return "passed", []


def incomplete_receipt(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "profile": PROFILE,
        "outcome": "contract_or_product_decision",
        "reason": "no_real_process_terminal_receipt_supplied",
        "component_commits": {
            "mcp": git_commit(Path(args.mcp_root).resolve()),
            "bot": git_commit(Path(args.bot_root).resolve()) if args.bot_root else None,
        },
        "required_hosts": list(REQUIRED_HOSTS),
        "runner": "scripts/run_mcp_alpha_host_promotion_topology.py",
        "next_safe_action": (
            "Obtain a valid scoped DispatchGrant through a deployed Factory Admission "
            "Controller, then run separate real Claude Code and Codex production-host "
            "sessions and supply their sanitized terminal receipts."
        ),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-root", default=".", help="bicameral-mcp checkout path")
    parser.add_argument("--bot-root", help="bicameral-bot checkout path")
    parser.add_argument(
        "--receipt-input",
        type=Path,
        help="Sanitized real-process receipt JSON to validate.",
    )
    parser.add_argument(
        "--release-artifact",
        action="append",
        default=[],
        type=Path,
        help="Built package/archive/directory to scan for Factory runtime artifacts.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable receipt JSON.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.receipt_input is None:
        receipt = incomplete_receipt(args)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 2

    receipt = json.loads(args.receipt_input.read_text())
    receipt = sanitize(receipt)
    artifact_findings = scan_release_artifacts(args.release_artifact)
    if artifact_findings:
        receipt["release_package_factory_artifact_absence"] = {
            "status": "failed",
            "findings": artifact_findings,
        }
    outcome, failures = validate_receipt(receipt)
    receipt["outcome"] = outcome
    if failures:
        receipt["failures"] = sorted(failures)
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(f"outcome: {outcome}")
        for failure in failures:
            print(f"- {failure}")
    return 0 if outcome == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
