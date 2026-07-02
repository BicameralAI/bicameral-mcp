## Summary

<!-- Brief description of the change and motivation. -->

## Backward Compatibility

- [ ] **Data migration**: If this PR introduces or changes business logic that affects existing ledger data, a corresponding schema migration backfills or transforms legacy rows to match the new behavior.
- [ ] **Wire format**: Existing MCP tool responses remain backward-compatible (new fields are additive, not breaking).
- [ ] **Skill contract**: Any tool behavior change ships with a matching `skills/*/SKILL.md` update in the same commit.

## Testing

- [ ] New or updated tests cover the change (sociable tests preferred per `CLAUDE.md`).
- [ ] `ruff check . && ruff format --check .` passes locally.
- [ ] CI regression suite passes (ubuntu + windows).

## Review Control

- [ ] This PR has, or will receive before merge, approval from a named human reviewer. AI agents and automation identities do not satisfy the approval requirement.

## Notes

<!-- Anything reviewers should know: tradeoffs, follow-ups, known limitations. -->
