-- Registry of configured sources. A connector type (e.g. markdown_notes) is code;
-- an *instance* is one configured use of it (e.g. that code pointed at ~/notes).
-- One type can have many instances — one per directory/repo/account — each with its own config, schedule, and independent sync_state and sync_runs.
-- connector_type: names which connector class the sync runner instantiates
-- config: instance-specific settings for that class (e.g. root path, include/exclude globs)
CREATE TABLE connector_instances(
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connector_type          text NOT NULL,
    config                  jsonb NOT NULL DEFAULT '{}',
    state                   text NOT NULL DEFAULT 'active'
                            CHECK(state IN ('active', 'paused', 'disabled')),
    default_sensitivity     text NOT NULL DEFAULT 'personal',
    tombstone_mode          text NOT NULL DEFAULT 'delete'
                            CHECK(tombstone_mode IN ('delete', 'archive')),
    schedule_interval       interval,
    created_at              timestamptz NOT NULL DEFAULT now()
);

-- What the source looked like at the last successful sync: the diff basis
-- for classifying items as new / changed / deleted.
-- connector_instance_id: which instance the item belongs to
-- source_id: the connector's stable ID for the item within its instance (e.g. relative path)
-- fingerprint: cheap change-detection value (e.g. mtime+size); differs => re-ingest
CREATE TABLE sync_state(
    connector_instance_id   uuid NOT NULL REFERENCES connector_instances(id) ON DELETE CASCADE,
    source_id               text NOT NULL,
    fingerprint             text NOT NULL,
    PRIMARY KEY (connector_instance_id, source_id)
);

-- One row per execution of one connector instance's sync. Five instances synced = five rows
CREATE TABLE sync_runs(
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connector_instance_id   uuid NOT NULL REFERENCES connector_instances(id) ON DELETE CASCADE,
    started_at              timestamptz NOT NULL DEFAULT now(),
    finished_at             timestamptz,
    discovered              integer NOT NULL DEFAULT 0,
    new                     integer NOT NULL DEFAULT 0,
    changed                 integer NOT NULL DEFAULT 0,
    deleted                 integer NOT NULL DEFAULT 0,
    ingested                integer NOT NULL DEFAULT 0,
    errors                  jsonb NOT NULL DEFAULT '[]'
);

CREATE INDEX sync_runs_instance_started_idx ON sync_runs (connector_instance_id, started_at DESC);
