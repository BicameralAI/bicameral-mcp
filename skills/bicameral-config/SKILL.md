# /bicameral:config — Interactive Configuration

**Trigger**: user types `/bicameral:config`

Walk through each bicameral configuration setting interactively, write the
updated `config.yaml`, and reinstall all hooks so changes take effect
immediately — identical to running `bicameral-mcp setup` in the terminal.

---

## Step 1 — Read current config

```python
from pathlib import Path
import yaml, subprocess, sys

repo_path = Path.cwd()
config_path = repo_path / ".bicameral" / "config.yaml"

if config_path.exists():
    cfg = yaml.safe_load(config_path.read_text()) or {}
else:
    cfg = {}

current_mode     = cfg.get("mode", "team")
current_guided   = cfg.get("guided", True)
current_telemetry = cfg.get("telemetry", True)
```

Present current values to the user before asking about changes:

```
Current bicameral config:
  mode:      {current_mode}
  guided:    {current_guided}
  telemetry: {current_telemetry}
```

---

## Step 2 — Ask about each setting

Ask the user the three questions in sequence. Accept their current value as
the default (pressing Enter keeps it unchanged).

### 2a — Collaboration mode

```
Collaboration mode:
  [1] team  — decisions shared via git (append-only event files)  ← current if team
  [2] solo  — decisions stored locally
Choose [1/2, Enter = keep current]:
```

### 2b — Guided mode

```
Interaction intensity:
  [1] Guided — blocking hints + git post-commit hook (status updates after every commit)  ← current if guided
  [2] Normal — advisory hints only
Choose [1/2, Enter = keep current]:
```

### 2c — Anonymous telemetry

```
Anonymous telemetry (no code, no decision text, no personal data):
  [1] Yes — share anonymous usage stats to improve Bicameral  ← current if on
  [2] No  — keep telemetry off
Choose [1/2, Enter = keep current]:
```

---

## Step 3 — Write updated config.yaml

```python
new_mode      = # user's answer from 2a
new_guided    = # user's answer from 2b
new_telemetry = # user's answer from 2c

config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(
    "# Bicameral configuration\n"
    f"mode: {new_mode}\n"
    f"guided: {'true' if new_guided else 'false'}\n"
    f"telemetry: {'true' if new_telemetry else 'false'}\n"
)
```

---

## Step 4 — Reinstall skills and hooks via subprocess

Run through the new binary so the latest hook commands are written (avoids
stale `sys.modules` from the current process):

```python
script = (
    "from setup_wizard import _install_skills, _install_claude_hooks, _install_git_post_commit_hook; "
    "from pathlib import Path; "
    f"rp = Path(r'{repo_path}'); "
    f"n = _install_skills(rp); "
    f"_install_claude_hooks(rp); "
    + (f"_install_git_post_commit_hook(rp); " if new_guided else "")
    + "print(n)"
)
result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
skills_n = int(result.stdout.strip() or "0") if result.returncode == 0 else 0
```

---

## Step 5 — Report what changed

```
bicameral config updated:
  mode:      {old} → {new}   (or "unchanged")
  guided:    {old} → {new}
  telemetry: {old} → {new}

Skills reinstalled: {skills_n}
Hooks updated:      .claude/settings.json
Git post-commit:    {"installed" if new_guided else "not installed (Normal mode)"}
```

If nothing changed, say: "No changes — config already matches your selections."
