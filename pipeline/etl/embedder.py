from typing import List
from dataclasses import dataclass
import httpx

@dataclass(frozen=True)
class Embedding:
    vector: List[float]
    model: str
    model_version: str

class Embedder:
    
    def __init__(self, 
            model: str, 
            model_version: str, 
            base_url: str,
            document_prefix: str
        ):
        self.model = model
        self.model_version = model_version
        self.base_url = base_url # Model endpoint (Ollama or similar)
        self.document_prefix = document_prefix
    
    def embed(self, texts: List[str]) -> List[Embedding]:
        resp = httpx.post(
            url=f"{self.base_url}/api/embed",
            json={"model": self.model, "input": [f"{self.document_prefix}{text}" for text in texts]},
            timeout=120.0
        )
        
        resp.raise_for_status()
        return [
            Embedding(vector=v, model=self.model, model_version=self.model_version)
            for v in resp.json()["embeddings"]
        ]