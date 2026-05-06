"""Functionality tests for `release.hooks_manifest_generator` (#218 Phase 1).

Locks the deterministic manifest contract:
- One entry per known hook function the installer would write
- Each entry carries `event_type`, `command`, `sha256` (hex)
- `sha256` equals `hashlib.sha256(command.encode()).hexdigest()` exactly
- Serialized JSON is byte-deterministic across calls (sorted keys, fixed indent)
- Entries ordered by `event_type` lexicographically regardless of declaration order
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from release import hooks_manifest_generator as hmg


def _stub_module(hooks: list[dict[str, str]]) -> SimpleNamespace:
    """Build a stub matching the contract `generate_manifest` consumes:
    each item is `{event_type: str, command: str}`."""
    return SimpleNamespace(BICAMERAL_HOOKS=hooks)


def test_generate_manifest_emits_entry_per_hook_function() -> None:
    stub = _stub_module(
        [
            {"event_type": "PostToolUse", "command": "echo claude-hook-fired"},
            {"event_type": "post-commit", "command": "git log -1"},
        ]
    )
    manifest = hmg.generate_manifest(stub)
    assert "hooks" in manifest
    assert len(manifest["hooks"]) == 2
    event_types = {entry["event_type"] for entry in manifest["hooks"]}
    assert event_types == {"PostToolUse", "post-commit"}


def test_sha256_matches_command_bytes() -> None:
    cmd = "echo deterministic-hook-payload"
    stub = _stub_module([{"event_type": "PostToolUse", "command": cmd}])
    manifest = hmg.generate_manifest(stub)
    expected = hashlib.sha256(cmd.encode("utf-8")).hexdigest()
    assert manifest["hooks"][0]["sha256"] == expected


def test_manifest_serialization_is_deterministic(tmp_path: Path) -> None:
    stub_a = _stub_module(
        [
            {"event_type": "PostToolUse", "command": "echo a"},
            {"event_type": "post-commit", "command": "git log -1"},
        ]
    )
    stub_b = _stub_module(
        [
            {"event_type": "post-commit", "command": "git log -1"},
            {"event_type": "PostToolUse", "command": "echo a"},
        ]
    )
    out_a = tmp_path / "manifest_a.json"
    out_b = tmp_path / "manifest_b.json"
    hmg.write_manifest(hmg.generate_manifest(stub_a), out_a)
    hmg.write_manifest(hmg.generate_manifest(stub_b), out_b)
    assert out_a.read_bytes() == out_b.read_bytes()


def test_manifest_orders_entries_by_event_type(tmp_path: Path) -> None:
    stub = _stub_module(
        [
            {"event_type": "post-commit", "command": "x"},
            {"event_type": "PostToolUse", "command": "y"},
            {"event_type": "pre-push", "command": "z"},
        ]
    )
    out = tmp_path / "m.json"
    hmg.write_manifest(hmg.generate_manifest(stub), out)
    parsed = json.loads(out.read_text())
    event_types = [entry["event_type"] for entry in parsed["hooks"]]
    assert event_types == sorted(event_types)


def test_generate_manifest_raises_when_module_missing_hooks_attr() -> None:
    stub = SimpleNamespace()
    with pytest.raises(AttributeError):
        hmg.generate_manifest(stub)
