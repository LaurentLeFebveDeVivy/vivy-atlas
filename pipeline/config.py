import yaml
from pathlib import Path
import os
from dataclasses import dataclass

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config.yaml"

@dataclass(frozen=True)
class DatabaseConfig:
    url: str
    
@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str
    model: str
    model_version: str
    document_prefix: str
    query_prefix: str
    tokenizer_path: str
    dimension: int
    
@dataclass(frozen=True)
class ChunkingConfig:
    max_tokens: int
    overlap: int

@dataclass(frozen=True)
class Config:
    database: DatabaseConfig
    embedding: EmbeddingConfig
    chunking: ChunkingConfig


def load_config(path: Path | None = None) -> Config:
    
    path = path or Path(os.environ.get("VIVYATLAS_CONFIG", _DEFAULT_PATH))
    with open(path) as f:
        raw = yaml.safe_load(f)
        
    return Config(
        database=DatabaseConfig(**raw["database"]),
        embedding=EmbeddingConfig(**raw["embedding"]),
        chunking=ChunkingConfig(**raw["chunking"])
    )
    
    