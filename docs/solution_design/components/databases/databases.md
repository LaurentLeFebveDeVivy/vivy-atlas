# Databases

## Component Solution Design

---

# Purpose

This document describes the **physical** realization of the memory layer: which database technology stores each memory category, the schema areas, indexing, hybrid search mechanics, backup, export, and purge.

The **logical** design — what the memory categories are and the store interfaces agents use — is defined in [memory.md](memory.md). This document implements those contracts.

Scope: the local-first phases (1–3). Scaling and cloud synchronization are future work.

---

# Decision: A Single PostgreSQL Instance

All four memory categories live in **one local PostgreSQL instance**, using:

* Relational tables — documents, user memory, graph, episodes, ingestion state
* **pgvector** — embedding storage and similarity search
* Native **full-text search** (`tsvector`) — keyword retrieval

## Rationale

* **Two concurrent writers by design.** The Go API and the Python pipeline both write to storage. Postgres handles concurrent access natively
* **One system to run, back up, export, and reason about.** Local-first means the user operates this. A single `pg_dump` is the entire backup required.
* **Transactions across categories.** A document update, its chunk replacement, and its embedding replacement commit atomically. In a polyglot setup this consistency must be built by hand.
* **Replaceability is preserved.** Postgres sits behind the store interfaces; nothing about this choice leaks into agents.

## Rejected Alternatives — and When to Revisit

| Alternative | Why not now | Revisit when |
|---|---|---|
| SQLite (+ sqlite-vec, FTS5) | Single-writer model conflicts with concurrent Go + Python writers; would force all writes through one process | The architecture ever collapses into a single process |
| Dedicated vector DB (Qdrant, Chroma) | Second system to operate before scale demands it | Vector search latency degrades past personal scale, or filtering needs outgrow pgvector |
| Dedicated graph DB (Neo4j) | The graph starts small and shallow; recursive SQL covers Phase 1–3 traversal | Traversals routinely exceed 2–3 hops or graph queries dominate retrieval latency |

Each revisit is an implementation swap behind one store interface (`DocumentStore.HybridSearch`, `GraphStore`), not a redesign.

---

# Access Topology

```
        Go API                    Python ETL Pipeline
          │                              │
          │ online reads/writes          │ bulk ingestion writes
          │                              │
          ▼                              ▼
   ┌─────────────────────────────────────────────┐
   │              PostgreSQL                     │
   │                                             │
   │  ingestion │ documents │ user_memory        │
   │            │           │ graph │ episodic   │
   └─────────────────────────────────────────────┘
```

Both processes connect directly. Ownership is partitioned by schema area, mirroring the write-path table in memory.md:

| Schema area | Writes | Reads |
|---|---|---|
| `ingestion` | Python (sync state), Go (instance config) | Both |
| `documents` | Python | Go |
| `user_memory` | Go | Go |
| `graph` | Python (candidates), Go (resolution, curation) | Go |
| `episodic` | Go | Go |

---

# Schema Areas

Schemas below are illustrative.

## `ingestion` — connector bookkeeping

Mirrors the framework in [connectors.md](connectors.md).

```sql
connector_instances (
    id, connector_type, config jsonb, state,        -- lifecycle state
    default_sensitivity, tombstone_mode,             -- delete | archive
    schedule_interval, created_at
)

sync_state (
    connector_instance_id, source_id, fingerprint,   -- the diff basis
    PRIMARY KEY (connector_instance_id, source_id)
)

sync_runs (
    id, connector_instance_id, started_at, finished_at,
    discovered, new, changed, deleted, ingested,
    errors jsonb                                     -- per-item failures
)
```

## `documents` — Document Memory

Every field of the `NormalizedDocument` handoff has a column here.

```sql
documents (
    id,                                  -- deterministic, from connector
    connector_instance_id, connector_type, uri,
    title, content, content_ref,         -- inline text OR file reference
    content_type, content_hash,
    created_at, modified_at, synced_at,
    sensitivity, metadata jsonb,
    status                               -- active | archived | tombstoned
)

chunks (
    id, document_id, position,           -- ordering + citation anchor
    text,
    text_search tsvector GENERATED,      -- keyword retrieval
    sensitivity                          -- inherited from document
)

embeddings (
    chunk_id, model, model_version,      -- which embedder produced this
    vector vector(N)
)
```


## `user_memory` — User Memory

The record envelope from memory.md, plus its evidence links.

```sql
user_memories (
    id, value, kind,                     -- goal | interest | preference | skill | project | trait
    origin,                              -- explicit | inferred
    confidence, status,                  -- proposed | active | superseded | rejected | archived
    superseded_by,                       -- successor memory, if any
    sensitivity,
    created_at, updated_at, last_confirmed_at
)

memory_evidence (
    user_memory_id,
    evidence_type,                       -- chunk | message | agent_run | user_statement
    evidence_id,
    stance,                              -- supports | contradicts
    noted_at
)
```

* Evidence is polymorphic by design: user memories cite document chunks and episodes alike
* When evidence is deleted or purged, rows here are marked rather than removed, which is what powers the "flagged for review" behavior in memory.md
* Proposals are not a separate table — `status: proposed` keeps one lifecycle in one place

## `graph` — Structured Knowledge

```sql
entities (
    id, entity_type,                     -- technology | project | topic | paper | person | organization
    canonical_name, confidence, created_at
)

entity_aliases (
    alias, entity_id                     -- "PostgreSQL" ← "Postgres", "postgresql"
)

relationships (
    id, from_entity, to_entity,
    rel_type,                            -- uses | depends_on | relates_to | authored_by | part_of
    confidence
)

relationship_evidence (
    relationship_id, document_id, extracted_at
)
```

* Graph traversal (`GraphStore.Expand`) is a recursive CTE with a depth limit — sufficient for the 1–3 hop expansions retrieval performs
* Repeated extraction of the same relationship from new documents adds evidence rows and raises confidence, matching the merge behavior in memory.md
* When documents are purged, `relationship_evidence` cascades; relationships left with no evidence are candidates for removal

## `episodic` — Episodic Memory

```sql
conversations (
    id, started_at, title, metadata jsonb
)

messages (
    id, conversation_id, role, content, created_at,
    text_search tsvector GENERATED       -- episodes are searchable too
)

agent_runs (
    id, agent_type, trigger,             -- user_request | schedule | sync
    conversation_id,                     -- nullable: not all runs are conversational
    input, output, started_at, finished_at,
    memories_read jsonb, memories_written jsonb   -- the observability trail
)
```

---

# Indexing and Hybrid Search

| Index | On | Serves |
|---|---|---|
| HNSW (pgvector) | `embeddings.vector` | Vector similarity |
| GIN | `chunks.text_search` | Keyword / full-text |
| GIN | `messages.text_search` | Episode search |
| B-tree | `documents.connector_instance_id` | Purge, provenance queries |
| B-tree | `sync_state (connector_instance_id, source_id)` | Sync diffing |

## Hybrid Query Pattern

`DocumentStore.HybridSearch` runs two candidate queries and fuses them:

```
Query text
   ├──▶ embed → pgvector HNSW top-K        (semantic candidates)
   └──▶ websearch_to_tsquery → FTS top-K   (keyword candidates)
                    │
                    ▼
     Reciprocal Rank Fusion (in Go)
                    │
                    ▼
   sensitivity / time / status filters applied as
   WHERE clauses INSIDE both candidate queries
                    │
                    ▼
            ranked chunks + citations
```

Filtering happens inside the candidate queries.

Both candidate queries are single SQL statements; fusion is a few lines of Go. No search infrastructure beyond Postgres.

---

# Embedding Versioning

Embeddings are tied to the model that produced them (`model`, `model_version` columns). Changing the embedding model is a first-class operation, per the extensibility principle:

1. New model is configured; new ingestion writes new-model embeddings
2. A **backfill job** (Python, batch) re-embeds existing chunks — resumable, since `(chunk_id, model)` shows exactly what remains
3. Search uses one model at a time (query and corpus must match); the switch flips when backfill completes
4. Old-model embeddings are dropped after the switch

The corpus never needs re-chunking or re-ingestion — content and vectors are deliberately separate tables.

---

# Backup, Export, and Purge

## Backup

`pg_dump` on a schedule, retained locally. One command captures every memory category, all sync state, and all history.

## Export (user-facing)

Backup is for restoring; export is for **user ownership** — leaving with your data in a form you can read:

* Documents: original content + provenance as files, metadata as JSON
* User Memory: JSON including full envelope, evidence references, and superseded history
* Structured Knowledge: entities and relationships as JSON (importable elsewhere)
* Episodic: conversations as markdown transcripts, agent runs as JSON

## Purge

`DocumentStore.PurgeByConnectorInstance` is implemented as a cascade keyed on `connector_instance_id`:

```
documents → chunks → embeddings          (removed)
relationship_evidence via documents      (removed; orphaned relationships flagged)
memory_evidence via chunks               (marked evidence-purged; memories flagged)
sync_state, sync_runs                    (removed)
```

User memories are never deleted by a purge, only flagged for the user's review.

---

# Replaceability

Every store interface has exactly one Postgres-backed implementation, and each can be swapped independently:

| Interface | Today | Future swap | What changes |
|---|---|---|---|
| `DocumentStore.HybridSearch` | pgvector + FTS + RRF | Qdrant/dedicated vector DB for the vector leg | One candidate-query implementation |
| `GraphStore` | tables + recursive CTE | Neo4j | Graph queries only; entities keep their IDs |
| `UserMemoryStore`, `EpisodeStore` | tables | unlikely to move | — |

The migration cost of the single-Postgres decision is deliberately low: data is relational and exportable, and no agent code references Postgres.

---

# Open Questions

* **Postgres distribution** — system package, Docker container, or embedded (e.g. a managed local binary)? Affects the installation story for a local-first product.
* **File reference storage** — `content_ref` points at original files; do we copy originals into a managed blob directory (survives source deletion) or reference in place (no duplication)? Interacts with tombstone `archive` mode.
* **Embedding dimensionality** — `vector(N)` is fixed per column; changing dimensions requires a column migration, not just a backfill. Worth choosing the first model carefully.
* **FTS language configuration** — Postgres FTS is language-aware; a single-language default is fine to start, but mixed-language notes will need per-document configuration.
