---
name: flagged-skill-fixture
description: Skill fixture WITH unregistered default-behavior claim (lint should flag).
---

# Flagged skill fixture

This skill claims a privacy default in skill text without a backing gate.

## Behavior

By default, the agent extracts only the public keys and discards values.
Branch names are redacted by default.

## When to use

Whenever the operator passes in a config payload.

## Note

The above default claims have no backing gate in this fixture's
imaginary handler. A linter scan should produce findings.
