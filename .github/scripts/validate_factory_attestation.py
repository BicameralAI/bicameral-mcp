#!/usr/bin/env python3
"""Validate a Bicameral factory context attestation.

Default target: .bicameral/factory-attestation.json in the current repo.
This intentionally uses only Python stdlib so product repos do not need extra deps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FACTORY_PR_RE = re.compile(r"^https://github\.com/BicameralAI/bicameral-factory/pull/[0-9]+$")
ALLOWED_DIVERGENCE_RESOLUTIONS = {"none", "promoted", "disabled", "not_applicable"}
ALLOWED_CONTEXT_EVIDENCE_SOURCES = {"agent-declared", "wrapper-observed", "human-declared"}
REQUIRED_TOP_LEVEL = {
    "version",
    "factory_repo",
    "factory_commit",
    "loaded_context",
    "local_setup_divergence",
    "end_of_session_capture",
    "attested_by",
    "timestamp",
}
REQUIRED_CONTEXT = {"README.md", "CONTRIBUTING.md"}


def fail(errors: list[str]) -> int:
    print("Bicameral factory attestation failed:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    return 1


def load_json(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.exists():
        return None, [f"missing attestation file: {path}"]
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON in {path}: {exc}"]
    if not isinstance(data, dict):
        return None, ["attestation must be a JSON object"]
    return data, []


def valid_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_context_evidence(
    data: dict[str, Any], loaded_context: list[str] | None, context_root: Path | None
) -> list[str]:
    errors: list[str] = []
    evidence = data.get("context_evidence")
    if evidence is None:
        return errors
    if not isinstance(evidence, dict):
        return ["context_evidence must be an object keyed by loaded_context path"]

    loaded = set(loaded_context or [])
    for context_path, item in evidence.items():
        if not isinstance(context_path, str) or not context_path:
            errors.append("context_evidence keys must be non-empty strings")
            continue
        if context_path not in loaded:
            errors.append(f"context_evidence key not present in loaded_context: {context_path}")
        if not isinstance(item, dict):
            errors.append(f"context_evidence.{context_path} must be an object")
            continue

        source = item.get("source")
        if source is not None and source not in ALLOWED_CONTEXT_EVIDENCE_SOURCES:
            errors.append(
                f"context_evidence.{context_path}.source must be one of: "
                + ", ".join(sorted(ALLOWED_CONTEXT_EVIDENCE_SOURCES))
            )

        reason = item.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            errors.append(f"context_evidence.{context_path}.reason must be a non-empty string")

        sha256 = item.get("sha256")
        if sha256 is not None:
            if not isinstance(sha256, str) or not SHA256_RE.match(sha256):
                errors.append(
                    f"context_evidence.{context_path}.sha256 must be a 64-character lowercase SHA-256"
                )
            elif context_root is not None:
                file_path = context_root / context_path
                if not file_path.is_file():
                    errors.append(
                        f"context_evidence.{context_path}.sha256 cannot be verified; missing file: {file_path}"
                    )
                else:
                    actual = sha256_file(file_path)
                    if actual != sha256:
                        errors.append(
                            f"context_evidence.{context_path}.sha256 mismatch: expected {sha256}, got {actual}"
                        )

    return errors


def validate(
    data: dict[str, Any], require_context: set[str], context_root: Path | None = None
) -> list[str]:
    errors: list[str] = []

    missing = sorted(REQUIRED_TOP_LEVEL - set(data))
    if missing:
        errors.append(f"missing required field(s): {', '.join(missing)}")

    if data.get("version") != 1:
        errors.append("version must be 1")

    if data.get("factory_repo") != "BicameralAI/bicameral-factory":
        errors.append("factory_repo must be BicameralAI/bicameral-factory")

    factory_commit = data.get("factory_commit")
    if not isinstance(factory_commit, str) or not SHA_RE.match(factory_commit):
        errors.append("factory_commit must be a 40-character lowercase git SHA")

    loaded_context = data.get("loaded_context")
    if not isinstance(loaded_context, list) or not all(
        isinstance(item, str) and item for item in loaded_context
    ):
        errors.append("loaded_context must be a non-empty array of strings")
    else:
        missing_context = sorted(require_context - set(loaded_context))
        if missing_context:
            errors.append(f"loaded_context missing required item(s): {', '.join(missing_context)}")
    errors.extend(
        validate_context_evidence(
            data,
            loaded_context if isinstance(loaded_context, list) else None,
            context_root,
        )
    )

    divergence = data.get("local_setup_divergence")
    if not isinstance(divergence, dict):
        errors.append("local_setup_divergence must be an object")
    else:
        if divergence.get("checked") is not True:
            errors.append("local_setup_divergence.checked must be true")
        resolution = divergence.get("resolution")
        if resolution not in ALLOWED_DIVERGENCE_RESOLUTIONS:
            errors.append(
                "local_setup_divergence.resolution must be one of: "
                + ", ".join(sorted(ALLOWED_DIVERGENCE_RESOLUTIONS))
            )
        unresolved = divergence.get("unresolved_items", [])
        if unresolved:
            errors.append("local_setup_divergence.unresolved_items must be empty or omitted")
        if resolution == "promoted":
            factory_pr = divergence.get("factory_pr")
            if not isinstance(factory_pr, str) or not FACTORY_PR_RE.match(factory_pr):
                errors.append(
                    "promoted local setup divergence must include a bicameral-factory PR URL"
                )

    capture = data.get("end_of_session_capture")
    if not isinstance(capture, dict):
        errors.append("end_of_session_capture must be an object")
    else:
        if capture.get("run") is not True:
            errors.append("end_of_session_capture.run must be true")
        factory_pr = capture.get("factory_pr")
        if factory_pr is not None and (
            not isinstance(factory_pr, str) or not FACTORY_PR_RE.match(factory_pr)
        ):
            errors.append(
                "end_of_session_capture.factory_pr must be null or a bicameral-factory PR URL"
            )

    attested_by = data.get("attested_by")
    if not isinstance(attested_by, str) or not attested_by.strip():
        errors.append("attested_by must be a non-empty string")

    timestamp = data.get("timestamp")
    if not isinstance(timestamp, str) or not valid_datetime(timestamp):
        errors.append("timestamp must be an ISO-8601 date-time string")

    return errors


def collect_attestation_paths(path: Path) -> tuple[list[Path], list[str]]:
    if path.is_dir():
        paths = sorted(item for item in path.glob("*.json") if item.is_file())
        if not paths:
            return [], [f"missing attestation JSON files in directory: {path}"]
        return paths, []
    return [path], []


def validate_file(path: Path, require_context: set[str], context_root: Path | None) -> list[str]:
    data, errors = load_json(path)
    if errors:
        return errors
    assert data is not None

    errors = validate(data, require_context, context_root)
    factory_commit = data.get("factory_commit")
    if path.parent.name == "factory-attestations" and isinstance(factory_commit, str):
        expected_name = f"{factory_commit}.json"
        if path.name != expected_name:
            errors.append(
                f"commit-grounded attestation filename must be {expected_name} for factory_commit {factory_commit}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        default=".bicameral/factory-attestation.json",
        help=(
            "Path to attestation JSON or a directory of commit-grounded JSON files. "
            "Default: .bicameral/factory-attestation.json"
        ),
    )
    parser.add_argument(
        "--require-context",
        action="append",
        default=[],
        help="Additional loaded_context entry to require. Can be repeated.",
    )
    parser.add_argument(
        "--context-root",
        default=None,
        help="Optional root directory for verifying context_evidence.<path>.sha256 values.",
    )
    args = parser.parse_args()

    require_context = REQUIRED_CONTEXT | set(args.require_context)
    context_root = Path(args.context_root) if args.context_root else None
    attestation_paths, errors = collect_attestation_paths(Path(args.path))
    for path in attestation_paths:
        path_errors = validate_file(path, require_context, context_root)
        errors.extend(f"{path}: {error}" for error in path_errors)

    if errors:
        return fail(errors)

    print("Bicameral factory attestation OK: " + ", ".join(str(path) for path in attestation_paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
