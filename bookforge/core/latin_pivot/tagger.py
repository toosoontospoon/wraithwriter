"""Find tell-list words in English and ask Jev whether each one sounds like AI prose."""
from __future__ import annotations

import re

from bookforge.core.detector import STYLE_DIR, load_catalog
from bookforge.core.deai import banned_words
from bookforge.core.latin_pivot.seeds import mask_seeds

TAG_QUESTION = (
    "Is the word/phrase {term}, as used here, typical of AI-generated prose "
    "rather than natural human writing?"
)

_QUOTE_SPAN = re.compile(r'"[^"\n]*"|“[^”\n]*”|«[^»\n]*»')


def load_tell_terms() -> list[str]:
    """Words and phrases the de-AI lists already load. Longer phrases first."""
    terms: list[str] = []
    for rule in load_catalog():
        if rule.get("kind") in ("word", "phrase"):
            terms.extend(str(p) for p in rule.get("patterns") or [] if str(p).strip())
    terms.extend(banned_words())
    bulk = STYLE_DIR / "antislop_phrases.txt"
    if bulk.exists():
        terms.extend(l.strip() for l in bulk.read_text(encoding="utf-8").splitlines()
                     if l.strip() and not l.startswith("#"))
    ban = STYLE_DIR / "generated" / "deai_skill_banlist.txt"
    if ban.exists():
        for line in ban.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("GENERATED"):
                continue
            kind, rest = line.split(None, 1)
            if kind in ("word", "phrase"):
                term = rest.rsplit(None, 1)[0] if rest.endswith((" figurative", " verb")) else rest
                if term.strip():
                    terms.append(term.strip())
    seen, out = set(), []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            out.append(term)
    out.sort(key=len, reverse=True)
    return out


def _pattern(term: str) -> re.Pattern:
    esc = re.escape(term)
    pre = r"\b" if term[:1].isalnum() else ""
    post = r"\b" if term[-1:].isalnum() else ""
    return re.compile(pre + esc + post, re.I)


def mask_dialogue(text: str) -> str:
    return _QUOTE_SPAN.sub(lambda m: " " * len(m.group(0)), text)


def find_candidates(text: str, terms: list[str] | None = None, *, skip_dialogue: bool = False,
                    limit: int = 12) -> list[dict]:
    """Case-insensitive word-boundary hits outside seed spans, capped per paragraph."""
    terms = load_tell_terms() if terms is None else terms
    masked = mask_seeds(text or "")
    if skip_dialogue:
        masked = mask_dialogue(masked)
    occupied = [False] * len(masked)
    found: list[dict] = []
    for term in sorted(terms, key=len, reverse=True):
        if not term.strip():
            continue
        for match in _pattern(term).finditer(masked):
            if any(occupied[match.start():match.end()]):
                continue
            if not masked[match.start():match.end()].strip():
                continue
            for i in range(match.start(), match.end()):
                occupied[i] = True
            found.append({
                "text": text[match.start():match.end()],
                "start": match.start(),
                "end": match.end(),
                "term": term,
            })
    found.sort(key=lambda hit: hit["start"])
    return found[: max(0, int(limit))]


def tag_questions(candidates: list[dict]) -> dict:
    questions = {}
    for i, hit in enumerate(candidates):
        term = hit["text"]
        questions[f"c{i}"] = {
            "type": "noul",
            "instructions": TAG_QUESTION.format(term=term),
            "criteria": {
                "true": f"The word/phrase {term} as used here is typical of AI-generated prose.",
                "false": "It reads as natural human writing in this use.",
            },
        }
    return questions


def tag_state(paragraph: str) -> dict:
    return {"paragraph": paragraph}
