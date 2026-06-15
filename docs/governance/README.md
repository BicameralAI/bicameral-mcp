# Governance (tracked)

This directory holds the **tracked, public-facing** governance contract. It declares
*which classes* of artifact are local versus shared, and never reproduces local contents.

| File | Purpose |
|---|---|
| [`BOUNDARY.md`](BOUNDARY.md) | The tracked governance boundary contract — three layers, five invariants, enforced leak rules. |
| [`SIBLINGS.md`](SIBLINGS.md) | The sibling registry — every leak-guarded local tool (Qor-logic, FailSafe, contributor tooling) and the rules that keep it out of commits. |

**Doctrine is shared upstream.** The shared process rules (bic-logic) are owned in
`bicameral-factory` and consumed here — not copied. **Local artifacts are never here**:
any sibling's scratch stays local and gitignored.
