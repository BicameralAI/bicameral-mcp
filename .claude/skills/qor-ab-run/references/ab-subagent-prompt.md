# A/B subagent prompt template (Phase 39b)

Template consumed by `/qor-ab-run` Step 2. Skill prose splices `{VARIANT_IDENTITY_ACTIVATION_BLOCK}` with the variant content and `{FIXTURES_CONCATENATED}` with the 20 fixture contents before dispatch.

---

```
{VARIANT_IDENTITY_ACTIVATION_BLOCK}

You are reviewing 20 source-code fixtures for defects. The fixtures may contain planted defects from this closed enum:

- razor-overage: function > 40 lines, file > 250 lines, nesting > 3, nested ternaries
- ghost-ui: button/form/interactive element without backend handler; "coming soon" placeholders
- security-l3: hardcoded credentials, placeholder auth, bypassed security checks
- owasp-violation: injection, insecure design, misconfiguration, unsafe deserialization (pickle/eval/shell=True)
- orphan-file: module not reachable from build path
- macro-architecture: cyclic dependencies, mixed domains, layering reversal
- dependency-unjustified: hallucinated or unnecessary package
- schema-migration-missing: breaking schema change without migration path
- specification-drift: prose-code mismatch, undeclared referent
- test-failure: failing assertion, skipped test without rationale
- coverage-gap: missing test coverage for delta
- infrastructure-mismatch: plan claim contradicts actual repository behavior

For EACH fixture, return a JSON record with its defect_id and the findings_categories you detect. Respond with exactly one JSON object in this shape — no prose outside the JSON:

{"trials": [{"defect_id": N, "findings_categories": ["..."]}, {"defect_id": M, "findings_categories": []}, ...]}

Empty findings_categories means the fixture exhibits no detectable defect. Do not invent categories not in the enum above.

Fixtures follow, each prefixed with its defect_id:

{FIXTURES_CONCATENATED}
```
