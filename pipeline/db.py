from __future__ import annotations
from typing import Dict, Any, List, TYPE_CHECKING

import os
import psycopg
from psycopg.types.json import Jsonb
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from pipeline.connectors.base import NormalizedDocument
from pipeline.etl.embedder import Embedding
from pipeline.etl.chunker import Chunk

if TYPE_CHECKING:
    from pipeline.sync import SyncRunReport

@dataclass(frozen=True)
class SyncState:
    connector_instance_id: str | None = None
    source_id: str | None = None
    fingerprint: str | None = None

@dataclass(frozen=True)
class ConnectorInstance:
    id: str | None = None
    connector_type: str | None = None
    config: Dict | None = None
    tombstone_mode: str | None = None
    default_sensitivity: str | None = None
    
@dataclass(frozen=True)
class PGDocument:
    id:                      str 
    connector_instance_id:   str
    connector_type:          str
    uri:                     str
    title:                   str
    content:                 str
    content_ref:             str
    content_type:            str
    content_hash:            str
    created_at:              datetime
    modified_at:             datetime
    synced_at:               datetime
    sensitivity:             str
    metadata:                Dict[str, Any]
    status:                  str              


DB_URL = os.environ.get("VIVY_DB_URL", "postgresql://vivy:vivy@127.0.0.1:5433/vivyatlas")

def connect() -> psycopg.Connection:
    return psycopg.connect(DB_URL, autocommit=True)

class Database:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
    
    @classmethod
    def connect(cls, url: str | None = None) -> "Database":
        url = url or DB_URL
        return cls(psycopg.connect(url, autocommit=True))

    def transaction(self):
        return self.conn.transaction()
    
    def delete_chunks_by_doc_id(self, doc_id: str) -> None:
        """
        There is a cascade on the embedding, which will automatically be deleted as well
        """
        self.conn.execute(
            """
            DELETE FROM chunks
            WHERE document_id = %s
            """,
            (doc_id,)            
        )

    def delete_document(self, doc_id: str) -> None:
        """
        There is cascade to chunks and from chunks to its embeddings
        """
        self.conn.execute(
            """
            DELETE FROM documents
            WHERE id = %s
            """,
            (doc_id,)
        )
    
    def delete_sync_state(self, source_id: str, instance_id: str) -> None:
        self.conn.execute(
            """
            DELETE FROM sync_state
            WHERE source_id = %s AND connector_instance_id = %s
            """,
            (source_id, instance_id)
        )


    def update_sync_state(self, source_id: str, instance_id: str, fingerprint: str) -> None:
        
        self.conn.execute(
            """
            UPDATE sync_state
            SET fingerprint = %s
            WHERE source_id = %s AND connector_instance_id = %s            
            """,
            (fingerprint, source_id, instance_id)               
        )

    def update_document(self, doc_id: str, updates: Dict[str, Any]) -> None:
        
        assignments = ", ".join(f"{col} = %s" for col in updates)
        params = tuple(updates.values()) + (doc_id,)
        
        self.conn.execute(
            f"""
            UPDATE documents
            SET {assignments}
            WHERE id = %s
            """,
            params
        )

    def update_sync_run(self, sync_run_id: str, report: SyncRunReport) -> None:
        
        updates = {
            "discovered": report.discovered,
            "new": report.new,
            "changed": report.changed,
            "deleted": report.deleted,
            "ingested": report.ingested,
            "errors": Jsonb([asdict(e) for e in report.errors]),
            "finished_at": datetime.now(timezone.utc)
        }
        
        assignments = ", ".join(f"{col} = %s" for col in updates)
        params = tuple(updates.values()) + (sync_run_id,)
        
        self.conn.execute(
            f"""
            UPDATE sync_runs
            SET {assignments}
            WHERE id = %s
            """,
            params        
        )


    def insert_sync_run(self, instance_id: str) -> None:
        row = self.conn.execute(
            """
            INSERT INTO sync_runs (connector_instance_id)
            VALUES (%s)
            RETURNING id
            """,
            (instance_id,)
        ).fetchone()
        
        return row[0] # sync_run id

    def insert_sync_state(self, source_id: str, instance_id: str, fingerprint: str) -> None:
        self.conn.execute(
            """
            INSERT INTO sync_state (connector_instance_id, source_id, fingerprint)
            VALUES (%s, %s, %s)
            """, 
            (instance_id, source_id, fingerprint)
        )

    def insert_embedding(self, embedding: Embedding, chunk_id: str) -> None:
        self.conn.execute(
            """
            INSERT INTO embeddings (chunk_id, model, model_version, vector)
            VALUES (%s, %s, %s, %s)
            """,
            (chunk_id, embedding.model, embedding.model_version, embedding.vector)
        )

    def insert_chunk(self, doc_id: str, chunk: Chunk, doc: NormalizedDocument) -> str:
        row = self.conn.execute(
            """
            INSERT INTO chunks (document_id, position, text, sensitivity)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (
                doc_id,
                chunk.position,
                chunk.content,
                doc.sensitivity.value
            )
        ).fetchone()

        return row[0] #chunk_id

    def insert_document(self, doc: NormalizedDocument, doc_id: str, instance_id: str) -> str:
            
        row = self.conn.execute(
            """
            INSERT INTO documents 
                (
                    id,
                    connector_instance_id,
                    connector_type,
                    uri,
                    title,
                    content,
                    content_ref,
                    content_type,
                    content_hash,
                    created_at,
                    modified_at,
                    synced_at,
                    sensitivity,
                    metadata
                )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                doc_id,
                instance_id,
                doc.connector_type,
                doc.uri,
                doc.title,
                doc.content,
                doc.content_ref,
                doc.content_type,
                doc.content_hash,
                doc.created_at,
                doc.modified_at,
                doc.synced_at,
                doc.sensitivity.value,
                Jsonb(doc.metadata),
            )
        ).fetchone()
        
        return row[0] #doc_id
        

    def get_sync_states(self, instance: ConnectorInstance) -> List[SyncState]:
        rows = self.conn.execute(
            """
            SELECT connector_instance_id, source_id, fingerprint
            FROM sync_state
            WHERE connector_instance_id = %s
            """,
            (instance.id,)
        ).fetchall()
        
        states: List[SyncState] =  [
            SyncState(
                connector_instance_id=r[0],
                source_id=r[1],
                fingerprint=r[2]
            )
            for r in rows
        ]
        
        return states   

    def get_instances(self) -> List[ConnectorInstance]:
        rows = self.conn.execute(
            """
            SELECT id, connector_type, tombstone_mode, config, default_sensitivity
            FROM connector_instances
            WHERE state = 'active'
            """
        ).fetchall()
        
        instances: List[ConnectorInstance] = [
            ConnectorInstance(
                id=r[0], 
                connector_type=r[1], 
                tombstone_mode=r[2],
                config=r[3],
                default_sensitivity=r[4]
            ) 
            for r in rows
        ]
        
        return instances

    def get_document(self, doc_id: str) -> PGDocument:
        
        row = self.conn.execute(
            """
            SELECT
                    id, 
                    connector_instance_id,
                    connector_type,
                    uri,
                    title,
                    content,
                    content_ref,
                    content_type,
                    content_hash,
                    created_at,
                    modified_at,
                    synced_at,
                    sensitivity,
                    metadata,
                    status       
                
            FROM documents
            WHERE id = %s
            """,
            (doc_id,)
        ).fetchone()

        return PGDocument(
            id=row[0], 
            connector_instance_id=row[1],
            connector_type=row[2],
            uri=row[3],
            title=row[4],
            content=row[5],
            content_ref=row[6],
            content_type=row[7],
            content_hash=row[8],
            created_at=row[9],
            modified_at=row[10],
            synced_at=row[11],
            sensitivity=row[12],
            metadata=row[13],
            status=row[14],   
        )
