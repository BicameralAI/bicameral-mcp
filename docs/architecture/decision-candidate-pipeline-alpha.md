# Alpha Decision-Candidate Pipeline

**Status:** Accepted direction, pending component RFQs and implementation  
**Decision date:** 2026-07-16  
**Participants:** Jin Kuan, Kevin Knapp

## Purpose

Bicameral's alpha must ingest broad collaboration data, identify decision-relevant candidates, assign configurable relevance, and surface those candidates to users without confusing probabilistic suggestions with ratified decision truth.

The pipeline is intentionally modular. Each stage must expose inspectable inputs, outputs, reasons, and metrics so it can be tested independently before end-to-end integration.

## Pipeline

### 1. Injection

Injection accepts raw source material, normalizes it, and records stable provenance metadata.

Initial dogfooding targets GitHub issues. Later sources may include meeting transcripts, pull requests, Slack threads, PRDs, and other collaboration artifacts.

Metadata is captured at ingest but does not permanently determine rank or inclusion. This allows policy and weighting changes without re-ingesting the source.

Expected metadata includes:

- source type and stable source identifier
- source and ingest timestamps
- contributors, participants, and known roles
- repository, issue, pull request, commit, and external links
- extraction and transformation provenance
- candidate lifecycle and promotion state

### 2. Candidate extraction

Candidate extraction applies deterministic, heuristic-first filtration before any optional LLM processing.

The alpha uses a **fail-open** posture. An input remains eligible unless a rule explicitly identifies it as noise or excluded content. This minimizes silent recall loss while still reducing unnecessary model calls.

Every result must include:

- include or exclude outcome
- matching rule
- human-readable reason
- preserved source provenance
- rule and configuration version

### 3. Weighting and decay

Weighting happens after ingestion and filtration. It must remain configurable and replayable.

The weighting RFQ will compare deterministic scoring, LLM enrichment, constrained LLM scoring, graph-native approaches, and hybrid models. The selected alpha approach must expose each factor that contributed to a candidate's score.

Unpromoted transient candidates may decay over time. Half-life may vary by source type, candidate class, or lifecycle state.

Decay must never weaken, mutate, or delete ratified persistent decisions.

### 4. Routing and retrieval

Routing determines which candidates are presented to a user or MCP client. Retrieval may use LLM assistance during alpha, provided that:

- source provenance is always returned
- uncertainty is visible
- candidates are never presented as ratified truth
- retrieval cannot alter persistent decision state
- persistent decision and code-grounding paths remain deterministic

The retrieval RFQ will compare vector, graph, lexical, hybrid, and bounded-LLM approaches.

## Memory boundary

Bicameral distinguishes two memory classes:

### Transient memory

Decision candidates are provisional. Their extraction, weighting, and retrieval may be probabilistic. They may decay while unpromoted.

### Persistent memory

Ratified decisions, stable identity, provenance, code bindings, and drift behavior require deterministic semantics. Probabilistic retrieval may help locate persistent records, but it cannot redefine them.

## Storage direction

The alpha should preserve distinct logical layers for:

1. raw or normalized ingest records
2. internal candidate-processing state
3. shaped user-facing output
4. promoted persistent decisions

A knowledge graph may support internal processing, but the final storage model remains subject to the weighting and storage RFQ.

## Evaluation

Decision-candidate retrieval will be evaluated against representative real-world samples using precision and recall.

Evaluation must distinguish:

- relevant candidates that were missed
- irrelevant candidates that were surfaced
- correct candidates with incorrect provenance
- retrieval failures
- persistent code-grounding failures

Candidate retrieval metrics are separate from code-grounding accuracy and drift-detection metrics.

## Alpha acceptance principles

- GitHub issues work as the first dogfooding source.
- Each stage can run and be tested independently.
- Intermediate representations are inspectable.
- Policy configuration is separated from ingestion.
- Exclusion and ranking decisions are explainable.
- Pipeline transformations preserve provenance.
- Policy changes support replay or recomputation.
- Candidate decay cannot affect persistent decisions.
- Retrieval exposes uncertainty and can abstain.

## Workstreams

- **BIC-162:** Specify decision-candidate metadata, weighting inputs, and decay semantics.
- **BIC-163:** Build bot-side injection filtration and exclusion-policy module.
- **BIC-164:** RFQ for weighting, storage, and LLM involvement.
- **BIC-165:** RFQ for surfacing decision candidates to MCP users.

Canonical collaborative note: https://app.notion.com/p/39f2a51619c481cfa871c049b46433ea

## Deferred topics

- deterministic retrieval optimization after alpha
- final storage selection
- source-specific half-life policies
- UI routing policy details
- promotion and ratification workflow refinements
- additional ingestion sources beyond GitHub issues
