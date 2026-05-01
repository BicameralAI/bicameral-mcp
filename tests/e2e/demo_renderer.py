#!/usr/bin/env python3
# Pretty-print Claude Code stream-json to xterm and detect scene boundaries.
#
# Reads stream-json from stdin (one JSON object per line). Writes:
#   - human-readable output to stdout (visible in the recorded xterm)
#   - raw stream-json to $DEMO_TRANSCRIPT
#   - scene-boundary timestamps to $DEMO_SCENES_FILE
#
# Scene boundaries (option a — tool-call ordering, no LLM-emitted sentinels):
#   t1 (Scene 1 → Scene 2): first mcp__bicameral__bicameral_preflight call
#   t2 (Scene 2 → Scene 3): first mcp__bicameral__bicameral_history call
#                           AFTER any mcp__bicameral__bicameral_link_commit call

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

TRANSCRIPT = Path(os.environ.get("DEMO_TRANSCRIPT", "/tmp/demo-transcript.ndjson"))
SCENES_FILE = Path(os.environ.get("DEMO_SCENES_FILE", "/tmp/demo-scenes.txt"))


def _record_scene(name: str) -> None:
    with SCENES_FILE.open("a") as f:
        f.write(f"{name}={time.time():.3f}\n")


def _tool_bare(name: str) -> str:
    return name.split("__")[-1] if "__" in name else name


def _input_summary(payload: dict) -> str:
    if not isinstance(payload, dict) or not payload:
        return ""
    parts: list[str] = []
    for k, v in list(payload.items())[:3]:
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s}")
    return " ".join(parts)


def _flush(line: str = "") -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main() -> int:
    SCENES_FILE.write_text("")
    TRANSCRIPT.write_text("")
    _record_scene("recording_start")

    saw_link_commit = False
    saw_preflight = False
    saw_post_history = False

    raw = TRANSCRIPT.open("a")

    for line in sys.stdin:
        if not line.strip():
            continue

        raw.write(line)
        raw.flush()

        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        t = obj.get("type")

        if t == "system" and obj.get("subtype") == "init":
            _flush(f"[demo] session started — model={obj.get('model', '?')}")
            continue

        if t == "assistant":
            msg = obj.get("message") or {}
            for block in msg.get("content") or []:
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "").rstrip()
                    if text:
                        _flush()
                        _flush(text)
                elif btype == "tool_use":
                    name = block.get("name", "")
                    bare = _tool_bare(name)
                    summary = _input_summary(block.get("input") or {})
                    _flush(f"\n  ▸ tool: {bare}  {summary}".rstrip())

                    if not saw_preflight and name.endswith("bicameral_preflight"):
                        saw_preflight = True
                        _record_scene("scene_1_to_2")
                    if name.endswith("bicameral_link_commit"):
                        saw_link_commit = True
                    if (
                        not saw_post_history
                        and saw_link_commit
                        and name.endswith("bicameral_history")
                    ):
                        saw_post_history = True
                        _record_scene("scene_2_to_3")
            continue

        if t == "user":
            msg = obj.get("message") or {}
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content") or ""
                    if isinstance(content, list):
                        content = "".join(
                            part.get("text", "") if isinstance(part, dict) else str(part)
                            for part in content
                        )
                    snippet = str(content).replace("\n", " ")
                    if len(snippet) > 220:
                        snippet = snippet[:217] + "..."
                    _flush(f"  ◂ result: {snippet}")
            continue

        if t == "result":
            duration = obj.get("duration_ms", "?")
            cost = obj.get("total_cost_usd", "?")
            _flush(f"\n[demo] session complete — duration={duration}ms cost=${cost}")

    _record_scene("recording_end")
    raw.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
