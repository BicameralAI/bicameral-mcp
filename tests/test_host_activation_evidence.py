"""Product-side validation of sanitized authentic-host activation evidence."""

from __future__ import annotations

import json

import pytest

from preflight_adapters.cli import run_adapters_cli
from preflight_adapters.evidence import HostEvidenceError, evaluate_host_activation
from preflight_adapters.state import AdapterState


def _receipt(*, fired: bool) -> dict:
    return {
        "sanitized": True,
        "host_versions": {"codex": "codex-cli 0.144.0"},
        "credential_presence_only": {"OPENAI_API_KEY": "present"},
        "lanes": [
            {
                "classification": "product_primary",
                "provider": "codex",
                "interaction_surface": "pty",
                "clean_home": True,
                "consented_install": True,
                "released_artifact": True,
                "version_probe_ok": True,
                "hook_written": True,
                "host_fired_managed_hook": fired,
                "direct_hook_invocation": False,
                "provenance_method": "proc-ancestry",
                "evidence_ceiling": (
                    "authentic-host/synthetic-operator" if fired else "host-activation-failed"
                ),
                "hook_chain": (
                    [
                        {
                            "pid": 4312,
                            "ppid": 4300,
                            "argv_shape": "bicameral-mcp prework-run --host codex",
                            "source": "proc-ancestry",
                        }
                    ]
                    if fired
                    else []
                ),
            }
        ],
    }


def test_installed_host_that_did_not_fire_is_not_authentic() -> None:
    result = evaluate_host_activation("codex", _receipt(fired=False))

    assert result.state == AdapterState.INSTALLED_BUT_HOST_DID_NOT_FIRE
    assert result.authentic is False
    assert result.exactly_once is False
    assert result.invocation_count == 0


def test_independent_host_subtree_proof_accepts_exactly_one_invocation() -> None:
    result = evaluate_host_activation("codex", _receipt(fired=True))

    assert result.state == AdapterState.AUTHENTIC_HOST_FIRED
    assert result.authentic is True
    assert result.exactly_once is True
    assert result.invocation_count == 1
    assert result.provenance_method == "proc-ancestry"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("raw_transcript", "model output"),
        ("raw_output", "terminal bytes"),
        ("api_token", "secret-value"),
        ("credentials", "session credential"),
        ("challenge_value", "confirmation challenge"),
    ],
)
def test_secret_or_raw_content_is_rejected(field: str, value: str) -> None:
    receipt = _receipt(fired=True)
    receipt[field] = value

    with pytest.raises(HostEvidenceError, match="forbidden evidence field"):
        evaluate_host_activation("codex", receipt)


def test_duplicate_host_hook_processes_do_not_satisfy_exactly_once() -> None:
    receipt = _receipt(fired=True)
    receipt["lanes"][0]["hook_chain"].append(
        {
            "pid": 4313,
            "ppid": 4300,
            "argv_shape": "bicameral-mcp prework-run --host codex",
            "source": "proc-ancestry",
        }
    )

    with pytest.raises(HostEvidenceError, match="exactly one hook process"):
        evaluate_host_activation("codex", receipt)


def test_direct_invocation_never_satisfies_authentic_host_firing() -> None:
    receipt = _receipt(fired=True)
    receipt["lanes"][0]["direct_hook_invocation"] = True

    with pytest.raises(HostEvidenceError, match="direct hook invocation"):
        evaluate_host_activation("codex", receipt)


def test_unrelated_matching_process_outside_primary_lane_does_not_count() -> None:
    receipt = _receipt(fired=True)
    receipt["lanes"].append(
        {
            "classification": "negative_control",
            "provider": "codex",
            "hook_chain": [{"pid": 9999, "argv_shape": "bicameral-mcp prework-run"}],
        }
    )

    result = evaluate_host_activation("codex", receipt)

    assert result.invocation_count == 1
    assert result.authentic is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("clean_home", False),
        ("consented_install", False),
        ("released_artifact", False),
        ("version_probe_ok", False),
        ("hook_written", False),
        ("interaction_surface", "pipe"),
    ],
)
def test_authentic_claim_requires_real_host_topology_gates(field: str, value: object) -> None:
    receipt = _receipt(fired=True)
    receipt["lanes"][0][field] = value

    with pytest.raises(HostEvidenceError, match="authentic host topology"):
        evaluate_host_activation("codex", receipt)


def test_authentic_claim_requires_an_exact_host_version() -> None:
    receipt = _receipt(fired=True)
    receipt["host_versions"] = {}

    with pytest.raises(HostEvidenceError, match="host version"):
        evaluate_host_activation("codex", receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("terminal_output", "provider output"),
        ("stdout", "provider output"),
        ("credential_presence_only", {"OPENAI_API_KEY": "sk-secret-value-123456789"}),
        ("note", "Bearer abcdefghijklmnopqrstuvwxyz123456"),
    ],
)
def test_sanitizer_rejects_raw_fields_and_credential_values(field: str, value: object) -> None:
    receipt = _receipt(fired=True)
    receipt[field] = value

    with pytest.raises(HostEvidenceError, match="forbidden|credential"):
        evaluate_host_activation("codex", receipt)


@pytest.mark.parametrize(("fired", "expected_exit"), [(True, 0), (False, 1)])
def test_cli_verifies_external_host_receipt(
    fired: bool,
    expected_exit: int,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    receipt_path = tmp_path / "host-receipt.json"
    receipt_path.write_text(json.dumps(_receipt(fired=fired)))

    exit_code = run_adapters_cli(
        [
            "verify-host",
            "--host",
            "codex",
            "--receipt",
            str(receipt_path),
            "--json",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == expected_exit
    assert result["host"] == "codex"
    assert result["authentic"] is fired
