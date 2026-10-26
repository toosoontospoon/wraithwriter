"""Deterministic AI-prose tell detector (stage A of the anti-AI pass).

Loads the catalog in bookforge/style/ai_tells.yaml (+ antislop_phrases.txt), scans text and
returns flagged spans with reasons and fix guidance, density per 1k words and a 0-10 score.
No LLM calls. ``autofix`` applies only trivially safe edits (token scrub + catalog autofix swaps).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

STYLE_DIR = Path(__file__).resolve().parent.parent / "style"
_QUOTE = re.compile(r'"[^"\n]*"')
_FIGURATIVE_LANDSCAPE = (
    r"\b(?:the|a|an|this|that|our|their|its|his|her)\s+(?:political|digital|emotional|media|"
    r"business|economic|cultural|social|competitive|regulatory|technological|corporate|financial|"
    r"moral|strategic|mental|psychological|intellectual|ideological|geopolitical|legal|educational|"
    r"global|national|industrial|healthcare)\s+landscape\b",
    r"\blandscape of (?:the |a |an )?(?:power|grief|fear|opportunity|possibility|ideas|idea|change|"
    r"meaning|identity|politics|emotion|emotions|discourse|control|influence|memory|desire|"
    r"uncertainty|choice|trust|conflict|innovation|technology|data|opinion|thought|knowledge|risk|"
    r"competition|culture|society|ambition|success|failure|progress|experience|policy|commerce|"
    r"finance|information|security|privacy|attention|narrative|morality|ethics)\b",
)
_LEVERAGE_VERB = (
    r"\b(?:leveraged|leveraging|leverages)\b",
    r"\b(?:to|can|could|will|would|shall|should|may|might|must|we|they|you|i)\s+(?:not\s+)?leverage\b",
)


def _bounded_pattern(term: str) -> str:
    """Case-insensitive phrase pattern. A trailing word-boundary after punctuation never matches."""
    esc = re.escape(term)
    pre = r"\b" if term[:1].isalnum() else ""
    post = r"\b" if term[-1:].isalnum() else ""
    return pre + esc + post


def _banlist_rows() -> list[tuple[str, str, str]]:
    path = STYLE_DIR / "generated" / "deai_skill_banlist.txt"
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("GENERATED") or line.startswith("#"):
            continue
        kind, rest = line.split(None, 1)
        qual = ""
        if kind == "word":
            parts = rest.rsplit(None, 1)
            if len(parts) == 2 and parts[1] in ("figurative", "verb"):
                rest, qual = parts
        rows.append((kind, rest, qual))
    return rows


def _skill_banlist_rules() -> list[dict]:
    """Generated skill banlist. Words are severity 2; assistant phrases are severity 3.
    landscape is figurative-only and leverage is verb-only. Narration only."""
    words, extra, phrases = [], [], []
    for kind, term, qual in _banlist_rows():
        if kind == "phrase":
            phrases.append(term)
        elif qual == "figurative" and term.lower() == "landscape":
            extra.extend(_FIGURATIVE_LANDSCAPE)
        elif qual == "verb" and term.lower() == "leverage":
            extra.extend(_LEVERAGE_VERB)
        else:
            words.append(term)
    rules = []
    if words or extra:
        patterns = []
        if words:
            patterns.append(r"\b(?:" + "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True)) + r")\b")
        patterns.extend(extra)
        rules.append({
            "id": "skill_banlist", "category": "word_choice", "kind": "regex", "severity": 2,
            "where": "narration", "patterns": patterns,
            "fix": "Banned word in narration (skill banlist). Use a plain, specific word.",
            "_rx": [re.compile(p, re.I) for p in patterns],
        })
    if phrases:
        patterns = [_bounded_pattern(p) for p in phrases]
        rules.append({
            "id": "skill_banlist", "category": "stock_phrase", "kind": "regex", "severity": 3,
            "where": "narration", "patterns": patterns,
            "fix": "Assistant phrase in narration (skill banlist). Delete it.",
            "_rx": [re.compile(p, re.I) for p in patterns],
        })
    return rules


def _fragment_max_ratio() -> float:
    from bookforge.config import load_config
    try:
        return float((load_config().get("deai") or {}).get("fragment_max_ratio", 0.02))
    except (TypeError, ValueError):
        return 0.02


def narration_fragment_ratio(paras: list[str]) -> tuple[float, int, int]:
    """Narration fragments over narration sentences. Quoted dialogue is not counted."""
    frag = sent = 0
    for p in paras:
        nar = _mask_dialogue(p)
        for s in _SENT.split(nar.strip()):
            if not s.strip() or not _WORD.findall(s):
                continue
            sent += 1
            if len(_WORD.findall(s)) <= 6 and not _VERBISH.search(s):
                frag += 1
    return (frag / sent if sent else 0.0), frag, sent


_QMAP = str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2018": "'", "\u2019": "'"})
_SENT = re.compile(r"(?<!\bSt\.)(?<!\bMr\.)(?<!\bDr\.)(?<!\bMt\.)(?<!\bMrs\.)(?<!\bMs\.)(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z']+")
SEV_WEIGHT = {1: 0.5, 2: 1.0, 3: 2.0}

STRUCT_MSG = {}


@lru_cache(maxsize=4)
def load_catalog(path: str | None = None) -> list[dict]:
    p = Path(path) if path else STYLE_DIR / "ai_tells.yaml"
    rules = yaml.safe_load(p.read_text()) or []
    for r in rules:
        r.setdefault("severity", 1)
        r.setdefault("where", "any")
        if r["kind"] == "word":
            words = sorted({w.lower() for w in r.get("patterns", [])}, key=len, reverse=True)
            r["_rx"] = [re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)]
        elif r["kind"] == "phrase":
            r["_rx"] = [re.compile(r"\b" + re.escape(x) + r"\b", re.I) for x in r.get("patterns", [])]
        elif r["kind"] == "regex":
            r["_rx"] = [re.compile(x, re.I | re.M) for x in r.get("patterns", [])]
        else:
            r["_rx"] = []
    bulk = STYLE_DIR / "antislop_phrases.txt"
    if bulk.exists() and not path:
        phr = [l.strip() for l in bulk.read_text().splitlines() if l.strip() and not l.startswith("#")]
        rules.append({"id": "antislop_phrase", "category": "stock_phrase", "kind": "phrase", "severity": 1,
                      "where": "narration", "patterns": phr,
                      "fix": "Common LLM-fiction phrase (antislop list). Rephrase in fresher, specific words.",
                      "_rx": [re.compile(r"\b(" + "|".join(re.escape(x) for x in sorted(phr, key=len, reverse=True))
                                         + r")\b", re.I)]})
    if not path:
        rules.extend(_skill_banlist_rules())
    return rules


def rule_index() -> dict[str, dict]:
    return {r["id"]: r for r in load_catalog()}


def split_paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _mask_dialogue(p: str) -> str:
    return _QUOTE.sub(lambda m: " " * len(m.group(0)), p)


def _mask_narration(p: str) -> str:
    out = [" "] * len(p)
    for m in _QUOTE.finditer(p):
        out[m.start():m.end()] = m.group(0)
    return "".join(out)


def _hit(rule, para_i, start, end, text, detail=None):
    return {"rule": rule["id"], "category": rule["category"], "severity": rule["severity"],
            "para": para_i, "start": start, "end": end, "span": text[start:end][:120],
            "reason": detail or f"{rule['category']}: '{text[start:end][:60]}'", "fix": rule.get("fix", "")}


# ------------------------------------------------------------------ structural heuristics
_VERBISH = re.compile(
    r"\b(is|was|were|are|am|be|been|had|has|have|did|do|does|could|would|should|might|must|can|will|\w+n't|\w+'s|\w+'re|\w+'d|"
    r"\w+ed|\w+s|went|came|saw|said|took|made|felt|knew|thought|ran|stood|sat|got|let|put|held|left|"
    r"kept|told|found|gave|began|seemed|looked|turned|moved|fell|rose|heard|brought|arose|awoke|bore|beat|became|befell|begot|bent|bet|bid|bit|bled|blew|broke|bred|built|burnt|burst|bought|cast|caught|chose|clung|cost|crept|cut|dealt|dug|dove|drew|dreamt|drank|drove|dwelt|ate|fed|fought|fled|flung|flew|forbade|forgot|forgave|froze|grew|hung|hid|hit|hurt|knelt|knit|laid|led|leapt|lent|lay|lit|lost|meant|met|paid|quit|read|rode|rang|ran|sank|sang|sat|shook|shone|shot|shrank|shut|slew|slept|slid|slung|slit|smote|sold|sent|set|sewed|shed|spoke|sped|spent|spun|spat|split|spread|sprang|stood|stole|stuck|stung|stank|strode|struck|strove|swore|swept|swam|swung|took|taught|tore|thought|threw|thrust|trod|understood|woke|wore|wove|wept|won|wound|wrung|wrote)\b", re.I)
_MORAL = re.compile(r"\b(realized|understood|knew (then|now)|learned that|(that|it) (meant|mattered)|was enough|"
                    r"never again|for the first time|somehow|truly|perhaps|maybe|would remember|"
                    r"that was what|that was the|lesson|forgive|never be the same|only the beginning|"
                    r"just beginning|in that moment)\b", re.I)
_SENSE = re.compile(r"\b(smell|scent|stench|odor|aroma|sound|taste|tang|hum|whir|creak|feel|texture|cold|warmth|"
                    r"light|glow|shadow)s?\b", re.I)
_EXPLAIN = re.compile(r"\b(because|as if|as though|which meant|the way (she|he|they) did when|it was (the )?"
                      r"(fear|anger|grief|shame|guilt|relief|love|hope))\b", re.I)
_EMO = re.compile(r"\b(fear|anger|grief|shame|guilt|relief|love|hope|sadness|joy|dread|longing|jealousy|pride|"
                  r"embarrass\w*|afraid|angry|ashamed|guilty|relieved|hurt|lonely)\b", re.I)
_TRIPLE = re.compile(r"\b[\w' -]{2,30}, [\w' -]{2,30},? and [\w' -]{2,30}[.,!?]", re.I)
_THESIS_OPEN = re.compile(
    r"\b(?:was|is|were|are) the kind of\b"
    r"|\b(?:is|was|were) a place\b"
    r"|\bthere are (?:two|three|four|five|six|seven|eight|nine|ten|\d+) kinds\b"
    r"|\b(?:are|were) built on\b"
    r"|\bthe routine (?:is|was)\b",
    re.I)
_COPULA_FIRST = re.compile(
    r"^(?:there (?:are|were|is|was)\b|(?:[A-Z][\w']*(?:\s+[A-Z][\w']*){0,4}\s+)(?:is|was|were|are)\b)")
_CONCRETE_ACTION = re.compile(
    r"\b(?:pulled|grabbed|seized|ran|walked|opened|closed|shut|slammed|threw|caught|held|"
    r"lifted|dropped|stepped|turned|looked|glanced|said|asked|shouted|whispered|took|put|"
    r"picked|pushed|climbed|sat|stood|knelt|reached|struck|hit|cut|poured|lit|broke|fell|"
    r"drove|rowed|wrote|read|ate|drank|woke|knocked|locked|fired|aimed|ducked|checked|"
    r"checks|stirred|worked|handed|gave|set|wiped|swore|yanked|hauled|dragged|kicked|"
    r"leaned|crossed|entered|left|watched|listened|tied|shoved|slid|went|came|started|"
    r"stopped|called|answered|nodded|shook|carried|brought|sent|kept|let|made|told|found|"
    r"puts|put|drives|drove|runs|ran)\b",
    re.I)
_LIST_PREAMBLE = re.compile(
    r"\b(?:two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?:things|reasons|habits|rules|ways|kinds)\b",
    re.I)
_SCENE_MARK = re.compile(r"\s*(?:\* \* \*|={3,}|#+.*)\s*")


def _narration_sentences(p: str) -> list[str]:
    nar = _mask_dialogue(p)
    return [s.strip() for s in _SENT.split(nar.strip()) if s.strip() and _WORD.findall(s)]


def _low_len_variance(sents: list[str]) -> bool:
    counts = [len(_WORD.findall(s)) for s in sents]
    if len(counts) < 2:
        return False
    mean = sum(counts) / len(counts)
    if mean < 4:
        return False
    var = sum((c - mean) ** 2 for c in counts) / len(counts)
    return (var ** 0.5) / mean <= 0.35


def polished_intro_evidence(p: str) -> str | None:
    """First paragraph of a chapter or scene that opens as a thesis, definition, or summary.

    A hit needs no dialogue, and either a thesis pattern ("was the kind of", "is a place",
    "there are two kinds", "the routine is") or a copula definition in sentence 1 with no
    concrete action there and low sentence-length variance.
    """
    if not p or _SCENE_MARK.fullmatch(p) or '"' in p or "\u201c" in p:
        return None
    sents = _narration_sentences(p)
    if not sents:
        return None
    first = sents[0]
    if _THESIS_OPEN.search(p):
        return first[:180]
    if (_COPULA_FIRST.match(first) and not _CONCRETE_ACTION.search(first)
            and len(sents) >= 2 and _low_len_variance(sents)):
        return first[:180]
    return None


def _opening_word(s: str) -> str:
    m = re.match(r"[\s\"']*([A-Za-z']+)", s)
    return m.group(1).lower() if m else ""


_TRICOLON_LEADS = {"the", "a", "an", "it", "we", "they", "he", "she", "i", "you", "my", "our", "their",
                   "his", "her", "its", "this", "that", "these", "those", "no", "every", "each", "some"}


def _closing_tricolon(s: str) -> bool:
    m = re.match(r"^(.+?), (.+?), and (.+?)\.$", s.strip())
    if not m:
        return False
    parts = m.groups()
    if any("," in part for part in parts[:2]):
        return False
    counts = [len(_WORD.findall(part)) for part in parts]
    if not all(2 <= c <= 12 for c in counts):
        return False
    # A plain action series ("walked in, pulled the gate shut, and went home") is not a summary.
    # Keep it only when the last two items are parallel phrases or fresh clauses.
    a, b = (part.split()[0].lower().strip(",;:") for part in parts[1:])
    return a == b or (a in _TRICOLON_LEADS and b in _TRICOLON_LEADS)


def parallel_list_ending_evidence(p: str) -> str | None:
    """Last paragraph of a scene or chapter that closes as a tidy parallel list.

    Hits: three or more consecutive short sentences with the same opening word; an
    "N things/reasons/habits" preamble followed by short same-shaped sentences; or a
    closing tricolon summary.
    """
    if not p or _SCENE_MARK.fullmatch(p) or p.count('"') >= 2 or "\u201c" in p:
        return None
    sents = _narration_sentences(p)
    if not sents:
        return None
    return _parallel_list_match(sents)


def _parallel_list_match(sents: list[str]) -> str | None:
    words = [_opening_word(s) for s in sents]
    run_at = 0
    for i in range(1, len(words) + 1):
        if i < len(words) and words[i] and words[i] == words[run_at] and len(words[i]) >= 2:
            continue
        length = i - run_at
        chunk = sents[run_at:i]
        if length >= 3 and all(len(_WORD.findall(s)) <= 16 for s in chunk):
            return " ".join(chunk)[:180]
        run_at = i
    if _LIST_PREAMBLE.search(sents[0]) and len(sents) >= 4:
        rest = sents[1:]
        counts = [len(_WORD.findall(s)) for s in rest]
        if len(rest) >= 3 and max(counts) <= 12 and max(counts) - min(counts) <= 5:
            return " ".join(sents)[:180]
    if _closing_tricolon(sents[-1]):
        return sents[-1][:180]
    return None


def _structural(paras: list[str], rules: dict, scene_ends: set[int], scene_starts: set[int]) -> list[dict]:
    out = []
    R = rules
    words_total = max(1, sum(len(p.split()) for p in paras))
    one_liners, rq_paras = [], []
    for i, p in enumerate(paras):
        nar = _mask_dialogue(p)
        sents = [s for s in _SENT.split(nar.strip()) if s.strip()]
        first = sents[0].strip() if sents else ""
        if "rhetorical_opener" in R and first.endswith("?") and i in scene_starts | {0}:
            out.append(_hit(R["rhetorical_opener"], i, 0, len(first), p, f"opens with question: '{first[:60]}'"))
        qs = [s for s in sents if s.strip().endswith("?")]
        if "rhetorical_cluster" in R and len(qs) >= 2:
            out.append(_hit(R["rhetorical_cluster"], i, 0, min(len(p), 120), p, f"{len(qs)} narrative questions"))
            rq_paras.append(i)
        if "fragment" in R:
            for s in sents:
                w = _WORD.findall(s)
                if 0 < len(w) <= 6 and not _VERBISH.search(s):
                    st = p.find(s.strip()[:20])
                    out.append(_hit(R["fragment"], i, max(0, st), max(0, st) + len(s.strip()), p,
                                    f"fragment: '{s.strip()[:60]}'"))
        wc = len(p.split())
        if wc <= 12 and '"' not in p and "==" not in p:
            one_liners.append(i)
        if "em_dash" in R:
            for m in re.finditer(r"[\u2014\u2013]|(?<=\w)--(?=\w|\s)", p):
                out.append(_hit(R["em_dash"], i, m.start(), m.end(), p, "em/en dash"))
        if "semicolon_heavy" in R and nar.count(";") >= 2:
            out.append(_hit(R["semicolon_heavy"], i, 0, min(len(p), 80), p, f"{nar.count(';')} semicolons"))
        if "ellipsis_heavy" in R and (p.count("...") + p.count("\u2026")) >= 3:
            out.append(_hit(R["ellipsis_heavy"], i, 0, min(len(p), 80), p, "3+ ellipses"))
        if "rule_of_three" in R:
            tri = list(_TRIPLE.finditer(nar))
            if len(tri) >= 2:
                out.append(_hit(R["rule_of_three"], i, tri[0].start(), tri[0].end(), p, f"{len(tri)} triplet lists"))
        if "emotion_after_show" in R and len(sents) >= 2:
            last = sents[-1]
            if _EMO.search(last) and _EXPLAIN.search(last):
                st = p.rfind(last.strip()[:20])
                out.append(_hit(R["emotion_after_show"], i, max(0, st), max(0, st) + len(last), p,
                                f"explains emotion: '{last.strip()[:60]}'"))
        if "scene_end_moral" in R and i in scene_ends and sents:
            last = sents[-1].strip()
            if not p.rstrip().endswith('"') and _MORAL.search(last) and len(_WORD.findall(last)) >= 4:
                st = p.rfind(last[:20])
                out.append(_hit(R["scene_end_moral"], i, max(0, st), max(0, st) + len(last), p,
                                f"reflective close: '{last[:70]}'"))
        if "sensory_list_opening" in R and i in scene_starts | {0}:
            senses = {m.group(1).lower() for m in _SENSE.finditer(" ".join(sents[:3]))}
            if len(senses) >= 3:
                out.append(_hit(R["sensory_list_opening"], i, 0, min(len(p), 100), p,
                                f"opening sense catalog: {sorted(senses)}"))
        if "polished_intro" in R and i in scene_starts:
            ev = polished_intro_evidence(p)
            if ev:
                st = p.find(ev[:40])
                out.append(_hit(R["polished_intro"], i, max(0, st), max(0, st) + len(ev), p,
                                f"polished intro: '{ev[:70]}'"))
        if "parallel_list_ending" in R and i in scene_ends:
            ev = parallel_list_ending_evidence(p)
            if ev:
                st = p.find(ev[:40])
                out.append(_hit(R["parallel_list_ending"], i, max(0, st), max(0, st) + len(ev), p,
                                f"parallel-list ending: '{ev[:70]}'"))
    if "one_line_punch" in R and len(paras) >= 6:
        ratio = len(one_liners) / len(paras)
        per_1k = len(one_liners) * 1000 / words_total
        if ratio > 0.25 and per_1k > 6:
            for i in one_liners:
                out.append(_hit(R["one_line_punch"], i, 0, len(paras[i]), paras[i],
                                f"one-line paragraph ({len(one_liners)}/{len(paras)} paragraphs)"))
    if "dialogue_rhythm" in R:
        # ping-pong runs: 6+ consecutive short, quote-led paragraphs of similar length
        run = []
        for i, p in enumerate(paras + [""]):
            s = p.strip()
            short = s.startswith('"') and len(s.split()) <= 16
            if short:
                run.append(i)
                continue
            if len(run) >= 6:
                lens = [len(paras[j].split()) for j in run]
                if max(lens) - min(lens) <= 10:
                    out.append(_hit(R["dialogue_rhythm"], run[0], 0, min(len(paras[run[0]]), 80), paras[run[0]],
                                    f"{len(run)} back-to-back short lines in one rhythm"))
            run = []
    if "quip_cluster" in R:
        # 4+ consecutive bare one-liners (<= 9 words, little or no narration): sitcom volley
        run = []
        for i, p in enumerate(paras + [""]):
            s = p.strip()
            nar_words = len(_mask_dialogue(s).split()) - s.count('"') // 2 * 0
            if s.startswith('"') and len(s.split()) <= 9 and nar_words <= 4:
                run.append(i)
                continue
            if len(run) >= 4:
                out.append(_hit(R["quip_cluster"], run[0], 0, min(len(paras[run[0]]), 80), paras[run[0]],
                                f"{len(run)} quip lines in a row"))
            run = []
    if "narration_block" in R:
        run, wc = [], 0
        for i, p in enumerate(paras + ['"']):
            if '"' not in p and "==" not in p and p.strip() != "* * *":
                run.append(i)
                wc += len(p.split())
                continue
            if len(run) >= 5 and wc >= 380:
                out.append(_hit(R["narration_block"], run[0], 0, min(len(paras[run[0]]), 80), paras[run[0]],
                                f"{len(run)} paragraphs / {wc} words of unbroken narration"))
            run, wc = [], 0
    if "quip_density" in R:
        lines = [m.group(0) for p in paras for m in _QUOTE.finditer(p)]
        if len(lines) >= 10:
            short = [l for l in lines if len(l.split()) <= 7]
            if len(short) / len(lines) > 0.8:
                out.append(_hit(R["quip_density"], 0, 0, 0, "", f"{len(short)}/{len(lines)} dialogue lines <= 7 words"))
    return out


# ------------------------------------------------------------------ public API
def detect(text: str, allow: list[str] | None = None, known_names: list[str] | None = None) -> dict:
    """Scan text. ``allow``: phrases to ignore (style-bible allowlist). ``known_names``: cast names
    exempt from the stock_names rule."""
    rules = load_catalog()
    R = {r["id"]: r for r in rules}
    # analyse with straight quotes (same length, so offsets still match the original text);
    # em dashes are still caught by the structural em_dash rule on the original characters
    text = text.translate(_QMAP)
    allow_rx = [re.compile(r"\b" + re.escape(a) + r"\b", re.I) for a in (allow or []) if a.strip()]
    known = {n.lower() for n in (known_names or [])}
    paras = split_paragraphs(text)
    scene_starts, scene_ends = {0}, {len(paras) - 1}
    for i, p in enumerate(paras):
        if re.fullmatch(r"\s*(\* \* \*|={3,}|#+.*)\s*", p):
            scene_ends.add(i - 1)
            scene_starts.add(i + 1)
    hits = []
    for i, p in enumerate(paras):
        views = {"any": p, "narration": _mask_dialogue(p), "dialogue": _mask_narration(p)}
        for r in rules:
            if not r["_rx"]:
                continue
            v = views.get(r["where"], p)
            for rx in r["_rx"]:
                for m in rx.finditer(v):
                    if m.end() <= m.start():
                        continue
                    s = p[m.start():m.end()]
                    if any(a.search(s) or a.search(p[max(0, m.start() - 40):m.end() + 40]) and a.pattern.lower()
                           .find(re.escape(s.lower())) >= 0 for a in allow_rx):
                        continue
                    if r["id"] == "stock_names" and s.lower() in known:
                        continue
                    hits.append(_hit(r, i, m.start(), m.end(), p))
    hits += _structural(paras, R, scene_ends, scene_starts)
    # de-duplicate overlapping hits of same para, keeping highest severity
    hits.sort(key=lambda h: (h["para"], h["start"], -h["severity"]))
    dedup, seen = [], set()
    for h in hits:
        key = (h["para"], h["start"], h["end"], h["rule"])
        if key in seen:
            continue
        if any(d["para"] == h["para"] and d["start"] <= h["start"] < d["end"] and d["severity"] >= h["severity"]
               and d["end"] > d["start"] and d["category"] == h["category"] for d in dedup[-6:]):
            continue
        seen.add(key)
        dedup.append(h)
    ratio, _nfrag, _nsent = narration_fragment_ratio(paras)
    flags = ["fragment_budget"] if _nsent and ratio > _fragment_max_ratio() else []
    words = max(1, len(text.split()))
    counts, by_cat, weighted = {}, {}, 0.0
    for h in dedup:
        counts[h["rule"]] = counts.get(h["rule"], 0) + 1
        by_cat[h["category"]] = by_cat.get(h["category"], 0) + 1
        weighted += SEV_WEIGHT.get(h["severity"], 1)
    density = weighted * 1000 / words
    score_density = weighted * 1000 / max(words, 300)   # damp short samples
    flagged = {}
    for h in dedup:
        flagged.setdefault(h["para"], []).append(h)
    return {"words": words, "paragraphs": len(paras), "hits": dedup, "flagged_paragraphs": flagged,
            "counts": counts, "by_category": by_cat,
            "per_1k": {k: round(v * 1000 / words, 2) for k, v in counts.items()},
            "weighted_per_1k": round(density, 2), "score": score_from_density(score_density),
            "fragment_ratio": ratio, "flags": flags}


def score_from_density(d: float) -> float:
    """0-10, 10 = clean. 4 weighted tells/1k words = 9; 20/1k = 5; 40+/1k = 0."""
    return round(max(0.0, 10.0 - d / 4.0), 1)


def needs_rewrite(para_hits: list[dict], min_severity: int = 2, cluster: int = 3) -> bool:
    """A paragraph goes to the LLM if it has a severity>=3 hit, or a severity>=min_severity hit,
    or `cluster` or more mild hits. Pure token issues (em_dash) are handled by scrub instead."""
    real = [h for h in para_hits if h["rule"] != "em_dash"]
    if any(h["severity"] >= 3 for h in real):
        return True
    if any(h["severity"] >= min_severity for h in real):
        return True
    return len(real) >= cluster


def autofix(text: str) -> tuple[str, list[str]]:
    """Only trivially safe edits: token scrub + catalog ``autofix`` swaps in narration."""
    from bookforge.core.deai import scrub
    applied = []
    out = scrub(text)
    if out != text:
        applied.append("token_scrub")
    for r in load_catalog():
        rep = r.get("autofix")
        if not rep:
            continue
        for rx in r["_rx"]:
            def sub(m):
                return rep
            paras = split_paragraphs(out)
            new = []
            for p in paras:
                segs = re.split(r'("[^"\n]*")', p)
                segs = [s if s.startswith('"') else rx.sub(sub, s) for s in segs]
                new.append("".join(segs))
            o2 = "\n\n".join(new)
            if o2 != out:
                applied.append(r["id"])
                out = o2
    return out, applied


def family_summary(rep: dict, text: str) -> str:
    """Short per-family counts for ``bookforge detect --fail-on``."""
    from bookforge.core.deai import count_token_tells
    buckets = {"word choice": 0, "stock phrases": 0, "grammar tells": 0, "sentences": 0, "tokens": 0}
    phrase = {"stock_phrase", "therapy_speak", "body_cliche", "sensory_cliche", "dialogue_tag"}
    grammar = {"contrast_frame", "filler", "emotion_telling", "cadence"}
    for h in rep["hits"]:
        if h["rule"] in ("fragment", "fragment_budget"):
            buckets["sentences"] += 1
        elif h["category"] in ("lexicon", "names", "word_choice"):
            buckets["word choice"] += 1
        elif h["category"] in phrase:
            buckets["stock phrases"] += 1
        elif h["category"] in grammar:
            buckets["grammar tells"] += 1
        elif h["category"] in ("structure", "scene_shape"):
            buckets["sentences"] += 1
        elif h["category"] == "punctuation":
            buckets["tokens"] += 1
    if "fragment_budget" in rep.get("flags", []):
        buckets["sentences"] += 1
    buckets["tokens"] += sum(count_token_tells(text).values())
    return "\n".join(f"{name}: {buckets[name]}" for name in buckets)


def format_report(rep: dict, text: str | None = None, max_hits: int = 40) -> str:
    lines = [f"words={rep['words']} paragraphs={rep['paragraphs']} hits={len(rep['hits'])} "
             f"weighted/1k={rep['weighted_per_1k']} score={rep['score']}/10",
             "by category: " + ", ".join(f"{k}={v}" for k, v in sorted(rep["by_category"].items()))]
    for h in rep["hits"][:max_hits]:
        lines.append(f"  p{h['para']} [{h['severity']}] {h['rule']}: {h['reason']}")
    if len(rep["hits"]) > max_hits:
        lines.append(f"  ... {len(rep['hits']) - max_hits} more")
    return "\n".join(lines)
