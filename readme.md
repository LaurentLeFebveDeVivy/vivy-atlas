# VivyAtlas

A local-first personal AI memory platform: connectors ingest your data (markdown
notes first; PDFs, git, and more later) into Postgres, queryable by meaning or
keyword with citations. Design docs live in [`docs/`](docs/solution_design/solution_design_high_level.md);
the current implementation plan is [Milestone 1](docs/implementation/milestone_01_vertical_slice.md).

## Prerequisites

- **podman + podman-compose** (or Docker — run make with `COMPOSE="docker compose"`)
- **[golang-migrate](https://github.com/golang-migrate/migrate)** CLI, for schema migrations
- **[Ollama](https://ollama.com) on the host** (not containerized), serving the
  embedding model used by both the ingestion pipeline and query-side search:

  ```bash
  ollama pull nomic-embed-text
  ```

- optional: `postgresql-client` for `make inspect-pg`

## Quickstart

```bash
make up          # start Postgres 16 + pgvector (data persists in a volume)
make migrate     # apply schema migrations (see migrations/README.md)
make inspect-pg  # psql into the DB (user/password: vivy)
make down        # stop the container
```

Postgres listens on `127.0.0.1:5433` (non-default port to avoid clashing with a
host Postgres).
