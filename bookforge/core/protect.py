"""Protected planned hints. FACT:/EVENT: hints always; exact KEEP/SUBTEXT lines only when opted in.

Mark them in a plan (chapter or scene beats, summary, voice_notes) as
    KEEP: "line one" / "line two"
    SUBTEXT: "line that reads innocently now"
or list them in chapter["protected_lines"]. The critic is told not to flag them (and any finding that
targets one is dropped); the editor is told not to touch them, and any protected line present in the
draft but missing after the edit is restored paragraph-for-paragraph from the draft.

Exact-line protection (KEEP/SUBTEXT/protected_lines) is OFF by default (design decision: plans give tone
and intent, never lines). Opt in with chapter["protect_exact_lines"] = True or env
BOOKFORGE_PROTECT_EXACT_LINES=1. FACT:/EVENT: hints (and chapter["protected_facts"]) always work: the critic
and editor are told to keep those facts/events on the page, with no wording enforced.
"""
from __future__ import annotations

import os
import re
from difflib import SequenceMatcher

_MARK = re.compile(r"\b(KEEP|SUBTEXT)\s*:\s*(.*?)(?=\b(?:KEEP|SUBTEXT)\s*:|$)", re.S)
_QUOTE = re.compile(r"[\"\u201c]([^\"\u201c\u201d]{3,}?)[\"\u201d]")


def norm(s: str) -> str:
    s = (s or "").lower().replace("\u2019", "'").replace("\u2018", "'")
    s = re.sub(r"[^\w' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _texts(chapter: dict):
    yield chapter.get("summary", "")
    for s in chapter.get("scenes") or []:
        if isinstance(s, dict):
            yield from (b for b in s.get("beats") or [] if isinstance(b, str))
            yield from (v for v in s.get("voice_notes") or [] if isinstance(v, str))
            yield s.get("action", "")


_FACT = re.compile(r"\b(FACT|EVENT)\s*:\s*(.*?)(?=\b(?:FACT|EVENT|KEEP|SUBTEXT)\s*:|$)", re.S)


def exact_enabled(chapter: dict | None = None) -> bool:
    if chapter and "protect_exact_lines" in chapter:
        return bool(chapter["protect_exact_lines"])
    return os.environ.get("BOOKFORGE_PROTECT_EXACT_LINES", "").strip().lower() in ("1", "true", "yes", "on")


def facts(chapter: dict) -> list[str]:
    """FACT:/EVENT: hints: facts or events that must stay on the page (wording free)."""
    out = [x for x in chapter.get("protected_facts") or [] if isinstance(x, str) and x.strip()]
    for t in _texts(chapter):
        for m in _FACT.finditer(t or ""):
            v = m.group(2).strip().strip(";.").strip()
            if v:
                out.append(v)
    seen, res = set(), []
    for x in out:
        if norm(x) and norm(x) not in seen:
            seen.add(norm(x))
            res.append(x)
    return res


def protected(chapter: dict) -> list[str]:
    """Exact protected lines; empty unless exact-line protection is opted in."""
    if not exact_enabled(chapter):
        return []
    out = [x for x in chapter.get("protected_lines") or [] if isinstance(x, str) and x.strip()]
    for t in _texts(chapter):
        for m in _MARK.finditer(t or ""):
            out += [q.strip() for q in _QUOTE.findall(m.group(2))]
    seen, res = set(), []
    for x in out:
        if norm(x) and norm(x) not in seen:
            seen.add(norm(x))
            res.append(x)
    return res


def hits(quote: str, prot: list[str]) -> bool:
    """Does a critic/editor quote target a protected line?"""
    q = norm(quote)
    if not q:
        return False
    for p in map(norm, prot):
        if p in q or q in p:
            return True
        if len(q) > 15 and SequenceMatcher(None, q, p).ratio() >= 0.6:
            return True
    return False


def filter_spots(spots: list[dict], prot: list[str], text_fields=("location", "quote", "problem")) -> tuple[list, list]:
    """Drop weak spots / rule failures that target a protected line. Returns (kept, dropped)."""
    if not prot:
        return list(spots), []
    kept, dropped = [], []
    for w in spots:
        loc = w.get("location") or w.get("quote") or ""
        target = hits(loc, prot) or any(norm(p) in norm(w.get("problem", "")) for p in prot if len(norm(p)) > 12)
        (dropped if target else kept).append(w)
    return kept, dropped


def block(prot: list[str], fact_list: list[str] | None = None) -> str:
    parts = []
    if fact_list:
        parts.append("PROTECTED FACTS/EVENTS (planned on purpose; each must still happen or be true on the page. "
                     "Wording is free; do not cut the fact or event itself):\n" + "\n".join(f"- {f}" for f in fact_list))
    if prot:
        parts.append(_lines_block(prot))
    return "\n\n".join(parts)


def _lines_block(prot: list[str]) -> str:
    return ("PROTECTED LINES (planned on purpose: hints and subtext that must read innocently now and pay off later. "
            "Do NOT flag, cut, soften, explain or reword them; edit around them):\n" + "\n".join(f'- "{p}"' for p in prot))


def _paras(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def restore(before: str, after: str, prot: list[str]) -> tuple[str, list[str]]:
    """Put back any protected line the edit removed (present in `before`, absent in `after`)."""
    if not prot:
        return after, []
    nb, na = norm(before), norm(after)
    missing = [p for p in prot if norm(p) in nb and norm(p) not in na]
    if not missing:
        return after, []
    bp, ap = _paras(before), _paras(after)
    restored = []
    for p in missing:
        np_ = norm(p)
        if np_ in norm("\n\n".join(ap)):
            continue
        k = next(i for i, x in enumerate(bp) if np_ in norm(x))
        src = bp[k]
        scores = [SequenceMatcher(None, norm(src), norm(x)).ratio() for x in ap]
        j = max(range(len(ap)), key=scores.__getitem__) if ap else -1
        if j >= 0 and scores[j] >= 0.35:
            ap[j] = src
        else:  # paragraph was deleted: insert after the edited twin of its predecessor
            prev = bp[k - 1] if k else ""
            ps = [SequenceMatcher(None, norm(prev), norm(x)).ratio() for x in ap] if prev else []
            at = (max(range(len(ap)), key=ps.__getitem__) + 1) if ps else 0
            ap.insert(at, src)
        restored.append(p)
    return "\n\n".join(ap), restored
