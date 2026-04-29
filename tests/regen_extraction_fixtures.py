#!/usr/bin/env python3
"""Bootstrap the M1 ground-truth extraction fixtures.

Runs the current .claude/skills/bicameral-ingest/SKILL.md Step-1 prompt
against each transcript in TRANSCRIPT_SOURCES using a strong model
(default: claude-opus-4-6-20251015) and writes the extracted decisions
+ action items to tests/fixtures/extraction/<source_ref>.json.

The committed JSONs are the **ground truth** for the M1 eval's
precision/recall metric — they are hand-editable after bootstrap,
and re-running this script overwrites them. Commit the resulting
diff like any other reference fixture.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-api03-...
    .venv/bin/python tests/regen_extraction_fixtures.py --all
    .venv/bin/python tests/regen_extraction_fixtures.py \\
        --source-ref adv-strat-fake \\
        --model claude-opus-4-6-20251015
    .venv/bin/python tests/regen_extraction_fixtures.py --all --dry-run

Flags:
    --all             Regenerate all transcripts in TRANSCRIPT_SOURCES.
    --source-ref REF  Regenerate a single transcript (overrides --all).
    --model MODEL     Model to use (default: claude-opus-4-6-20251015).
    --force           Overwrite existing fixtures without confirmation.
    --dry-run         Don't write files; print what would change.

Cost (approximate, one-time bootstrap with Opus 4.6):
    ~9 transcripts × ~3k input tokens × $15/Mtok  ≈ $0.40
    ~9 transcripts × ~2k output tokens × $75/Mtok ≈ $1.35
    Total ≈ $1.75 per full-corpus regen. Cheap.

After running, `git diff tests/fixtures/extraction/` should
show the new/changed fixtures. Review, hand-edit if needed, commit.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _extract_headless import (  # noqa: E402  (sibling module)
    DEFAULT_MODEL,
    SKILL_MD_PATH,
    _load_skill_md,
    _sha,
    extract_from_current_skill,
)
from fixtures.expected.decisions import TRANSCRIPT_SOURCES  # noqa: E402

MCP_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "extraction"
RECOMMENDED_MODEL = "claude-opus-4-6-20251015"


def _load_transcript(rel_path: str) -> str:
    p = (MCP_ROOT / rel_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"transcript not found: {p}")
    return p.read_text(encoding="utf-8")


def _fixture_path(source_ref: str) -> Path:
    return FIXTURES_DIR / f"{source_ref}.json"


def _regenerate_one(
    source_ref: str,
    *,
    model: str,
    force: bool,
    dry_run: bool,
) -> str:
    """Return a one-word status: 'written' | 'skipped' | 'dry-run' | 'error'."""
    src = TRANSCRIPT_SOURCES[source_ref]
    transcript_text = _load_transcript(src["transcript"])
    out = _fixture_path(source_ref)

    if out.exists() and not force and not dry_run:
        print(f"  [{source_ref}] exists, pass --force to overwrite — skipped")
        return "skipped"

    try:
        extracted = extract_from_current_skill(
            transcript_text,
            source_ref=source_ref,
            model=model,
            use_cache=False,  # always hit the API for a fresh generation
        )
    except Exception as exc:
        print(f"  [{source_ref}] ERROR: {exc}")
        return "error"

    skill_md = _load_skill_md(SKILL_MD_PATH)

    fixture = {
        "source_ref": source_ref,
        "transcript_path": src["transcript"],
        "repo_key": src["repo_key"],
        "generated_by": model,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "skill_md_sha": _sha(skill_md)[:12],
        "decisions": extracted.get("decisions", []),
        "action_items": extracted.get("action_items", []),
    }

    n_dec = len(fixture["decisions"])
    n_act = len(fixture["action_items"])

    if dry_run:
        print(f"  [{source_ref}] dry-run: {n_dec} decisions, {n_act} action_items")
        return "dry-run"

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"  [{source_ref}] wrote {n_dec} decisions, {n_act} action_items → {out.name}")
    return "written"


def main():
    parser = argparse.ArgumentParser(description="Regenerate M1 ground-truth extraction fixtures")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Regenerate all transcripts")
    group.add_argument("--source-ref", help="Regenerate a single transcript")
    parser.add_argument(
        "--model",
        default=RECOMMENDED_MODEL,
        help=f"Model to use (default: {RECOMMENDED_MODEL}; {DEFAULT_MODEL} for cheap tests)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing fixtures")
    parser.add_argument("--dry-run", action="store_true", help="Don't write files")
    args = parser.parse_args()

    if args.source_ref:
        if args.source_ref not in TRANSCRIPT_SOURCES:
            print(f"error: unknown source_ref {args.source_ref!r}", file=sys.stderr)
            print(f"  available: {sorted(TRANSCRIPT_SOURCES.keys())}", file=sys.stderr)
            sys.exit(2)
        targets = [args.source_ref]
    else:
        targets = sorted(TRANSCRIPT_SOURCES.keys())

    print(f"Regenerating {len(targets)} extraction fixture(s) with model={args.model}")
    print(f"  fixtures dir: {FIXTURES_DIR}")
    if args.dry_run:
        print("  mode: DRY RUN (no files written)")
    print()

    status_counts: dict[str, int] = {}
    for source_ref in targets:
        status = _regenerate_one(
            source_ref,
            model=args.model,
            force=args.force,
            dry_run=args.dry_run,
        )
        status_counts[status] = status_counts.get(status, 0) + 1

    print()
    print(f"  summary: {status_counts}")

    if status_counts.get("error", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
