# Milestone 1 — Vertical Slice

> **Goal:** ask questions over my own notes.
>
> The thinnest end-to-end path that exercises every designed contract:
> **markdown notes folder → connector → chunk + embed → Postgres → hybrid search from a CLI.**
>
> End state: type a question-shaped query and get ranked, cited passages from real notes.

---

# Context

The component design docs ([connectors](../solution_design/components/connectors.md), [memory](../solution_design/components/memory.md), [databases](../solution_design/components/databases.md), [agents](../solution_design/components/agents.md)) pin down the contracts: `NormalizedDocument`, the sync lifecycle, the store interfaces, hybrid search. The remaining open items — exact schemas, chunking strategy — are best resolved by building against real data.

**Explicitly deferred:** Go API server, all agents, user memory, knowledge graph, episodic memory, scheduling (syncs are manually triggered), archive-mode tombstones, PDF and git connectors.

---

# Decision to make first: embedding model

Recommendation: **Ollama running `nomic-embed-text` (768 dims)**.

Rationale: both writers need the same embedder — the Python pipeline embeds chunks, the Go side embeds query text (per databases.md, query and corpus models must match). Ollama exposes one local HTTP endpoint both can call identically, keeps the slice fully local, and makes the embedder a config value (model name + URL) rather than a linked dependency.

Alternative if the Ollama dependency is unwanted: sentence-transformers in Python only, with the search CLI also living in Python for this milestone (Go enters in Milestone 2).

---

# Proposed layout

```
vivy-atlas/
├── docker-compose.yml            # postgres:16 + pgvector extension
├── Makefile                      # up, migrate, sync, search
├── migrations/                   # golang-migrate SQL, source of truth for schema
├── server/                       # Go module
│   ├── cmd/vivy/                 # CLI: `vivy search "..."`
│   └── internal/
│       ├── store/                # DocumentStore impl (pgx)
│       ├── search/               # hybrid query + RRF fusion
│       └── embed/                # Ollama client (query-side embedding)
└── pipeline/                     # Python project (uv)
    ├── connectors/
    │   ├── base.py               # Connector ABC, SourceItem, NormalizedDocument
    │   └── markdown_notes.py
    ├── etl/
    │   ├── chunker.py            # heading-based chunking
    │   └── embedder.py           # Ollama client (corpus-side embedding)
    ├── sync.py                   # sync runner (CLI: `python -m pipeline.sync <instance>`)
    └── db.py                     # psycopg writes
```

---

# Steps

## 1. Infrastructure

* `docker-compose.yml`: Postgres 16 with pgvector. (Ollama assumed installed on host; documented in README.)
* Migrations `0001_ingestion.sql` and `0002_documents.sql` implementing the two schema areas from databases.md that this slice needs:
  * `connector_instances`, `sync_state`, `sync_runs`
  * `documents`, `chunks` (tsvector generated column), `embeddings` (`vector(768)`, PK `(chunk_id, model)`)
  * Indexes: HNSW on `embeddings.vector`, GIN on `chunks.text_search`, B-trees per databases.md
* Writing these migrations **is** the "exact DB schema" design work — reviewed as SQL, not as more markdown.

## 2. Python: connector framework + markdown_notes

* `base.py`: the ABC from connectors.md verbatim — `validate_config`, `estimate`, `discover`, `fetch`, `normalize` — plus `SourceItem` and `NormalizedDocument` dataclasses.
* `markdown_notes.py`: recursive scan with include/exclude globs; fingerprint = mtime+size (hash tiebreak); frontmatter → metadata; first heading or filename → title; wiki/markdown links → `metadata.links` (stored, unused until the graph exists).
* Connector instances are rows in `connector_instances` (config as jsonb); a tiny CLI registers one.

## 3. Python: sync runner

* Implements the lifecycle from connectors.md: load state → discover → diff (new/changed/deleted) → fetch+normalize → write → commit state; per-item failures recorded in `sync_runs.errors`, state committed only after successful writes.
* Idempotency via deterministic document id = hash(instance_id, source_id); `content_hash` unchanged → skip chunk/embed entirely.
* Tombstones: delete mode only (document + chunks + embeddings removed).

## 4. Python: chunk + embed

* Chunker v1: split on headings, merge small sections, cap at ~512 tokens with slight overlap; store `position` for citations. Deliberately naive — the module boundary is the design, the strategy is a placeholder to iterate on with real retrieval results.
* Embedder: batch calls to Ollama; write `(chunk_id, model, model_version, vector)`.
* Document update = transactional chunk replacement (delete old, insert new), per databases.md.

## 5. Go: hybrid search CLI

* `vivy search "query" [--limit N]`:
  * embed query via Ollama
  * two candidate queries — pgvector cosine top-K and `websearch_to_tsquery` FTS top-K, filters inside each query (`status = 'active'`)
  * Reciprocal Rank Fusion in Go
  * output: ranked chunks with title, URI, position — the citation format
* Structured as a `DocumentStore` interface + pgx implementation from the start (the seam memory.md requires), even though only `HybridSearch` gets a real consumer this milestone.

## 6. Stretch (optional, only if the slice feels solid)

* `vivy ask "question"`: top chunks + question → one cloud LLM call → cited answer. First taste of the `answer_question` flow; fine to defer to Milestone 2.

---

# Verification

1. `make up && make migrate` — clean DB with all tables/indexes
2. Register a `markdown_notes` instance pointing at a real notes folder; `estimate()` prints a sane count
3. First sync: `sync_runs` row shows discovered == new, ingested count matches; chunks and embeddings populated
4. **Idempotency**: immediate second sync → 0 new / 0 changed / 0 ingested
5. Edit one note → sync → exactly 1 changed, its chunks replaced; delete one note → sync → exactly 1 tombstone, rows gone
6. `vivy search` with (a) a keyword-ish query (exact term from a note) and (b) a paraphrase query — both return the right passages with correct file citations; this validates both legs of hybrid search
7. Kill the sync mid-run, rerun — completes correctly (state-commit-last semantics)

---

# Milestone exit criteria

VivyAtlas can be pointed at a real notes folder, synced repeatedly as notes change, and queried by meaning or keyword with correct citations — proving `NormalizedDocument`, the sync lifecycle, the documents schema, and hybrid search against real data.

Chunking quality observations from verification step 6 feed the next design iteration.
