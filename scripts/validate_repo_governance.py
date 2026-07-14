#!/usr/bin/env python3
"""Validate the MCP repo-governance descriptor with the Python standard library.

The committed `.yaml` descriptor is intentionally encoded as JSON, which is a
valid YAML 1.2 subset. This keeps repository CI dependency-free while preserving
compatibility with the Factory YAML aggregation path.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CLASSIFICATIONS = {
    "product-critical",
    "product-supporting",
    "docs",
    "infrastructure",
    "watchlist",
    "ignored",
}
VISIBILITIES = {"public", "internal", "restricted"}
RUNTIME_ROLES = {
    "authority-runtime",
    "agent-surface",
    "evidence-adapter",
    "factory-control-plane",
    "other",
}
CHECKS = {
    "sbom",
    "codeql",
    "scorecard",
    "dependency-review",
    "governance-gate",
    "factory-attestation",
}
SHADOW_SOURCES = {
    "failed_ci",
    "reverted_prs",
    "governance_gate_failures",
    "correction_records",
    "repeated_review_findings",
}
TOP_LEVEL = {
    "schema_version",
    "repo",
    "classification",
    "visibility",
    "governance_owner",
    "runtime_role",
    "release_trust_required",
    "required_checks",
    "branch_protection",
    "shadow_genome_sources",
    "public_exposure",
}
REQUIRED = {
    "repo",
    "classification",
    "visibility",
    "governance_owner",
    "runtime_role",
    "release_trust_required",
    "required_checks",
    "branch_protection",
}
MAIN_KEYS = {
    "require_pr",
    "required_approvals",
    "require_status_checks",
    "restrict_force_push",
}


def strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def unique(value: list[str]) -> bool:
    return len(value) == len(set(value))


def validate(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["descriptor must be an object"]

    unknown = sorted(set(data) - TOP_LEVEL)
    if unknown:
        errors.append("unknown top-level field(s): " + ", ".join(unknown))
    missing = sorted(REQUIRED - set(data))
    if missing:
        errors.append("missing required field(s): " + ", ".join(missing))

    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    repo = data.get("repo")
    if repo != "BicameralAI/bicameral-mcp" or not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        errors.append("repo must be BicameralAI/bicameral-mcp")
    if data.get("classification") not in CLASSIFICATIONS:
        errors.append("classification is invalid")
    if data.get("visibility") not in VISIBILITIES:
        errors.append("visibility is invalid")
    if data.get("governance_owner") != "BicameralAI/bicameral-factory":
        errors.append("governance_owner must be BicameralAI/bicameral-factory")
    if data.get("runtime_role") not in RUNTIME_ROLES:
        errors.append("runtime_role is invalid")
    if not isinstance(data.get("release_trust_required"), bool):
        errors.append("release_trust_required must be boolean")

    checks = data.get("required_checks")
    if not strings(checks):
        errors.append("required_checks must be an array of non-empty strings")
    else:
        invalid = sorted(set(checks) - CHECKS)
        if invalid:
            errors.append("unsupported required check(s): " + ", ".join(invalid))
        if not unique(checks):
            errors.append("required_checks must be unique")

    protection = data.get("branch_protection")
    if not isinstance(protection, dict) or set(protection) != {"main"}:
        errors.append("branch_protection must contain only main")
    else:
        main = protection.get("main")
        if not isinstance(main, dict) or set(main) != MAIN_KEYS:
            errors.append("branch_protection.main fields do not match the Factory schema")
        else:
            for key in ("require_pr", "require_status_checks", "restrict_force_push"):
                if not isinstance(main.get(key), bool):
                    errors.append(f"branch_protection.main.{key} must be boolean")
            approvals = main.get("required_approvals")
            if not isinstance(approvals, int) or approvals < 0:
                errors.append("branch_protection.main.required_approvals must be non-negative")

    sources = data.get("shadow_genome_sources")
    if sources is not None:
        if not strings(sources):
            errors.append("shadow_genome_sources must be an array of non-empty strings")
        else:
            invalid = sorted(set(sources) - SHADOW_SOURCES)
            if invalid:
                errors.append("unsupported shadow source(s): " + ", ".join(invalid))
            if not unique(sources):
                errors.append("shadow_genome_sources must be unique")

    exposure = data.get("public_exposure")
    if exposure is not None:
        if not isinstance(exposure, dict) or set(exposure) - {"allowed", "prohibited"}:
            errors.append("public_exposure contains unsupported fields")
        else:
            for key in ("allowed", "prohibited"):
                value = exposure.get(key, [])
                if not strings(value):
                    errors.append(f"public_exposure.{key} must be an array of non-empty strings")

    # This initial descriptor must remain honest until Factory #171/#213 applies
    # and verifies the organization rulesets. A later evidence-backed PR may turn
    # these controls on, but this declaration must not predict the future.
    if isinstance(protection, dict) and isinstance(protection.get("main"), dict):
        main = protection["main"]
        expected_current = {
            "require_pr": False,
            "required_approvals": 0,
            "require_status_checks": False,
            "restrict_force_push": False,
        }
        if main != expected_current:
            errors.append(
                "initial branch_protection.main must disclose the currently unprotected live state; "
                "update only after Factory #171 post-apply evidence"
            )

    return errors


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".bicameral/repo-governance.yaml")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"ERROR: descriptor not found: {path}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR: descriptor must remain JSON-compatible YAML: {exc}", file=sys.stderr)
        return 2

    errors = validate(data)
    if errors:
        print("Repo governance validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"OK: valid repo governance descriptor: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
