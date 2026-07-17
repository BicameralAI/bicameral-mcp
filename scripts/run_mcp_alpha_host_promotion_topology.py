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
    "challenge_resubmitted",
    "daemon_materialized_decision",
    "ledger_visible_after_restart",
    "factory_runtime_dependency_absent",
)

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
                sanitized[key] = "[REDACTED]"
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
        if run.get("preflight_invocations") != 1:
            failures.append(f"{host}: preflight_invocations must equal 1")
        for bool_field in (
            "clean_host_configuration",
            "bounded_context_sanitization",
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

    if contains_secret(receipt):
        failures.append("receipt contains unredacted secret-like keys or values")

    release_absence = receipt.get("release_package_factory_artifact_absence", {})
    if isinstance(release_absence, dict) and release_absence.get("findings"):
        failures.append("release package contains Factory runtime artifacts")

    if receipt.get("deterministic_teardown", {}).get("status") != "passed":
        failures.append("deterministic_teardown.status must be passed")

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
            "Run this profile only after #735 is merged to dev, bot #828 is accepted "
            "or explicitly waived, and a real Claude Code + Codex host receipt is available."
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
