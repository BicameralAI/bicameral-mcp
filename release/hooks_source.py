"""Single source of truth for the hook commands enumerated in the
release-time ``hooks-manifest.json``.

Each entry mirrors what ``setup_wizard._install_*_hooks`` would write to
the host config files. ``hooks_manifest_generator`` walks the
``BICAMERAL_HOOKS`` list to produce the signed manifest; the install-time
verifier (`release.manifest_verify`) re-derives the same shape from the
installer's own intent and cross-checks SHA-256 equality before any write.

If a new hook is added to ``setup_wizard``, append it here in the same
commit. Drift between this list and the installers' actual writes is
exactly what the verify-side gate catches.
"""

from __future__ import annotations

import setup_wizard as _sw

BICAMERAL_HOOKS: list[dict[str, str]] = [
    {
        "event_type": "claude:PostToolUse:Bash",
        "command": _sw._BICAMERAL_POST_COMMIT_COMMAND,
    },
    {
        "event_type": "claude:PostToolUse:bicameral_preflight",
        "command": _sw._BICAMERAL_COLLISION_CAPTURE_REMINDER_COMMAND,
    },
    {
        "event_type": "claude:SessionEnd",
        "command": _sw._BICAMERAL_SESSION_END_COMMAND,
    },
    {
        "event_type": "claude:UserPromptSubmit",
        "command": _sw._BICAMERAL_PREFLIGHT_REMINDER_COMMAND,
    },
    {
        "event_type": "git:post-commit",
        "command": _sw._GIT_POST_COMMIT_HOOK,
    },
    {
        "event_type": "git:pre-push",
        "command": _sw._GIT_PRE_PUSH_HOOK,
    },
]
