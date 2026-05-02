# /bicameral:config — Interactive Configuration

**Trigger**: user types `/bicameral:config`

Walk through each bicameral configuration setting interactively, write the
updated `config.yaml`, and reinstall all hooks so changes take effect
immediately.

---

## Step 1 — Read current config

```python
from pathlib import Path
import yaml

repo_path = Path.cwd()
config_path = repo_path / ".bicameral" / "config.yaml"

if config_path.exists():
    cfg = yaml.safe_load(config_path.read_text()) or {}
else:
    cfg = {}

current_mode      = cfg.get("mode", "team")
current_guided    = cfg.get("guided", True)
current_telemetry = cfg.get("telemetry", True)
```

---

## Step 2 — Ask all three settings at once via AskUserQuestion

Call `AskUserQuestion` with all three questions in a single call. Mark the
current value as the first (default-selected) option for each question.

```
AskUserQuestion({
  questions: [
    {
      question: "Collaboration mode?",
      header: "Mode",
      multiSelect: false,
      options: [
        { label: "Team",
          description: "Decisions shared via git — append-only event files committed alongside code" },
        { label: "Solo",
          description: "Decisions stored locally only" }
      ]
      // put the current value first
    },
    {
      question: "Interaction intensity?",
      header: "Guided",
      multiSelect: false,
      options: [
        { label: "Guided",
          description: "Blocking hints + git post-commit hook — surfaces decisions after every commit" },
        { label: "Normal",
          description: "Advisory hints only — no git hook" }
      ]
    },
    {
      question: "Anonymous telemetry?",
      header: "Telemetry",
      multiSelect: false,
      options: [
        { label: "On",
          description: "Share anonymous skill timing stats (no code, no decision text, no personal data)" },
        { label: "Off",
          description: "Disable telemetry entirely" }
      ]
    }
  ]
})
```

Put the **current value first** in each option list so it appears pre-selected.

---

## Step 3 — Write updated config.yaml

Map answers back to config values and write:

```python
import subprocess, sys

new_mode      = "team" if answers["Collaboration mode?"] == "Team" else "solo"
new_guided    = answers["Interaction intensity?"] == "Guided"
new_telemetry = answers["Anonymous telemetry?"] == "On"

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

```python
script = (
    "from setup_wizard import _install_skills, _install_claude_hooks"
    + (", _install_git_post_commit_hook" if new_guided else "")
    + "; from pathlib import Path; "
    f"rp = Path(r'{repo_path}'); "
    "n = _install_skills(rp); _install_claude_hooks(rp); "
    + ("_install_git_post_commit_hook(rp); " if new_guided else "")
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
Git post-commit hook: {"installed" if new_guided else "not installed (Normal mode)"}
```

If nothing changed, say: "No changes — config already matches your selections."
