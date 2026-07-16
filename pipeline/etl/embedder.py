from typing import List
from dataclasses import dataclass

from pipeline.etl.chunker import Chunk

@dataclass(frozen=True)
class Embedding:
    vector: List[float]
    model: str
    model_version: str

class Embedder:
    
    def __init__(self, model: str, model_version: str, base_url: str):
        self.model = model
        self.base_url = base_url # Model endpoint (Ollama or similar)
        self.model_version = model_version    
    
    def embed(self, chunks: List[Chunk]) -> List[Embedding]:
        return [Embedding([0.1]*768, self.model, self.model_version) for _ in chunks]