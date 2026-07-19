import argparse
import questionary
import time
import uuid
from typing import List, Tuple, Dict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pipeline.db import Database, SyncState, ConnectorInstance, PGDocument
from pipeline.connectors.base import NormalizedDocument, RawContent
from pipeline.connectors.registry import CONNECTOR_REGISTRY
from pipeline.connectors.base import Connector, SourceItem
from pipeline.etl.chunker import Chunker, Chunk
from pipeline.etl.embedder import Embedder, Embedding
from pipeline.config import load_config, Config
from tokenizers import Tokenizer

@dataclass(frozen=True)
class SyncItemError:
    source_id: str
    stage: str 
    error: str 
    
@dataclass
class SyncRunReport:
    discovered: int = 0
    new: int = 0
    changed: int = 0
    deleted: int = 0
    ingested: int = 0
    errors: List[SyncItemError] = field(default_factory=list) 

def main() -> int:

    parser = argparse.ArgumentParser(description="Sync connector instances")
    parser.add_argument("--select", action="store_true",
                        help="interactively pick which instances to sync (default: all)")
    args = parser.parse_args()
    
    cfg = load_config()

    db = Database.connect(url=cfg.database.url)
    instances: List[ConnectorInstance] = db.get_instances()

    if not instances:
        print("No active connector instances. Register one first: make register ...")
        return 1

    if args.select:
        instances = _select_instances(instances)
        if not instances:
            print("Nothing selected.")
            return 0

    for instance in instances:
        started = time.monotonic()
        report = run_sync(db, instance, cfg)
        elapsed = time.monotonic() - started
        _print_report(instance, report, elapsed)

    return 0

def run_sync(db: Database, instance: ConnectorInstance, cfg: Config) -> SyncRunReport:
    
    with db.transaction():
       sync_run_id = db.insert_sync_run(instance.id)
        
    report = SyncRunReport()
    
    states = db.get_sync_states(instance)
    connector: Connector = CONNECTOR_REGISTRY[instance.connector_type]()
    
    connector.validate_config(instance.config)
    
    new_items, changed_items, del_items= _categorize_items(connector, instance.config, states, report)
    
    tokenizer = Tokenizer.from_file(cfg.embedding.tokenizer_path)
    chunker = Chunker(
        tokenizer=tokenizer,
        max_tokens=cfg.chunking.max_tokens,
        overlap=cfg.chunking.overlap
    )
    embedder = Embedder(
        model=cfg.embedding.model, 
        base_url=cfg.embedding.base_url, 
        model_version=cfg.embedding.model_version,
        document_prefix=cfg.embedding.document_prefix
    ) 
    
    _handle_new(connector, db, new_items, instance, report, chunker, embedder)
    _handle_deleted(db, del_items, instance, report)
    _handle_changed(connector, db, changed_items, instance, report, chunker, embedder)
    
    with db.transaction():
        db.update_sync_run(sync_run_id, report)

    return report


#############################################
#               HELPERS
#############################################

def _select_instances(instances: List[ConnectorInstance]) -> List[ConnectorInstance]:
    """
    Checkbox prompt: arrows to move, space to toggle, 'a' to toggle all, enter to confirm.
    Returns [] if the user selects nothing or aborts (ctrl-c / esc).
    """
    choices = [
        questionary.Choice(title=_instance_label(i), value=i)
        for i in instances
    ]
    selected = questionary.checkbox(
        "Which instances do you want to sync?",
        choices=choices,
    ).ask()

    return selected or []

def _instance_label(instance: ConnectorInstance) -> str:
    roots = ", ".join(instance.config.get("root_paths", [])) or "?"
    return f"{instance.connector_type}: {roots}  [{instance.id}]"

def _print_report(instance: ConnectorInstance, report: SyncRunReport, elapsed: float) -> None:
    status = "OK" if not report.errors else f"{len(report.errors)} ERROR(S)"
    print(f"\n=== sync {instance.connector_type} ({instance.id}) — {status} in {elapsed:.2f}s ===")
    print(f"  discovered: {report.discovered}")
    print(f"  new:        {report.new}")
    print(f"  changed:    {report.changed}")
    print(f"  deleted:    {report.deleted}")
    print(f"  ingested:   {report.ingested}")

    for err in report.errors:
        print(f"  ! {err.source_id} [{err.stage}]: {err.error}")

def _categorize_items(
    connector: Connector, 
    config: Dict, 
    states: List[SyncState], 
    report: SyncRunReport
) -> Tuple:
    old = {s.source_id:s for s in states}

    new_items: List[SourceItem] = []
    changed_items: List[SourceItem] = []

    for item in connector.discover(config):
        state = old.pop(item.source_id, None)
        if state is None:
            new_items.append(item)
        elif state.fingerprint != item.fingerprint:
            changed_items.append(item)
        # else: Item is unchanged. No-op
        report.discovered += 1
                
    del_items: List[SyncState] = list(old.values())
    
    return new_items, changed_items, del_items
    
def _handle_new(
    connector: Connector, 
    db: Database, 
    items: List[SourceItem], 
    instance: ConnectorInstance, 
    report: SyncRunReport, 
    chunker: Chunker,
    embedder: Embedder
) -> None:
    """
    - Insert documents into DB
    - Create chunks via chunker
    - Insert chunks into DB
    - Embed chunks
    - Insert embedding into DB
    """
    for item in items:
        stage = "fetch"
        try:
            raw: RawContent = connector.fetch(item)
            
            stage = "normalize"
            doc: NormalizedDocument = connector.normalize(item, raw)
            doc_id = uuid.uuid5(instance.id, item.source_id)
        
            stage = "chunk"
            chunks: List[Chunk] = chunker.chunk(doc)
        
            stage = "embed"
            embeddings: List[Embedding] = embedder.embed([c.content for c in chunks])

            stage = "write"
            with db.transaction():
                db.insert_document(doc, doc_id, instance.id)
                for chunk, embedding in zip(chunks, embeddings):
                    chunk_id = db.insert_chunk(doc_id, chunk, doc)
                    db.insert_embedding(embedding, chunk_id)
                
                db.insert_sync_state(item.source_id, instance.id, item.fingerprint)
            report.new += 1
            report.ingested += 1

        except Exception as e:
            report.errors.append(SyncItemError(item.source_id, stage, str(e)))
                                    
def _handle_deleted(db: Database, items: List[SyncState], instance: ConnectorInstance, report: SyncRunReport) -> None:
    """
    Items exist in sync_state but were not discovered: gone from the source.
    Deleting the document cascades to its chunks and their embeddings.
    """
    
    for state in items:
        try:
            doc_id = uuid.uuid5(instance.id, state.source_id)
            with db.transaction():
                db.delete_document(doc_id)
                db.delete_sync_state(state.source_id, instance.id)
            report.deleted += 1
        except Exception as e:
            report.errors.append(SyncItemError(state.source_id, "delete", str(e)))
  
def _handle_changed(
    connector: Connector, 
    db: Database, 
    items: List[SourceItem], 
    instance: ConnectorInstance, 
    report: SyncRunReport,
    chunker: Chunker,
    embedder: Embedder
) -> None:
    """
    - Non-matching ingerprints can be false alarms
    - Compute the actual content hash and compare it to what was recorded
        - Match: No-op, update fingerprint
        - No match: Re-compute chunks and embeddings, and update atomically
    """
    for item in items:
        stage = "get_document"
        try: 
            doc_id = uuid.uuid5(instance.id, item.source_id)
            doc: PGDocument = db.get_document(doc_id)
            
            stage = "fetch"
            raw: RawContent = connector.fetch(item)
            
            stage = "normalize"
            new_doc: NormalizedDocument = connector.normalize(item, raw)
            
            if doc.content_hash == new_doc.content_hash:
                stage = "write"
                with db.transaction():
                    db.update_sync_state(item.source_id, instance.id, item.fingerprint)
            else:
                stage = "chunk"
                chunks: List[Chunk] = chunker.chunk(new_doc)
                
                stage = "embed"
                embeddings: List[Embedding] = embedder.embed([c.content for c in chunks])
                
                stage = "write"
                with db.transaction():
                    db.delete_chunks_by_doc_id(doc_id)
                    
                    updates = {
                        "content_hash": new_doc.content_hash,
                        "content": new_doc.content,
                        "title": new_doc.title,
                        "modified_at": new_doc.modified_at,
                        "synced_at": datetime.now(timezone.utc)
                    }
                    db.update_document(doc_id, updates)
                                
                    for chunk, embedding in zip(chunks, embeddings):
                        chunk_id = db.insert_chunk(doc_id, chunk, new_doc)
                        db.insert_embedding(embedding, chunk_id)
                    
                    db.update_sync_state(item.source_id, instance.id, item.fingerprint)
                report.changed += 1
                report.ingested += 1
                
        except Exception as e:
            report.errors.append(SyncItemError(item.source_id, stage, str(e)))
        
#############################################
#               DB HELPERS 
#############################################


if __name__ == "__main__":
    raise SystemExit(main())