#!/usr/bin/env python3
"""Emit and verify a commit-bound MCP release descriptor."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"^[a-f0-9]{40}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


def files_digest(paths: list[str]) -> str:
    hasher = hashlib.sha256()
    for name in sorted(paths):
        path = ROOT / name
        if not path.is_file():
            raise ValueError(f"descriptor input is missing: {name}")
        hasher.update(name.encode())
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
    return "sha256:" + hasher.hexdigest()


def tree_digest(path_name: str) -> str:
    root = ROOT / path_name
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise ValueError(f"descriptor tree is missing or empty: {path_name}")
    hasher = hashlib.sha256()
    for path in files:
        hasher.update(path.relative_to(ROOT).as_posix().encode())
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
    return "sha256:" + hasher.hexdigest()


def canonical_digest(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def build_descriptor(commit: str) -> dict:
    if not SHA.fullmatch(commit):
        raise ValueError("commit must be a full lowercase git SHA")
    payload = {
        "schema_version": 1,
        "component": "mcp",
        "commit": commit,
        "artifacts": {
            "server": files_digest(
                ["daemon_client.py", "server.py", "tool_request.py", "tool_schemas.py"]
            ),
            "package": files_digest(["pyproject.toml", "requirements-ci.lock"]),
        },
        "interfaces": {
            "tool_request": files_digest(["tool_request.py", "tool_schemas.py"]),
            "bot_daemon_client": files_digest(["daemon_client.py"]),
        },
    }
    payload["descriptor_digest"] = canonical_digest(payload)
    return payload


def validate_descriptor(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["descriptor must be an object"]
    errors: list[str] = []
    if payload.get("schema_version") != 1 or payload.get("component") != "mcp":
        errors.append("schema/component mismatch")
    if not isinstance(payload.get("commit"), str) or not SHA.fullmatch(payload["commit"]):
        errors.append("commit must be a full lowercase git SHA")
    for section in ("artifacts", "interfaces"):
        values = payload.get(section)
        if not isinstance(values, dict) or not values:
            errors.append(f"{section} must be non-empty")
            continue
        for name, value in values.items():
            if not isinstance(value, str) or not DIGEST.fullmatch(value):
                errors.append(f"{section}.{name} must be a sha256 digest")
    unsigned = {key: value for key, value in payload.items() if key != "descriptor_digest"}
    if payload.get("descriptor_digest") != canonical_digest(unsigned):
        errors.append("descriptor_digest does not bind the descriptor")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "release-artifacts" / "mcp-release-descriptor.json"
    )
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        errors = validate_descriptor(json.loads(args.verify.read_text()))
        if errors:
            print("\n".join(f"- {error}" for error in errors))
            return 1
        return 0
    commit = (
        os.environ.get("RELEASE_SOURCE_COMMIT")
        or os.environ.get("GITHUB_SHA")
        or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    )
    args.output.parent.mkdir(exist_ok=True)
    args.output.write_text(json.dumps(build_descriptor(commit), indent=2, sort_keys=True) + "\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
