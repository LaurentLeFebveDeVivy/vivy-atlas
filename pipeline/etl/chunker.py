from pipeline.connectors.base import NormalizedDocument
from dataclasses import dataclass
from typing import List
from tokenizers import Tokenizer 
import re


@dataclass(frozen=True)
class Chunk:
    content: str
    position: int

class Chunker:
    
    def __init__(self, tokenizer = None, max_tokens: int = 512, overlap: int = 50):
        
        if overlap >= max_tokens:
            raise ValueError("overlap must be smaller than max_tokens")
        
        if tokenizer is None:
            tokenizer = Tokenizer.from_file("pipeline/etl/tokenizer.json")
        
        self.tokenizer = tokenizer
        self.max_tokens = max_tokens
        self.overlap = 50
    
    def chunk(self, doc: NormalizedDocument) -> List[Chunk]:
        
        segments = self.split_to_segments(doc)
            
        chunks = self.segments_to_chunks(segments)

        return self.merge_chunks(chunks)
    
    def split_to_segments(self, doc: NormalizedDocument) -> List[str]:
        lines = doc.content.split("\n")
        segments = []
        start = 0
        is_inline = False # Markdowns can contain inline comments starting with # that are not actual sections
        for end, ln in enumerate(lines):
            if re.match(r"#{1,6}", ln) and not is_inline: # Current line is a header. Store curr segment and start a new segment
                segments.append("\n".join(lines[start: end]))
                start = end
            elif ln.startswith("```"):
                is_inline = not is_inline
        
        # Process last segment
        segments.append("\n".join(lines[start:]))
        segments = [s.strip() for s in segments if s.strip()]
        return segments
    
    def segments_to_chunks(self, segments: List[str]) -> List[str]:
        token_counts = [len(e.ids) for e  in self.tokenizer.encode_batch(segments)]
        
        chunks = []
        for count, text in zip(token_counts, segments):
            if count <= self.max_tokens:
                chunks.append(text)
            else: # Recursive split
                chunks.extend(self._split(text, separators=["\n\n", ". "])) # First, split on segments, then on sentences.
        
        return chunks
    
    def merge_chunks(self, chunks: List[str]) -> List[Chunk]:
        """
        Merges (top to bottom) subsequent chunks if their combined token count is less than 512
        """
        token_counts = [len(e.ids) for e  in self.tokenizer.encode_batch(chunks)]
        merged: List[str] = []
        current = None
        for count, chunk in zip(token_counts, chunks):
            if current is None:
                current = [count, [chunk]]
                continue
            
            if count + current[0] > self.max_tokens:
                merged.append("\n".join(current[1]))
                current = [count, [chunk]]
            else:
                current[0] += count
                current[1].append(chunk)
        
        if current:
            merged.append("\n".join(current[1]))
        
        chunks: List[Chunk] = [
            Chunk(content, i)
            for i, content in enumerate(merged)
        ]
        
        return chunks
    
    def _split(self, text: str , separators: List[str]) -> List[str]:
        if self._count_tokens(text) <= self.max_tokens:
            return [text]
        
        if not separators: 
            return self._hard_split(text) # separators could not produce a fitting chunks. resolve to brute-force split
    
        sep, rest = separators[0], separators[1:]
        parts = text.split(sep)
        
        chunks: List[str] = []
        current: List[str] = []
        for part in parts:
            candidate = sep.join(current + [part])
            
            # adding the part exceeds token limit.
            if current and self._count_tokens(candidate) > self.max_tokens:
                
                # Usually current already fits. Recursion also handles a single oversized part.
                chunks.extend(self._split(sep.join(current), rest))
                current = [part]
            else:
                current.append(part)
        
        if current:
            chunks.extend(self._split(sep.join(current), rest))
        
        return chunks
        
    def _count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text).ids)
        
    def _hard_split(self, text: str) -> List[str]:
        """
        Naively take the next max_token tokens for each chunk, until we exhaust the text
        """
        encoding = self.tokenizer.encode(text, add_special_tokens=False)
        offsets = encoding.offsets
        
        stride = self.max_tokens - self.overlap # Here the boundaries are arbitrary and not given by structure, so overlap makes sense
        
        chunks: List[str] = []
        for i in range(0, len(offsets), stride):
            window = offsets[i : i+self.max_tokens]
            start, end = window[0][0], window[-1][1]
            chunks.append(text[start:end])
            if i + self.max_tokens >= len(offsets):
                break
            
        return chunks
        
if __name__ == "__main__":
    
    dummy_md = """
    # This is a dummy markdown
    Let's see how this works
    
    ## Wow a H2
    
    ### And a H3
    
    ## Does it work?
    
    #### Let's see! 
    
    ```python
    #Inline code!!
    Dont forget to close!
    ```
    
    # And another one!
    cool!
    """
    
    doc = NormalizedDocument(
        content = dummy_md,
        doc_id = "x",
        connector_instance_id = "x",
        connector_type = "x",
        uri = "x",
        title = "x",
        content_type = "x",
        content_hash = "x",
        synced_at = "datetime",
        sensitivity = "x",
        metadata = {}
    )

    tokenizer = Tokenizer.from_file("pipeline/etl/tokenizer.json")
    chunker = Chunker(tokenizer)
    chunks = chunker.chunk(doc)
    
    for i, c in enumerate(chunks):
        print(f"Chunk {i}")
        print(chunker._count_tokens(c.content))
        print("============================")
        

