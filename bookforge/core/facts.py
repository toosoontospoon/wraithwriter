"""Per-chapter facts file: the compact bible slice the post-draft screen sends to Jev and to the
revision packet. Lives next to the chapter plan (`<facts_dir>/<chapter>-facts.json`).

Generated from the project (outline scenes and beats, characters.json, the bible timetable file
and book-level screen config) and MAINTAINED: every regeneration rewrites the generated keys but
keeps any top-level key listed in "_manual" (hand-edited facts such as count_rules, a hand beat
plan, or extra canon lines). Add a key name to "_manual" to freeze your edits.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from bookforge.config import load_config

DEFAULT_MANUAL = ["count_rules"]
_BEAT_PREFIX = re.compile(r"^\(\s*~?\d+\s*w[^)]*\)\s*")


def scfg() -> dict:
    return load_config().get("screen") or {}


def facts_dir(ctx) -> Path:
    d = scfg().get("facts_dir")
    if d:
        p = Path(d)
        return p if p.is_absolute() else (ctx.project_dir / p)
    from bookforge.core import pack
    bd = pack.bible_dir(ctx)
    book = (ctx.metadata or {}).get("book")
    if bd and book and (Path(bd) / f"book{book}").is_dir():
        return Path(bd) / f"book{book}"
    return ctx.state_dir / "facts"


def path(ctx, chapter_id: str) -> Path:
    return facts_dir(ctx) / f"{chapter_id}-facts.json"


def _timetable(ctx) -> list[str]:
    f = scfg().get("timetable_file")
    if not f:
        return []
    from bookforge.core import pack
    p = Path(f)
    if not p.is_absolute():
        p = Path(pack.bible_dir(ctx) or ctx.project_dir) / p
    if not p.exists():
        return []
    out = []
    for line in p.read_text(errors="ignore").splitlines():
        m = re.match(r"^\s*[-*]\s*(\d{1,2}:\d\d(?:\s*-\s*\d{1,2}:\d\d)?\s+.+)$", line)
        if m:
            out.append(re.sub(r"\s+", " ", m.group(1)).strip()[:200])
    return out


TITLES = {"master", "lady", "lord", "sir", "mr", "mrs", "miss", "captain", "dame", "the", "a", "an", "instructor", "father"}


def _first_name(name: str) -> str:
    """'Master Simon Grey' -> 'Simon'; 'Tom \u201cAce\u201d Reed' -> 'Tom'; 'an usher' -> ''."""
    from bookforge.core.pack import norm_name
    for tok in norm_name(name).split():
        if tok not in TITLES:
            orig = next((w for w in re.findall(r"[A-Za-z'-]+", name) if w.lower() == tok), tok)
            return orig if orig[:1].isupper() else ""
    return ""


def _short(c: dict, n: int = 240) -> str:
    bits = [c.get("role", ""), c.get("age") and f"age {c['age']}", c.get("physical", ""), c.get("personality", "")]
    s = "; ".join(b for b in bits if b)
    return s[:n]


def generate(ctx, chapter: dict) -> dict:
    from bookforge.core import pack
    sc = scfg()
    cid = chapter["id"]
    scenes = chapter.get("scenes") or []
    pov = next((s.get("pov") for s in scenes if s.get("pov")), "")
    person = next((s.get("person") for s in scenes if s.get("person")), "third")
    chars = ctx.load_characters() or {}
    present: list[str] = []
    for s in scenes:
        for n in s.get("characters_present") or []:
            if n not in present:
                present.append(n)
    prior_text = "\n".join(ctx.best_chapter_text(o["id"]) or "" for o in ctx.load_outline() if o["id"] < cid)
    cast, before, planned_new, firsts = {}, [], [], []
    for n in present:
        c = pack.match_character(chars, n)
        name = c["name"] if c else n
        if c:
            cast[c["name"]] = _short(c)
        key = _first_name(name)
        words = [w for w in re.findall(r"[A-Za-z'-]+", re.sub(r"[\"\u201c\u201d][^\"\u201c\u201d]*[\"\u201c\u201d]", "", name))]
        if key and words and words[0].lower() not in ("the", "a", "an") and all(w[:1].isupper() for w in words):
            firsts.append(key)
        if not key or re.search(rf"\b{re.escape(key)}\b", prior_text):
            before.append(name)
        else:
            planned_new.append(name)
    plan_scenes = {}
    for s in scenes:
        n = s.get("scene_number")
        beats = {f"b{n}_{i + 1}": _BEAT_PREFIX.sub("", b)[:320] for i, b in enumerate(s.get("beats") or [])}
        plan_scenes[str(n)] = {"scene": s.get("action") or s.get("summary", ""), "place": s.get("location", ""),
                               "clock": s.get("time", ""), "beats": beats, "rules": []}
    pov_first = _first_name(pov) if pov else ""
    non_pov = sorted({f for f in firsts if f != pov_first})
    return {
        "chapter": cid,
        "bible": {"pov": f"Close {person} person, limited to {pov}. Only {pov_first}'s thoughts and feelings are narrated; everyone else is seen from outside." if pov else "",
                  "timetable": _timetable(ctx), "characters": cast, "canon": list(sc.get("canon", []))},
        "known_characters": {"before_this_chapter": before, "planned_new_in_this_chapter": planned_new},
        "plan_scenes": plan_scenes,
        "default_rules": list(sc.get("default_rules", [])),
        "count_rules": [],
        "screen": {"subjects": sc.get("subjects", []), "session_subjects": sc.get("session_subjects", {}),
                   "afternoon_only": sc.get("afternoon_only", []), "event_times": sc.get("event_times", {}),
                   "non_pov_names": non_pov, "max_new_per_scene": sc.get("max_new_per_scene", 0)},
        "_manual": list(DEFAULT_MANUAL),
    }


def load_or_generate(ctx, chapter: dict, regenerate: bool = True) -> dict:
    """Return the chapter facts, regenerating generated keys and keeping `_manual` keys."""
    p = path(ctx, chapter["id"])
    old = json.loads(p.read_text()) if p.exists() else None
    if old is not None and not regenerate:
        return old
    new = generate(ctx, chapter)
    if old:
        manual = old.get("_manual", DEFAULT_MANUAL)
        for k in manual:
            if k in old:
                new[k] = old[k]
        new["_manual"] = manual
    if new != old:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(new, indent=1, ensure_ascii=False))
        tmp.replace(p)
    return new
