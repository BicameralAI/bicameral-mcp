#!/usr/bin/env python3
"""Fail closed when a customer artifact contains Factory-only material.

The validator is intentionally Python-stdlib only so it can be vendored into
public product repositories without granting CI access to the private Factory.
It scans directories, regular files, ZIP/wheel archives, and tar archives.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import sys
import tarfile
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

DEFAULT_FORBIDDEN_PATHS = (
    ".bicameral/factory-context.local.json",
    ".bicameral/factory-attestations/**",
    ".bicameral/factory-skills/**",
    ".agents/skills/bic-*/**",
    "**/factory-context.local.json",
    "**/factory-attestations/**",
    "**/factory-run-manifest*.json",
    "**/factory-worker-receipt*.json",
    "**/bicameral-factory/**",
)

DEFAULT_FORBIDDEN_TEXT = (
    "github.com/BicameralAI/bicameral-factory",
    "git@github.com:BicameralAI/bicameral-factory.git",
    "BicameralAI/bicameral-factory",
    ".bicameral/factory-context.local.json",
    ".bicameral/factory-attestations",
    "/bic:setup-bicameral-factory",
)

MAX_DEFAULT_TEXT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Entry:
    artifact: str
    member: str
    data: bytes


@dataclass(frozen=True)
class Violation:
    artifact: str
    member: str
    rule_type: str
    rule: str


def normalize_member(name: str) -> str:
    value = name.replace("\\", "/").lstrip("./")
    parts = PurePosixPath(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"unsafe artifact member path: {name!r}")
    return "/".join(parts)


def matches(path: str, pattern: str) -> bool:
    normalized_pattern = pattern.replace("\\", "/").lstrip("./")
    return fnmatch.fnmatchcase(path, normalized_pattern) or fnmatch.fnmatchcase(
        f"/{path}", f"/{normalized_pattern}"
    )


def load_policy(path: Path | None) -> dict:
    policy: dict = {}
    if path is not None:
        try:
            policy = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"policy file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON policy {path}: {exc}") from exc
        if not isinstance(policy, dict):
            raise ValueError("policy must be a JSON object")
        if policy.get("schema_version", 1) != 1:
            raise ValueError("policy schema_version must be 1")
    return policy


def iter_zip(path: Path) -> Iterator[Entry]:
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            member = normalize_member(info.filename)
            yield Entry(str(path), member, archive.read(info))


def iter_tar(path: Path) -> Iterator[Entry]:
    with tarfile.open(path, mode="r:*") as archive:
        for info in archive.getmembers():
            if not info.isfile():
                continue
            member = normalize_member(info.name)
            handle = archive.extractfile(info)
            if handle is None:
                continue
            yield Entry(str(path), member, handle.read())


def iter_directory(path: Path) -> Iterator[Entry]:
    root = path.resolve()
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        resolved = item.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"artifact member escapes root: {item}") from exc
        member = normalize_member(relative.as_posix())
        yield Entry(str(path), member, item.read_bytes())


def iter_artifact(path: Path) -> Iterator[Entry]:
    if not path.exists():
        raise ValueError(f"artifact does not exist: {path}")
    if path.is_dir():
        yield from iter_directory(path)
        return
    lower = path.name.lower()
    if lower.endswith((".zip", ".whl")):
        yield from iter_zip(path)
        return
    if lower.endswith((".tar", ".tar.gz", ".tgz")):
        yield from iter_tar(path)
        return
    yield Entry(str(path), normalize_member(path.name), path.read_bytes())


def allowed(member: str, rule: str, allow: dict[str, list[str]]) -> bool:
    patterns = allow.get(rule, [])
    return any(matches(member, pattern) for pattern in patterns)


def text_content(data: bytes, max_bytes: int) -> str | None:
    if len(data) > max_bytes or b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def validate_entries(entries: Iterable[Entry], policy: dict) -> tuple[list[Violation], list[dict]]:
    path_rules = tuple(DEFAULT_FORBIDDEN_PATHS) + tuple(policy.get("forbidden_paths", []))
    text_rules = tuple(DEFAULT_FORBIDDEN_TEXT) + tuple(policy.get("forbidden_text", []))
    path_allow = policy.get("allow_path_rules", {})
    text_allow = policy.get("allow_text_rules", {})
    max_text_bytes = int(policy.get("max_text_bytes", MAX_DEFAULT_TEXT_BYTES))
    violations: list[Violation] = []
    inventory: list[dict] = []

    for entry in entries:
        inventory.append(
            {
                "artifact": entry.artifact,
                "member": entry.member,
                "size": len(entry.data),
                "sha256": hashlib.sha256(entry.data).hexdigest(),
            }
        )
        for rule in path_rules:
            if matches(entry.member, rule) and not allowed(entry.member, rule, path_allow):
                violations.append(Violation(entry.artifact, entry.member, "path", rule))
        text = text_content(entry.data, max_bytes=max_text_bytes)
        if text is None:
            continue
        for rule in text_rules:
            if rule in text and not allowed(entry.member, rule, text_allow):
                violations.append(Violation(entry.artifact, entry.member, "text", rule))

    return violations, inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact", action="append", required=True, help="Artifact path; repeatable."
    )
    parser.add_argument("--policy", type=Path, default=None, help="Optional JSON policy.")
    parser.add_argument("--report", type=Path, default=None, help="Write a JSON receipt.")
    args = parser.parse_args()

    try:
        policy = load_policy(args.policy)
        all_entries: list[Entry] = []
        for value in args.artifact:
            all_entries.extend(iter_artifact(Path(value)))
        violations, inventory = validate_entries(all_entries, policy)
    except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
        print(f"customer artifact boundary validation failed: {exc}", file=sys.stderr)
        return 2

    receipt = {
        "schema_version": 1,
        "status": "failed" if violations else "passed",
        "artifacts": sorted({entry.artifact for entry in all_entries}),
        "inventory": inventory,
        "violations": [asdict(item) for item in violations],
    }
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    if violations:
        print("Customer artifact boundary check failed:", file=sys.stderr)
        for item in violations:
            print(
                f"- {item.artifact}:{item.member}: forbidden {item.rule_type} rule {item.rule!r}",
                file=sys.stderr,
            )
        return 1

    print(f"Customer artifact boundary check passed for {len(receipt['artifacts'])} artifact(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
