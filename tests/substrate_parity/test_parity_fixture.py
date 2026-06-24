"""Tests for the substrate parity fixture (GH #611).

These validate the *instrument* deterministically with synthetic logs, so CI
proves the diff/gate logic works without needing to spin up the three live
substrates. The live cross-substrate run is a manual/CI-scheduled step
documented in README.md; this file guarantees the analysis it feeds is sound.

Sociable where it counts: the hook test runs the real ``parity_hook.py`` as a
subprocess over real stdin + a real log file (no mocks).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).parent


def _load_diff_module():
    spec = importlib.util.spec_from_file_location(
        "parity_diff_under_test", _HERE / "diff_parity.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diff_parity = _load_diff_module()


# ── parity_hook.py (sociable: real subprocess + real file) ───────────────────


def test_hook_appends_record_from_stdin_payload(tmp_path):
    log = tmp_path / "parity-headless.jsonl"
    payload = {
        "hook_event_name": "SessionEnd",
        "session_id": "abc-123",
        "cwd": "/repo",
    }
    proc = subprocess.run(
        [sys.executable, str(_HERE / "parity_hook.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={
            "BICAMERAL_PARITY_LOG": str(log),
            "BICAMERAL_PARITY_SUBSTRATE": "headless",
            "PATH": __import__("os").environ.get("PATH", ""),
            "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", "probe must be a strict no-op (no stdout)"

    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["event"] == "SessionEnd"
    assert rec["substrate"] == "headless"
    assert rec["session_id"] == "abc-123"
    assert "ts" in rec


def test_hook_event_arg_overrides_and_tolerates_empty_stdin(tmp_path):
    log = tmp_path / "parity-cron.jsonl"
    proc = subprocess.run(
        [sys.executable, str(_HERE / "parity_hook.py"), "--event", "SessionStart"],
        input="",  # cron/bare invocation: no stdin JSON
        capture_output=True,
        text=True,
        env={
            "BICAMERAL_PARITY_LOG": str(log),
            "BICAMERAL_PARITY_SUBSTRATE": "cron",
            "PATH": __import__("os").environ.get("PATH", ""),
            "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
        },
    )
    assert proc.returncode == 0, proc.stderr
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["event"] == "SessionStart"
    assert rec["substrate"] == "cron"


# ── diff_parity.py gate logic ────────────────────────────────────────────────


def _counts(*events):
    out = {}
    for e in events:
        out[e] = out.get(e, 0) + 1
    return out


def test_clean_parity_has_no_divergence():
    logs = {
        "interactive": _counts("SessionStart", "PostToolUse", "Stop", "SessionEnd"),
        "headless": _counts("SessionStart", "PostToolUse", "Stop", "SessionEnd"),
    }
    report = diff_parity.build_report(logs, reference="interactive")
    assert report["divergences"] == []
    assert report["missing_critical"] == []


def test_missing_capture_critical_event_is_flagged():
    # headless never fired SessionEnd — the exact #611 failure mode.
    logs = {
        "interactive": _counts("SessionStart", "PostToolUse", "SessionEnd"),
        "headless": _counts("SessionStart", "PostToolUse"),
    }
    report = diff_parity.build_report(logs, reference="interactive")
    assert any(f["event"] == "SessionEnd" for f in report["missing_critical"])


def test_non_critical_divergence_not_treated_as_critical():
    logs = {
        "interactive": _counts("SessionStart", "UserPromptSubmit", "SessionEnd", "PostToolUse"),
        "cron": _counts("SessionStart", "SessionEnd", "PostToolUse"),  # no UserPromptSubmit
    }
    report = diff_parity.build_report(logs, reference="interactive")
    assert report["missing_critical"] == []
    assert any(f["event"] == "UserPromptSubmit" for f in report["divergences"])


def test_main_exit_code_2_on_capture_critical_gap(tmp_path):
    ref = tmp_path / "i.jsonl"
    hl = tmp_path / "h.jsonl"
    ref.write_text("\n".join(json.dumps({"event": e}) for e in ("SessionStart", "SessionEnd")))
    hl.write_text(json.dumps({"event": "SessionStart"}))
    rc = diff_parity.main(["--log", f"interactive={ref}", "--log", f"headless={hl}"])
    assert rc == 2


def test_main_exit_code_0_when_clean(tmp_path):
    ref = tmp_path / "i.jsonl"
    hl = tmp_path / "h.jsonl"
    for p in (ref, hl):
        p.write_text(
            "\n".join(
                json.dumps({"event": e})
                for e in ("SessionStart", "SessionEnd", "Stop", "PostToolUse")
            )
        )
    rc = diff_parity.main(["--log", f"interactive={ref}", "--log", f"headless={hl}"])
    assert rc == 0


def test_strict_mode_fails_on_non_critical_divergence(tmp_path):
    ref = tmp_path / "i.jsonl"
    cr = tmp_path / "c.jsonl"
    ref.write_text(
        "\n".join(
            json.dumps({"event": e})
            for e in ("SessionStart", "UserPromptSubmit", "SessionEnd", "Stop", "PostToolUse")
        )
    )
    cr.write_text(
        "\n".join(
            json.dumps({"event": e}) for e in ("SessionStart", "SessionEnd", "Stop", "PostToolUse")
        )
    )
    assert diff_parity.main(["--log", f"interactive={ref}", "--log", f"cron={cr}"]) == 0
    assert diff_parity.main(["--log", f"interactive={ref}", "--log", f"cron={cr}", "--strict"]) == 1
