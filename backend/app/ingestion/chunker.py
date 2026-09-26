"""Split page texts into retrieval chunks with stable locators."""
from __future__ import annotations

from dataclasses import dataclass

MAX_CHARS = 1200
OVERLAP = 150


@dataclass
class Chunk:
    page_number: int
    locator: str
    text: str
    char_start: int
    char_end: int


def chunk_page(page_number: int, locator: str, text: str) -> list[Chunk]:
    if len(text) <= MAX_CHARS:
        return [Chunk(page_number, locator, text, 0, len(text))]
    chunks: list[Chunk] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + MAX_CHARS)
        if end < n:
            cut = max(text.rfind("\n", start + MAX_CHARS // 2, end), text.rfind(". ", start + MAX_CHARS // 2, end))
            if cut > start:
                end = cut + 1
        chunks.append(Chunk(page_number, locator, text[start:end].strip(), start, end))
        if end >= n:
            break
        start = max(end - OVERLAP, start + 1)
    return [c for c in chunks if c.text]
