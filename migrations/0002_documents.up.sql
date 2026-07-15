-- Enables vector column type, distance operators (cosine, L2, inner-product), index access methods (e.g., HNSW)
CREATE EXTENSION IF NOT EXISTS vector;

-- One row per NormalizedDocument: the connector handoff, persisted.
-- id is deterministic — hash(connector_instance_id, source_id), computed by the pipeline — so re-syncing the same item always hits the same row (idempotency).
-- content vs content_ref: exactly one is set — inline text, or a file reference (for payloads too large to inline).
CREATE TABLE documents (
    id                      uuid PRIMARY KEY,
    connector_instance_id   uuid NOT NULL REFERENCES connector_instances(id) ON DELETE CASCADE,
    connector_type          text NOT NULL,
    uri                     text NOT NULL,
    title                   text,
    content                 text,
    content_ref             text,
    content_type            text NOT NULL,
    content_hash            text NOT NULL,
    created_at              timestamptz,
    modified_at             timestamptz,
    synced_at               timestamptz NOT NULL DEFAULT now(),
    sensitivity             text NOT NULL,
    metadata                jsonb NOT NULL DEFAULT '{}',
    status                  text NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'archived', 'tombstoned')),
    CHECK (content IS NOT NULL or content_ref IS NOT NULL)
);

-- Retrieval units. 
-- A document's chunks are replaced transactionally on update (delete old, insert new), so chunk ids are NOT stable across re-ingestion.
-- position: ordering within the document; the citation anchor.
-- text_search: generated tsvector — computed by Postgres on write, never by us. Required for efficient full-text search
CREATE TABLE chunks (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id             uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position                integer NOT NULL,
    text                    text NOT NULL,
    text_search             tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    sensitivity             text NOT NULL,
    UNIQUE(document_id, position)
);

-- One row per (chunk, embedding model): vectors are tied to the model that produced them, so a model migration writes new rows instead of overwriting.
-- Dimension is fixed to nomic-embed-text (768); 
-- A model with other dimensions needs a schema change — acceptable until a second model actually exists.
CREATE TABLE embeddings (
    chunk_id                uuid NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    model                   text NOT NULL,
    model_version           text NOT NULL,
    vector                  vector(768) NOT NULL,
    PRIMARY KEY (chunk_id, model)
);

-- The two legs of hybrid search, plus provenance lookups (per databases.md).

-- Hierarchical Navigable Small World indexing for semantic search (Note: This is ANN, not exact)
CREATE INDEX embeddings_vector_hnsw_idx ON embeddings USING hnsw (vector vector_cosine_ops);

-- Build inverted index for keyword search 
CREATE INDEX chunks_text_search_gin_idx ON chunks USING gin (text_search);

CREATE INDEX documents_instance_idx ON documents (connector_instance_id);

CREATE INDEX chunks_document_idx ON chunks (document_id);