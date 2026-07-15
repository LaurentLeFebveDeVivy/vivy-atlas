# Migrations

This directory is the **source of truth for the database schema**. Every table,
index, and constraint in VivyAtlas is defined here as plain SQL, applied by
[golang-migrate](https://github.com/golang-migrate/migrate). Migrations define
*structure only* — data is written by the application (sync runner, CLIs), never
here.

## File format

Migrations come in numbered up/down pairs:

```
0001_ingestion.up.sql      # apply: create the ingestion bookkeeping tables
0001_ingestion.down.sql    # revert: drop them (exact inverse of the up)
```

The leading number is the **version**. golang-migrate only looks at filenames —
it sorts by version and executes the SQL as an opaque payload.

## How it works

The tool keeps a one-row ledger table `schema_migrations (version, dirty)` in the
database itself, recording "this database is at version N". Running `up` applies
every `.up.sql` with a version greater than N, in order, and advances the ledger.
Hence:

- Each migration runs **exactly once** per database (no `IF NOT EXISTS` needed —
  an unexpected pre-existing table should fail loudly, not be papered over).
- Re-running `up` with no new files is a no-op (`no change`).
- `down 1` steps back exactly one version by running that migration's `.down.sql`.

If a migration fails mid-way, the ledger is left `dirty` and the tool refuses to
proceed until you fix the file and run `migrate force <version>`. In dev, a full
reset (`make down` with volumes removed, then `make up migrate`) is usually faster.

## Usage

```bash
make migrate        # apply all pending migrations

# directly, for anything beyond "up":
migrate -path migrations -database "$DB_URL" down 1     # revert latest version
migrate -path migrations -database "$DB_URL" version    # show current version
migrate -path migrations -database "$DB_URL" force N    # clear dirty flag
```

`DB_URL` is defined in the Makefile (note the `?sslmode=disable` — the local
containerized Postgres does not speak TLS).

## Rules

1. **Never edit a migration that has been applied somewhere that matters.**
   The ledger tracks version numbers, not content — edits to an applied file are silently ignored. 
2. **Every up needs a down** that undoes exactly its counterpart, in reverse
   order (drop dependents before dependencies).
3. **Downs are destructive** — dropping a table takes its rows with it. Rolling
   back and forward restores structure, not data.
