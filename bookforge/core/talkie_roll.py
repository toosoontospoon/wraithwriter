"""Rolling reword: talkie writes each paragraph, code and Jev score it, talkie splices
dropped facts, and Sol may only edit or delete inside a word budget.

Order for paragraph i, with history = paragraphs already fixed:
context, talkie candidates, code + one batched Jev pick, localise, splice, dedupe,
budgeted Sol, style check, final Jev. Each paragraph is appended to the JSONL log
before the next one starts, so a resumed run reloads 0..k and continues at k+1.
"""
from __future__ import annotations

import copy
import difflib
import json
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bookforge.core import jev, llm, talkie
from bookforge.core.sentences import split_sentences
from bookforge.core.talkie import TalkieUnavailable

TEMPLATE = Path(__file__).resolve().parents[1] / "jev_templates" / "talkie_fidelity.json"
LOG_NAME = "talkie.jsonl"
SUMMARY_NAME = "talkie_summary.json"
SOFAR_WORDS = 70
JEV_MODEL = "jev-1.13.0"

# Synthetic pupil/master pairs. These are the project's own demo lines.
SENT_DEMO = (
    "Exercise in single sentences. Each of the pupil's sentences from this passage is rewritten by the master in his own words, "
    "keeping every circumstance.\n\n"
    "Pupil: The market square was a symphony of sound and color.\nMaster: Everybody in the market seemed to be shouting at once.\n\n"
    "Pupil: A fire crackled in the hearth, casting dancing shadows across the worn rug.\nMaster: There was a good fire, and the rug before it was worn quite thin.\n\n"
    "Pupil: Her gaze fell upon a narrow staircase hidden behind a curtain on the landing.\nMaster: Nobody else had noticed the curtain at the end of the landing, or the narrow stairs behind it.\n\n"
    "Pupil: She felt a wave of relief wash over her as the door finally opened.\nMaster: She was very glad indeed when at last the door opened.\n\n"
)

DEFAULT_FEWSHOT = """Exercise 1.

The persons in this story are: Lila, the baker.

Pupil's version:

The morning rush was a symphony of noise as Lila navigated the crowded shop, and she felt a wave of relief when the last tray left the oven.

Master's version:

Lila worked through the loud morning. She was glad when the last tray came out of the oven.

Exercise 2.

The persons in this story are: Otto, Captain Bram.

Pupil's version:

Otto's gaze fell upon a narrow stair hidden behind a crate, and a mix of fear and curiosity carried him down into the dark hold.

Master's version:

Otto saw a narrow stair behind a crate. He went down it, afraid and curious both.
"""

SOL_SYS = """You correct factual errors in a paragraph written in TALKIE's voice. SOURCE is the authority for facts.
Keep TALKIE's own wording wherever it is not wrong. Where you must change words, match TALKIE's vocabulary, sentence length and clause joins.
Do not use modern or AI-sounding phrasing (no "as if", "somehow", "the weight of", "a mix of", "for a moment", "softly", "gently", "whispered", "lingered", "settled", "echoed", "something about", em dashes, or "not just X but Y").
Never copy a run of four or more words from SOURCE.
You may NOT add sentences. You may only (a) edit an existing numbered sentence, changing as few words as possible, or (b) delete a sentence that is flagged or that repeats a fact already given by another sentence.
Restore a missing fact only by working a few words into an existing sentence. If that cannot be done inside the word budget, leave the fact missing.
List edits most important first.
Return JSON only: {"edits": [{"n": 1, "text": "the whole edited sentence"}], "delete": []}"""

_TELLS = [
    re.compile(r"\bas if\b", re.I),
    re.compile(r"\bsomehow\b", re.I),
    re.compile(r"\bthe weight of\b", re.I),
    re.compile(r"\ba mix of\b", re.I),
    re.compile(r"\bfor a moment\b", re.I),
    re.compile(r"\bsoftly\b", re.I),
    re.compile(r"\bgently\b", re.I),
    re.compile(r"\bwhispered\b", re.I),
    re.compile(r"\blingered\b", re.I),
    re.compile(r"\bsettled\b", re.I),
    re.compile(r"\bechoed\b", re.I),
    re.compile(r"\bsomething about\b", re.I),
    re.compile("\u2014"),
    re.compile(r"\bnot just\b[^.!?]{0,80}\bbut\b", re.I),
]
_MARKER = re.compile(r"^(?:\* \* \*|===+|---+)$")
_WORD = re.compile(r"[a-z']+")
_QUOTES = '"\u201c\u201d'
COMMON_CAPS = {"I", "Mr", "Mrs", "Miss", "Aunt", "Uncle", "Sir", "God", "O", "Oh", "Dr", "St"}
_TEMPLATE = None


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]


def is_marker(paragraph: str) -> bool:
    return bool(_MARKER.match((paragraph or "").strip()))


def _merge(base: dict, over: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve_cfg(cfg: dict | None = None) -> dict:
    from bookforge.config import load_config
    file_cfg = load_config().get("talkie") or {}
    return _merge(_merge(talkie.DEFAULTS, file_cfg), cfg)


def fewshot_text(cfg: dict) -> str:
    path = cfg.get("fewshot_path")
    if not path:
        return DEFAULT_FEWSHOT
    p = Path(path)
    if not p.is_file():
        raise TalkieUnavailable(f"fewshot file not found: {path}")
    return p.read_text()


def _template() -> dict:
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = json.loads(TEMPLATE.read_text())
    return _TEMPLATE


def words(text: str) -> int:
    return len((text or "").split())


def toks(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def ngrams(text: str, n: int = 4) -> set[tuple]:
    t = toks(text)
    return {tuple(t[i:i + n]) for i in range(len(t) - n + 1)}


def content_words(text: str) -> set[str]:
    return {w for w in toks(text) if len(w) > 3}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cap_words(text: str, sentence_initial: bool = False) -> list[str]:
    out = []
    s = re.sub(r"\b(Mrs|Mr|Dr|St)\.", r"\1", text or "")
    s = re.sub(r"[’']\w{1,2}\b", "", s)
    for sent in re.split(r'(?<=[.!?:;])["”’]?\s+|[“"]\s*', s):
        found = re.findall(r"[A-Za-z][A-Za-z'’-]*", sent)
        for j, w in enumerate(found):
            if w[:1].isupper() and (j > 0 or sentence_initial):
                out.append(w.rstrip("'’s") if w.endswith(("'s", "’s")) else w)
    return out


def allowlist(*texts: str) -> set[str]:
    allow = set(COMMON_CAPS)
    for text in texts:
        allow.update(cap_words(text or "", sentence_initial=True))
    return allow


def code_checks(cand: str, src: str, allow: set, src_vocab: set | None = None) -> dict:
    """Names, length and balanced quotes. ``sol_4gram_share`` is the source overlap."""
    cand = cand or ""
    ratio = words(cand) / max(1, words(src))
    len_ok = 0.75 <= ratio <= 1.3
    vocab = src_vocab or set()
    bad = sorted({w for w in cap_words(cand) if w not in allow and w.lower() not in vocab})
    dq = cand.count('"')
    oq, cq = cand.count("“"), cand.count("”")
    quotes_ok = (dq % 2 == 0) and (oq == cq or (dq > 0 and (oq + cq + dq) % 2 == 0))
    grams = ngrams(cand)
    share = (len(grams & ngrams(src)) / len(grams)) if grams else 0.0
    fails = []
    if not len_ok:
        fails.append("len_ok")
    if bad:
        fails.append("names_ok")
    if not quotes_ok:
        fails.append("quotes_ok")
    return {
        "len_ratio": round(ratio, 2), "len_ok": len_ok, "names_ok": not bad, "bad_names": bad,
        "quotes_ok": quotes_ok, "sol_4gram_share": round(share, 3),
        "code_pass": not fails, "code_fails": fails,
    }


def copyguard(checks: dict, copy_max: float) -> dict:
    out = dict(checks)
    out["code_fails"] = list(checks["code_fails"])
    if out["sol_4gram_share"] > copy_max:
        out["code_fails"].append("copy")
        out["code_pass"] = False
    return out


def _fill_q(spec: dict, repl: dict) -> dict:
    def sub(text: str) -> str:
        for a, b in repl.items():
            text = text.replace(a, b)
        return text
    return {"type": spec["type"], "instructions": sub(spec["instructions"]),
            "criteria": {k: sub(v) for k, v in spec["criteria"].items()}}


def _score(res: dict, qid: str) -> float:
    value = jev.noul(res, qid)
    return 1.0 if value is None else round(float(value), 3)


def jev_para(src, before, cands, tag, log_dir, cap) -> list[dict]:
    """One batched call. Returns ``[{cat: p}]`` per candidate."""
    spec = _template()["paragraph"]["questions"]
    state = {"before": before or "", "source": src, "rewrites": list(cands)}
    qs = {}
    for n in range(len(cands)):
        for cat, qspec in spec.items():
            qs[f"{cat}__{n}"] = _fill_q(qspec, {"{n}": str(n)})
    res = jev.ask(state, qs, model=JEV_MODEL, tag=tag, log_dir=log_dir, cap_usd=cap)
    return [{cat: _score(res, f"{cat}__{n}") for cat in spec} for n in range(len(cands))]


def jev_localise(src, rewrite, tag, log_dir, cap):
    """Per-sentence miss (source) and err (rewrite) probabilities."""
    rs, ss = split_sentences(rewrite), split_sentences(src)
    spec = _template()["localise"]
    state = {
        "source": "\n".join(f"m{i + 1}| {x}" for i, x in enumerate(ss)),
        "rewrite": "\n".join(f"s{i + 1}| {x}" for i, x in enumerate(rs)),
    }
    qs = {}
    for i in range(len(rs)):
        qs[f"err__{i}"] = _fill_q(spec["err"], {"{line}": str(i + 1)})
    for i in range(len(ss)):
        qs[f"miss__{i}"] = _fill_q(spec["miss"], {"{line}": str(i + 1)})
    if not qs:
        return rs, ss, [], []
    res = jev.ask(state, qs, model=JEV_MODEL, tag=tag, log_dir=log_dir, cap_usd=cap)
    err = [_score(res, f"err__{i}") for i in range(len(rs))]
    miss = [_score(res, f"miss__{i}") for i in range(len(ss))]
    return rs, ss, err, miss


def passes(scores: dict, checks: dict, threshold: float) -> bool:
    return bool(checks.get("code_pass")) and all(v <= threshold for v in scores.values())


def _pick(scores, checks, threshold):
    def key(n):
        return (sum(scores[n].values()), len(checks[n]["code_fails"]), n)
    ok = [n for n in range(len(scores)) if passes(scores[n], checks[n], threshold)]
    if ok:
        return min(ok, key=key)
    nocopy = [n for n in range(len(scores)) if "copy" not in checks[n]["code_fails"]]
    return min(nocopy or range(len(scores)), key=key)


def story_so_far(fixed: list[str], window: int) -> str:
    older = fixed[:-window] if window else []
    firsts = []
    for para in older:
        ss = split_sentences(para)
        if ss:
            firsts.append(ss[0])
    tokens = " ".join(firsts).split()
    return " ".join(tokens[-SOFAR_WORDS:])


def build_context(src_before, fixed, persons, opening, fewshot, window, exercise_i) -> str:
    """Few-shot, persons, capped story-so-far, then pupil sources and fixed masters."""
    head = fewshot.strip() + f"\n\nExercise {5 + exercise_i}.\n\nThe persons in this story are: {persons}.\n\n"
    if not fixed:
        prev = (opening or "").strip()
        return head + (f"The passage before:\n\n{prev}\n\n" if prev else "")
    brief = story_so_far(fixed, window)
    if brief:
        head += "The story so far, in brief: " + brief + "\n\n"
    pupil = src_before[-window:] if window else []
    master = fixed[-window:] if window else []
    return head + (
        "The pupil's version of the passage before:\n\n" + "\n\n".join(pupil) + "\n\n"
        "The master's version of the passage before:\n\n" + "\n\n".join(master) + "\n\n"
    )


def paragraph_prompt(context: str, src: str) -> str:
    return context + "Pupil's version:\n\n" + src + "\n\nMaster's version:\n\n"


def clean_generation(text: str) -> str:
    text = (text or "").split("Pupil:")[0].split("Master:")[0].strip()
    return " ".join(split_sentences(text)[:2])


def splice(talk, src, dropped, stock, allow, vocab, copy_max):
    """Insert the best retelling of each dropped source sentence after its predecessor."""
    rs = split_sentences(talk)
    ss = split_sentences(src)
    inserts: dict[int, list[str]] = {}
    used = []
    dropped_set = set(dropped)
    for m in dropped:
        if m < 0 or m >= len(ss):
            continue
        best = None
        for cand in stock.get(m, []):
            text = clean_generation(cand.get("text") or "")
            if not text:
                continue
            checks = code_checks(text, ss[m], allow, vocab)
            overlap = len(content_words(text) & content_words(ss[m]))
            if checks["names_ok"] and overlap > 0 and checks["sol_4gram_share"] <= copy_max:
                if best is None or overlap > best[0]:
                    best = (overlap, text)
        if not best:
            continue
        pos = -1
        for p in range(m - 1, -1, -1):
            if p in dropped_set or p >= len(ss):
                continue
            scored = [(len(content_words(r) & content_words(ss[p])), k) for k, r in enumerate(rs)]
            if not scored:
                break
            overlap, k = max(scored)
            if overlap > 0:
                pos = k
                break
        inserts.setdefault(pos, []).append(best[1])
        used.append({"source_sentence": m + 1, "talkie_text": best[1], "after": pos + 1})
    sentences = []
    splice_idx = set()
    for item in inserts.get(-1, []):
        splice_idx.add(len(sentences))
        sentences.append(item)
    for k, sentence in enumerate(rs):
        sentences.append(sentence)
        for item in inserts.get(k, []):
            splice_idx.add(len(sentences))
            sentences.append(item)
    return " ".join(sentences), used, sentences, splice_idx


def dedupe_splices(sentences, splice_indexes, threshold):
    """Drop a spliced sentence that repeats another sentence. Originals stay."""
    drop = set()
    for i in sorted(splice_indexes):
        if i < 0 or i >= len(sentences):
            continue
        cw = content_words(sentences[i])
        if not cw:
            continue
        for j, other in enumerate(sentences):
            if j == i or j in drop:
                continue
            if jaccard(cw, content_words(other)) >= threshold:
                drop.add(i)
                break
    kept = [s for i, s in enumerate(sentences) if i not in drop]
    return kept, sorted(drop)


def sol_new_words(before: str, after: str) -> int:
    aw, bw = (before or "").split(), (after or "").split()
    ops = difflib.SequenceMatcher(a=aw, b=bw, autojunk=False).get_opcodes()
    return sum(j2 - j1 for tag, _i1, _i2, j1, j2 in ops if tag != "equal")


def _keep_quotes(original: str, edited: str) -> str:
    text = edited
    for q in _QUOTES:
        if original.startswith(q) and not text.startswith(q):
            text = q + text
        if original.endswith(q) and not text.endswith(q):
            text = text + q
    return text


def is_duplicate(n: int, sentences, threshold: float) -> bool:
    cw = content_words(sentences[n])
    if not cw:
        return False
    return any(k != n and jaccard(cw, content_words(other)) >= threshold for k, other in enumerate(sentences))


def apply_edits(sentences, edits, delete, flagged, budget, threshold):
    """Apply Sol's edits in listed order. Skip any edit that would pass the word budget.

    Deletions land only on flagged sentences or sentences that duplicate another.
    Edge quote marks on an edited sentence are put back if Sol dropped them.
    """
    out = list(sentences)
    used = 0
    took, skipped, deleted = [], [], []
    flagged = set(flagged or [])
    for edit in edits or []:
        try:
            n = int(edit.get("n", 0)) - 1
        except (TypeError, ValueError):
            continue
        text = (edit.get("text") or "").strip()
        if not (0 <= n < len(sentences)) or not text:
            continue
        if len(split_sentences(text)) > max(1, len(split_sentences(sentences[n]))):
            continue
        new = sol_new_words(sentences[n], text)
        if used + new > budget:
            skipped.append(n + 1)
            continue
        out[n] = _keep_quotes(sentences[n], text)
        used += new
        took.append(n + 1)
    for raw in delete or []:
        try:
            n = int(raw) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= n < len(sentences) and (n in flagged or is_duplicate(n, out, threshold)):
            deleted.append(n)
    text = " ".join(s for k, s in enumerate(out) if k not in set(deleted))
    return text, used, took, skipped, [d + 1 for d in deleted]


def _tell_hits(text: str) -> list[str]:
    hits = []
    for rx in _TELLS:
        hits.extend(m.group(0) for m in rx.finditer(text or ""))
    return hits


def _profile(text: str) -> dict:
    lengths = [len(s.split()) for s in split_sentences(text)] or [0]
    return {
        "n": len(lengths),
        "mean": statistics.mean(lengths),
        "sd": statistics.pstdev(lengths) if len(lengths) > 1 else 0.0,
    }


def _inserted(before: str, after: str) -> list[str]:
    aw, bw = (before or "").split(), (after or "").split()
    ops = difflib.SequenceMatcher(a=aw, b=bw, autojunk=False).get_opcodes()
    return [" ".join(bw[j1:j2]) for tag, _a, _b, j1, j2 in ops if tag != "equal" and j2 > j1]


def style_feats(text: str) -> str:
    lengths = [len(s.split()) for s in split_sentences(text)] or [0]
    joins = len(re.findall(r",\s+and\b", text or ""))
    semis = (text or "").count(";")
    return (f"- Sentence length in this paragraph: mean {statistics.mean(lengths):.0f} words, "
            f"range {min(lengths)}-{max(lengths)}.\n"
            f"- Clause joins: {joins} ', and' and {semis} ';'.")


def style_check(before: str, after: str) -> list[str]:
    """Sentence-length mean/sd shift, and AI-tell hits in the words that changed."""
    flags = []
    hits = []
    for span in _inserted(before, after):
        hits.extend(_tell_hits(span))
    if hits:
        flags.append("AI-tell in changed text: " + ", ".join(hits[:8]))
    before_n, after_n = len(_tell_hits(before)), len(_tell_hits(after))
    if after_n > before_n:
        flags.append(f"AI tells rose {before_n}->{after_n}")
    p0, p1 = _profile(before), _profile(after)
    if p0["mean"] and abs(p1["mean"] - p0["mean"]) > 0.25 * p0["mean"]:
        flags.append(f"sentence-length mean shifted {p0['mean']:.1f}->{p1['mean']:.1f}")
    if abs(p1["sd"] - p0["sd"]) > max(3.0, 0.35 * p0["sd"]):
        flags.append(f"sentence-length sd shifted {p0['sd']:.1f}->{p1['sd']:.1f}")
    return flags


def _asked_words(sentences, edits) -> int:
    total = 0
    for edit in edits or []:
        try:
            n = int(edit.get("n", 0)) - 1
        except (TypeError, ValueError):
            continue
        text = (edit.get("text") or "").strip()
        if 0 <= n < len(sentences) and text:
            total += sol_new_words(sentences[n], text)
    return total


def sol_fix(src, sentences, flagged, missing, fixed, opening, budget, tries, threshold):
    """Planner-tier JSON edit. Code trims to ``budget`` and retries with the overage."""
    ref = "\n\n".join(fixed[-2:]) or (opening or "").strip() or "(no earlier paragraph yet)"
    numbered = "\n".join(f"[{n + 1}] {s}" for n, s in enumerate(sentences))
    flagged_s = ", ".join(f"[{n + 1}]" for n in flagged) or "none"
    missing_s = "\n".join(f"- {s}" for s in missing) or "none"
    base = (
        "STYLE REFERENCE (the story so far in TALKIE's voice; match it):\n" + ref
        + "\n\nTALKIE STYLE FEATURES (measured by code):\n" + style_feats(" ".join(sentences))
        + "\n\nSOURCE (facts):\n" + src
        + "\n\nTALKIE (sentences numbered):\n" + numbered
        + "\n\nFlagged TALKIE sentences (invented, contradicting or misnamed): " + flagged_s
        + "\nSOURCE facts still missing from TALKIE:\n" + missing_s
        + f"\n\nWord budget: you may write at most {budget} new words in total. "
        "Fix wrong names and contradictions first, then neutralise invented things, "
        "delete a sentence that repeats a fact another sentence already gives, "
        "then work missing facts into existing sentences. Do not add a sentence."
    )
    prompt = base
    last = None
    spent0 = llm.spent()
    for attempt in range(max(1, int(tries))):
        data = llm.llm_call(prompt, temperature=0.2, json_mode=True, role="talkie_fix",
                            system=SOL_SYS, max_tokens=2000)
        if not isinstance(data, dict):
            break
        last = data
        asked = _asked_words(sentences, data.get("edits") or [])
        if asked <= budget or attempt + 1 >= int(tries):
            break
        prompt = (base + f"\n\nThose edits write {asked} new words; the budget is {budget}. "
                  "Make fewer, smaller edits, most important first.\nPrevious JSON:\n" + json.dumps(data))
    if not last:
        return " ".join(sentences), _sol_info(False, 0, budget, [], [], [], spent0)
    text, used, took, skipped, deleted = apply_edits(
        sentences, last.get("edits") or [], last.get("delete") or [], flagged, budget, threshold)
    return text, _sol_info(True, used, budget, took, skipped, deleted, spent0)


def _sol_info(used, sol_words, budget, edits, skipped, deleted, spent0):
    return {
        "sol_used": used, "sol_words": sol_words, "budget": budget,
        "sol_edits": edits, "sol_skipped": skipped, "sol_deletions": deleted,
        "sol_usd": round(llm.spent() - spent0, 6),
    }


def _empty_sol(budget=0):
    return {"sol_used": False, "sol_words": 0, "budget": budget, "sol_edits": [],
            "sol_skipped": [], "sol_deletions": [], "sol_usd": 0.0}


def _ratio(probs, threshold) -> float:
    if not probs:
        return 1.0
    return round(sum(p <= threshold for p in probs) / len(probs), 3)


def _persons(persons) -> str:
    if isinstance(persons, (list, tuple)):
        return ", ".join(str(p) for p in persons if p)
    return str(persons or "").strip() or "the people in this chapter"


def _append(path: Path, row: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def _new_failure(before: dict, after: dict, threshold: float) -> bool:
    """True when invented, contradiction or name newly crosses the threshold."""
    for key in ("invented", "contradiction", "name"):
        if after.get(key, 1.0) > threshold >= float(before.get(key, 0.0)):
            return True
    return False


def load_targeted(path: Path) -> dict[int, dict]:
    found = {}
    if not path.exists():
        return found
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("phase") == "targeted" and isinstance(row.get("i"), int):
            found[row["i"]] = row
    return found


def _gen_many(jobs, cfg, log_dir):
    """Run ``(prompt, src, seed)`` jobs on the server's parallel slots; results keep job order."""
    workers = max(1, int(cfg.get("parallel", 4)))
    def one(job):
        prompt, src, seed = job
        return talkie.generate(prompt, src, seed, cfg=cfg, log_dir=log_dir)
    if workers == 1 or len(jobs) <= 1:
        return [one(j) for j in jobs]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, jobs))


def _retell_stock(context, ss, dropped, base, cfg, log_dir):
    """Single-sentence retellings for each dropped source sentence. Returns (stock, slot seconds)."""
    jobs, keys = [], []
    for m in dropped:
        if not (0 <= m < len(ss)) or not ss[m]:
            continue
        prompt = context + SENT_DEMO + "Pupil: " + ss[m] + "\nMaster:"
        for s in range(int(cfg["splice_seeds"])):
            jobs.append((prompt, ss[m], base + 10 * m + s))
            keys.append(m)
    stock = {m: [] for m in dropped}
    gens = _gen_many(jobs, cfg, log_dir)
    for m, gen in zip(keys, gens):
        stock[m].append(gen)
    return stock, _wall(gens, cfg)


def _wall(gens, cfg) -> float:
    """Wall-clock estimate: per-call seconds summed, divided by the parallel slots used."""
    total = sum(float(g["wall"]) for g in gens)
    return total / max(1, min(len(gens) or 1, int(cfg.get("parallel", 4))))


def _fact_splice(talk, src, dropped, context, allow, vocab, cfg, log_dir, index):
    """Steps d-e: retell each dropped source sentence and drop splice duplicates."""
    ss = split_sentences(src)
    stock, wall = _retell_stock(context, ss, dropped, 8000 + 100 * index, cfg, log_dir)
    _text, used, sents, splice_idx = splice(
        talk, src, dropped, stock, allow, vocab, float(cfg["splice_copy_max"]))
    sents, _gone = dedupe_splices(sents, splice_idx, float(cfg["dedupe_jaccard"]))
    return " ".join(sents), used, sents, wall


def targeted_pass(paragraphs, finals, rows, cfg, persons, opening, log_dir, fewshot):
    """One more splice and a wider Sol budget, only where facts are still missing.

    The new paragraph replaces the old one only when Jev's dropped score falls and
    invented, contradiction and name do not newly fail.
    """
    tcfg = cfg.get("targeted") or {}
    if not tcfg.get("enabled", True):
        return finals
    path = Path(log_dir) / LOG_NAME
    done = load_targeted(path)
    frac = float(tcfg.get("edit_budget", 0.25))
    rounds = max(1, int(tcfg.get("rounds", 1)))
    threshold = float(cfg["jev_threshold"])
    cap = float(cfg["cap_usd"])
    for row in rows:
        i = row["i"]
        if i in done:
            rec = done[i]
            row["targeted"] = _targeted_view(rec)
            if rec.get("kept") and rec.get("final"):
                finals[i] = rec["final"]
            continue
        if row.get("skipped") == "marker" or not row.get("dropped_idxs"):
            continue
        src = paragraphs[i]
        prose_src = [p for p in paragraphs[:i] if not is_marker(p)]
        prose_fixed = [p for p in finals[:i] if not is_marker(p)]
        allow = allowlist(opening or "", persons, src, *prose_src, *prose_fixed)
        vocab = set()
        for text in (opening or "", src, *prose_src, *prose_fixed):
            vocab.update(toks(text))
        context = build_context(prose_src, prose_fixed, persons, opening, fewshot,
                                int(cfg["history_window"]), i)
        before = prose_src[-1] if prose_src else (opening or "")
        original = finals[i]
        cur = original
        scores = dict(row.get("jev_after") or {})
        attempts = []
        sol_usd = 0.0
        wall = 0.0
        budget = 0
        for rnd in range(rounds):
            _rs, ss, _err, miss = jev_localise(src, cur, f"talkie:{i}:targeted-loc:{rnd}", log_dir, cap)
            dropped = [m for m, p in enumerate(miss) if p > threshold] or list(row.get("dropped_idxs") or [])
            spliced, used, sents, splice_wall = _fact_splice(
                cur, src, dropped, context, allow, vocab, cfg, log_dir, i)
            wall += splice_wall
            budget = max(3, int(frac * max(1, words(spliced))))
            rs2, ss2, err2, miss2 = jev_localise(src, spliced, f"talkie:{i}:targeted-loc2:{rnd}", log_dir, cap)
            flagged = [n for n, p in enumerate(err2) if p > threshold]
            still = [ss2[m] for m, p in enumerate(miss2) if p > threshold and m < len(ss2)]
            edited, sol = sol_fix(src, rs2 or sents, flagged, still, prose_fixed, opening, budget,
                                  int(cfg["sol_tries"]), float(cfg["dedupe_jaccard"]))
            sol_usd += float(sol.get("sol_usd") or 0)
            after = jev_para(src, before, [edited], f"talkie:{i}:targeted:{rnd}", log_dir, cap)[0]
            keep = after.get("dropped", 1.0) < scores.get("dropped", 1.0) and not _new_failure(scores, after, threshold)
            attempts.append({
                "round": rnd, "budget": budget, "splices": used, "sol_words": sol["sol_words"],
                "sol_edits": sol["sol_edits"], "sol_deletions": sol["sol_deletions"],
                "sol_skipped": sol["sol_skipped"], "jev_before": scores, "jev_after": after, "kept": keep,
            })
            if not keep:
                break
            cur = edited
            scores = after
            _rs3, _ss3, _e3, miss3 = jev_localise(src, cur, f"talkie:{i}:targeted-after:{rnd}", log_dir, cap)
            if not any(p > threshold for p in miss3):
                break
        kept = cur != original
        finals[i] = cur
        rec = {
            "phase": "targeted", "i": i, "budget": budget, "jev_before": row.get("jev_after") or {},
            "jev_after": attempts[-1]["jev_after"] if attempts else {}, "kept": kept, "final": cur,
            "attempts": attempts, "sol_usd": round(sol_usd, 6), "talkie_wall": round(wall, 3),
        }
        _append(path, rec)
        row["targeted"] = _targeted_view(rec)
        row["final"] = cur
    return finals


def _targeted_view(rec: dict) -> dict:
    return {
        "kept": bool(rec.get("kept")), "budget": rec.get("budget"),
        "jev_before": rec.get("jev_before") or {}, "jev_after": rec.get("jev_after") or {},
        "sol_usd": rec.get("sol_usd") or 0.0, "talkie_wall": rec.get("talkie_wall") or 0.0,
        "attempts": rec.get("attempts") or [],
    }


def load_rows(path: Path) -> list[dict]:
    """Contiguous paragraph rows 0..k already on disk. Targeted lines are not paragraphs."""
    if not path.exists():
        return []
    saved = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("phase") == "targeted":
            continue
        saved.append(row)
    out = []
    for n, row in enumerate(saved):
        if row.get("i") != n or "final" not in row:
            break
        out.append(row)
    return out


def _marker_row(i, text) -> dict:
    return {
        "i": i, "final": text, "skipped": "marker", "passed": True, "chosen_seed": None,
        "cand_jev": [], "splices": [], "sol_edits": [], "sol_deletions": [], "sol_skipped": [],
        "sol_words": 0, "budget": 0, "talkie_word_share": 1.0, "sol_word_share": 0.0,
        "sol_4gram_share": 0.0, "jev_before": {}, "jev_after": {}, "kept": 1.0, "clean": 1.0,
        "kept_n": [0, 0], "clean_n": [0, 0], "style_flags": [], "talkie_wall": 0.0,
        "dropped_idxs": [], "sol_usd": 0.0,
    }


def _finish_row(i, chosen_seed, cand_jev, splices, sol, final, before_scores, after_scores,
                checks, kept, clean, kept_n, clean_n, flags, passed, wall, dropped_idxs):
    total = max(1, words(final))
    sol_words = sol["sol_words"]
    return {
        "i": i,
        "final": final,
        "chosen_seed": chosen_seed,
        "cand_jev": cand_jev,
        "splices": splices,
        "sol_edits": sol["sol_edits"],
        "sol_deletions": sol["sol_deletions"],
        "sol_skipped": sol["sol_skipped"],
        "sol_words": sol_words,
        "budget": sol["budget"],
        "talkie_word_share": round(1 - sol_words / total, 3),
        "sol_word_share": round(sol_words / total, 3),
        "sol_4gram_share": checks["sol_4gram_share"],
        "jev_before": before_scores,
        "jev_after": after_scores,
        "kept": kept,
        "clean": clean,
        "kept_n": kept_n,
        "clean_n": clean_n,
        "style_flags": flags,
        "passed": passed,
        "talkie_wall": round(wall, 3),
        "dropped_idxs": dropped_idxs,
        "sol_usd": sol.get("sol_usd", 0.0),
        "code_fails": checks["code_fails"],
    }


def _one(i, paragraphs, finals, cfg, persons, opening, log_dir, fewshot):
    src = paragraphs[i]
    prose_src = [p for p in paragraphs[:i] if not is_marker(p)]
    prose_fixed = [p for p in finals if not is_marker(p)]
    before = prose_src[-1] if prose_src else (opening or "")
    allow = allowlist(opening or "", persons, src, *prose_src, *prose_fixed)
    vocab = set()
    for text in (opening or "", src, *prose_src, *prose_fixed):
        vocab.update(toks(text))
    window = int(cfg["history_window"])
    context = build_context(prose_src, prose_fixed, persons, opening, fewshot, window, i)
    prompt = paragraph_prompt(context, src)
    cands = _gen_many([(prompt, src, 1000 + 100 * i + k) for k in range(max(1, int(cfg["n_rerolls"])))],
                      cfg, log_dir)
    wall = _wall(cands, cfg)
    threshold = float(cfg["jev_threshold"])
    cap = float(cfg["cap_usd"])
    scores = jev_para(src, before, [c["text"] for c in cands], f"talkie:{i}", log_dir, cap)
    checks = [copyguard(code_checks(c["text"], src, allow, vocab), float(cfg["copy_max"])) for c in cands]
    chosen = _pick(scores, checks, threshold)
    talk = cands[chosen]["text"]
    passed_talkie = passes(scores[chosen], checks[chosen], threshold)
    splices = []
    sol = _empty_sol(0)
    if not passed_talkie:
        _rs, ss, _err, miss = jev_localise(src, talk, f"talkie:{i}:loc", log_dir, cap)
        dropped = [m for m, p in enumerate(miss) if p > threshold]
        stock, splice_wall = _retell_stock(context, ss, dropped, 5000 + 100 * i, cfg, log_dir)
        wall += splice_wall
        _spl, splices, sents, splice_idx = splice(
            talk, src, dropped, stock, allow, vocab, float(cfg["splice_copy_max"]))
        sents, _gone = dedupe_splices(sents, splice_idx, float(cfg["dedupe_jaccard"]))
        pre_sol = " ".join(sents)
        rs2, ss2, err2, miss2 = jev_localise(src, pre_sol, f"talkie:{i}:loc2", log_dir, cap)
        flagged = [n for n, p in enumerate(err2) if p > threshold]
        still = [m for m, p in enumerate(miss2) if p > threshold]
        if flagged or still:
            budget = max(3, int(float(cfg["edit_budget"]) * max(1, words(pre_sol))))
            missing = [ss2[m] for m in still if m < len(ss2)]
            edited, sol = sol_fix(src, rs2, flagged, missing, prose_fixed, opening, budget,
                                  int(cfg["sol_tries"]), float(cfg["dedupe_jaccard"]))
            flags = style_check(pre_sol, edited) if sol["sol_used"] else []
            final = edited
        else:
            sol = _empty_sol(0)
            flags = []
            final = pre_sol
    else:
        flags = []
        final = talk
    after = jev_para(src, before, [final], f"talkie:{i}:final", log_dir, cap)[0]
    final_checks = copyguard(code_checks(final, src, allow, vocab), float(cfg["copy_max"]))
    _rs3, _ss3, err3, miss3 = jev_localise(src, final, f"talkie:{i}:loc3", log_dir, cap)
    dropped_idxs = [m for m, p in enumerate(miss3) if p > threshold]
    row = _finish_row(
        i, cands[chosen]["seed"], scores, splices, sol, final, scores[chosen], after, final_checks,
        _ratio(miss3, threshold), _ratio(err3, threshold),
        [sum(p <= threshold for p in miss3), len(miss3)],
        [sum(p <= threshold for p in err3), len(err3)],
        flags, passes(after, final_checks, threshold), wall, dropped_idxs)
    return row, final


def run_chapter(paragraphs, cfg, persons, opening, log_dir):
    """Reword ``paragraphs`` in order. Returns ``(final_paragraphs, summary)``."""
    if isinstance(paragraphs, str):
        paragraphs = split_paragraphs(paragraphs)
    paragraphs = list(paragraphs)
    cfg = resolve_cfg(cfg)
    persons = _persons(persons)
    opening = opening or ""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / LOG_NAME
    rows = load_rows(path)
    finals = [r["final"] for r in rows]
    fewshot = fewshot_text(cfg)
    for rec in load_targeted(path).values():
        i = rec["i"]
        if 0 <= i < len(rows):
            rows[i]["targeted"] = _targeted_view(rec)
            if rec.get("kept") and rec.get("final"):
                finals[i] = rec["final"]
                rows[i]["final"] = rec["final"]
    for i in range(len(rows), len(paragraphs)):
        if is_marker(paragraphs[i]):
            row = _marker_row(i, paragraphs[i])
            _append(path, row)
            rows.append(row)
            finals.append(paragraphs[i])
            continue
        row, final = _one(i, paragraphs, finals, cfg, persons, opening, log_dir, fewshot)
        _append(path, row)
        rows.append(row)
        finals.append(final)
    targeted_pass(paragraphs, finals, rows, cfg, persons, opening, log_dir, fewshot)
    jev_usd = jev.spent(log_dir)
    sol_usd = sum(float(r.get("sol_usd") or 0) for r in rows) + sum(
        float((r.get("targeted") or {}).get("sol_usd") or 0) for r in rows)
    summary = summarize(rows, jev_sol_usd=jev_usd + sol_usd)
    (log_dir / SUMMARY_NAME).write_text(json.dumps(summary, indent=2))
    return finals, summary


def summarize(rows, jev_sol_usd=0.0, talkie_seconds=None) -> dict:
    prose = [r for r in rows if r.get("skipped") != "marker" and r.get("phase") != "targeted"]
    word_count = sum(words(r.get("final") or "") for r in prose)
    if talkie_seconds is None:
        talkie_seconds = sum(float(r.get("talkie_wall") or 0) for r in prose) + sum(
            float((r.get("targeted") or {}).get("talkie_wall") or 0) for r in prose)

    def avg(key):
        vals = [r[key] for r in prose if isinstance(r.get(key), (int, float))]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    targeted_rows = [r["targeted"] for r in prose if isinstance(r.get("targeted"), dict)]
    minutes = talkie_seconds / 60.0
    return {
        "paragraphs": len(prose),
        "passes": sum(1 for r in prose if r.get("passed")),
        "facts_kept": avg("kept"),
        "sentences_clean": avg("clean"),
        "talkie_word_share": avg("talkie_word_share"),
        "sol_word_share": avg("sol_word_share"),
        "mean_sol_4gram_share": avg("sol_4gram_share"),
        "style_flagged": sum(1 for r in prose if r.get("style_flags")),
        "targeted_pass_counts": {
            "attempted": len(targeted_rows),
            "kept": sum(1 for t in targeted_rows if t.get("kept")),
            "reverted": sum(1 for t in targeted_rows if t.get("kept") is False),
        },
        "jev_sol_usd": round(float(jev_sol_usd), 4),
        "talkie_minutes": round(minutes, 3),
        "minutes_per_1k_words": round(minutes / (word_count / 1000.0), 3) if word_count else 0.0,
    }
