import argparse
import sys

import yaml
from psycopg.types.json import Jsonb

from pipeline import db
from pipeline.connectors.registry import CONNECTOR_REGISTRY

def main() -> int:
    
    parser = argparse.ArgumentParser(description="Register a connector instance")
    
    parser.add_argument("connector_type", choices=CONNECTOR_REGISTRY)
    parser.add_argument("config_file", help="YAML file with the instance config")
    parser.add_argument("--sensitivity", default="personal")
    parser.add_argument("--tombstone-mode", default="delete", choices=["delete", "archive"])
    parser.add_argument("--force", action="store_true",
                        help="register even if an instance with the same type and config exists")
    
    args = parser.parse_args()
    
    with open(args.config_file) as f:
        config = yaml.safe_load(f)
    
    connector = CONNECTOR_REGISTRY[args.connector_type]()

    errors = connector.validate_config(config)
    if errors:
        for e in errors:
            print(f"config error: {e}", file=sys.stderr)
        return 1

    est = connector.estimate(config)
    
    print(f"First sync would ingest {est.item_count} items, {est.total_size_bytes:,} bytes")
    for sample in est.sample_items:
        print(f"  e.g. {sample}")
    for w in est.warnings:
        print(f"  warning: {w}")
    
    with db.connect() as conn:
        existing = conn.execute(
            """
            SELECT id FROM connector_instances
            WHERE connector_type = %s AND config = %s
            """,
            (args.connector_type, Jsonb(config))
        ).fetchone()
        if existing and not args.force:
            print(f"already registered as {existing[0]} — use --force to register anyway",
                  file=sys.stderr)
            return 1

        row = conn.execute(
            """
            INSERT INTO connector_instances (connector_type, config, default_sensitivity, tombstone_mode)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (args.connector_type, Jsonb(config), args.sensitivity, args.tombstone_mode)
        ).fetchone()

        print(f"registered instance: {row[0]}")
        return 0
    
    
if __name__ == "__main__":
    raise SystemExit(main())