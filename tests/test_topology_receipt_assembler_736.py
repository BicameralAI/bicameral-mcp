"""Regression tests for assembling mcp#736 terminal receipts from source evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts import assemble_mcp_alpha_host_promotion_receipt as assembler
from scripts import run_mcp_alpha_host_promotion_topology as topology
from scripts import validate_mcp_alpha_host_promotion_grant as grant_contract


def _write_json(path: Path, value) -> Path:
    path.write_text(json.dumps(value))
    return path


def _host_run() -> dict:
    return {
        "host_version": "production-host 1.0",
        "documented_mechanism": "SessionStart command hook",
        "clean_host_configuration": {
            "status": "passed",
            "host_home": "/tmp/clean-host-home",
            "config_root": "/tmp/clean-host-home/config",
        },
        "bounded_context_sanitization": {
            "status": "passed",
            "raw_transcript_collected": False,
            "secrets_collected": False,
        },
        "preflight_invocations": 1,
        "candidate_rendered": True,
        "confirmation_required_rendered": True,
        "explicit_human_confirmation": True,
        "agent_or_hook_self_confirm_possible": False,
        "challenge_resubmitted": True,
        "daemon_materialized_decision": True,
        "ledger_visible_after_restart": True,
        "factory_runtime_dependency_absent": True,
    }


def _lifecycle_files(tmp_path: Path, host: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for step, state in {
        "install": "enabled",
        "update": "enabled",
        "disable": "disabled",
        "uninstall": "not_installed",
    }.items():
        path = _write_json(
            tmp_path / f"{host}-{step}.json",
            {"host": host, "action": step, "ok": True, "state": state},
        )
        files[step] = path.name
    status = _write_json(
        tmp_path / f"{host}-status.json",
        [
            {
                "host": host,
                "state": "enabled",
                "capability_supported": True,
                "consent_granted": True,
                "hook_present": True,
            }
        ],
    )
    files["status"] = status.name
    return files


def _host_evidence(tmp_path: Path, host: str) -> Path:
    return _write_json(
        tmp_path / f"{host}-evidence.json",
        {
            "profile": topology.PROFILE,
            "evidence_level": topology.TERMINAL_EVIDENCE_LEVEL,
            "capture_kind": assembler.REAL_HOST_CAPTURE,
            "host": host,
            "host_run": _host_run(),
            "negative_path_receipts": {path: "passed" for path in topology.REQUIRED_NEGATIVE_PATHS},
            "lifecycle_receipt_files": _lifecycle_files(tmp_path, host),
        },
    )


def _shared_evidence(tmp_path: Path) -> Path:
    return _write_json(
        tmp_path / "shared-evidence.json",
        {
            "profile": topology.PROFILE,
            "evidence_level": topology.TERMINAL_EVIDENCE_LEVEL,
            "capture_kind": assembler.REAL_TOPOLOGY_CAPTURE,
            "production_process_health": {
                "mcp": {"status": "passed", "identity": "mcp-stdio:123"},
                "daemon": {"status": "passed", "identity": "daemon:456"},
            },
            "disposable_event_store_and_workspace_isolation": {"status": "passed"},
            "candidate_challenge_result_and_ledger_correlation": {"status": "passed"},
            "restart_replay": {"status": "passed"},
            "deterministic_teardown": {"status": "passed"},
            "sanitization": {"status": "passed"},
        },
    )


def _authorization_grant(tmp_path: Path) -> Path:
    return _write_json(
        tmp_path / "bounded-manual-grant.json",
        {
            "version": 1,
            "grant_id": "bmtg_mcp736_test_0001",
            "status": "active",
            "issued_by": "@owner",
            "issued_at": "2026-07-21T00:00:00Z",
            "expires_at": "2099-07-29T00:00:00Z",
            "approval_record_url": "https://github.com/BicameralAI/bicameral-factory/issues/298",
            "execution_surface": "existing-human-created",
            "executor": "devin",
            "human_operator": "@operator",
            "human_presence_required": True,
            "source_issue": grant_contract.SOURCE_ISSUE,
            "profile": grant_contract.PROFILE,
            "runner": grant_contract.RUNNER,
            "privilege_class": "privileged-external",
            "allowed_actions": ["execute_declared_runner"],
            "credential_classes": ["existing_operator_authenticated_host_session"],
            "resource_scope": ["clean_temporary_host_homes"],
            "run_budget": {
                "successful_runs_required": 2,
                "maximum_attempts": 4,
                "timeout_minutes_per_attempt": 45,
                "maximum_concurrent_executors": 1,
                "maximum_external_spend_usd": 0,
            },
            "required_receipt": ["deterministic_teardown"],
            "prohibited_actions": sorted(grant_contract.REQUIRED_PROHIBITIONS),
        },
    )


def _assemble(tmp_path: Path, monkeypatch, **overrides) -> dict:
    monkeypatch.setattr(topology, "git_commit", lambda _path: "a" * 40)
    artifact = tmp_path / "bicameral_mcp.whl"
    artifact.write_bytes(b"customer package")
    contract = tmp_path / "topology-contract.json"
    contract.write_bytes(b"contract")
    values: Any = {
        "mcp_root": tmp_path,
        "bot_root": tmp_path,
        "artifacts": {"mcp_wheel": artifact},
        "contracts": {"topology_contract": contract},
    }
    if "authorization_grant_path" not in overrides:
        values["authorization_grant_path"] = _authorization_grant(tmp_path)
    if "host_evidence_paths" not in overrides:
        values["host_evidence_paths"] = {
            host: _host_evidence(tmp_path, host) for host in topology.REQUIRED_HOSTS
        }
    if "shared_evidence_path" not in overrides:
        values["shared_evidence_path"] = _shared_evidence(tmp_path)
    values.update(overrides)
    return assembler.assemble_receipt(**values)


def test_assembler_derives_objective_metadata_and_passes_complete_sources(
    tmp_path: Path, monkeypatch
):
    receipt = _assemble(tmp_path, monkeypatch)

    assert receipt["outcome"] == "passed"
    assert receipt["topology_authorization"]["grant_id"] == "bmtg_mcp736_test_0001"
    assert receipt["topology_authorization"]["provider_session_authority"] is False
    assert receipt["evidence_level"] == topology.TERMINAL_EVIDENCE_LEVEL
    assert len(receipt["product_artifact_and_contract_digests"]["mcp_wheel"]) == 64
    assert receipt["host_runs"]["claude"]["consented_adapter_lifecycle_receipts"] == {
        step: "passed" for step in topology.REQUIRED_ADAPTER_LIFECYCLE_STEPS
    }
    assert receipt["evidence_sources"]["hosts"]["claude"]["lifecycle"]["update"]


def test_july_17_style_lifecycle_without_update_cannot_pass(tmp_path: Path, monkeypatch):
    claude = _host_evidence(tmp_path, "claude")
    evidence = json.loads(claude.read_text())
    del evidence["lifecycle_receipt_files"]["update"]
    claude.write_text(json.dumps(evidence))

    receipt = _assemble(
        tmp_path,
        monkeypatch,
        host_evidence_paths={"claude": claude, "codex": _host_evidence(tmp_path, "codex")},
    )

    assert receipt["outcome"] == "product_failure"
    assert (
        receipt["host_runs"]["claude"]["consented_adapter_lifecycle_receipts"]["update"]
        == "missing"
    )
    assert (
        "claude: consented_adapter_lifecycle_receipts.update must be passed" in receipt["failures"]
    )


def test_supporting_or_synthetic_host_capture_cannot_be_terminal(tmp_path: Path, monkeypatch):
    claude = _host_evidence(tmp_path, "claude")
    evidence = json.loads(claude.read_text())
    evidence["capture_kind"] = "direct_prework_invocation"
    claude.write_text(json.dumps(evidence))

    receipt = _assemble(
        tmp_path,
        monkeypatch,
        host_evidence_paths={"claude": claude, "codex": _host_evidence(tmp_path, "codex")},
    )

    assert receipt["evidence_level"] == "supporting_evidence_only"
    assert receipt["outcome"] == "product_failure"
    assert f"evidence_level must be {topology.TERMINAL_EVIDENCE_LEVEL}" in receipt["failures"]


def test_host_label_must_match_captured_host(tmp_path: Path, monkeypatch):
    claude = _host_evidence(tmp_path, "claude")

    with pytest.raises(assembler.AssemblyError, match="codex evidence must declare host=codex"):
        _assemble(tmp_path, monkeypatch, host_evidence_paths={"codex": claude})


def test_missing_release_artifact_fails_package_boundary(tmp_path: Path, monkeypatch):
    receipt = _assemble(tmp_path, monkeypatch, artifacts={})

    assert receipt["outcome"] == "product_failure"
    assert receipt["release_package_factory_artifact_absence"] == {
        "status": "failed",
        "findings": [{"artifact": "", "finding": "no_release_artifact_supplied"}],
    }


def test_missing_contract_file_is_an_assembly_error(tmp_path: Path, monkeypatch):
    with pytest.raises(assembler.AssemblyError, match="digest input is not a file"):
        _assemble(tmp_path, monkeypatch, contracts={"topology_contract": tmp_path / "missing"})


def test_artifact_and_contract_labels_cannot_collide(tmp_path: Path, monkeypatch):
    artifact = tmp_path / "artifact.whl"
    artifact.write_bytes(b"package")

    with pytest.raises(assembler.AssemblyError, match="duplicate artifact/contract label"):
        _assemble(
            tmp_path,
            monkeypatch,
            artifacts={"release": artifact},
            contracts={"release": artifact},
        )


def test_expired_authorization_grant_fails_before_receipt_assembly(tmp_path: Path, monkeypatch):
    grant = _authorization_grant(tmp_path)
    value = json.loads(grant.read_text())
    value["expires_at"] = "2026-07-21T00:00:01Z"
    grant.write_text(json.dumps(value))

    with pytest.raises(
        assembler.AssemblyError,
        match="authorization denied: authorization grant is expired",
    ):
        _assemble(
            tmp_path,
            monkeypatch,
            authorization_grant_path=grant,
            now=datetime(2026, 7, 21, 0, 0, 2, tzinfo=UTC),
        )


def test_grant_cannot_authorize_provider_session_actions(tmp_path: Path, monkeypatch):
    grant = _authorization_grant(tmp_path)
    value = json.loads(grant.read_text())
    value["prohibited_actions"].remove("create_retry_or_terminate_implementation_session")
    grant.write_text(json.dumps(value))

    with pytest.raises(assembler.AssemblyError, match="prohibited_actions missing"):
        _assemble(tmp_path, monkeypatch, authorization_grant_path=grant)
