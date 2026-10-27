"""Sentence splits that keep a closing quote on its sentence.

Mr., Mrs., Dr. and St. are not treated as sentence ends. A closing quote after
. ! or ? stays with the sentence it closes (a split that consumes the quote
would leave the next sentence owning it).
"""
from __future__ import annotations

import re

_ABBREV = re.compile(r"\b(Mrs|Mr|Dr|St)\.")
_SPLIT = re.compile(r'(?<=[.!?]["\u201d\u2019])\s+|(?<=[.!?])\s+(?=[\u201c"A-Z])')


def split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences, preserving edge quotes and abbreviations."""
    raw = (text or "").strip()
    if not raw:
        return []
    protected = _ABBREV.sub(lambda m: m.group(1) + "<D>", raw)
    parts = [p.replace("<D>", ".").strip() for p in _SPLIT.split(protected) if p.strip()]
    return parts
