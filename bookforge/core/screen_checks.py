"""Free, deterministic first-pass checks for the post-draft screen (no model calls).

All rules come from the chapter facts file (core/facts.py), so nothing here is book-specific:
  times vs the bible schedule, session subjects, afternoon-only lessons, other-character
  thoughts (POV), new named people per scene, mirrored/tricolon/epigram tells, and candidate
  line extraction for 'answers once'-style count rules.
Each finding: {check, section, evidence, detail, bible}.
"""
from __future__ import annotations

import re

WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
TIME_RE = re.compile(r"\b(?:at|by|until|till)\s+(?:(a quarter past|a quarter to|half past|twenty past|ten past)\s+)?"
                     r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,2}(?::\d\d)?)\b", re.I)
COGNITION = r"(felt|thought|wondered|knew|realised|realized|decided|hoped|feared|sensed|understood|wanted)"
DET = r"(?:the|a|an|his|her|their|its|only)"
TRI1 = re.compile(rf"\b({DET} [^,;.\"]{{1,40}}), ({DET} [^,;.\"]{{1,40}}),? and ({DET} [^,;.\"]{{1,40}})", re.I)
TRI2 = re.compile(r"\b([^,;.\"]{3,40}), and ([^,;.\"]{3,40}), and ([^,;.\"]{3,40})", re.I)
EPIGRAM = re.compile(r", (?:a|an) [\w' ]{2,25}, [\w' ]{2,20}\.$")
TIMETABLE_LINE = re.compile(r"^\s*(\d{1,2}:\d\d)(?:\s*-\s*(\d{1,2}:\d\d))?\s+(.*)$")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?\"'])\s+(?=[A-Z\"*])", text) if s.strip()]


def _hm(s: str) -> int:
    h, m = s.split(":")
    return (int(h) % 12) * 60 + int(m)


def _parse_time(prefix, num) -> int:
    n = num.lower()
    if n in WORDNUM:
        h, m = WORDNUM[n], 0
    elif ":" in n:
        h, m = map(int, n.split(":"))
    else:
        h, m = int(n), 0
    p = (prefix or "").lower()
    m = {"a quarter past": 15, "half past": 30, "twenty past": 20, "ten past": 10}.get(p, m)
    if p == "a quarter to":
        h, m = h - 1, 45
    return (h % 12) * 60 + m


def event_times(facts: dict) -> dict[str, list[str]]:
    """{event keyword: [allowed H:MM]} from facts.screen.event_times, or parsed from bible.timetable."""
    explicit = ((facts.get("screen") or {}).get("event_times")) or {}
    if explicit:
        return {k.lower(): v for k, v in explicit.items()}
    out: dict[str, list[str]] = {}
    for line in (facts.get("bible") or {}).get("timetable", []):
        m = TIMETABLE_LINE.match(line)
        if not m:
            continue
        start, desc = m.group(1), m.group(3)
        for kw in re.findall(r"\b([A-Z][A-Za-z-]+(?: School| Class)?)\b", desc):
            out.setdefault(kw.lower(), [])
            if start not in out[kw.lower()]:
                out[kw.lower()].append(start)
    return out


def _bible_line(facts: dict, keyword: str) -> str:
    for line in (facts.get("bible") or {}).get("timetable", []):
        if keyword.lower() in line.lower():
            return line
    return ""


def check_times(sid, text, facts):
    out, ev = [], event_times(facts)
    for s in sentences(text):
        low = s.lower()
        for m in TIME_RE.finditer(s):
            t = _parse_time(m.group(1), m.group(2))
            for kw, allowed in ev.items():
                if re.search(rf"\b{re.escape(kw)}\b", low) and not any(_hm(a) == t for a in allowed):
                    out.append({"check": "time_vs_timetable", "section": sid, "evidence": s[:240],
                                "detail": f"'{m.group(0)}' with '{kw}', bible has {', '.join(allowed)}",
                                "bible": _bible_line(facts, kw)})
    return out


def check_sessions(sid, text, facts):
    sc = facts.get("screen") or {}
    subjects = [x.lower() for x in sc.get("subjects", [])]
    sessions = {k.lower(): {x.lower() for x in v} for k, v in (sc.get("session_subjects") or {}).items()}
    afternoon = [x.lower() for x in sc.get("afternoon_only", [])]
    out = []
    for s in sentences(text):
        low = s.lower()
        for sess, allowed in sessions.items():
            if sess in low:
                bad = [x for x in subjects if re.search(rf"\b{x}\b", low) and x not in allowed]
                if bad:
                    out.append({"check": "session_subject", "section": sid, "evidence": s[:240],
                                "detail": f"{sess} with {bad}; bible allows {sorted(allowed)}",
                                "bible": _bible_line(facts, sess)})
        for subj in afternoon:
            if re.search(rf"\b{subj}\b", low) and "morning" in low:
                out.append({"check": "afternoon_only_in_morning", "section": sid, "evidence": s[:240],
                            "detail": f"'{subj}' is an afternoon-only slot but the sentence says 'morning'",
                            "bible": _bible_line(facts, subj)})
    return out


def check_pov(sid, text, facts):
    others = (facts.get("screen") or {}).get("non_pov_names") or []
    if not others:
        return []
    rx = re.compile(rf"\b({'|'.join(map(re.escape, others))})\b (?:had |could |must have )?{COGNITION}\b")
    out = []
    for s in sentences(text):
        if s.startswith('"'):
            continue
        m = rx.search(s)
        if m:
            out.append({"check": "pov_other_thoughts", "section": sid, "evidence": s[:240],
                        "detail": f"narration reports {m.group(1)}'s inner state ('{m.group(0)}')",
                        "bible": (facts.get("bible") or {}).get("pov", "")})
    return out


def check_tells(sid, text, facts):
    out = []
    ss = sentences(text)
    for s in ss:
        for rx, kind in ((TRI1, "tricolon 'the A, the B and the C'"), (TRI2, "polysyndeton 'A, and B, and C'")):
            m = rx.search(s)
            if m:
                out.append({"check": "rule_of_three", "section": sid, "evidence": m.group(0)[:200], "detail": kind,
                            "bible": "Style lock: no tricolons."})
        if len(re.findall(r"\b(they had seen|he had seen|she had seen)\b", s.lower())) >= 2:
            out.append({"check": "mirrored", "section": sid, "evidence": s[:240], "detail": "repeated clause in one sentence",
                        "bible": "Style lock: no mirrored lines."})
        if EPIGRAM.search(s):
            out.append({"check": "epigram_closer", "section": sid, "evidence": s[-160:], "detail": "appositive epigram ', a X, Y.'",
                        "bible": "Style lock: no tidy morals."})
    for a, b in zip(ss, ss[1:]):
        wa, wb = re.findall(r"\w+", a.lower())[:3], re.findall(r"\w+", b.lower())[:3]
        if len(wa) == 3 and wa == wb and not a.startswith('"'):
            out.append({"check": "mirrored", "section": sid, "evidence": (a + " " + b)[:260],
                        "detail": f"consecutive sentences both open '{' '.join(wa)}'", "bible": "Style lock: no mirrored lines."})
    return out


# ------------------------------------------------------------------ chapter-level
def new_people_per_scene(sections: list[dict], facts: dict) -> list[dict]:
    """First appearance per planned-new named person, counted per scene against facts.screen.max_new_per_scene."""
    kc = facts.get("known_characters") or {}
    planned = [n for n in kc.get("planned_new_in_this_chapter", []) if n and n[0].isupper()]
    limit = int((facts.get("screen") or {}).get("max_new_per_scene", 0) or 0)
    first: dict[str, tuple[int, str]] = {}
    for s in sections:
        for name in planned:
            key = name.split()[0]
            if name not in first and re.search(rf"\b{re.escape(key)}\b", s["text"]):
                first[name] = (s["scene"], s["sid"])
    per: dict[int, list] = {}
    for name, (scene, sid) in first.items():
        per.setdefault(scene, []).append((name, sid))
    out = []
    for scene, names in sorted(per.items()):
        out.append({"scene": scene, "new_named": [n for n, _ in names], "sections": sorted({s for _, s in names}),
                    "limit": limit, "over": bool(limit and len(names) > limit)})
    return out


def _speaker(p: str, who: str):
    if re.search(rf'"[^"]*"[,]? ({who}) (said|muttered|asked|answered|whispered|called)', p) or re.match(rf'^{who}\b[^"]*"', p):
        return who
    m = re.search(r'"[^"]*"[,]? (?:said |asked |muttered )?([A-Z][a-z]+)', p) or re.match(r'^([A-Z][a-z]+)\b[^"]*"', p)
    return m.group(1) if m else None


def character_lines(sections: list[dict], who: str, scenes: set[int] | None = None) -> list[dict]:
    """Dialogue paragraphs spoken by `who` (attributed, or unattributed in a who/other alternation)."""
    out = []
    for s in sections:
        if scenes and s["scene"] not in scenes:
            continue
        paras = [p.strip() for p in s["text"].split("\n") if p.strip()]
        spk = [(_speaker(p, who) if '"' in p else None) for p in paras]
        for i, p in enumerate(paras):   # resolve unattributed lines in a two-person alternation
            if spk[i] is None and p.startswith('"') and i >= 2 and spk[i - 2] and spk[i - 1] \
                    and spk[i - 1] != spk[i - 2] and paras[i - 1].startswith('"'):
                spk[i] = spk[i - 2]
        for i, p in enumerate(paras):
            if '"' not in p:
                continue
            w = spk[i]
            if w == who:
                out.append({"section": s["sid"], "scene": s["scene"], "line": p[:400], "context": paras[i - 1][:400] if i else ""})
    return out


def run(sections: list[dict], facts: dict) -> list[dict]:
    out = []
    for s in sections:
        for fn in (check_times, check_sessions, check_pov, check_tells):
            out += fn(s["sid"], s["text"], facts)
    return out
