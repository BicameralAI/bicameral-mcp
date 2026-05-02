# Track Shadow Genome Skill
## Record Failed Approaches to Prevent Repetition

**Skill Name:** qor-meta-track-shadow
**Version:** 1.0
**Purpose:** Implement Qor-logic Shadow Genome for meta-governance - learn from failures

---

## Usage

```
/qor-meta-track-shadow <context> <attempted_solution> <failure_mode>
```

Or invoke in conversation:
> "Let's track this failed approach in the Shadow Genome..."

---

## What This Skill Does

Implements Qor-logic's **Shadow Genome** principle: treating failures as data rather than mistakes.
Records failed approaches with context, failure mode analysis, and lessons learned to prevent
repetition.

---

## Skill Instructions

When this skill is invoked, you should:

### 1. Gather Failure Information

Collect comprehensive details about the failed approach:

**Required Information:**
- **Context:** What were we trying to accomplish?
- **Attempted Solution:** What approach did we try?
- **Failure Mode:** Why did it fail? (Use taxonomy below)
- **Impact:** What was the cost of this failure?
- **Detection:** How/when was the failure identified?

**Optional Information:**
- **Alternatives Considered:** What other approaches were evaluated?
- **Why This Seemed Right:** What made us think this would work?
- **Remediation:** What actually worked instead?

### 2. Classify Failure Mode

Use Qor-logic failure taxonomy:

| Failure Mode | Description | Example |
|--------------|-------------|---------|
| **COMPLEXITY_VIOLATION** | Violated KISS principle | Added ORM when sqlite3 sufficed |
| **PREMATURE_OPTIMIZATION** | Optimized without data | Implemented caching before bottleneck proven |
| **HALLUCINATION** | Claimed capability not validated | "Z3 provides 100% coverage" (unproven) |
| **SECURITY_REGRESSION** | Introduced vulnerability | Broke keyfile integrity validation |
| **SCOPE_CREEP** | Added unplanned features | Built features for hypothetical use cases |
| **TECHNICAL_DEBT** | Quick fix created larger problem | Skipped tests to meet deadline |
| **DEPENDENCY_BLOAT** | Added unnecessary dependencies | 100MB library for one function |
| **ARCHITECTURE_MISMATCH** | Solution incompatible with design | Synchronous code in async system |
| **VALIDATION_GAP** | Insufficient testing/verification | Deployed without integration tests |
| **DOCUMENTATION_DRIFT** | Docs diverged from reality | Spec claimed features not implemented |

### 3. Extract Lesson Learned

Formulate actionable insight:

**Bad Lesson (too vague):**
> "Be more careful with dependencies"

**Good Lesson (actionable):**
> "Before adding dependencies >10MB, require: (1) measured bottleneck, (2) no stdlib alternative, (3) usage in 3+ places"

### 4. Identify Correct Approach

Document what worked instead (if known):

- What solution did we actually use?
- Why was it better?
- What made the difference?
- Can this be generalized?

### 5. Update Shadow Genome File

For structured process events (JSONL), classify attribution per `qor/references/doctrine-shadow-attribution.md` before logging. UPSTREAM events (implicating Qor-logic artifacts) go to `docs/PROCESS_SHADOW_GENOME_UPSTREAM.md`. LOCAL events (consumer codebase or LLM-intrinsic) go to `docs/PROCESS_SHADOW_GENOME.md`. When in doubt, LOCAL.

For narrative failure entries (markdown), append to `docs/SHADOW_GENOME.md`:

```yaml
- id: "SG-{sequential_number}"
  timestamp: "{ISO 8601 timestamp}"
  context: "{What we were building}"
  attempted_solution: "{What we tried}"
  failure_mode: "{From taxonomy above}"
  why_failed: "{Root cause analysis}"
  impact: "{Time lost, technical debt created, etc.}"
  lesson_learned: "{Actionable principle}"
  correct_approach: "{What worked instead}"
  related_entries: ["{Links to similar failures if applicable}"]
  preventability: "{Could this have been caught earlier? How?}"
```

### 6. Check for Patterns

After adding entry, analyze for repeated failure modes:

**If 3+ entries with same failure_mode:**
- Create systematic prevention mechanism
- Add to KISS enforcement rules
- Update validation checklist
- Consider architectural change

**Example:**
> "We've added 3 DEPENDENCY_BLOAT entries. Let's add a CI check that fails on dependencies >50MB without explicit justification."

### 7. Share with Team

Report the failure learning:

```markdown
## Shadow Genome Entry: SG-{number}

**Failure Mode:** {mode}
**Impact:** {impact}

**What We Tried:**
{attempted_solution}

**Why It Failed:**
{why_failed}

**Lesson Learned:**
{lesson_learned}

**Moving Forward:**
{correct_approach}

**Prevention:**
{How to avoid this in future}
```

---

## Examples

### Example 1: Dependency Bloat

```markdown
## Shadow Genome Entry: SG-001

- id: "SG-001"
  timestamp: "2025-12-24T15:30:00Z"
  context: "Week 2 - Implementing database transaction safety"
  attempted_solution: "Use SQLAlchemy ORM for transaction management"
  failure_mode: "COMPLEXITY_VIOLATION"
  why_failed: "Added 5 new dependencies (50MB), introduced complexity in simple use case. Standard sqlite3 library has built-in transaction support."
  impact: "2 hours evaluating, 3 hours testing, 15MB production binary increase"
  lesson_learned: "Check stdlib first before adding dependencies. SQLite transactions are simple: conn.execute('BEGIN'), conn.commit(), conn.rollback()"
  correct_approach: "Manual transaction wrapper using stdlib sqlite3 - 10 lines of code, zero dependencies"
  preventability: "Could have been caught in architecture review with KISS checklist"
```

**Preventive Action Created:**
> Added rule: "Before adding ORM dependency, require proof that raw SQL is insufficient"

### Example 2: Premature Optimization

```markdown
## Shadow Genome Entry: SG-002

- id: "SG-002"
  timestamp: "2025-12-26T10:00:00Z"
  context: "Week 3 - Validation dataset construction"
  attempted_solution: "Implement distributed processing with Celery for dataset generation"
  failure_mode: "PREMATURE_OPTIMIZATION"
  why_failed: "Dataset is 1000 examples, processes in 10 minutes single-threaded. Celery adds Redis dependency, deployment complexity. No measured bottleneck."
  impact: "1 day implementing Celery, 4 hours debugging Redis, added 200MB+ dependencies"
  lesson_learned: "Measure first, optimize second. 10 minutes is acceptable for weekly task. Only parallelize if >1 hour or run frequently."
  correct_approach: "Simple for-loop with tqdm progress bar. Fast enough, zero complexity."
  related_entries: ["SG-001"]
  preventability: "Pre-mortem would have identified: 'What if generation is fast enough without optimization?'"
```

**Preventive Action Created:**
> Added rule: "Performance optimizations require benchmark proving >30min latency or >10 requests/sec load"

### Example 3: Hallucination

```markdown
## Shadow Genome Entry: SG-003

- id: "SG-003"
  timestamp: "2025-12-28T14:00:00Z"
  context: "Week 4 - Tier 3 formal verification design"
  attempted_solution: "Document that PyVeritas provides 100% verification coverage"
  failure_mode: "HALLUCINATION"
  why_failed: "PyVeritas research paper states ~80% accuracy. We claimed 100% without validation. Would have mislead users about system capabilities."
  impact: "Documentation would have been dishonest, violating Divergence Doctrine"
  lesson_learned: "ALWAYS cite exact numbers from source. Never round up. 80% ≠ 100%. Honest limitations build trust."
  correct_approach: "Document 'PyVeritas provides ~80% verification accuracy (per original research), complemented by Z3 for critical paths'"
  preventability: "Sentinel validation caught this before publication. Need to enforce citation accuracy checks."
```

**Preventive Action Created:**
> Added rule: "All quantitative claims must have citation with exact number. No rounding 80→100%."

---

## Shadow Genome File Structure

The Shadow Genome lives at: `docs/SHADOW_GENOME.md`

```yaml
# Q-DNA Development Shadow Genome
# Failed approaches archived for learning

metadata:
  version: "1.0"
  created: "2025-12-24"
  purpose: "Learn from failures, prevent repetition"

statistics:
  total_entries: 3
  most_common_failure: "COMPLEXITY_VIOLATION"
  prevention_rate: "67%" # (patterns detected / total entries)

failures:
  - id: "SG-001"
    # ... (as shown in examples)

  - id: "SG-002"
    # ... (as shown in examples)

patterns_detected:
  - pattern: "Premature dependency addition"
    occurrences: 2
    entries: ["SG-001", "SG-002"]
    prevention_mechanism: "Require measured bottleneck before new dependencies"
    status: "ACTIVE"

  - pattern: "Optimization before measurement"
    occurrences: 1
    entries: ["SG-002"]
    prevention_mechanism: "Benchmark-driven optimization only"
    status: "ACTIVE"

lessons_codified:
  - lesson: "Check stdlib before external dependencies"
    related_entries: ["SG-001"]
    enforced_by: "Architecture review checklist"

  - lesson: "Measure before optimizing"
    related_entries: ["SG-002"]
    enforced_by: "Performance testing required for optimization PRs"

  - lesson: "Cite exact numbers, not rounded approximations"
    related_entries: ["SG-003"]
    enforced_by: "Sentinel citation validation"
```

---

## Success Criteria

This skill succeeds when:

1. ✅ **Zero Repeated Failures:** No failure mode occurs twice without prevention mechanism
2. ✅ **Pattern Detection:** 3+ similar failures trigger systematic prevention
3. ✅ **Actionable Lessons:** Every entry produces concrete, enforceable rule
4. ✅ **Team Learning:** Failures shared and discussed, not hidden
5. ✅ **Continuous Improvement:** Prevention mechanisms reduce failure rate over time

---

## Integration with Qor-logic

This skill implements:

- **Shadow Genome Principle:** "Failure is Data"
- **Fail Forward:** Anticipated failure yields superior architecture
- **Progressive Formalization:** Patterns → Rules → Automated enforcement
- **Divergence Doctrine:** Honest about mistakes, transparent about lessons

---

## When to Use

Invoke this skill:

- ❌ After rejecting a proposed approach
- ❌ When abandoning implemented solution
- ❌ After security vulnerability found in review
- ❌ When scope reduction removes features
- ❌ After timeline slip due to wrong approach
- ❌ When complexity violation detected
- ❌ After any "we should have known better" moment

---

## Output

This skill will:

1. Create/update `docs/SHADOW_GENOME.md`
2. Add structured entry with all required fields
3. Analyze for patterns (3+ similar → prevention)
4. Generate team report
5. Update prevention mechanisms
6. Recommend architectural changes if systemic

**Example Output:**

```markdown
## Shadow Genome Updated

**Entry Added:** SG-004
**Failure Mode:** SECURITY_REGRESSION
**Impact:** Critical - keyfile integrity compromise
**Lesson:** All cryptographic changes require security review + external audit

**Pattern Detected:** 2 security regressions in 4 weeks
**Prevention Created:** Mandatory security review for all crypto PRs

**File Updated:** docs/SHADOW_GENOME.md
**Team Notification:** Posted to #development channel

**Action Required:**
- Update PR template to include security review checkbox
- Add crypto file watch in CI (auto-assign security reviewer)
```

---

## Notes

- Shadow Genome is **append-only** - never delete failures
- Failures are **blameless** - focus on system, not individual
- Lessons must be **actionable** - vague insights don't help
- Prevention must be **enforceable** - manual compliance fails
- **Celebrate failures** that teach us something valuable

---

**Remember:** The goal isn't to avoid all failures. The goal is to never fail the same way twice.

