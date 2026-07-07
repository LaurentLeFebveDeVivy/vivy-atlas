from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List
from collections.abc import Iterator
from pathlib import Path
from enum import Enum
from datetime import datetime

class Sensitivity(str, Enum):
    PUBLIC = "public"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    SECRET = "secret"

@dataclass(frozen=True)
class SyncEstimate:
    item_count: int
    total_size_bytes: int
    sample_items: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

@dataclass(frozen=True)
class SourceItem:
    source_id: str
    uri: str
    fingerprint: str
    sensitivity: Sensitivity
    metadata: Dict | None = None

@dataclass(frozen=True)
class RawContent:
    content_type: str # e.g., "application/octet-stream"
    data: bytes | None = None # in memory content
    file_ref: Path | None = None # content on disk (large/binary)
    encoding: str | None = None # e.g., utf-8
    metadata: Dict = field(default_factory=dict)

@dataclass(frozen=True)
class NormalizedDocument:
    doc_id: str
    connector_instance_id: str
    connector_type: str
    uri: str
    title: str
    content_type: str
    content_hash: str
    synced_at: datetime
    sensitivity: Sensitivity
    metadata: Dict
    created_at: datetime | None = None
    modified_at: datetime | None = None
    content: str | None = None
    content_ref: str | None = None
    
class Connector(ABC):
    
    @abstractmethod
    def validate_config(self, config: Dict) -> List[str]:
        """
        Checks if a defined configuration is valid
        """
        
    @abstractmethod
    def estimate(self, config: Dict) -> SyncEstimate:
        """
        Dry run: 
        How many items would a first sync ingest? 
        What is the size of the content?
        Shown to the user before enabling.
        """
    
    @abstractmethod
    def discover(self, config: Dict) -> Iterator[SourceItem]:
        """
        Enumerate all items currently in the source, including fingerprints. Cheap, no content fetching
        The sync engine diffs this full listing against stored state
        The connector does not filter. THis is done by the sync engine
        """
        
    @abstractmethod
    def fetch(self, item: SourceItem) -> RawContent:
        """
        Retrieves the raw content of a single item
        """
        
    @abstractmethod
    def normalize(self, item: SourceItem, raw: RawContent) -> NormalizedDocument:
        """
        Converts raw content into a normalized document format that can be stored
        """
        
        
    