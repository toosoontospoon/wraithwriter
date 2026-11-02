"""Silence-motif checks for the latin_pivot path.

Latin stem patterns and the English always-flag list are data. Callers pass the
lists from config so they can be tuned without a code change. Matching skips
``<<SEED>>`` spans. These motifs are strong AI-prose tells in this pipeline's
outputs, so English hits are flagged for a later edit and are not auto-swapped.
"""
from __future__ import annotations

import re

from bookforge.core.latin_pivot.seeds import mask_seeds

MOTIF_INSTRUCTION = (
    "Do not use or describe silence or quiet as a motif."
)

# Tunable stem patterns. Word-bounded so silva, tactus, and mutare do not match.
LATIN_PATTERNS = [
    r"\bsilenti\w*",
    r"\bsil(e|u)\w*",
    r"\btac(e|i|u)\w*",
    r"\bquie(s|t)\w*",
    r"\bsusurr\w*",
    r"\bcontic\w*",
    r"\bobtic\w*",
    r"\bmut(us|a|um|i|ae|o|os|as|is)\b",
]

LATIN_LEMMAS = [
    "silentium", "silens", "silere", "tacere", "tacitus", "taciturnus",
    "quies", "quietus", "quiescere", "susurrus", "susurrare",
    "conticescere", "obticescere", "mutus", "silentio", "taceo", "conticuit",
]

ENGLISH_BAN = [
    "silence", "silent", "quiet", "quietly", "hush", "hushed", "stillness",
    "whisper", "whispered", "unsaid", "unspoken", "said nothing", "without a word",
]

ALWAYS_FLAG_PHRASES = [
    "silence", "silent", "silently", "quiet", "quietly", "quieted",
    "hush", "hushed", "still", "stillness", "unsaid", "unspoken",
    "words left unsaid", "said nothing", "without a word",
    "kept silent", "fell silent", "went quiet",
]

MOTIF_BAN_DEFAULT = {
    "enabled": True,
    "action": "regenerate",
    "max_regens": 1,
    "english": list(ENGLISH_BAN),
    "latin": list(LATIN_LEMMAS),
    "latin_patterns": list(LATIN_PATTERNS),
}

ALWAYS_FLAG_DEFAULT = {
    "enabled": True,
    "still_classifier": "jev",
    "phrases": list(ALWAYS_FLAG_PHRASES),
}

_INFLECT = r"(?:s|es|ed|ing|ly|er|est)?"
_ADVERB_NEXT = {"not", "more", "less", "there", "be"}
_AUX = {"is", "was", "were", "had", "has", "have", "will", "would", "could"}
_LINK = {"is", "was", "were", "am", "are", "be", "been"}
_STILL_VERBS = {
    "sit", "stand", "stay", "stood", "sat", "lay", "lie", "keep", "kept",
    "hold", "held", "grew", "went", "fell",
}
_DET = {"the", "a", "an", "this", "that", "my", "your", "his", "her", "its", "our", "their"}
_IRREGULAR_PARTICIPLE = {
    "been", "gone", "done", "seen", "known", "left", "made", "come", "begun",
    "taken", "given", "written", "fallen", "grown", "kept", "held",
}
_SENT = re.compile(r"[^.!?\n]+(?:[.!?]+)?")


def motif_instruction(english: list[str] | None = None, latin: list[str] | None = None) -> str:
    """Explicit ban for the Latin-draft prompt and the latin_pivot beat planner."""
    en = ", ".join(english if english is not None else ENGLISH_BAN)
    la = ", ".join(latin if latin is not None else LATIN_LEMMAS)
    return (
        f"{MOTIF_INSTRUCTION} "
        f"Do not use these English words or phrases: {en}. "
        f"Do not use these Latin stems or their inflections: {la}."
    )


def corrective_note(hits: list[dict]) -> str:
    """Name the hits so a regeneration can avoid them."""
    words = []
    for hit in hits:
        word = str(hit.get("word") or hit.get("matched_text") or "").strip()
        if word and word not in words:
            words.append(word)
    named = ", ".join(words) if words else "a banned silence word"
    return (
        "The previous draft used a banned silence motif "
        f"({named}). Rewrite without those words and without describing silence or quiet."
    )


def _compile(pattern: str) -> re.Pattern:
    try:
        return re.compile(pattern, re.I)
    except re.error as exc:
        raise ValueError(f"latin motif pattern is not a valid regex: {pattern}") from exc


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Blank-line paragraphs as ``(start, end)`` into ``text``."""
    if not (text or "").strip():
        return []
    spans = []
    for match in re.finditer(r"\S(?:.|\n)*?(?=\n\s*\n|\Z)", text):
        spans.append((match.start(), match.end()))
    return spans or [(0, len(text))]


def _sentence_at(text: str, start: int, end: int) -> tuple[int, str]:
    """``(sentence_index, sentence)`` for a span. Index is within the paragraph slice."""
    sentences = list(_SENT.finditer(text or ""))
    for index, match in enumerate(sentences):
        if match.start() <= start < match.end() or match.start() < end <= match.end():
            return index, match.group(0).strip()
    if sentences:
        return 0, sentences[0].group(0).strip()
    return 0, (text or "").strip()


def _drop_overlaps(hits: list[dict]) -> list[dict]:
    """Keep the longest hit when spans overlap. Earlier longer hits win."""
    ordered = sorted(hits, key=lambda hit: (hit["start"], -(hit["end"] - hit["start"])))
    kept: list[dict] = []
    occupied: list[tuple[int, int]] = []
    for hit in ordered:
        if any(hit["start"] < end and start < hit["end"] for start, end in occupied):
            continue
        occupied.append((hit["start"], hit["end"]))
        kept.append(hit)
    kept.sort(key=lambda hit: hit["start"])
    return kept


def latin_hits(text: str, stems: list[str] | None = None) -> list[dict]:
    """Case-insensitive stem hits outside seed spans.

    Each hit is ``paragraph_index``, ``word``, ``sentence``, plus ``start`` and
    ``end`` offsets into ``text``. ``stems`` are regex patterns (the config list).
    """
    raw = text or ""
    masked = mask_seeds(raw)
    patterns = [_compile(stem) for stem in (stems if stems is not None else LATIN_PATTERNS) if str(stem).strip()]
    found: list[dict] = []
    for paragraph_index, (pstart, pend) in enumerate(_paragraph_spans(raw)):
        region = masked[pstart:pend]
        paragraph = raw[pstart:pend]
        for cre in patterns:
            for match in cre.finditer(region):
                if not region[match.start():match.end()].strip():
                    continue
                abs_start = pstart + match.start()
                abs_end = pstart + match.end()
                _index, sentence = _sentence_at(paragraph, match.start(), match.end())
                found.append({
                    "paragraph_index": paragraph_index,
                    "word": raw[abs_start:abs_end],
                    "sentence": sentence,
                    "start": abs_start,
                    "end": abs_end,
                })
    return _drop_overlaps(found)


def _phrase_pattern(phrase: str) -> re.Pattern:
    parts = phrase.split()
    if not parts:
        return re.compile(r"(?!)")
    if len(parts) == 1 and parts[0].lower() == "still":
        return re.compile(r"\bstill\b", re.I)
    bits = [re.escape(part) for part in parts]
    bits[-1] = bits[-1] + _INFLECT
    return re.compile(r"\b" + r"\s+".join(bits) + r"\b", re.I)


def _word_before(sentence: str, start: int) -> str:
    match = re.search(r"([A-Za-z']+)\s*$", sentence[:start])
    return match.group(1).lower() if match else ""


def _word_after(sentence: str, end: int) -> str:
    match = re.match(r"\s*([A-Za-z']+)", sentence[end:])
    return match.group(1).lower() if match else ""


def _is_participle(word: str) -> bool:
    if not word:
        return False
    if word in _IRREGULAR_PARTICIPLE:
        return True
    return len(word) > 4 and word.endswith("ed")


def is_stillness_sense(sentence: str, span: tuple[int, int]) -> bool | None:
    """True stillness, False adverb ('yet'), None when the sentence is ambiguous.

    ``span`` is ``(start, end)`` of the word ``still`` inside ``sentence``.
    """
    start, end = span
    sentence = sentence or ""
    if sentence[:start].strip() == "" and re.match(r"\s*,", sentence[end:]):
        return False
    nxt = _word_after(sentence, end)
    prev = _word_before(sentence, start)
    if nxt in _ADVERB_NEXT or nxt.endswith("ing") or _is_participle(nxt):
        return False
    if prev in _AUX and (nxt.endswith("ing") or _is_participle(nxt)):
        return False
    of_match = re.search(r"\bthe\s+still\s+of\b", sentence, re.I)
    if of_match and of_match.start() <= start < of_match.end():
        return True
    if prev in _DET or prev.endswith("'s"):
        return True
    if prev in _STILL_VERBS:
        return True
    tail = sentence[end:].lstrip()
    if prev in _LINK and (tail == "" or tail[:1] in ",.;:!?"):
        return True
    return None


def _locate(text: str, start: int, end: int) -> tuple[int, str, tuple[int, int]]:
    """Sentence index, stripped sentence, and the span inside that sentence."""
    for index, match in enumerate(_SENT.finditer(text or "")):
        if match.start() <= start < match.end() or match.start() < end <= match.end():
            piece = match.group(0)
            pad = len(piece) - len(piece.lstrip())
            sentence = piece.strip()
            origin = match.start() + pad
            return index, sentence, (start - origin, end - origin)
    stripped = (text or "").strip()
    return 0, stripped, (start, end)


def match_always_flag(text: str, phrases: list[str] | None = None) -> list[dict]:
    """Always-flag phrase hits outside seed spans. ``still`` carries ``sense``.

    Offsets in ``span`` are character offsets in ``text``. Sentence index is
    within that text, so a single paragraph uses paragraph offsets. This
    function does not call Jev. ``sense`` is None only for an ambiguous ``still``.
    Adverb ``still`` is not returned.
    """
    raw = text or ""
    masked = mask_seeds(raw)
    phrases = list(phrases if phrases is not None else ALWAYS_FLAG_PHRASES)
    found: list[dict] = []
    for phrase in sorted((p for p in phrases if str(p).strip()), key=len, reverse=True):
        cre = _phrase_pattern(phrase)
        for match in cre.finditer(masked):
            if not masked[match.start():match.end()].strip():
                continue
            sentence_index, sentence, local = _locate(raw, match.start(), match.end())
            sense = None
            if phrase.lower() == "still":
                sense = is_stillness_sense(sentence, local)
                if sense is False:
                    continue
            found.append({
                "motif": phrase,
                "matched_text": raw[match.start():match.end()],
                "span": [match.start(), match.end()],
                "start": match.start(),
                "end": match.end(),
                "sentence_index": sentence_index,
                "sentence": sentence,
                "sense": sense,
                "ambiguous": phrase.lower() == "still" and sense is None,
            })
    return _drop_overlaps(found)


def paragraph_flags(paragraph: str, phrases: list[str] | None = None) -> list[dict]:
    """Always-flag hits whose ``span`` is relative to ``paragraph``."""
    return match_always_flag(paragraph, phrases)


def motif_density(text: str, matcher) -> dict:
    """``{hits, words, per_1k}``. ``per_1k`` is hits per 1,000 whitespace words.

    Eight hits in 570 words is 14.0 per 1k.
    """
    hits = list(matcher(text or ""))
    words = len((text or "").split())
    per = (len(hits) / words * 1000.0) if words else 0.0
    return {"hits": len(hits), "words": words, "per_1k": round(per, 1)}


def still_question(sentence: str) -> dict:
    """One Jev noul for an ambiguous ``still``. True means the stillness sense."""
    return {
        "stillness": {
            "type": "noul",
            "instructions": (
                "Is the word still, as used in this sentence, the stillness sense "
                "(quiet or motionless) rather than the adverb meaning yet, even now, "
                "or nevertheless?"
            ),
            "criteria": {
                "true": "Still here means quiet or motionless.",
                "false": "Still here means yet, even now, or nevertheless.",
            },
        }
    }


def still_state(sentence: str) -> dict:
    return {"sentence": sentence}
