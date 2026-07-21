"""Contract tests for the mcp#736 topology receipt runner."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

from scripts import run_mcp_alpha_host_promotion_topology as runner


def _valid_host_run() -> dict:
    return {
        "host_version": "test-host 1.0",
        "documented_mechanism": "SessionStart command hook",
        "clean_host_configuration": {
            "status": "passed",
            "host_home": "/tmp/host-home",
            "config_root": "/tmp/host-home/config",
        },
        "consented_adapter_lifecycle_receipts": {
            "install": "ok",
            "status": "ok",
            "disable": "ok",
            "update": "ok",
            "uninstall": "ok",
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


def _valid_receipt() -> dict:
    return {
        "profile": runner.PROFILE,
        "evidence_level": runner.TERMINAL_EVIDENCE_LEVEL,
        "component_commits": {"mcp": "a" * 40, "bot": "b" * 40},
        "product_artifact_and_contract_digests": {
            "mcp_wheel": "c" * 64,
            "bot_binary": "d" * 64,
            "topology_contract": "e" * 64,
        },
        "host_runs": {
            "claude": _valid_host_run(),
            "codex": _valid_host_run(),
        },
        "production_process_health": {
            "mcp": {"status": "passed", "identity": "mcp-stdio:123"},
            "daemon": {"status": "passed", "identity": "daemon:456"},
        },
        "disposable_event_store_and_workspace_isolation": {"status": "passed"},
        "candidate_challenge_result_and_ledger_correlation": {"status": "passed"},
        "restart_replay": {"status": "passed"},
        "negative_path_receipts": {
            host: {path: "passed" for path in runner.REQUIRED_NEGATIVE_PATHS}
            for host in runner.REQUIRED_HOSTS
        },
        "release_package_factory_artifact_absence": {"status": "passed", "findings": []},
        "deterministic_teardown": {"status": "passed"},
        "sanitization": {"status": "passed"},
    }


def test_valid_receipt_requires_independent_claude_and_codex_runs():
    receipt = _valid_receipt()
    del receipt["host_runs"]["codex"]

    outcome, failures = runner.validate_receipt(receipt)

    assert outcome == "product_failure"
    assert "missing host run: codex" in failures


def test_valid_receipt_passes_when_both_hosts_are_complete():
    outcome, failures = runner.validate_receipt(_valid_receipt())

    assert outcome == "passed"
    assert failures == []


def test_receipt_requires_every_negative_path_for_each_host():
    receipt = _valid_receipt()
    del receipt["negative_path_receipts"]["codex"]["challenge_replay"]

    outcome, failures = runner.validate_receipt(receipt)

    assert outcome == "product_failure"
    assert "negative_path_receipts.codex.challenge_replay must be passed" in failures


def test_receipt_rejects_empty_adapter_lifecycle_evidence():
    receipt = _valid_receipt()
    receipt["host_runs"]["claude"]["consented_adapter_lifecycle_receipts"] = {}

    outcome, failures = runner.validate_receipt(receipt)

    assert outcome == "product_failure"
    assert "claude: consented_adapter_lifecycle_receipts.install must be passed" in failures


def test_receipt_requires_production_process_health_to_pass():
    receipt = _valid_receipt()
    receipt["production_process_health"]["daemon"]["status"] = "failed"

    outcome, failures = runner.validate_receipt(receipt)

    assert outcome == "product_failure"
    assert "production_process_health.daemon.status must be passed" in failures


def test_receipt_requires_exact_component_commits_and_digests():
    receipt = _valid_receipt()
    receipt["component_commits"]["bot"] = "dev"
    receipt["product_artifact_and_contract_digests"]["mcp_wheel"] = "not-a-digest"

    outcome, failures = runner.validate_receipt(receipt)

    assert outcome == "product_failure"
    assert "component_commits.bot must be a full git commit" in failures
    assert "product_artifact_and_contract_digests.mcp_wheel must be a sha256 digest" in failures


def test_receipt_requires_clean_host_paths_and_bounded_collection_assertions():
    receipt = _valid_receipt()
    receipt["host_runs"]["claude"]["clean_host_configuration"]["host_home"] = ""
    receipt["host_runs"]["claude"]["bounded_context_sanitization"]["raw_transcript_collected"] = (
        True
    )

    outcome, failures = runner.validate_receipt(receipt)

    assert outcome == "product_failure"
    assert "claude: clean_host_configuration.host_home must be a non-empty path" in failures
    assert "claude: bounded_context_sanitization.raw_transcript_collected must be false" in failures


def test_receipt_requires_release_package_scan_to_pass():
    receipt = _valid_receipt()
    receipt["release_package_factory_artifact_absence"]["status"] = "failed"

    outcome, failures = runner.validate_receipt(receipt)

    assert outcome == "product_failure"
    assert "release_package_factory_artifact_absence.status must be passed" in failures


def test_receipt_rejects_agent_or_hook_self_confirmation():
    receipt = _valid_receipt()
    receipt["host_runs"]["codex"]["agent_or_hook_self_confirm_possible"] = True

    outcome, failures = runner.validate_receipt(receipt)

    assert outcome == "product_failure"
    assert "codex: agent_or_hook_self_confirm_possible must be false" in failures


def test_receipt_with_challenge_secret_is_rejected():
    receipt = _valid_receipt()
    receipt["candidate_challenge_result_and_ledger_correlation"]["challenge_secret"] = (
        "super-secret"
    )

    outcome, failures = runner.validate_receipt(receipt)

    assert outcome == "product_failure"
    assert "receipt contains unredacted secret-like keys or values" in failures


def test_sanitize_redacts_secret_keys_and_token_values():
    sanitized = runner.sanitize(
        {
            "challenge_token": "plain-token",
            "log": "received sk-abcdefghijklmnopqrstuvwxyz",
        }
    )

    assert sanitized["challenge_token"] == "[REDACTED]"
    assert sanitized["log"] == "received [REDACTED]"


def test_release_artifact_scan_flags_factory_runtime_paths(tmp_path: Path):
    archive = tmp_path / "artifact.tar.gz"
    leaked = tmp_path / ".bicameral" / "factory-attestations" / "run.json"
    leaked.parent.mkdir(parents=True)
    leaked.write_text("{}")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(leaked, arcname=".bicameral/factory-attestations/run.json")

    findings = runner.scan_release_artifacts([archive])

    assert findings
    assert findings[0]["finding"] == "forbidden_factory_runtime_artifact"


def test_runner_without_terminal_receipt_fails_closed(capsys):
    code = runner.main(["--mcp-root", "."])
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert output["outcome"] == "contract_or_product_decision"
    assert output["profile"] == runner.PROFILE
    assert "DispatchGrant" in output["next_safe_action"]
    assert "#735" not in output["next_safe_action"]
