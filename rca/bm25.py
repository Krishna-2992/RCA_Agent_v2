"""BM25 as pgvector sparse vectors.

Document vectors carry the full BM25 term weight (IDF x saturated term
frequency); query vectors carry a 1 for each query term. Their inner product,
which pgvector computes, is then exactly the BM25 score.

The tokenizer keeps identifiers whole (in0011.mst, s0c7, a.f8rh0410) and also
emits their parts, so "IN0011.MST" and "IN0011" both match.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field


DIMENSIONS = 1_000_000          # fixed sparse dimension; vocabulary indices stay below it
K1 = 1.2
B = 0.75

TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._=/-][a-z0-9]+)*")
PART_SPLIT_RE = re.compile(r"[._=/-]+")

STOPWORDS = frozenset(
    "a an and are as at be been but by can could did do does for from had has have he her his i if in "
    "into is it its me my no not of on or our she so than that the their them then there these they "
    "this to too us was we were what when where which while who will with would you your".split()
)

_KEEP_DOUBLE = ("ll", "ss", "ff", "zz")


def _stem(token: str) -> str:
    """Conservative suffix stripping for plain words only (never for codes)."""

    if not token.isalpha() or len(token) <= 4:
        return token
    stem = token
    if token.endswith("ing") and len(token) > 5:
        stem = token[:-3]
    elif token.endswith("ed") and len(token) > 4:
        stem = token[:-2]
    elif token.endswith(("ches", "shes", "sses", "xes", "zes")):
        stem = token[:-2]
    elif token.endswith("s") and not token.endswith("ss"):
        stem = token[:-1]
    if stem != token and len(stem) > 3 and stem[-1] == stem[-2] and stem[-2:] not in _KEEP_DOUBLE:
        stem = stem[:-1]
    return stem


def tokenize(text: str) -> list[str]:
    tokens = []
    for match in TOKEN_RE.finditer((text or "").lower()):
        token = match.group()
        if token in STOPWORDS:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        tokens.append(_stem(token))
        if PART_SPLIT_RE.search(token):
            for part in PART_SPLIT_RE.split(token):
                if len(part) > 1 and part not in STOPWORDS:
                    tokens.append(_stem(part))
    return tokens


@dataclass
class Bm25Index:
    vocab: dict[str, int] = field(default_factory=dict)       # token -> dimension index
    df: Counter = field(default_factory=Counter)              # token -> documents containing it
    n_docs: int = 0

    @classmethod
    def build(cls, corpus: list[list[str]]) -> "Bm25Index":
        index = cls()
        for tokens in corpus:
            index.n_docs += 1
            for token in set(tokens):
                index.df[token] += 1
        for token in sorted(index.df):
            index.vocab[token] = len(index.vocab)
        if len(index.vocab) >= DIMENSIONS:
            raise ValueError(f"vocabulary of {len(index.vocab)} exceeds {DIMENSIONS} dimensions")
        return index

    def idf(self, token: str) -> float:
        df = self.df.get(token, 0)
        return math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))

    def document_vector(self, tokens: list[str], avg_length: float) -> dict[int, float]:
        counts = Counter(tokens)
        length = len(tokens) or 1
        norm = K1 * (1 - B + B * length / (avg_length or 1))
        return {
            self.vocab[token]: self.idf(token) * tf * (K1 + 1) / (tf + norm)
            for token, tf in counts.items()
            if token in self.vocab
        }

    def query_vector(self, text: str) -> dict[int, float]:
        return {self.vocab[t]: 1.0 for t in set(tokenize(text)) if t in self.vocab}
