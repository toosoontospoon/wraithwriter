"""Per-sentence Jev tell tagger.

Code splits a chapter into numbered sentences and pages. Jev (TypeSafe System One,
via ``bookforge.core.jev.ask``) only returns probabilities for a fixed yes/no template.
This module turns those probabilities into located-hit rows shaped like
``deai.located_hits``, so the editor and the screen can consume them unchanged.
A full LLM is not used here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import statistics
from pathlib import Path

from bookforge.core import jev

_TAX_PATH = Path(__file__).resolve().parent.parent / "jev_templates" / "tell_taxonomy.json"
_BREAK = re.compile(r"\s*(\* \* \*|={3,}|#+.*)\s*")
_QMAP = str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2018": "'", "\u2019": "'"})
_INSTEAD = re.compile(r"\bInstead\b")

DEFAULTS = {
    "enabled": True,
    "page_words": 350,
    "min_severity": 1,
    "band": [0.4, 0.6],
    "reask": 1,
    "repeat_window": 6,
    "max_hits": 40,
    "cap_usd": 1.0,
    "model": "jev-1.13.0",
}


def load_taxonomy() -> dict:
    return json.loads(_TAX_PATH.read_text())


def settings(cfg: dict | None = None) -> dict:
    from bookforge.config import load_config
    base = dict(DEFAULTS)
    file_cfg = (load_config().get("deai") or {}).get("jev_tells") or {}
    if isinstance(file_cfg, dict):
        base.update(file_cfg)
    if cfg:
        base.update(cfg)
    band = base.get("band") or [0.4, 0.6]
    base["band"] = [float(band[0]), float(band[1])]
    base["page_words"] = int(base["page_words"])
    base["min_severity"] = int(base["min_severity"])
    base["reask"] = int(base["reask"])
    base["repeat_window"] = int(base["repeat_window"])
    base["max_hits"] = int(base["max_hits"])
    base["cap_usd"] = float(base["cap_usd"])
    return base


def _apply_thresholds(tax: dict, cfg: dict) -> dict:
    tax = copy.deepcopy(tax)
    over = {k: float(v) for k, v in (cfg.get("thresholds") or {}).items()}
    for group in (tax["categories"], tax["code_categories"], tax["page_questions"]):
        for row in group:
            if row["id"] in over:
                row["threshold"] = over[row["id"]]
    return tax


def sid(i: int) -> str:
    return f"s{i + 1:02d}"


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans. Closing quotes stay on the sentence. A blank line is never crossed."""
    spans: list[tuple[int, int]] = []
    pos = 0
    parts = list(re.finditer(r"\n\s*\n", text))
    bounds = []
    for m in parts:
        bounds.append((pos, m.start()))
        pos = m.end()
    bounds.append((pos, len(text)))
    for a, b in bounds:
        chunk = text[a:b]
        if not chunk.strip() or _BREAK.fullmatch(chunk.strip() or ""):
            continue
        spans.extend((a + s, a + e) for s, e in _split_chunk(chunk))
    return spans


def _split_chunk(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    i = 0
    for m in re.finditer(r'[.!?]+["\u201d\']?(?=\s+["\u201c\']?[A-Z]|\s*$)', text):
        end = m.end()
        start = i
        while start < end and text[start].isspace():
            start += 1
        if start < end:
            spans.append((start, end))
        i = end
    rest = text[i:]
    if rest.strip():
        start = i + (len(rest) - len(rest.lstrip()))
        spans.append((start, len(text.rstrip())))
    return spans


def _paragraphs(text: str) -> list[tuple[int, int, str]]:
    """Non-empty paragraphs as (start, end, text), same order as detector.split_paragraphs."""
    out = []
    pos = 0
    for m in re.finditer(r"\n\s*\n", text):
        chunk = text[pos:m.start()]
        if chunk.strip():
            out.append((pos, m.start(), chunk))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        out.append((pos, len(text), tail))
    return out


def _letters_outside_quotes(paragraph: str) -> list[bool]:
    norm = paragraph.translate(_QMAP)
    outside = []
    in_q = False
    for ch in norm:
        if ch == '"':
            in_q = not in_q
            outside.append(False)
        else:
            outside.append((not in_q) and ch.isalpha())
    return outside


def _mask_quotes(sentence: str) -> str:
    norm = sentence.translate(_QMAP)
    return re.sub(r'"[^"]*"', " ", norm)


def annotate(text: str) -> list[dict]:
    paras = _paragraphs(text)
    scene = 1
    located = []
    for pi, (a, b, para) in enumerate(paras):
        located.append((pi, a, b, para, scene))
        if _BREAK.fullmatch(para.strip() or ""):
            scene += 1
    spans = split_sentences(text)
    sents = []
    for i, (s, e) in enumerate(spans):
        pi, pa, pb, para, scene = 0, 0, 0, "", 1
        for item in located:
            if item[1] <= s < item[2] or (s == item[1] and item[1] == item[2]):
                pi, pa, pb, para, scene = item
                break
        raw = text[s:e]
        # Quote state is per paragraph, so a sentence that continues an open quote is dialogue.
        rel_s, rel_e = s - pa, e - pa
        flags = _letters_outside_quotes(para)
        window = flags[rel_s:rel_e] if 0 <= rel_s < len(flags) else []
        has_letter_out = any(window)
        has_quote = '"' in para.translate(_QMAP)[rel_s:rel_e] or (
            para.translate(_QMAP)[:rel_s].count('"') % 2 == 1)
        dialogue_only = (not has_letter_out) and (has_quote or raw.translate(_QMAP).count('"') >= 1)
        # A sentence with no quotes and no letters is not dialogue (a scene fragment).
        if '"' not in raw.translate(_QMAP) and para.translate(_QMAP)[:rel_s].count('"') % 2 == 0:
            dialogue_only = False
        sents.append({
            "id": sid(i),
            "start": s,
            "end": e,
            "text": raw,
            "para": pi,
            "scene": scene,
            "paragraph": para,
            "dialogue_only": dialogue_only,
            "mixed": (not dialogue_only) and ('"' in raw.translate(_QMAP) or has_quote and not has_letter_out),
        })
        if not sents[-1]["dialogue_only"] and '"' in raw.translate(_QMAP):
            sents[-1]["mixed"] = True
    return sents


def _pages_of(sents: list[dict], text: str, page_words: int) -> list[list[dict]]:
    """Groups of about ``page_words`` on paragraph boundaries. A scene break starts a page."""
    if not sents:
        return []
    by_para: dict[int, list[dict]] = {}
    for s in sents:
        by_para.setdefault(s["para"], []).append(s)
    pages: list[list[dict]] = []
    cur: list[dict] = []
    words = 0
    for pi, (_a, _b, para) in enumerate(_paragraphs(text)):
        if _BREAK.fullmatch(para.strip() or ""):
            if cur:
                pages.append(cur)
                cur, words = [], 0
            continue
        group = by_para.get(pi, [])
        w = len(para.split())
        if cur and words + w > page_words:
            pages.append(cur)
            cur, words = [], 0
        cur.extend(group)
        words += w
    if cur:
        pages.append(cur)
    return [p for p in pages if p]


def doc_state(sents: list[dict]) -> str:
    return "\n".join(f"{s['id']}| {s['text']}" for s in sents)


def sentence_question(cat: dict, sent: dict, wording: str = "primary") -> dict:
    if wording == "paraphrase" and cat.get("paraphrase"):
        instr = cat["paraphrase"].replace("{sid}", sent["id"])
    else:
        instr = (f"Look only at line {sent['id']} of `document` (other lines are context). "
                 f"Does line {sent['id']} contain {cat['name']}: {cat['definition']}?")
    if sent.get("mixed") and "quotation marks" not in instr.lower():
        instr += " Ignore words inside quotation marks."
    return {"type": "noul", "instructions": instr,
            "criteria": {"true": cat.get("true") or "", "false": cat.get("false") or ""}}


def page_question(q: dict) -> dict:
    return {"type": "noul", "instructions": q["instructions"],
            "criteria": {"true": q.get("true") or "", "false": q.get("false") or ""}}


def severity_for(p: float) -> int:
    if p >= 0.9:
        return 3
    if p >= 0.75:
        return 2
    return 1


def _noul(res: dict, qid: str) -> float | None:
    p = jev.noul(res, qid)
    if p is None:
        return None
    try:
        return float(p)
    except (TypeError, ValueError):
        return None


def _lookup(tax: dict, cat_id: str) -> dict:
    for group in (tax["categories"], tax["code_categories"]):
        for row in group:
            if row["id"] == cat_id:
                return row
    return {"id": cat_id, "name": cat_id, "definition": cat_id, "fix": "", "source": "jev"}


def make_hit(sent: dict, cat_id: str, p: float | None, tax: dict, ids: list[str] | None = None,
             source: str | None = None, span: str | None = None) -> dict:
    meta = _lookup(tax, cat_id)
    sev = severity_for(p) if isinstance(p, float) else 1
    return {
        "rule": f"jev_{cat_id}",
        "category": cat_id,
        "severity": sev,
        "p": None if p is None else round(float(p), 3),
        "ids": list(ids or [sent["id"]]),
        "span": span if span is not None else sent["text"],
        "para": sent["para"],
        "scene": sent["scene"],
        "paragraph": sent["paragraph"],
        "reason": f"{meta.get('name') or cat_id}: {meta.get('definition') or ''}".strip(),
        "fix": meta.get("fix") or "Fix this narration tell. Leave quoted dialogue alone.",
        "source": source or meta.get("source") or "jev",
        "confirm": False,
    }


def keep_verbatim(hits: list[dict], text: str) -> list[dict]:
    return [h for h in hits if h.get("span") and h["span"] in text]


def _narration(sents: list[dict]) -> list[dict]:
    return [s for s in sents if not s.get("dialogue_only")]


def _tidy_allowed(page: list[dict], scene_last: set[str]) -> set[str]:
    narr = _narration(page)
    allowed = {s["id"] for s in narr[-3:]}
    allowed |= {s["id"] for s in page if s["id"] in scene_last}
    return allowed


def _scene_last_ids(sents: list[dict]) -> set[str]:
    by: dict[int, list[dict]] = {}
    for s in _narration(sents):
        by.setdefault(s["scene"], []).append(s)
    out: set[str] = set()
    for group in by.values():
        out.update(s["id"] for s in group[-3:])
    return out


def _instead_sents(page: list[dict]) -> list[dict]:
    found = []
    for s in page:
        if s.get("dialogue_only"):
            continue
        if _INSTEAD.search(_mask_quotes(s["text"])):
            found.append(s)
    return found


def _ask(state: dict, questions: dict, cfg: dict, log_dir, tag: str) -> dict:
    if not questions:
        return {"answers": {}}
    return jev.ask(state, questions, model=cfg.get("model") or "jev-1.13.0", tag=tag,
                   log_dir=log_dir, cap_usd=float(cfg.get("cap_usd") or 1.0))


def tag_page(sents: list[dict], cfg: dict | None = None, log_dir=None, tax: dict | None = None) -> dict:
    """One sentence-level Jev call and one page-level call. Gating is applied by ``tag_text``."""
    cfg = cfg or settings(None)
    tax = tax or _apply_thresholds(load_taxonomy(), cfg)
    state = {"document": doc_state(sents)}
    narr = _narration(sents)
    cats = [c for c in tax["categories"] if c.get("source", "jev") == "jev"]
    qs = {}
    for cat in cats:
        for sent in narr:
            qs[f"{cat['id']}__{sent['id']}"] = sentence_question(cat, sent)
    sent_res = _ask(state, qs, cfg, log_dir, "jev_tells:sentences") if qs else {"answers": {}}
    probs: dict[tuple[str, str], list[float]] = {}
    for cat in cats:
        for sent in narr:
            qid = f"{cat['id']}__{sent['id']}"
            p = _noul(sent_res, qid)
            if p is None:
                continue
            probs[(cat["id"], sent["id"])] = [p]
    reasked = _reask(state, sents, cats, probs, cfg, log_dir, tax)
    page_qs = {f"pg__{q['id']}": page_question(q) for q in tax["page_questions"]}
    page_res = _ask(state, page_qs, cfg, log_dir, "jev_tells:page") if page_qs else {"answers": {}}
    page_scores = {}
    for q in tax["page_questions"]:
        p = _noul(page_res, f"pg__{q['id']}")
        if p is not None:
            page_scores[q["id"]] = round(p, 3)
    by_id = {s["id"]: s for s in sents}
    hits = []
    for cat in cats:
        th = float(cat.get("threshold", 0.5))
        for sent in narr:
            ps = probs.get((cat["id"], sent["id"]))
            if not ps:
                continue
            p = sum(ps) / len(ps)
            if p >= th:
                hits.append(make_hit(sent, cat["id"], p, tax))
    return {"hits": hits, "page_scores": page_scores, "reasked": reasked, "by_id": by_id, "probs": probs}


def _reask(state, sents, cats, probs, cfg, log_dir, tax) -> list[dict]:
    """One extra batched call for probabilities inside ``band``. ``reask`` 2 asks the original again too."""
    reask_n = int(cfg.get("reask") or 0)
    if reask_n <= 0 or not probs:
        return []
    lo, hi = float(cfg["band"][0]), float(cfg["band"][1])
    band = [(cat_id, sid_) for (cat_id, sid_), ps in probs.items() if ps and lo <= ps[0] <= hi]
    if not band:
        return []
    by_id = {s["id"]: s for s in sents}
    cats_by = {c["id"]: c for c in cats}
    res = _ask(state, {f"para__{cat_id}__{sid_}": sentence_question(cats_by[cat_id], by_id[sid_], "paraphrase")
                       for cat_id, sid_ in band}, cfg, log_dir, "jev_tells:reask")
    for cat_id, sid_ in band:
        p = _noul(res, f"para__{cat_id}__{sid_}")
        if p is not None:
            probs[(cat_id, sid_)].append(p)
    if reask_n >= 2:
        res2 = _ask(state, {f"again__{cat_id}__{sid_}": sentence_question(cats_by[cat_id], by_id[sid_], "primary")
                            for cat_id, sid_ in band}, cfg, log_dir, "jev_tells:reask-again")
        for cat_id, sid_ in band:
            p = _noul(res2, f"again__{cat_id}__{sid_}")
            if p is not None:
                probs[(cat_id, sid_)].append(p)
    return [{"category": cat_id, "id": sid_, "ps": [round(p, 3) for p in probs[(cat_id, sid_)]]}
            for cat_id, sid_ in band]


_STOP = set("""
a an the her his their its my your our she he they it him them i we you
on in of to and or but with for from at by as into onto over under about
again then that this those these was were is are be been had has have did do does
not no so too very just than down up off out back first second third fourth
said asked says replied whispered shouted told
another same next last other own
""".split())


def _stem(token: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if token.endswith(suf) and len(token) - len(suf) >= 3:
            return token[: -len(suf)]
    return token


def _content_tokens(sentence: str, names: list[str] | None) -> list[str]:
    name_set = set()
    for name in names or []:
        for part in re.findall(r"[A-Za-z']+", name):
            name_set.add(part.lower())
            name_set.add(_stem(part.lower()))
    out = []
    for tok in re.findall(r"[A-Za-z']+", _mask_quotes(sentence)):
        low = tok.lower()
        if low in _STOP:
            continue
        stem = _stem(low)
        if stem in _STOP or low in name_set or stem in name_set or len(stem) < 2:
            continue
        out.append(stem)
    return out


def _grams(tokens: list[str]) -> set[tuple[str, ...]]:
    grams = set()
    for n in (2, 3):
        for i in range(len(tokens) - n + 1):
            grams.add(tuple(tokens[i:i + n]))
    return grams


def repeated_action(sents: list[dict], cfg: dict, tax: dict) -> list[dict]:
    """Flag a later narration sentence that repeats a verb-object pair or a 2-3 word content phrase."""
    window = int(cfg.get("repeat_window") or 6)
    names = cfg.get("names") or []
    sigs = []
    for s in sents:
        sigs.append(set() if s.get("dialogue_only") else _grams(_content_tokens(s["text"], names)))
    seen: dict[tuple[str, ...], list[int]] = {}
    hits = []
    for i, grams in enumerate(sigs):
        matched_prev = None
        best = 0
        for gram in grams:
            earlier = [k for k in seen.get(gram, [])
                       if sents[k]["scene"] == sents[i]["scene"] and 0 < i - k <= window]
            if not earlier:
                continue
            cluster = len(earlier) + 1
            if cluster > best:
                best = cluster
                matched_prev = earlier[-1]
        for gram in grams:
            seen.setdefault(gram, []).append(i)
        if matched_prev is None:
            continue
        hit = make_hit(sents[i], "repeated_action", None, tax,
                       ids=[sents[matched_prev]["id"], sents[i]["id"]], source="code", span=sents[i]["text"])
        hit["severity"] = 2 if best >= 3 else 1
        hit["reason"] = "repeated action: a verb-object pair or a short content phrase comes back inside the window"
        hits.append(hit)
    return hits


def uniform_rhythm(sents: list[dict], tax: dict) -> list[dict]:
    """Runs of >=4 similar-length narration sentences, or >=3 in a row with the same opening word."""
    hits = []
    n = len(sents)
    narr = [not s.get("dialogue_only") for s in sents]
    wc = [len(_mask_quotes(s["text"]).split()) for s in sents]
    i = 0
    while i < n:
        j = i
        while j < n and narr[j] and sents[j]["scene"] == sents[i]["scene"]:
            run = wc[i:j + 1]
            mean = statistics.mean(run)
            # Similar means the whole run spans at most max(2, 20% of the mean) words.
            if mean <= 0 or max(run) - min(run) > max(2.0, 0.2 * mean):
                break
            j += 1
        if j - i >= 4:
            hit = make_hit(sents[i], "uniform_rhythm", None, tax,
                           ids=[sents[k]["id"] for k in range(i, j)], source="code", span=sents[i]["text"])
            hit["severity"] = 1 if j - i < 6 else 3 if j - i >= 8 else 2
            hit["reason"] = (f"uniform rhythm: {j - i} sentences of similar length "
                             f"({min(wc[i:j])}-{max(wc[i:j])} words)")
            hits.append(hit)
            i = j
        else:
            i += 1
    first = []
    for s in sents:
        words = re.sub(r"^\W+", "", _mask_quotes(s["text"]).strip()).split()
        first.append(words[0].lower() if words else "")
    i = 0
    while i < n:
        j = i
        while (j < n and first[j] and first[j] == first[i] and narr[j]
               and sents[j]["scene"] == sents[i]["scene"]):
            j += 1
        if j - i >= 3:
            hit = make_hit(sents[i], "uniform_rhythm", None, tax,
                           ids=[sents[k]["id"] for k in range(i, j)], source="code", span=sents[i]["text"])
            hit["severity"] = 2
            hit["reason"] = f"uniform rhythm: {j - i} sentences in a row open with '{first[i]}'"
            hits.append(hit)
            i = j
        else:
            i += 1
    return hits


def code_hits(sents: list[dict], cfg: dict, tax: dict) -> list[dict]:
    return repeated_action(sents, cfg, tax) + uniform_rhythm(sents, tax)


def _gate_page(page: list[dict], raw_hits: list[dict], page_scores: dict, tax: dict,
               scene_last: set[str]) -> list[dict]:
    allowed = _tidy_allowed(page, scene_last)
    kept = []
    for h in raw_hits:
        if h["category"] == "tidy_ending" and not (set(h["ids"]) & allowed):
            continue
        kept.append(h)
    pq = {q["id"]: q for q in tax["page_questions"]}
    tidy_q = pq.get("tidy_ending_page") or {"threshold": 0.5}
    tidy_p = page_scores.get("tidy_ending_page")
    narr = _narration(page)
    if tidy_p is not None and tidy_p >= float(tidy_q.get("threshold", 0.5)) and narr:
        in_zone = [h for h in kept if h["category"] == "tidy_ending" and (set(h["ids"]) & {s["id"] for s in narr[-3:]})]
        if not in_zone:
            last = narr[-1]
            if not any(h["category"] == "tidy_ending" and last["id"] in h["ids"] for h in kept):
                kept.append(make_hit(last, "tidy_ending", float(tidy_p), tax))
    instead_q = pq.get("instead_contrast_page") or {"threshold": 0.5}
    instead_p = page_scores.get("instead_contrast_page")
    if instead_p is not None and instead_p >= float(instead_q.get("threshold", 0.5)):
        for sent in _instead_sents(page):
            if any(h["category"] == "not_x_but_y" and sent["id"] in h["ids"] for h in kept):
                continue
            kept.append(make_hit(sent, "not_x_but_y", float(instead_p), tax))
    return kept


def _cache_key(text: str, tax: dict, cfg: dict) -> str:
    payload = json.dumps({
        "text": text,
        "version": tax.get("version"),
        "thresholds": {row["id"]: row.get("threshold") for group in
                       (tax["categories"], tax["code_categories"], tax["page_questions"]) for row in group},
        "page_words": cfg["page_words"],
        "band": cfg["band"],
        "reask": cfg["reask"],
        "repeat_window": cfg["repeat_window"],
        "min_severity": cfg["min_severity"],
        "names": list(cfg.get("names") or []),
    }, sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()


def _cache_path(log_dir) -> Path | None:
    if not log_dir:
        return None
    return Path(log_dir) / "jev_tells_cache.json"


def _cache_get(log_dir, key: str) -> dict | None:
    path = _cache_path(log_dir)
    if not path or not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    row = data.get(key)
    return copy.deepcopy(row) if isinstance(row, dict) else None


def _cache_put(log_dir, key: str, result: dict) -> None:
    path = _cache_path(log_dir)
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[key] = result
    path.write_text(json.dumps(data))


def _empty(sents, status: str) -> dict:
    return {"sentences": [_public_sent(s) for s in sents], "hits": [], "page_scores": [],
            "counts": {}, "reasked": [], "jev": status}


def _public_sent(s: dict) -> dict:
    return {"id": s["id"], "text": s["text"], "start": s["start"], "end": s["end"],
            "para": s["para"], "scene": s["scene"], "dialogue_only": s["dialogue_only"]}


def _counts(hits: list[dict], tax: dict) -> dict:
    ids = [c["id"] for c in tax["categories"] + tax["code_categories"]]
    counts = {i: 0 for i in ids}
    for h in hits:
        counts[h["category"]] = counts.get(h["category"], 0) + 1
    return counts


def _finish(text: str, sents: list[dict], hits: list[dict], page_scores: list, reasked: list,
            tax: dict, status: str, cfg: dict) -> dict:
    hits = keep_verbatim(hits, text)
    hits = [h for h in hits if int(h.get("severity") or 1) >= int(cfg["min_severity"])]
    hits.sort(key=lambda h: (-int(h["severity"]), int(h["para"]), h["rule"], h["ids"][0] if h["ids"] else ""))
    # One row per category per sentence id.
    seen = set()
    uniq = []
    for h in hits:
        key = (h["category"], tuple(h["ids"]), h.get("reason"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
    uniq = uniq[: int(cfg["max_hits"])]
    return {"sentences": [_public_sent(s) for s in sents], "hits": uniq, "page_scores": page_scores,
            "counts": _counts(uniq, tax), "reasked": reasked, "jev": status}


def project_log_dir(ctx):
    """Shared cache directory so the screen and the editor tag a chapter once."""
    state = getattr(ctx, "state_dir", None)
    if not state:
        return None
    return Path(state) / "jev_tells"


def hits_for(ctx, text: str) -> list[dict]:
    """Located tell rows for ``text``, or nothing when the tagger is disabled."""
    cfg = settings(None)
    if not cfg.get("enabled", True) or not text or not str(text).strip():
        return []
    names = ctx.cast_names() if hasattr(ctx, "cast_names") else None
    if names:
        cfg = dict(cfg)
        cfg["names"] = list(names)
    return tag_text(text, cfg=cfg, log_dir=project_log_dir(ctx)).get("hits") or []


def tag_text(text: str, cfg: dict | None = None, log_dir=None) -> dict:
    cfg = settings(cfg)
    tax = _apply_thresholds(load_taxonomy(), cfg)
    sents = annotate(text or "")
    if not cfg.get("enabled", True):
        return _empty(sents, "skipped: disabled")
    key = _cache_key(text or "", tax, cfg)
    cached = _cache_get(log_dir, key)
    if cached is not None:
        return cached
    try:
        pages = _pages_of(sents, text or "", cfg["page_words"]) or ([sents] if sents else [])
        scene_last = _scene_last_ids(sents)
        all_hits: list[dict] = []
        page_scores: list[dict] = []
        reasked: list[dict] = []
        if not sents:
            result = _finish(text or "", sents, [], [], [], tax, "ok", cfg)
            _cache_put(log_dir, key, result)
            return result
        for page in pages:
            tagged = tag_page(page, cfg, log_dir, tax)
            page_scores.append(tagged["page_scores"])
            reasked.extend(tagged["reasked"])
            all_hits.extend(_gate_page(page, tagged["hits"], tagged["page_scores"], tax, scene_last))
        all_hits.extend(code_hits(sents, cfg, tax))
        result = _finish(text or "", sents, all_hits, page_scores, reasked, tax, "ok", cfg)
    except jev.JevUnavailable as e:
        result = _finish(text or "", sents, code_hits(sents, cfg, tax), [], [], tax, f"skipped: {e}", cfg)
        return result
    _cache_put(log_dir, key, result)
    return result
