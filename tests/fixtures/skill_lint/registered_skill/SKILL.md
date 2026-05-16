---
name: registered-skill-fixture
description: Skill fixture with default-behavior claim that IS backed by a registered gate.
---

# Registered skill fixture

By default, the agent extracts only the public keys and discards values.

## Backing gate

See the per-test registry passed to the lint: the gate entry for this
skill points to `handlers/fixture.py::_extract_keys_only`. The lint
should match the SKILL.md text against the registered pattern and NOT
emit a finding.
