"""Synonyms with no model call. WordNet is optional; tests pass a dict source."""
from __future__ import annotations

import re

_SUFF = ("ingly", "edly", "iest", "ier", "ing", "est", "ed", "es", "ly", "er", "s")


class SynonymError(RuntimeError):
    """nltk or the WordNet corpus is missing. Nothing is downloaded."""


class SynonymSource:
    """Interface. ``lemmas`` returns base forms, roughly matched to ``pos``."""

    def lemmas(self, word: str, pos: str | None = None) -> list[str]:
        raise NotImplementedError


class DictSynonymSource(SynonymSource):
    """Map of lower-case word (or ``word/pos``) to base-form synonyms."""

    def __init__(self, table: dict):
        self.table = {str(k).lower(): list(v) for k, v in (table or {}).items()}

    def lemmas(self, word: str, pos: str | None = None) -> list[str]:
        key = (word or "").lower()
        if pos and f"{key}/{pos}" in self.table:
            return list(self.table[f"{key}/{pos}"])
        return list(self.table.get(key, []))


def guess_pos(word: str) -> str | None:
    low = (word or "").lower()
    if low.endswith("ly") and len(low) > 4:
        return "r"
    if low.endswith(("ing", "ed")) and len(low) > 4:
        return "v"
    if low.endswith(("est", "er")) and len(low) > 4:
        return "a"
    return None


def _split_suffix(word: str) -> str:
    low = (word or "").lower()
    for suf in _SUFF:
        if low.endswith(suf) and len(low) - len(suf) >= 3:
            return suf
    return ""


def _add_suffix(lemma: str, suf: str) -> str:
    base = lemma or ""
    if not suf:
        return base
    if suf == "s":
        if base.lower().endswith(("s", "x", "z", "ch", "sh")):
            return base + "es"
        if len(base) > 1 and base.lower().endswith("y") and base[-2].lower() not in "aeiou":
            return base[:-1] + "ies"
        return base + "s"
    if suf == "es":
        if base.lower().endswith("e"):
            return base + "s"
        return _add_suffix(base, "s")
    if suf == "ed":
        if base.lower().endswith("e"):
            return base + "d"
        if len(base) > 1 and base.lower().endswith("y") and base[-2].lower() not in "aeiou":
            return base[:-1] + "ied"
        return base + "ed"
    if suf == "ing":
        if len(base) > 2 and base.lower().endswith("e") and not base.lower().endswith("ee"):
            return base[:-1] + "ing"
        return base + "ing"
    if suf == "ly":
        if len(base) > 1 and base.lower().endswith("y"):
            return base[:-1] + "ily"
        return base + "ly"
    if suf in ("er", "est"):
        if base.lower().endswith("e"):
            return base + suf[1:]
        if len(base) > 1 and base.lower().endswith("y") and base[-2].lower() not in "aeiou":
            return base[:-1] + "i" + suf
        return base + suf
    if suf == "ier":
        return _add_suffix(base, "er")
    if suf == "iest":
        return _add_suffix(base, "est")
    if suf in ("ingly", "edly"):
        return _add_suffix(_add_suffix(base, suf[:-2]), "ly")
    return base + suf


def copy_caps(surface: str, formed: str) -> str:
    if not formed:
        return formed
    if len(surface) > 1 and surface.isupper():
        return formed.upper()
    if surface[:1].isupper():
        return formed[:1].upper() + formed[1:]
    return formed[:1].lower() + formed[1:] if formed[:1].isupper() else formed


def inflect_like(surface: str, lemma: str) -> str:
    """Apply the surface word's ending and capitalisation to a synonym lemma."""
    return copy_caps(surface, _add_suffix(lemma, _split_suffix(surface)))


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


class WordNetSynonymSource(SynonymSource):
    """nltk WordNet. Imported lazily. Never downloads a corpus."""

    def lemmas(self, word: str, pos: str | None = None) -> list[str]:
        wn = _wordnet()
        lemma = _lookup_form(word)
        wanted = pos or guess_pos(word)
        wn_pos = {"n": wn.NOUN, "v": wn.VERB, "a": wn.ADJ, "r": wn.ADV}.get(wanted or "")
        try:
            synsets = wn.synsets(lemma, pos=wn_pos) if wn_pos else wn.synsets(lemma)
        except LookupError as exc:
            raise SynonymError(
                "WordNet corpus is not installed. Download is disabled; install the corpus offline."
            ) from exc
        out: list[str] = []
        for syn in synsets:
            if wn_pos and syn.pos() not in (wn_pos, "s" if wn_pos == "a" else wn_pos):
                continue
            for item in syn.lemmas():
                name = item.name().replace("_", " ")
                if name.lower() != lemma.lower() and name not in out:
                    out.append(name)
        return out


def _wordnet():
    try:
        from nltk.corpus import wordnet as wn
    except ImportError as exc:
        raise SynonymError(
            "nltk is not installed. Install the optional extra: pip install 'bookforge[latin_pivot]'"
        ) from exc
    try:
        wn.synsets("dog")
    except LookupError as exc:
        raise SynonymError(
            "WordNet corpus is not installed. Download is disabled; install the corpus offline."
        ) from exc
    return wn


def _lookup_form(word: str) -> str:
    """A rough lemma so WordNet is not asked for an inflected surface only."""
    low = (word or "").lower()
    suf = _split_suffix(low)
    if not suf:
        return low
    stem = low[: -len(suf)]
    if suf == "ing" and stem and not stem.endswith("e"):
        # making -> stem mak; try the stem plus e as well via synsets on the stem.
        return stem
    if suf == "ed" and stem.endswith("i"):
        return stem[:-1] + "y"
    if suf == "ied":
        return low[:-3] + "y"
    return stem or low


def suggest(surface: str, source: SynonymSource, tell_terms: set[str], *, limit: int = 8,
            pos: str | None = None) -> list[str]:
    """Inflected synonyms, dropping tells, long phrases, and duplicates."""
    pos = pos if pos is not None else guess_pos(surface)
    raw = source.lemmas(surface, pos)
    if not raw and pos:
        raw = source.lemmas(surface, None)
    out: list[str] = []
    seen = {surface.lower()}
    for lemma in raw:
        if _word_count(lemma) > 3:
            continue
        if lemma.lower() in tell_terms:
            continue
        formed = inflect_like(surface, lemma)
        if not formed or formed.lower() in tell_terms or formed.lower() in seen:
            continue
        # A multi-word inflection that grew past 3 words is dropped too.
        if _word_count(formed) > 3:
            continue
        seen.add(formed.lower())
        out.append(formed)
        if len(out) >= int(limit):
            break
    return out
