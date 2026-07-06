# Connectors

## Component Solution Design

---

# Purpose

Connectors are the entry point of all data into VivyAtlas.

Each connector knows how to talk to exactly one type of data source — a notes folder, a git repository, an email account — and how to translate that source's items into a single normalized format that the rest of the system understands.

In the overall data flow, connectors sit at the very beginning:

```
Data Source
     │
     ▼
 Connector            ← this document
     │
     ▼
Python ETL Pipeline   (chunking, entity extraction, embeddings)
     │
     ▼
Memory Layer          (documents, user profile, knowledge graph)
```

---

# Responsibilities

A connector is responsible for:

* Discovering items in its data source
* Detecting what is new, changed, or deleted since the last sync
* Fetching raw content
* Normalizing content into the shared `NormalizedDocument` format
* Attaching provenance and sensitivity metadata
* Reporting sync progress and errors

A connector is explicitly **not** responsible for:

* Chunking
* Entity extraction
* Embedding generation
* Summarization
* Storage decisions

Those belong to the processing pipeline. This separation keeps connectors small and testable.

---

# Design Principles

## User Ownership

Every connector instance is explicitly created and enabled by the user.

The user can at any time:

* Pause or disable a connector
* See exactly what a connector has ingested
* Purge all data originating from a connector

Nothing is ingested silently.

---

## Local First

Phase 1 connectors operate entirely on the local filesystem.

They require:

* No network access
* No authentication
* No third-party services

Cloud connectors (GitHub, email, calendar) are designed for later phases.

---

## Extensible by Design

Adding a new connector must not require changes to the processing pipeline, the memory layer, or the serving layer.

This is achieved by two contracts:

1. A common **connector interface** every connector implements
2. A common **`NormalizedDocument` schema** every connector emits

As long as both contracts are honored, the rest of the system is indifferent to where data came from.

---

## Idempotency and Provenance

Syncing is idempotent: running the same sync twice produces no duplicates.

Every ingested item carries provenance — which connector instance produced it, from which URI, at what time — so any memory in the system can be traced back to its origin, and any origin can be purged.

---

# Connector Framework

## Anatomy of a Connector

Connectors live in the Python processing layer.

Each connector is:

* A Python class implementing the connector interface
* Registered in a **connector registry** under a unique type name (e.g. `markdown_notes`)
* Instantiated per **connector instance** — a user-created configuration of a connector type

The distinction between type and instance matters: a user may have two instances of `markdown_notes`, one pointed at `~/notes` and one at `~/work-notes`, each with its own configuration, sync state, and sensitivity defaults.

---

## Connector Interface

Illustrative contract:

```python
class Connector(ABC):

    @abstractmethod
    def validate_config(self, config: dict) -> list[str]:
        """Check a proposed configuration. Returns human-readable problems, empty if valid."""

    @abstractmethod
    def estimate(self, config: dict) -> SyncEstimate:
        """Dry run: how many items would a first sync ingest? Shown to the user before enabling."""

    @abstractmethod
    def discover(self, config: dict) -> Iterator[SourceItem]:
        """Enumerate all items currently in the source, with change fingerprints. Cheap — no content fetching."""

    @abstractmethod
    def fetch(self, item: SourceItem) -> RawContent:
        """Retrieve the raw content of a single item."""

    @abstractmethod
    def normalize(self, item: SourceItem, raw: RawContent) -> NormalizedDocument:
        """Convert raw content into the shared handoff format."""
```

Key properties of this design:

* `discover()` is cheap and change detection never requires fetching content
* `fetch()` and `normalize()` operate on single items, so failures are isolated per item
* `estimate()` supports informed user consent before a connector is enabled

---

## SourceItem

The unit returned by `discover()`:

| Field | Description |
|---|---|
| `source_id` | Stable identifier within the source (e.g. relative file path, repo name) |
| `uri` | Full address of the item (e.g. `file:///home/user/notes/ai.md`) |
| `fingerprint` | Cheap change signal — content hash |
| `metadata` | Source-specific hints (size, extension, …) |

---

## NormalizedDocument

The single handoff format from every connector to the ETL pipeline:

| Field | Description |
|---|---|
| `id` | Stable, deterministic ID derived from connector instance + `source_id` |
| `connector_instance_id` | Which connector instance produced this document |
| `connector_type` | e.g. `markdown_notes`, `local_git` |
| `uri` | where the content came from |
| `title` | Human-readable title |
| `content` | Extracted text, **or** a file reference for large/binary content |
| `content_type` | e.g. `text/markdown`, `application/pdf` |
| `content_hash` | Hash of the content, used for idempotency downstream |
| `created_at` / `modified_at` | Source timestamps where available |
| `synced_at` | When this version was ingested |
| `sensitivity` | Sensitivity level assigned at the connector boundary |
| `metadata` | Source-specific structured data (frontmatter, commit info, links, …) |

Because `id` is deterministic, re-ingesting the same item is an update, never a duplicate. Because `content_hash` travels with the document, the pipeline can skip reprocessing unchanged content.

---

## Sync State

Each connector instance owns a persistent sync state:

* `last_sync_at` — timestamp of the last successful sync
* `cursor` — optional source-specific position (useful for future cloud sources)
* `fingerprints` — map of `source_id → fingerprint` from the last successful sync

Diffing the current `discover()` output against stored fingerprints yields three sets:

* **New** — present in source, absent in state
* **Changed** — present in both, fingerprint differs
* **Deleted** — present in state, absent in source

Deletions are propagated to the pipeline as **tombstones**, so the memory layer can remove or archive documents whose source no longer exists.

---

## Sync Lifecycle

Syncs are triggered two ways:

* **Scheduled** — per-instance interval (e.g. every 30 minutes)
* **Manual** — the user triggers a sync on demand via the Go API

Real-time watching (inotify, webhooks) is intentionally deferred for now.

A sync run proceeds as follows:

```
Trigger (schedule or manual)
        │
        ▼
Load sync state
        │
        ▼
discover() → current items + fingerprints
        │
        ▼
Diff against state → new / changed / deleted
        │
        ▼
For each new or changed item:
    fetch() → normalize() → emit to pipeline
For each deleted item:
    emit tombstone
        │
        ▼
Emit sync run report
        │
        ▼
Commit sync state
```

**Failure semantics:** sync state is committed only after items are successfully emitted to the pipeline. If a sync is interrupted, the next run re-discovers the same changes and re-emits them — which is safe, because ingestion is idempotent. Per-item fetch or normalize failures are recorded in the run report and skip only that item, never the whole sync.

---

## Instance Lifecycle

A connector instance moves through these states:

```
registered → configured → enabled ⇄ paused → removed
                              │
                          (syncing)
```

* **registered** — connector type is available in the registry
* **configured** — the user has created an instance and its config passed `validate_config()`
* **enabled** — the instance syncs on schedule; the user saw `estimate()` output before enabling
* **paused** — sync suspended; data remains queryable
* **removed** — instance deleted; the user chooses whether ingested data is purged or retained as orphaned provenance

---

## Errors and Observability

Every sync produces a **sync run report**:

* Items discovered / new / changed / deleted
* Items successfully ingested
* Per-item errors with reasons
* Duration

Reports are exposed through the Go API so the user can always answer: *what does the system know, where did it come from, and when did it last update?*

---

# Phase 1 Connectors

Phase 1 targets the local, no-auth sources needed for the Personal Knowledge Base milestone.

## Markdown Notes (`markdown_notes`)

Ingests a user-selected folder of markdown files.

* **Discovery** — recursive scan honoring include/exclude glob patterns; fingerprint is content hash 
* **Normalization**:
  * YAML frontmatter extracted into `metadata` (tags, dates, aliases)
  * First heading (or filename) becomes `title`
  * Wiki-links (`[[...]]`) and markdown links are extracted into `metadata.links` — these are **relationship hints** for the knowledge graph, resolved downstream, not by the connector
* **Config** — root path(s), include/exclude globs, default sensitivity

---

## PDF / Documents (`local_documents`)

Ingests a folder of PDFs and office documents.

* **Discovery** — same folder-scan mechanics as markdown notes
* **Normalization** — the connector does **not** parse PDFs. It emits a `NormalizedDocument` whose `content` is a file reference and whose `content_type` identifies the format; text extraction and OCR live in the processing pipeline, where heavy parsing dependencies belong
* **Metadata** — file size, page count when cheaply available, and a `likely_scanned` hint so the pipeline can route to OCR
* **Config** — root path(s), accepted extensions, max file size, default sensitivity

---

## Local Git Repositories (`local_git`)

Treats each repository as a **project** — a first-class entity for the user model and knowledge graph, not just a pile of files.

* **Discovery** — user-configured list of repo paths (or a parent folder to scan); fingerprint is the current `HEAD` SHA, making change detection a single check
* **Normalization** — per repository, the connector emits:
  * README and files under `docs/` as documents
  * A repository summary document: languages, detected frameworks, commit activity over time — this feeds the user model's *frequently used technologies* and *skills*
  * Recent commit history as structured `metadata` (messages, timestamps — enabling "summarize what I worked on last month")
* **Safety** — default ignore rules exclude `.env`, key material, credentials, and anything matched by common secret patterns; source code beyond docs is excluded in Phase 1
* **Config** — repo paths, branch, history depth, additional ignore patterns

---

# Later Connector Categories

Each of these will receive its own design document. Listed here only to show the framework accommodates them.

## Development (GitHub, GitLab)

Same shape as `local_git` but adds OAuth, API pagination via the `cursor` field, rate limiting, and webhook potential.

## Productivity (Calendar, Email)

High-sensitivity defaults, incremental cursors (IMAP UIDs, sync tokens), and much stricter consent framing — email especially benefits from the `estimate()` step.

## Learning (Browser history, YouTube, Papers)

Often export-file based initially (takeout archives, bookmark exports), which fits the pull model naturally.

The framework's contracts — `discover`/`fetch`/`normalize`, fingerprints, cursors, tombstones — are designed so none of these require framework changes.

---

# Security and Privacy

* **Sensitivity at the boundary** — every document gets a sensitivity level as it enters the system; the connector instance's configured default applies unless the connector can infer better
* **Consent before ingestion** — `estimate()` runs before a connector is first enabled, so the user sees what they are about to ingest
* **Purge on removal** — removing an instance offers full deletion of everything it produced, made possible by provenance on every document
* **Secrets never ingested** — filesystem connectors ship with default exclusion rules for credential files and secret patterns
* **Credentials (future)** — cloud connector tokens will live in a dedicated secret store, never inside connector config or sync state

---

# Future Work

* **Real-time watching** — inotify for local folders, webhooks for cloud sources, as a supplement to scheduled pull
* **Cloud auth framework** — shared OAuth flow, token refresh, and secret storage for all cloud connectors
* **Binary and media handling** — images, audio, video; likely file-reference based with pipeline-side understanding
* **Connector packaging** — out-of-tree connectors as installable plugins once the interface stabilizes

---

# Open Questions

* Should the sync scheduler live in Go (which owns orchestration) or Python (which owns connectors)? Current lean: Go triggers, Python executes.
* How large can `content` be before we always switch to file references? Needs a concrete threshold.