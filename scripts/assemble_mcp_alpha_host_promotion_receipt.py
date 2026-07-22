#!/usr/bin/env python3
"""Assemble #736 terminal evidence without fabricating real-host proof."""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

topology = importlib.import_module(
    "scripts.run_mcp_alpha_host_promotion_topology"
    if __package__
    else "run_mcp_alpha_host_promotion_topology"
)
grant_contract = importlib.import_module(
    "scripts.validate_mcp_alpha_host_promotion_grant"
    if __package__
    else "validate_mcp_alpha_host_promotion_grant"
)

REAL_HOST_CAPTURE = "production_host_session"
REAL_TOPOLOGY_CAPTURE = "real_process_topology"

SHARED_EVIDENCE_FIELDS = (
    "production_process_health",
    "disposable_event_store_and_workspace_isolation",
    "candidate_challenge_result_and_ledger_correlation",
    "restart_replay",
    "deterministic_teardown",
    "sanitization",
)

LABEL_RE = re.compile(r"[a-z][a-z0-9_]*")


class AssemblyError(ValueError):
    """The supplied evidence cannot be assembled deterministically."""


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AssemblyError(f"cannot read JSON evidence {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AssemblyError(f"evidence must be a JSON object: {path}")
    return value


def parse_binding(value: str, kind: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or LABEL_RE.fullmatch(label) is None or not raw_path:
        raise argparse.ArgumentTypeError(f"{kind} must use label=/path/to/file")
    return label, Path(raw_path)


def _bindings(values: list[tuple[str, Path]], kind: str) -> dict[str, Path]:
    bound: dict[str, Path] = {}
    for label, path in values:
        if label in bound:
            raise AssemblyError(f"duplicate {kind} label: {label}")
        bound[label] = path.resolve()
    return bound


def _passed_lifecycle_receipt(host: str, step: str, value: Any) -> bool:
    if step == "status":
        if not isinstance(value, list):
            return False
        return any(
            isinstance(item, dict)
            and item.get("host") == host
            and item.get("state") == "enabled"
            and item.get("capability_supported") is True
            and item.get("consent_granted") is True
            and item.get("hook_present") is True
            for item in value
        )

    expected_state = {
        "install": "enabled",
        "update": "enabled",
        "disable": "disabled",
        "uninstall": "not_installed",
    }[step]
    return (
        isinstance(value, dict)
        and value.get("host") == host
        and value.get("action") == step
        and value.get("ok") is True
        and value.get("state") == expected_state
    )


def derive_lifecycle_receipts(
    host: str,
    host_evidence_path: Path,
    evidence: dict[str, Any],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    configured = evidence.get("lifecycle_receipt_files")
    if not isinstance(configured, dict):
        configured = {}

    statuses: dict[str, str] = {}
    sources: dict[str, dict[str, str]] = {}
    for step in topology.REQUIRED_ADAPTER_LIFECYCLE_STEPS:
        raw_path = configured.get(step)
        if not isinstance(raw_path, str) or not raw_path:
            statuses[step] = "missing"
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = host_evidence_path.parent / path
        path = path.resolve()
        try:
            value = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            statuses[step] = "invalid"
            continue
        statuses[step] = "passed" if _passed_lifecycle_receipt(host, step, value) else "failed"
        sources[step] = {"filename": path.name, "sha256": topology.sha256_file(path)}
    return statuses, sources


def _source(path: Path) -> dict[str, str]:
    return {"filename": path.name, "sha256": topology.sha256_file(path)}


def assemble_receipt(
    *,
    mcp_root: Path,
    bot_root: Path,
    artifacts: dict[str, Path],
    contracts: dict[str, Path],
    authorization_grant_path: Path,
    host_evidence_paths: dict[str, Path],
    shared_evidence_path: Path | None,
    provider_launch_receipt_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    duplicate_labels = artifacts.keys() & contracts.keys()
    if duplicate_labels:
        raise AssemblyError(f"duplicate artifact/contract label: {sorted(duplicate_labels)[0]}")
    digested_inputs = {**artifacts, **contracts}
    for label, path in digested_inputs.items():
        if not path.is_file():
            raise AssemblyError(f"digest input is not a file: {label}={path}")
    try:
        authorization_document, authorization = grant_contract.load_and_validate_grant(
            authorization_grant_path.resolve(), now=now
        )
        if authorization["mode"] == "topology-execution-grant-v2":
            if provider_launch_receipt_path is None:
                raise grant_contract.GrantError(
                    "provider-created execution requires a Topology Launch Receipt"
                )
            provider_launch_authorization = (
                grant_contract.load_and_validate_launch_receipt(
                    provider_launch_receipt_path.resolve(),
                    grant=authorization_document,
                )
            )
        elif provider_launch_receipt_path is not None:
            raise grant_contract.GrantError(
                "a Bounded Manual Topology Grant cannot carry provider launch authority"
            )
        else:
            provider_launch_authorization = {
                "mode": "not-applicable",
                "execution_surface": "existing-human-created",
                "reason": "provider session was not created under this grant",
            }
    except grant_contract.GrantError as exc:
        raise AssemblyError(f"authorization denied: {exc}") from exc
    component_commits = {
        "mcp": topology.git_commit(mcp_root.resolve()),
        "bot": topology.git_commit(bot_root.resolve()),
    }
    if (
        authorization["mode"] == "topology-execution-grant-v2"
        and authorization["component_revisions"] != component_commits
    ):
        raise AssemblyError(
            "authorization denied: checked-out component revisions do not match the v2 grant"
        )
    receipt: dict[str, Any] = {
        "profile": topology.PROFILE,
        "evidence_level": "supporting_evidence_only",
        "component_commits": component_commits,
        "product_artifact_and_contract_digests": {
            label: topology.sha256_file(path) for label, path in digested_inputs.items()
        },
        "host_runs": {},
        "negative_path_receipts": {},
        "provider_launch_authorization": provider_launch_authorization,
        "topology_action_authorization": authorization,
        "evidence_sources": {
            "digested_inputs": {label: _source(path) for label, path in digested_inputs.items()},
            "hosts": {},
            "authorization_grant": _source(authorization_grant_path.resolve()),
        },
    }
    if provider_launch_receipt_path is not None:
        receipt["evidence_sources"]["provider_launch_receipt"] = _source(
            provider_launch_receipt_path.resolve()
        )

    shared_is_terminal = False
    if shared_evidence_path is not None:
        shared = load_json_object(shared_evidence_path)
        if shared.get("profile") != topology.PROFILE:
            raise AssemblyError(f"shared evidence profile must be {topology.PROFILE}")
        for field in SHARED_EVIDENCE_FIELDS:
            if field in shared:
                receipt[field] = shared[field]
        shared_is_terminal = (
            shared.get("evidence_level") == topology.TERMINAL_EVIDENCE_LEVEL
            and shared.get("capture_kind") == REAL_TOPOLOGY_CAPTURE
        )
        receipt["evidence_sources"]["shared"] = _source(shared_evidence_path)

    terminal_hosts: set[str] = set()
    for host, path in host_evidence_paths.items():
        if host not in topology.REQUIRED_HOSTS:
            raise AssemblyError(f"unsupported host evidence label: {host}")
        evidence = load_json_object(path)
        if evidence.get("profile") != topology.PROFILE:
            raise AssemblyError(f"{host} evidence profile must be {topology.PROFILE}")
        if evidence.get("host") != host:
            raise AssemblyError(f"{host} evidence must declare host={host}")
        host_run = evidence.get("host_run")
        negative_paths = evidence.get("negative_path_receipts")
        if not isinstance(host_run, dict):
            raise AssemblyError(f"{host} evidence host_run must be an object")
        if not isinstance(negative_paths, dict):
            raise AssemblyError(f"{host} evidence negative_path_receipts must be an object")

        lifecycle, lifecycle_sources = derive_lifecycle_receipts(host, path, evidence)
        host_run = dict(host_run)
        host_run["consented_adapter_lifecycle_receipts"] = lifecycle
        receipt["host_runs"][host] = host_run
        receipt["negative_path_receipts"][host] = negative_paths
        receipt["evidence_sources"]["hosts"][host] = {
            "summary": _source(path),
            "lifecycle": lifecycle_sources,
        }
        if (
            evidence.get("evidence_level") == topology.TERMINAL_EVIDENCE_LEVEL
            and evidence.get("capture_kind") == REAL_HOST_CAPTURE
        ):
            terminal_hosts.add(host)

    findings = topology.scan_release_artifacts(list(artifacts.values()))
    if not artifacts:
        findings.append({"artifact": "", "finding": "no_release_artifact_supplied"})
    receipt["release_package_factory_artifact_absence"] = {
        "status": "passed" if not findings else "failed",
        "findings": findings,
    }

    if shared_is_terminal and terminal_hosts == set(topology.REQUIRED_HOSTS):
        receipt["evidence_level"] = topology.TERMINAL_EVIDENCE_LEVEL

    receipt = topology.sanitize(receipt)
    outcome, failures = topology.validate_receipt(receipt)
    if (
        authorization["mode"] == "topology-execution-grant-v2"
        and authorization["actor_mode"] != "real-human"
    ):
        failures.append(
            "topology authorization maximum evidence claim is "
            f"{authorization['actor_mode']}, not real-human"
        )
        outcome = "product_failure"
    receipt["outcome"] = outcome
    if failures:
        receipt["failures"] = sorted(failures)
    return receipt


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-root", type=Path, default=Path("."))
    parser.add_argument("--bot-root", type=Path, required=True)
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        type=lambda value: parse_binding(value, "artifact"),
        help="Release artifact as label=/path; it is hashed and scanned.",
    )
    parser.add_argument(
        "--contract",
        action="append",
        default=[],
        type=lambda value: parse_binding(value, "contract"),
        help="Contract input as label=/path; it is hashed but not package-scanned.",
    )
    parser.add_argument(
        "--host-evidence",
        action="append",
        default=[],
        type=lambda value: parse_binding(value, "host evidence"),
        help="Host capture summary as claude=/path or codex=/path.",
    )
    parser.add_argument("--shared-evidence", type=Path)
    parser.add_argument(
        "--authorization-grant",
        type=Path,
        required=True,
        help="Active BMTG v1 or Topology Execution Grant v2 for MCP #736.",
    )
    parser.add_argument(
        "--provider-launch-receipt",
        type=Path,
        help="Required Topology Launch Receipt when authorization is provider-created v2.",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        receipt = assemble_receipt(
            mcp_root=args.mcp_root,
            bot_root=args.bot_root,
            artifacts=_bindings(args.artifact, "artifact"),
            contracts=_bindings(args.contract, "contract"),
            authorization_grant_path=args.authorization_grant,
            provider_launch_receipt_path=(
                args.provider_launch_receipt.resolve()
                if args.provider_launch_receipt
                else None
            ),
            host_evidence_paths=_bindings(args.host_evidence, "host evidence"),
            shared_evidence_path=args.shared_evidence.resolve() if args.shared_evidence else None,
        )
    except AssemblyError as exc:
        print(f"assembly error: {exc}", file=sys.stderr)
        return 2

    output = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(output)
    else:
        print(output, end="")
    return 0 if receipt["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

