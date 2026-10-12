"""Split markdown/plain-text documents into overlapping, heading-aware chunks."""
from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass(frozen=True)
class Chunk:
    source: str  # file name
    title: str  # heading path, e.g. "Returns > Refund timing"
    text: str


def chunk_document(source: str, text: str, max_chars: int = 900, overlap: int = 150) -> list[Chunk]:
    sections: list[tuple[str, list[str]]] = []
    path: list[tuple[int, str]] = []
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append((" > ".join(h for _, h in path), [body]))
        buf.clear()

    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, m.group(2).strip()))
        else:
            buf.append(line)
    flush()

    chunks: list[Chunk] = []
    for title, parts in sections:
        body = parts[0]
        if len(body) <= max_chars:
            chunks.append(Chunk(source, title, body))
            continue
        # Pack paragraphs into windows; carry a tail of the previous window as overlap.
        window = ""
        for para in re.split(r"\n\s*\n", body):
            if window and len(window) + len(para) > max_chars:
                chunks.append(Chunk(source, title, window.strip()))
                window = window[-overlap:] if overlap else ""
            window += ("\n\n" if window else "") + para
        if window.strip():
            chunks.append(Chunk(source, title, window.strip()))
    return chunks
