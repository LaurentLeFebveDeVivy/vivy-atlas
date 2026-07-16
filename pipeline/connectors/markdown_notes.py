from __future__ import annotations
from pipeline.connectors.base import Connector, SourceItem, RawContent, NormalizedDocument, SyncEstimate, Sensitivity
from typing import List, Dict, Tuple, Set
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import yaml
import re

LARGE_FILE_BYTES = 1_000_000
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")

@dataclass(frozen=True)
class MarkdownNotesConfig:
    root_paths: List[Path]
    default_sensitivity: Sensitivity = Sensitivity.PERSONAL
    include_globs: List[str] = field(default_factory=lambda : ["**/*.md"])
    exclude_globs: List[str] = field(default_factory=list)   
    
    @classmethod
    def from_dict(cls, raw: dict) -> MarkdownNotesConfig:
        return cls(
            root_paths = [Path(p).expanduser().resolve() for p in raw["root_paths"]],
            include_globs = list(raw.get("include_globs", ["**/*.md"])), # Recursively find markdowns if nothing else specified
            exclude_globs = list(raw.get("exclude_globs", [])), # Don't exclude anything by default
            default_sensitivity = Sensitivity(raw.get("default_sensitivity", "personal"))
        )
    
class MarkdownNotesConnector(Connector):
    
    def validate_config(self, config: Dict) -> List[str]:
        try:
            cfg = MarkdownNotesConfig.from_dict(config)
        except KeyError as e:
            return [f"Missing required config field: {e}"]
        except (TypeError, ValueError) as e:
            return [f"Invalid config value: {e}"]
        
        problems: List[str] = []
        for root_path in cfg.root_paths:
            if root_path.is_dir():
                continue
            elif root_path.is_file():
                if root_path.suffix.lower() != ".md":
                    problems.append(f"The file '{root_path}' exists, but it is not a markdown (.md) file.")
            else:
                problems.append(f"'{root_path}' does not exist")
        
        return problems
    
    def estimate(self, config: Dict) -> SyncEstimate:
        
        cfg = MarkdownNotesConfig.from_dict(config)
        count = total_bytes = 0
        warnings = []
        samples = []
        
        def aggregate(path: Path):
            nonlocal count
            nonlocal total_bytes
            count += 1
            size = path.stat().st_size
            if size > LARGE_FILE_BYTES:
                warnings.append(f"File {path} exceeds 1MB.")
            total_bytes += size
            if len(samples) < 10:
                samples.append(str(path))
        
        for root_path in cfg.root_paths:
            if root_path.is_file():
                aggregate(root_path)
            elif root_path.is_dir():
                for file_path in self._iter_markdown_files(root_path, cfg):
                    aggregate(file_path)
            else:
                warnings.append(f"'{root_path}' does not exist")
        
        if count == 0:
            warnings.append("No files found in specified paths")
        
        return SyncEstimate(
            item_count=count,
            total_size_bytes=total_bytes,
            warnings=warnings,
            sample_items=samples[:10]
        )
                
    def discover(self, config: Dict) -> Iterator[SourceItem]:
        
        cfg = MarkdownNotesConfig.from_dict(config)
        
        for idx, root_path in enumerate(cfg.root_paths):
            if root_path.is_file():
                
                # Guard against files deleted during the routine
                try:
                    st = root_path.stat()
                except FileNotFoundError:
                    continue
                                
                yield SourceItem(
                    source_id= f"{idx}:{root_path.as_posix()}",
                    uri=root_path.as_uri(),
                    fingerprint=f"{st.st_mtime_ns}:{st.st_size}",
                    sensitivity=cfg.default_sensitivity,
                    metadata={"absolute_path": str(root_path)}
                )
            
            elif root_path.is_dir():
                for file_path in self._iter_markdown_files(root_path, cfg):
                    
                    rel = file_path.relative_to(root_path)
                    try: 
                        st = file_path.stat()
                    except FileNotFoundError:
                        continue
                    
                    yield SourceItem(
                        source_id=f"{idx}:{rel.as_posix()}",
                        uri=file_path.as_uri(),
                        fingerprint=f"{st.st_mtime_ns}:{st.st_size}",
                        sensitivity=cfg.default_sensitivity,
                        metadata={"absolute_path": str(file_path)}
                    )
    
    def fetch(self, item: SourceItem) -> RawContent:
        return RawContent(
            content_type="text/markdown",
            data=Path(item.metadata["absolute_path"]).read_bytes()
        ) 
    
    def normalize(self, item: SourceItem, raw: RawContent) -> NormalizedDocument:
        
        ts = datetime.now(timezone.utc)
        content = raw.data.decode(encoding="utf-8", errors="replace")
        
        frontmatter, body = self._split_frontmatter(content)
        title = self._extract_title(frontmatter, body, Path(item.metadata["absolute_path"]))
        
        metadata = {**item.metadata, **raw.metadata, "frontmatter": frontmatter}
                
        return NormalizedDocument(
            doc_id="xxx", # TODO
            connector_instance_id="xxx", # TODO
            connector_type="markdown_notes",
            uri=item.uri,
            title=title,
            content_type=raw.content_type,
            content_hash=hashlib.sha256(body.encode()).hexdigest(),
            sensitivity=item.sensitivity,
            metadata=metadata,
            synced_at=ts,
            created_at=ts,
            modified_at=ts,
            content=body
        )
    
    def _iter_markdown_files(self, root: Path, cfg: MarkdownNotesConfig) -> Iterator[Path]:
        
        seen: Set[Path] = set()
        
        for pattern in cfg.include_globs:
            for path in root.glob(pattern):
                if not path.is_file():
                    continue
                
                # Avoid duplicate paths, in case of multiple overlapping glob patterns
                if path in seen:
                    continue
                seen.add(path)
                
                rel = path.relative_to(root)
                
                if any(rel.match(pat) for pat in cfg.exclude_globs):
                    continue
                
                yield path
                
    def _split_frontmatter(self, text: str) -> Tuple[Dict, str]:
        
        # Frontmatter doesn't exist
        if not text.startswith("---\n"):
            return {}, text
        
        # Determine the index of the frontmatter's end
        end = text.find("\n---", 4)
        
        # Frontmatter end not found
        if end == -1:
            return {}, text
        
        try:
            fm = yaml.safe_load(text[4:end])
        except yaml.YAMLError:
            return {}, text

        body = text[end+4:].lstrip("\n")
        return (fm if isinstance(fm, dict) else {}), body
                
    def _extract_title(self, frontmatter: dict, body: str, path: Path) -> str:
        
        # If title explicitely defined in frontmatter
        fm_title = frontmatter.get("title")
        if isinstance(fm_title, str) and fm_title.strip():
            return fm_title.strip()
    
        # Go line by line to find the first markdown title (if exists)
        for line in body.splitlines():
            m = HEADING_RE.match(line)
            if m:
                return m.group(1).strip()
        
        # Use the raw document name without the file extension as title
        return path.stem
    

# For inspection purposes
if __name__ == "__main__":
    
    cwd = Path.cwd()
    config = {"root_paths": [cwd]}
    
    conn = MarkdownNotesConnector()
    
    problems = conn.validate_config(config)
    if problems:
        print(problems)
    
    estimate = conn.estimate(config)
    print("=========ESTIMATE=========")
    print(estimate)
    
    docs: List[NormalizedDocument] = []
    for item in conn.discover(config):
        raw = conn.fetch(item)
        doc = conn.normalize(item, raw)
        docs.append(doc)
        
    for idx, doc in enumerate(docs):
        print(f"============DOC {idx} ==============")
        print(doc)