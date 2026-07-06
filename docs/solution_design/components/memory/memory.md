# Memory Layer

## Component Solution Design

---

# Purpose

The memory layer is the shared substrate of VivyAtlas.

Every agent reads from it, and everything the system learns is written into it. Agents do not keep private state; what one agent learns, every agent can use. 

This document describes the **logical** design of memory: what kinds of memories exist, what they contain, how they are written, retrieved, updated, and deleted.

The **physical** design — how these memories are stored, indexed, and backed up — is described in [databases.md](databases.md). The two documents are deliberately paired: this one defines the contracts, that one implements them.

```
                Agents
   (Retrieval, User Model, Research, …)
                  │
          read / write via Go API
                  │
                  ▼
          ┌───────────────────────────────┐
          │         Memory Layer          │  
          │                               │
          │  Document   User   Knowledge  │
          │  Memory    Memory    Graph    │
          │              +                │
          │       Episodic Memory         │
          └───────────────────────────────┘
                  ▲
                  │
        Python ETL Pipeline
                  ▲
                  │
             Connectors
```

---

# Design Principles

## Not All Memory Is Equal

A PDF, a belief about the user's goals, a relationship between two technologies, and last week's conversation are fundamentally different kinds of knowledge. They have different lifecycles, different trust levels, and different retrieval patterns.

The memory layer therefore consists of **four categories**, each with its own contract:

| Category | Stores | Answers |
|---|---|---|
| Document Memory | Raw ingested content | "What do my notes say about X?" |
| User Memory | Durable beliefs about the user | "What are my current goals?" |
| Structured Knowledge | Entities and relationships | "Which projects use technology X?" |
| Episodic Memory | Conversations and agent activity | "What did we discuss last week?" |

---

## Memories Evolve, They Don't Just Accumulate

A traditional RAG system only ever appends. VivyAtlas memories are updated, superseded, contradicted, and confirmed over time. User Memory in particular is a set of *current beliefs with history*, not a log.

---

## Provenance on Everything

Every memory, of every category, can answer: *where did this come from?*

Documents trace to a connector instance and URI. User memories trace to the evidence that produced them. Relationships trace to the documents they were extracted from. Nothing exists in memory without an origin, which is what makes inspection, correction, and purging possible.

---

## User Ownership

The user can view, edit, delete, and export every memory in every category. No category is internal-only. This principle constrains the design throughout — it is why evidence links, provenance keys, and cascade rules exist.

---

## Logical / Physical Separation

Agents interact with memory exclusively through **store interfaces** (defined at the end of this document). No agent knows or cares that the initial implementation is Postgres.

---

# Document Memory

Stores everything ingested through connectors.

## Structure

Document memory has three levels:

```
Document  ──1:N──▶  Chunk  ──1:1──▶  Embedding
```

* **Document** — one ingested item, mirroring the `NormalizedDocument` handoff from connectors (see [connectors.md](connectors.md)): provenance, title, content, content hash, timestamps, sensitivity, source metadata
* **Chunk** — a retrieval-sized piece of a document, produced by the processing pipeline, carrying its position and its document's sensitivity
* **Embedding** — the vector representation of a chunk, tagged with the model that produced it

## Behavior

* Ingestion is **idempotent**: documents are keyed by the deterministic ID from the connector; re-ingestion is an update
* If an incoming document's `content_hash` is unchanged, chunking and embedding are **skipped entirely**
* When a document changes, its chunks and embeddings are replaced, not appended
* Every chunk retains enough position information to support **citations** — an answer can always point at the exact source passage

---

# User Memory

Stores durable beliefs about the user.

## The Memory Record Envelope

Every user memory carries the same envelope:

| Field | Description |
|---|---|
| `value` | The belief itself (e.g. "wants to deepen AI engineering skills") |
| `kind` | goal, interest, preference, skill, project, trait |
| `origin` | `explicit` (user stated it) or `inferred` (system concluded it) |
| `confidence` | Current confidence score, updated as evidence accumulates |
| `evidence` | Links to the chunks, episodes, or statements that support it |
| `status` | proposed, active, superseded, rejected, archived |
| `created_at` / `updated_at` | Lifecycle timestamps |
| `last_confirmed_at` | When evidence last supported this belief — drives staleness |
| `sensitivity` | Sensitivity level, same scale used at the connector boundary |

## Explicit vs Inferred

The two origins have different trust paths:

* **Explicit** statements ("I want to improve my AI engineering skills") become active memories directly, with high confidence
* **Inferred** beliefs ("prefers infrastructure-focused projects") must pass through the proposal workflow below

## Memory Proposal Workflow

Inferred memories are never silently created:

```
Observation (episode, document, behavior)
        │
        ▼
Proposal created (status: proposed)
        │
        ▼
Confidence assessment by User Model Agent
        │
   ┌────┴──────────────┐
   ▼                   ▼
confidence ≥ threshold    below threshold
   │                   │
   ▼                   ▼
user approval     remains proposed,
   │              accumulates evidence
   ▼
status: active
```

* The approval step is configurable: auto-accept above a threshold, or require explicit user review — sensitive kinds (e.g. traits) should default to requiring review
* Rejected proposals are kept (status: `rejected`) so the same wrong inference is not re-proposed forever

## Conflict and Evolution

When new evidence contradicts an active memory, the User Model Agent does not overwrite it. It either:

* Lowers confidence and records the contradicting evidence, or
* Creates a successor memory and marks the old one `superseded`

Superseded memories are retained: "the user was focused on frontend in 2024, backend since 2025" is itself knowledge.

## Staleness

`last_confirmed_at` lets confidence decay for beliefs that stop being supported. Interests fade; the system should notice.

---

# Structured Knowledge

Stores relationships that vector search cannot express: *this project uses that technology*, *this paper relates to that topic*.

## Structure

A property graph, kept deliberately simple:

* **Entity** — typed node: technology, project, topic, paper, person, organization
* **Relationship** — typed, directed edge between two entities: `uses`, `depends_on`, `relates_to`, `authored_by`, `part_of`
* Both carry `confidence` and `evidence` links back to the documents they were extracted from

## Population

The processing pipeline emits **extraction candidates** during ingestion (entities and relations found in documents). Candidates are merged into the graph with confidence scoring — repeated extraction across documents raises confidence.

**Entity resolution** keeps the graph canonical: "Postgres", "PostgreSQL", and "postgresql" resolve to one entity via an alias mechanism. Resolution is conservative; a wrong merge is worse than a duplicate node.

## Role in Retrieval

The graph is not queried instead of vector search, but **alongside and after it**: retrieval can expand from matched documents to their entities, then to related entities, then back to documents mentioning those. This answers relational questions ("which projects demonstrate backend experience?") that similarity alone cannot.

---

# Episodic Memory

Stores what happened: conversations with the user and the activity of agents.

## Structure

* **Conversation** — a session with the user, containing **messages**
* **Agent run** — a unit of agent activity (a research task, a sync-triggered analysis), with its inputs, outputs, and the memories it read or wrote

## Purpose

Episodic memory serves three consumers:

1. **The user** — "what did we discuss last week?", full interaction history
2. **The User Model Agent** — episodes are its primary raw material and its evidence trail; a user memory's `evidence` frequently points into episodes
3. **Observability** — agent runs make system behavior inspectable and debuggable

## Retention

Episodes are kept indefinitely by default, but the user can prune by age or delete individual conversations. Deleting an episode weakens — but does not silently delete — user memories that cite it as evidence; affected memories are flagged for review.

---

# Write Paths

There are exactly two paths into memory, with disjoint ownership:

```
Ingestion path (bulk, offline)          Interaction path (online)

Connectors                                User ⇄ Agents
    │                                          │
    ▼                                          ▼
Python ETL Pipeline                        Go API
    │                                          │
    ▼                                          ▼
Document Memory                          User Memory
Graph candidates                         Episodic Memory
                                         Graph (resolution decisions)
```

| Category | Written by | Via |
|---|---|---|
| Document Memory | Python pipeline | Direct bulk writes |
| Structured Knowledge (candidates) | Python pipeline | Direct bulk writes |
| Structured Knowledge (resolution, curation) | Go (User Model Agent, user edits) | Go API |
| User Memory | Go (User Model Agent, user edits) | Go API |
| Episodic Memory | Go (serving layer) | Go API |

The split follows the high-level architecture: Python owns computationally heavy offline ingestion, Go owns everything the user or agents touch online.

---

# Read Path

All reads go through the Go API. The central operation is **retrieval**, used by the Retrieval Agent and available to all others.

## Retrieval Contract

Illustrative, not final:

```go
type MemoryQuery struct {
    Text            string            // the information need
    Categories      []MemoryCategory  // which memories to search (default: all)
    MaxSensitivity  SensitivityLevel  // ceiling for this request's purpose
    TimeRange       *TimeRange        // optional recency scoping
    GraphExpansion  bool              // expand results through the knowledge graph
    Limit           int
}

type MemoryResult struct {
    Items     []MemoryItem  // chunks, user memories, entities, episodes
    Citations []Citation    // every item traceable to its source passage
}
```

## Hybrid Search

Document retrieval combines two candidate streams — **vector similarity** and **keyword/full-text** — fused into a single ranking (implementation in [databases.md](databases.md)). Neither alone is sufficient: vectors miss exact identifiers, keywords miss paraphrase.

On top of the fused ranking, retrieval applies:

* **Recency weighting** where the query implies it
* **Graph expansion** when enabled — matched documents pull in related entities and their documents
* **Sensitivity filtering** — items above the query's `MaxSensitivity` are excluded before ranking, not after

## User Context Assembly

A second read operation, distinct from search: **assemble the user context** — the active, relevant slice of User Memory that personalizes an agent's behavior (current goals, preferences, relevant skills). This is a curated projection, not a search result, and it is how the User Model Agent "improves every other agent" without answering questions itself.

## Citations Are Mandatory

Any memory item that reaches an LLM prompt carries its citation. Answers about the user's own data must always be traceable to that data.

---

# Deletion

Three distinct triggers, three distinct semantics:

| Trigger | Semantics |
|---|---|
| **Source tombstone** (connector reports item deleted at source) | Configurable per connector instance: **delete** (document, chunks, embeddings removed; derived memories keep evidence links marked as deleted-source) or **archive** (document hidden from retrieval, retained for provenance) |
| **User deletes a memory** | Always a hard delete of that memory. Derived memories citing it as evidence are flagged for review, not silently removed |
| **Connector purge** (instance removed with purge) | Hard cascade of everything keyed to the `connector_instance_id`: documents, chunks, embeddings, extraction-derived graph edges. User memories survive but lose the purged evidence and are flagged |

The common rule: **deleting raw data never silently deletes derived beliefs** — the user is shown what was learned from the deleted data and decides.

---

# Store Interfaces

Agents and the serving layer depend only on these; the Postgres implementation lives behind them (see [databases.md](databases.md)).

```go
type DocumentStore interface {
    UpsertDocument(doc Document) error
    ApplyTombstone(docID string, mode TombstoneMode) error
    HybridSearch(q MemoryQuery) ([]ScoredChunk, error)
    PurgeByConnectorInstance(id string) error
}

type UserMemoryStore interface {
    Propose(m UserMemory) error
    Accept(id string) error
    Reject(id string) error
    Supersede(oldID string, successor UserMemory) error
    ActiveContext(filter ContextFilter) ([]UserMemory, error)
}

type GraphStore interface {
    MergeCandidates(c []ExtractionCandidate) error
    ResolveAlias(alias string, entityID string) error
    Expand(seed []EntityRef, depth int) (Subgraph, error)
}

type EpisodeStore interface {
    AppendMessage(convID string, m Message) error
    RecordAgentRun(run AgentRun) error
    Search(q MemoryQuery) ([]Episode, error)
}
```

Interfaces are intentionally coarse: one interface per memory category, matching the four-category model exactly.

---

# Security and Privacy

* **Sensitivity is inherited, never dropped** — a chunk is at least as sensitive as its document; a user memory at least as sensitive as its most sensitive evidence
* **Retrieval enforces sensitivity** as a pre-ranking filter, so high-sensitivity content cannot leak into low-sensitivity contexts (e.g. a future proactive notification)
* **Everything is exportable** — all four categories serialize to a user-readable export format (see [databases.md](databases.md))
* **Evidence links are inspectable** — the user can always ask *why does the system believe this about me?* and get the actual sources

---

# Open Questions

* **Confidence mechanics** — is confidence a probability, a score, or a tier? How exactly does evidence accumulation raise it and staleness decay it? Needs a concrete model before Phase 2.
* **Chunking strategy** — fixed-size, structural (headings), or semantic? Owned by the pipeline, but affects citation granularity here.
* **User context size** — how much User Memory goes into every agent's context by default? Needs experimentation.
* **Graph write contention** — pipeline merges candidates while the User Model Agent curates; the resolution rules for concurrent edits need definition when the graph becomes active.
