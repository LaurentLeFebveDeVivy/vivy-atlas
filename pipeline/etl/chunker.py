from pipeline.connectors.base import NormalizedDocument
from dataclasses import dataclass
from typing import List

@dataclass(frozen=True)
class Chunk:
    content: str
    position: int

class Chunker:
    
    def __init__(self):
        pass
    
    def chunk(self, doc: NormalizedDocument) -> List[Chunk]:
        
        return [Chunk(
            content=doc.content,
            position=0
        )]