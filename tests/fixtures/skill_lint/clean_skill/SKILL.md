---
name: clean-skill-fixture
description: Skill fixture with NO default-behavior claims (lint should report zero findings).
---

# Clean skill fixture

This skill describes a tool. It tells the agent how to call the tool.
It explains when to use the tool and when to skip it.

## When to use

- When the operator asks for X.
- When the upstream context provides Y.

## When NOT to use

- When the operator has not authorized the action.
- When the tool's output would be misleading.

## Format

```json
{"name": "tool", "arguments": {"x": "..."}}
```

No default claims; no privacy / security defaults stated; lint passes.
