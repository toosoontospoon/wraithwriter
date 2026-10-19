"""Passage store for verbatim retrieval.

Revised prose is chunked into ~300-word passages tagged with chapter, scene and
entity ids. Retrieval is hybrid: BM25 over tokens (names matter) plus optional
dense vectors (sentence-transformers, if installed and enabled). Passages for a
chapter are replaced, never duplicated, when it is rewritten.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from bookforge.core.io import atomic_write_json, read_json

_TOK = re.compile(r"[a-z0-9']+")
_STOP = set("the a an and or but of to in on at for with as is was were be been he she it they his her their "
            "them i you we me my that this there then than so not no by from had has have".split())


def _tokens(t: str) -> list[str]:
    return [w for w in _TOK.findall(t.lower()) if w not in _STOP]


def chunk_passages(text: str, words: int = 300) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, cur, n = [], [], 0
    for p in paras:
        cur.append(p)
        n += len(p.split())
        if n >= words:
            out.append("\n\n".join(cur))
            cur, n = [], 0
    if cur:
        out.append("\n\n".join(cur))
    return out


class PassageStore:
    def __init__(self, project_dir: Path, use_vectors: bool = False, model_name: str = ""):
        self.path = Path(project_dir) / "embeddings" / "passages.json"
        self.items: list[dict] = read_json(self.path, []) or []
        self._model = None
        if use_vectors:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(model_name)
            except Exception as e:  # optional dependency
                print(f"[embeddings] vectors disabled: {e}")

    def _save(self):
        atomic_write_json(self.path, self.items)

    def replace(self, chapter: str, scene, text: str, entities: list[str]) -> int:
        self.items = [p for p in self.items if not (p["chapter"] == chapter and p["scene"] == scene)]
        for i, ptxt in enumerate(chunk_passages(text)):
            item = {"chapter": chapter, "scene": scene, "idx": i, "text": ptxt,
                    "entities": [e for e in entities if e.replace("_", " ").split()[0] in ptxt.lower()]}
            if self._model is not None:
                item["vec"] = [float(x) for x in self._model.encode([ptxt], normalize_embeddings=True)[0]]
            self.items.append(item)
        self._save()
        return len(self.items)

    def remove_chapter(self, chapter: str):
        self.items = [p for p in self.items if p["chapter"] != chapter]
        self._save()

    def retrieve(self, query: str, k: int = 4, entities: list[str] | None = None,
                 before_chapter: str | None = None) -> list[dict]:
        pool = [p for p in self.items if before_chapter is None or p["chapter"] < before_chapter]
        if not pool:
            return []
        q = _tokens(query)
        df = Counter()
        docs = [Counter(_tokens(p["text"])) for p in pool]
        for d in docs:
            df.update(set(d))
        N, avg = len(pool), sum(sum(d.values()) for d in docs) / len(pool)
        qv = None
        if self._model is not None:
            qv = self._model.encode([query], normalize_embeddings=True)[0]
        scored = []
        for p, d in zip(pool, docs):
            L = sum(d.values()) or 1
            s = 0.0
            for w in q:
                if w in d:
                    idf = math.log(1 + (N - df[w] + 0.5) / (df[w] + 0.5))
                    s += idf * d[w] * 2.2 / (d[w] + 1.2 * (0.25 + 0.75 * L / avg))
            if entities:
                s += 1.5 * len(set(entities) & set(p.get("entities", [])))
            if qv is not None and "vec" in p:
                s += 4.0 * sum(a * b for a, b in zip(qv, p["vec"]))
            scored.append((s, p))
        scored.sort(key=lambda x: -x[0])
        return [p for s, p in scored[:k] if s > 0]


# Backwards-compatible name used by older code.
EmbeddingStore = PassageStore
