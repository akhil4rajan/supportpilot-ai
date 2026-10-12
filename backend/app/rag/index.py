"""Dependency-free BM25 index with a calibrated "coverage" confidence score.

Why BM25 and not embeddings by default: zero infra, deterministic, explainable, and strong for
FAQ/policy docs. `KnowledgeBase` is the seam to swap in a vector store (pgvector, Qdrant, ...) -
keep the `search()` contract and nothing else changes.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .chunker import Chunk, chunk_document

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be but by can do does for from has have how i if in is it its me my of on or "
    "our so that the their there this to was we what when where which who why will with you your "
    "please about get".split()
)
SUPPORTED_SUFFIXES = {".md", ".txt", ".markdown"}


_SUFFIXES = ("ings", "ing", "ied", "ies", "ed", "es", "s", "ly")


def _stem(w: str) -> str:
    """Tiny stemmer: strip common suffixes, drop a trailing 'e', collapse a doubled final consonant.
    cancelling/cancel -> cancel, shipped/shipping -> ship, arrive/arrived -> arriv, refunds -> refund."""
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiou":
        w = w[:-1]
    return w


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


def expand_query(query: str, synonyms: dict[str, list[str]]) -> str:
    """Append canonical terms when a customer's own phrasing appears: {"refund": ["money back", "reimburse"]}.
    Per-tenant vocabulary is the standard way to bridge 'how customers talk' and 'how the docs are written'."""
    q = query.lower()
    extra = [canon for canon, phrases in synonyms.items() if any(p.lower() in q for p in phrases)]
    return query + " " + " ".join(extra) if extra else query


@dataclass(frozen=True)
class SearchHit:
    chunk: Chunk
    score: float
    coverage: float  # 0..1: share of the query's (idf-weighted) terms found in this chunk


class KnowledgeBase:
    def __init__(self, k1: float = 1.4, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._chunks: list[Chunk] = []
        self._tf: list[Counter[str]] = []
        self._len: list[int] = []
        self._df: Counter[str] = Counter()
        self._avg = 0.0

    @property
    def size(self) -> int:
        return len(self._chunks)

    @property
    def sources(self) -> list[str]:
        return sorted({c.source for c in self._chunks})

    def build(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks
        self._tf, self._len, self._df = [], [], Counter()
        for c in chunks:
            # Title terms are boosted by repetition: headings are strong relevance signals.
            toks = tokenize(c.title) * 2 + tokenize(c.text)
            tf = Counter(toks)
            self._tf.append(tf)
            self._len.append(len(toks))
            self._df.update(tf.keys())
        self._avg = (sum(self._len) / len(self._len)) if self._len else 0.0

    @classmethod
    def from_directory(cls, directory: Path) -> "KnowledgeBase":
        kb = cls()
        chunks: list[Chunk] = []
        if directory.is_dir():
            for p in sorted(directory.rglob("*")):
                if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES:
                    chunks += chunk_document(p.relative_to(directory).as_posix(), p.read_text("utf-8", "ignore"))
        kb.build(chunks)
        return kb

    def _idf(self, term: str) -> float:
        n = len(self._chunks)
        df = self._df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 4, synonyms: dict[str, list[str]] | None = None) -> list[SearchHit]:
        q = [t for t in dict.fromkeys(tokenize(expand_query(query, synonyms or {})))]
        if not q or not self._chunks:
            return []
        total_idf = sum(self._idf(t) for t in q) or 1.0
        hits: list[SearchHit] = []
        for i, tf in enumerate(self._tf):
            score, matched = 0.0, 0.0
            for t in q:
                f = tf.get(t, 0)
                if not f:
                    continue
                idf = self._idf(t)
                matched += idf
                score += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self._len[i] / self._avg))
            if score > 0:
                hits.append(SearchHit(self._chunks[i], score, matched / total_idf))
        hits.sort(key=lambda h: (h.score, h.coverage), reverse=True)
        return hits[:top_k]
