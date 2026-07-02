# Spike: The "LLM Wiki" model as a value-add layer for Bicameral

**Tracks:** #50
**Source:** Karpathy, *LLM Wiki* — https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
**Status:** **Scoping spike, not an implementation.** Issue #50 stands at *lowest priority, not scheduled.* This doc tests whether that ranking still holds and isolates the one genuinely net-new piece.
**Author:** Kevin Knapp (@Knapp-Kevin)

---

## TL;DR

The LLM Wiki pattern is **~80% already built into Bicameral.** Karpathy's three layers (Raw Sources / LLM-owned Wiki / Schema) and three workflows (Ingest / Query / Lint) map almost one-for-one onto components we already ship. The **only net-new piece is the "Wiki" synthesis layer** — agent-maintained entity/concept pages that compound over time — and that is *precisely* the part already flagged on the issue as "an additional output rather than improving the underlying organizational model," i.e. the expensive, token-heavy, lowest-priority part.

**Recommendation:** keep the synthesis-pages layer parked (the original ranking holds), but **adopt the LLM Wiki *lint discipline* now** — it is cheap, maps onto `detect_drift` / `judge_gaps`, and directly improves the core grounding loop rather than bolting on a parallel artifact. That is the "value-adding, not replacement" framing the issue asked for. It also lands the adjacency #428 already drew between these two issues.

---

## §1 — Layer-by-layer mapping

Karpathy's model has three layers. Bicameral already has analogs for all three:

| LLM Wiki layer | Bicameral analog | Gap |
|---|---|---|
| **Raw Sources** (immutable ground truth) | Integration ingest: Linear, Notion, GitHub, Drive, Slack (#337 landed all five — active + passive). | **None.** This is a strength; Bicameral has more source connectors than the gist assumes. |
| **The Wiki** (LLM-owned, compounding markdown: entity pages, concept summaries, cross-links) | *Partial.* The decision **ledger** stores structured records; `META_LEDGER.md` / `SHADOW_GENOME.md` are maintained governance prose. But these are **append-style logs and human/agent-curated docs, not compounding cross-referenced entity pages.** | **This is the real gap — the only net-new layer.** |
| **The Schema** (CLAUDE.md-style rules making the LLM a disciplined maintainer) | `CLAUDE.md`, every `skills/*/SKILL.md`, `ledger/schema.py`. | **None — arguably a Bicameral strength.** We already enforce "tool change requires skill change," symlink integrity, sociable-testing rules. Schema discipline is the hardest part of the gist and we already have it. |

### And the gist's two routing artifacts

| LLM Wiki artifact | Bicameral analog |
|---|---|
| `index.md` (one-line summary per page, fast routing) | `FEATURE_INDEX.md`, `GOVERNANCE_INDEX.md` |
| `log.md` (chronological ingest/query log) | `META_LEDGER.md` (the decision log itself) |

We already have the index and the log. What we don't have is *pages for them to point at* beyond the governance docs.

---

## §2 — Workflow-by-workflow mapping

| LLM Wiki workflow | Bicameral analog | Delta |
|---|---|---|
| **Ingest** — read source, extract takeaways, update 10–15 existing pages with cross-refs, append to log. | `handlers/ingest.py` files decision rows from source items, with universal + eval-hook filtering. | We file *rows*; we don't **fan a single source out across many pages and rewrite cross-references.** That fan-out synthesis is the net-new work. |
| **Query** — search index, read pages, synthesize cited answer, file the answer back as a new page. | `search_decisions`, `bicameral_history`. | We retrieve; we don't **write good answers back into the corpus** (the compounding step). |
| **Lint** — scan for orphan pages, stale claims, contradictions, missing cross-refs, gaps. Scoped (per-source neighbors), not O(n²). | `detect_drift` (decision↔code drift), `bicameral_judge_gaps`, SHADOW_GENOME contradiction tracking, the codegenome continuity service. | **Closest existing analog.** Bicameral's lint is code-grounded; the gist's lint is claim-coherence-grounded. These are complementary, and the gist's *scoped neighbor* discipline is a concrete improvement we can borrow. |

---

## §3 — The net-new delta, isolated

Strip out everything Bicameral already does and exactly one thing remains:

> **An agent that, on each ingest/query, maintains a set of compounding markdown entity/concept pages — synthesizing across sources, rewriting cross-references, and filing answers back — so knowledge accumulates in a curated artifact instead of being re-derived per query.**

This is the "Wiki" layer. Three reasons it stays parked (the original ranking is correct):

1. **Token cost is the dominant cost, and it's recurring.** "One source touches 10–15 pages" means every ingest triggers a multi-page rewrite. For a high-volume source (GitHub/Slack), that is a continuous spend with no hard gate on value.
2. **It produces a parallel artifact, not a better core model.** The decision↔code grounding loop is the differentiated product. A synthesis wiki sits *beside* it and risks drifting from the ledger (now two sources of truth to keep coherent — the very contradiction problem the lint layer is supposed to solve, but self-inflicted).
3. **Hallucination propagation.** The gist concedes "hallucinations in the wiki propagate; lint catches some but not all." A compounding artifact compounds its own errors. For a governance/compliance product, an authoritative-looking synthesized page that's subtly wrong is worse than no page.

---

## §4 — What *is* worth doing now (the value-add subset)

The gist's **lint discipline** is cheap, improves the core loop, and needs no new compounding artifact. Concretely:

- **Scoped contradiction checks, not O(n²).** The gist's practical insight is that full-corpus contradiction scanning is too expensive, so you check *per-source neighbors* on ingest. `judge_gaps` / `detect_drift` could adopt the same scoping: when a decision lands, check it only against its graph neighbors (already cheap to compute via the codegenome edges), not the whole ledger.
- **Stale-claim flagging.** The gist lints for "stale claims." Bicameral has the machinery (`detect_drift` already knows when code moved under a decision); extend the same idea to *time-stale* claims. This dovetails with #428's predicate-grounding work — a behavioral invariant whose predicate hasn't been re-evaluated recently is a stale claim.
- **Orphan / missing cross-ref detection.** Decisions with no edges to code or to other decisions are the ledger's "orphan pages." Surfacing them on the dashboard is a small, code-grounded win.

None of these require an LLM to *write and maintain prose pages*. They borrow the gist's **discipline** without its **cost**.

---

## §5 — Minimal prototype (if/when this leaves the parking lot)

Should usage signals ever justify the synthesis layer, the cheapest honest prototype:

1. Pick **one low-volume, high-value source** (e.g. Notion design docs, not Slack).
2. On ingest, generate **one** synthesized page per *entity* (not 10–15 page fan-out) and store it as a derived, clearly-labelled artifact — never as a decision row, so it can't pollute the ledger or grounding graph.
3. Run the **scoped lint** from §4 over those pages.
4. Measure: do queries that read the synthesized page beat queries that read raw ledger rows, by enough to justify the per-ingest token spend? If not, kill it — the experiment is the deliverable.

This keeps the wiki strictly *additive and disposable* until it earns persistence, which respects the "value-adding approach, not a replacement" framing in the issue.

---

## §6 — Recommendation

| | |
|---|---|
| **Synthesis-pages "Wiki" layer (#50 core)** | **Stay parked.** Ranking confirmed: highest cost, parallel artifact, hallucination risk. Revisit only on a concrete usage signal and start from the §5 minimal prototype. |
| **Lint discipline (§4)** | **Adopt incrementally into the existing loop.** Cheap, code-grounded, improves the core product. Fold the scoped-neighbor check into `judge_gaps`/`detect_drift` (and the #428 predicate-grounding work) rather than tracking it under #50. |
| **Schema / index / log** | **Already done.** Note in the issue that Bicameral's existing schema discipline is the part most projects find hardest. |

Jin's earlier "will find ways to incorporate this" lands on §4: the parts of the LLM Wiki worth incorporating are the lint workflow and the scoped-check discipline, not the wiki artifact itself.
