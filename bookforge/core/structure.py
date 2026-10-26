"""Story-structure vocabulary: a per-chapter `structure` block, a prompt block, and a lint.

Every outline chapter may carry:

  structure:
    act: "1" | "2a" | "2b" | "3"
    percent: 0-100              # position in the book (midpoint of the chapter); computed if blank
    save_the_cat: midpoint      # Save the Cat beat id (SAVE_THE_CAT); several allowed, joined with "+"
    heros_journey: ordeal       # Hero's Journey stage id (HEROS_JOURNEY)
    chapter_type: action | reaction | discovery | set_piece | transition | bottle
    strand: A | B | AB
    a_purpose: "..."            # one line on what the chapter does for the A (external) plot
    b_purpose: "..."            # one line for the B (relationship/theme) plot
    plot_function: "..."        # REQUIRED: what changes in the A or B plot that would not happen otherwise
    color: "..."                # optional: worldbuilding/whimsy flourishes the chapter carries
    value_turn: {from: "...", to: "..."}   # REQUIRED and must change
    scene_sequel: scene | sequel | scene+sequel | sequel+scene
    try_fail: "..."             # e.g. "try 2/3 at sealing: fails (no, and)"; blank if none
    setups: ["thread:<id>", "ledger:<n>", "free text"]
    payoffs: ["thread:<id>", ...]
    ending: cliffhanger | reveal | decision | resonant_image

Hard rule: no chapter may drag or be unnecessary. Colour is welcome, but every chapter
needs a plot_function and a real value turn, and its function may not duplicate a neighbour's.
`check()` reports those as FAIL; everything else is WARN or INFO.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

ACTS = ("1", "2a", "2b", "3")
# Save the Cat (Snyder/Brody) beats with target percent of the book.
SAVE_THE_CAT = {"opening_image": 0, "theme_stated": 5, "setup": 5, "catalyst": 10, "debate": 15,
                "break_into_two": 20, "b_story": 22, "fun_and_games": 35, "midpoint": 50,
                "bad_guys_close_in": 62, "all_is_lost": 75, "dark_night_of_the_soul": 78,
                "break_into_three": 80, "finale": 90, "final_image": 100}
STC_ALIASES = {"dark_night": "dark_night_of_the_soul", "break_into_2": "break_into_two",
               "break_into_3": "break_into_three", "fun_and_games": "fun_and_games", "promise_of_the_premise": "fun_and_games"}
HEROS_JOURNEY = {"ordinary_world": 0, "call_to_adventure": 10, "refusal_of_the_call": 15, "meeting_the_mentor": 20,
                 "crossing_the_threshold": 25, "tests_allies_enemies": 40, "approach": 50, "ordeal": 55,
                 "reward": 65, "road_back": 75, "resurrection": 90, "return_with_elixir": 100}
HJ_ALIASES = {"refusal": "refusal_of_the_call", "mentor": "meeting_the_mentor", "threshold": "crossing_the_threshold",
              "tests": "tests_allies_enemies", "approach_to_the_inmost_cave": "approach", "elixir": "return_with_elixir"}
CHAPTER_TYPES = ("action", "reaction", "discovery", "set_piece", "transition", "bottle")
TYPE_ALIASES = {"quiet": "reaction", "reaction_quiet": "reaction", "reveal": "discovery", "discovery_reveal": "discovery",
                "set-piece": "set_piece", "wonder": "set_piece", "set_piece_wonder": "set_piece"}
ENDINGS = ("cliffhanger", "reveal", "decision", "resonant_image")
ENDING_ALIASES = {"image": "resonant_image", "resonant": "resonant_image", "cliff": "cliffhanger"}
STRANDS = ("A", "B", "AB")
SCENE_SEQUEL = ("scene", "sequel", "scene+sequel", "sequel+scene")
ACT_RANGE = {"1": (15, 30), "2a": (18, 32), "2b": (18, 32), "3": (12, 30)}   # % of book (words)
REQUIRED = ("act", "save_the_cat", "heros_journey", "chapter_type", "strand", "plot_function",
            "value_turn", "scene_sequel", "ending")


def _key(v: Any) -> str:
    return re.sub(r"[^a-z0-9+]+", "_", str(v or "").strip().lower()).strip("_")


class Structure(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    act: str = ""
    percent: float | None = None
    save_the_cat: str = ""
    heros_journey: str = ""
    chapter_type: str = ""
    strand: str = ""
    a_purpose: str = ""
    b_purpose: str = ""
    plot_function: str = ""
    color: str = ""
    value_turn: dict = Field(default_factory=dict)
    scene_sequel: str = ""
    try_fail: str = ""
    setups: list[str] = Field(default_factory=list)
    payoffs: list[str] = Field(default_factory=list)
    ending: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, d):
        d = dict(d or {})
        if d.get("act") not in (None, ""):
            a = str(d["act"]).lower().replace("act", "").strip()
            d["act"] = {"2": "2a", "i": "1", "iia": "2a", "iib": "2b", "iii": "3"}.get(a, a)
        if d.get("save_the_cat"):
            d["save_the_cat"] = "+".join(STC_ALIASES.get(_key(x), _key(x)) for x in str(d["save_the_cat"]).split("+"))
        if d.get("heros_journey"):
            k = _key(d["heros_journey"]); d["heros_journey"] = HJ_ALIASES.get(k, k)
        if d.get("chapter_type"):
            k = _key(d["chapter_type"]); d["chapter_type"] = TYPE_ALIASES.get(k, k)
        if d.get("ending"):
            k = _key(d["ending"]); d["ending"] = ENDING_ALIASES.get(k, k)
        if d.get("strand"):
            s = str(d["strand"]).upper().replace(" ", "").replace("+", "").replace("BOTH", "AB")
            d["strand"] = "AB" if s in ("AB", "BA") else s
        if d.get("scene_sequel"):
            d["scene_sequel"] = str(d["scene_sequel"]).strip().lower().replace(" ", "")
        vt = d.get("value_turn")
        if isinstance(vt, str):
            parts = re.split(r"\s*(?:->|→|=>)\s*", vt, maxsplit=1)
            d["value_turn"] = {"from": parts[0], "to": parts[1] if len(parts) > 1 else ""}
        elif isinstance(vt, (list, tuple)):
            d["value_turn"] = {"from": str(vt[0]) if vt else "", "to": str(vt[1]) if len(vt) > 1 else ""}
        for k in ("setups", "payoffs"):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = [v] if v else []
        p = d.get("percent")
        if p in ("", None):
            d["percent"] = None
        else:
            try:
                d["percent"] = float(str(p).rstrip("%"))
            except ValueError:
                d["percent"] = None
        return d


def get(chapter: dict) -> dict:
    return Structure.model_validate(chapter.get("structure") or {}).model_dump()


def turn_text(s: dict) -> str:
    vt = s.get("value_turn") or {}
    return f"{vt.get('from', '')} -> {vt.get('to', '')}" if vt.get("from") or vt.get("to") else ""


# ------------------------------------------------------------------ positions
def positions(outline: list[dict], words: dict[str, int] | None = None, default_words: int = 0) -> dict[str, float]:
    """Percent position (chapter midpoint) of each chapter by word count. Unknown lengths use the mean of the
    known ones (or default_words, or equal shares)."""
    words = dict(words or {})
    for c in outline:
        if not words.get(c["id"]) and c.get("target_words"):
            words[c["id"]] = int(c["target_words"])
    known = [w for w in words.values() if w]
    fill = int(sum(known) / len(known)) if known else (default_words or 1)
    sizes = [words.get(c["id"]) or fill for c in outline]
    total = sum(sizes) or 1
    out, run = {}, 0
    for c, w in zip(outline, sizes):
        out[c["id"]] = round(100.0 * (run + w / 2) / total, 1)
        run += w
    return out


def word_counts(ctx) -> dict[str, int]:
    out = {}
    for c in ctx.load_outline():
        try:
            t = ctx.best_chapter_text(c["id"])
        except Exception:
            t = ""
        if t:
            out[c["id"]] = len(t.split())
    return out


# ------------------------------------------------------------------ prompt block
def block(chapter: dict) -> str:
    s = get(chapter)
    if not any(s.get(k) for k in REQUIRED):
        return ""
    rows = ["STRUCTURAL FUNCTION (why this chapter exists; every scene must serve it, nothing may drag):"]
    if s["plot_function"]:
        rows.append(f"- Plot function (what changes because of this chapter): {s['plot_function']}")
    pos = f" at about {s['percent']:.0f}% of the book" if s.get("percent") is not None else ""
    if s["act"] or s["save_the_cat"]:
        rows.append(f"- Act {s['act'] or '?'}{pos}; Save the Cat beat: {s['save_the_cat'].replace('_', ' ') or '-'}; "
                    f"Hero's Journey: {s['heros_journey'].replace('_', ' ') or '-'}")
    if s["chapter_type"]:
        rows.append(f"- Chapter type: {s['chapter_type'].replace('_', ' ')}; pattern: {s['scene_sequel'] or '-'}")
    if s["a_purpose"] or s["b_purpose"]:
        rows.append(f"- Strand {s['strand'] or '?'}. A plot: {s['a_purpose'] or '-'} | B plot: {s['b_purpose'] or '-'}")
    if turn_text(s):
        rows.append(f"- Value turn (the chapter must visibly move this): {turn_text(s)}")
    if s["try_fail"]:
        rows.append(f"- Try/fail: {s['try_fail']}")
    if s["setups"]:
        rows.append("- Plant (quietly, never signposted): " + "; ".join(s["setups"]))
    if s["payoffs"]:
        rows.append("- Pay off (land it clearly): " + "; ".join(s["payoffs"]))
    if s["ending"]:
        rows.append(f"- End on a {s['ending'].replace('_', ' ')}.")
    if s["color"]:
        rows.append(f"- Colour it may carry (welcome, but never at the expense of the function): {s['color']}")
    return "\n".join(rows)


PLANNER_RULE = ("STRUCTURE RULE: no chapter may drag or be unnecessary. Every chapter has a PLOT FUNCTION, one "
                "sentence on what changes in the A or B plot that would not happen otherwise, and a VALUE TURN "
                "(from -> to) that the scenes visibly deliver. Worldbuilding colour and whimsy are welcome as long as "
                "they ride on scenes that do that work; cut or merge any beat that does not. Do not repeat the "
                "function of the neighbouring chapters.")


# ------------------------------------------------------------------ lint
_STOP = set("a an the and or of to in on at for with by from is are was were be been it its his her their he she "
            "they them this that as into than then so but not no chapter".split())


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", (s or "").lower()) if w not in _STOP and len(w) > 2}


def similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _ref(x: str) -> tuple[str, str]:
    m = re.match(r"\s*(thread|ledger)\s*:\s*([\w.-]+)", x or "")
    return (m.group(1), m.group(2)) if m else ("", "")


def _chnum(cid: str) -> int:
    m = re.match(r"ch(\d+)$", str(cid or ""))
    return int(m.group(1)) if m else 10 ** 6 if str(cid or "").startswith("book") else -1


def prose_tells(chapter: dict) -> list[tuple[str, dict]]:
    """skill_banlist and contrast-frame hits in outline prose fields that seed the draft."""
    from bookforge.core.detector import detect
    fields: list[tuple[str, str]] = []
    st = get(chapter)
    for key in ("plot_function", "a_purpose", "b_purpose"):
        val = st.get(key) or ""
        if isinstance(val, str) and val.strip():
            fields.append((key, val))
    summary = chapter.get("summary") or ""
    if isinstance(summary, str) and summary.strip():
        fields.append(("summary", summary))
    for sc in chapter.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        beats = sc.get("beats") or []
        if isinstance(beats, str):
            beats = [beats]
        for beat in beats:
            if isinstance(beat, str) and beat.strip():
                fields.append(("beats", beat))
    found = []
    for field, text in fields:
        for hit in detect(text)["hits"]:
            if hit["rule"] == "skill_banlist" or hit["category"] == "contrast_frame":
                found.append((field, hit))
    return found


def check(outline: list[dict], threads: list[dict] | None = None, words: dict[str, int] | None = None,
          run_limit: int = 3, dup_threshold: float = 0.6) -> list[dict]:
    """Lint the structure blocks. Returns issues {severity: FAIL|WARN|INFO, chapter, check, description}."""
    issues = []

    def add(sev, ch, chk, desc):
        issues.append({"severity": sev, "chapter": ch, "check": chk, "description": desc})

    tmap = {t.get("id"): t for t in (threads or [])}
    pos = positions(outline, words)
    S = [(c["id"], get(c)) for c in outline]

    for c, (cid, s) in zip(outline, S):
        if not s["plot_function"].strip():
            add("FAIL", cid, "plot_function", "no plot_function: say what changes in the A or B plot that would not happen otherwise")
        vt = s["value_turn"] or {}
        f, t = str(vt.get("from", "")).strip().lower(), str(vt.get("to", "")).strip().lower()
        if not f or not t:
            add("FAIL", cid, "value_turn", "no value turn (from -> to)")
        elif f == t:
            add("FAIL", cid, "value_turn", f"value does not turn ({f} -> {t})")
        for k in REQUIRED:
            if k not in ("plot_function", "value_turn") and not s.get(k):
                add("WARN", cid, "missing", f"missing field: {k}")
        if s["act"] and s["act"] not in ACTS:
            add("WARN", cid, "enum", f"act '{s['act']}' not in {ACTS}")
        for b in filter(None, s["save_the_cat"].split("+")):
            if b not in SAVE_THE_CAT:
                add("WARN", cid, "enum", f"unknown Save the Cat beat '{b}'")
        if s["heros_journey"] and s["heros_journey"] not in HEROS_JOURNEY:
            add("WARN", cid, "enum", f"unknown Hero's Journey stage '{s['heros_journey']}'")
        if s["chapter_type"] and s["chapter_type"] not in CHAPTER_TYPES:
            add("WARN", cid, "enum", f"chapter_type '{s['chapter_type']}' not in {CHAPTER_TYPES}")
        if s["ending"] and s["ending"] not in ENDINGS:
            add("WARN", cid, "enum", f"ending '{s['ending']}' not in {ENDINGS}")
        if s["strand"] and s["strand"] not in STRANDS:
            add("WARN", cid, "enum", f"strand '{s['strand']}' not in {STRANDS}")
        if s["scene_sequel"] and s["scene_sequel"] not in SCENE_SEQUEL:
            add("WARN", cid, "enum", f"scene_sequel '{s['scene_sequel']}' not in {SCENE_SEQUEL}")
        if "A" in (s["strand"] or "") and not s["a_purpose"]:
            add("WARN", cid, "purpose", "strand includes A but a_purpose is blank")
        if "B" in (s["strand"] or "") and not s["b_purpose"]:
            add("WARN", cid, "purpose", "strand includes B but b_purpose is blank")
        p = s["percent"] if s["percent"] is not None else pos.get(cid)
        for b in filter(None, s["save_the_cat"].split("+")):
            if b in SAVE_THE_CAT and p is not None and abs(p - SAVE_THE_CAT[b]) > 12:
                add("WARN", cid, "beat_position", f"{b} at {p:.0f}% (target ~{SAVE_THE_CAT[b]}%)")
        for field, hit in prose_tells(c):
            add("WARN", cid, "prose_tell", f"{field}: {hit['rule']} ({hit['span'][:80]})")

    # neighbouring duplicates
    for (c1, s1), (c2, s2) in zip(S, S[1:]):
        if s1["plot_function"] and s2["plot_function"]:
            sim = similarity(s1["plot_function"], s2["plot_function"])
            if s1["plot_function"].strip().lower() == s2["plot_function"].strip().lower() or sim >= dup_threshold:
                add("FAIL", c2, "duplicate_function", f"plot_function duplicates {c1} (overlap {sim:.2f}): merge, cut, or give it its own job")

    # runs
    for field, label in (("chapter_type", "chapter type"), ("ending", "ending type")):
        run = []
        for cid, s in S + [("", {field: None})]:
            v = s.get(field)
            if v and run and v == run[-1][1]:
                run.append((cid, v))
                continue
            if len(run) >= run_limit:
                add("WARN", run[0][0], f"{field}_run", f"{len(run)} {label} '{run[0][1]}' in a row ({run[0][0]}-{run[-1][0]})")
            run = [(cid, v)] if v else []

    # act proportions and order
    sizes = dict(words or {})
    known = [w for w in sizes.values() if w]
    fill = int(sum(known) / len(known)) if known else 1
    tot = {a: 0 for a in ACTS}
    for c, (cid, s) in zip(outline, S):
        if s["act"] in tot:
            tot[s["act"]] += sizes.get(cid) or c.get("target_words") or fill
    total = sum(tot.values())
    if total:
        for a, (lo, hi) in ACT_RANGE.items():
            share = 100 * tot[a] / total
            if tot[a] and not lo <= share <= hi:
                add("WARN", "", "act_balance", f"Act {a} is {share:.0f}% of the book (healthy range {lo}-{hi}%)")
            if not tot[a]:
                add("WARN", "", "act_balance", f"no chapters marked Act {a}")
    order = [ACTS.index(s["act"]) for _, s in S if s["act"] in ACTS]
    if order != sorted(order):
        add("WARN", "", "act_order", "acts go backwards somewhere in the outline")

    # setups and payoffs
    idx = {cid: i for i, (cid, _) in enumerate(S)}
    paid = {}
    for cid, s in S:
        for x in s["payoffs"]:
            kind, ref = _ref(x)
            if kind:
                paid.setdefault((kind, ref), []).append(cid)
    for cid, s in S:
        for x in s["setups"]:
            kind, ref = _ref(x)
            if not kind:
                add("INFO", cid, "unlinked_setup", f"setup not linked to a thread or ledger entry: {x[:80]}")
                continue
            if kind == "thread" and tmap and ref not in tmap:
                add("WARN", cid, "unknown_thread", f"setup cites unknown thread '{ref}'")
            later = [p for p in paid.get((kind, ref), []) if idx[p] > idx[cid]]
            if later:
                continue
            t = tmap.get(ref) if kind == "thread" else None
            pc = str((t or {}).get("payoff_chapter") or "")
            if pc.startswith("book") or (not pc and (t or {}).get("type") == "series"):
                continue  # pays off in a later book (series threads with no payoff chapter count as later books)
            later_beats = sorted((k for k in ((t or {}).get("beats") or {}) if _chnum(k) > _chnum(cid)), key=_chnum)
            if not pc and later_beats:
                add("WARN", cid, "payoff_undeclared", f"{x}: thread has later beats (last {later_beats[-1]}) but no "
                    "payoff_chapter, and no later chapter lists it in structure.payoffs")
                continue
            if pc and _chnum(pc) > _chnum(cid) and pc in idx:
                add("WARN", cid, "payoff_unmarked", f"{x}: thread pays off in {pc}, but {pc}'s structure.payoffs doesn't list it")
                continue
            add("WARN", cid, "setup_no_payoff", f"setup with no later payoff: {x}")
        for x in s["payoffs"]:
            kind, ref = _ref(x)
            if kind == "thread" and tmap and ref not in tmap:
                add("WARN", cid, "unknown_thread", f"payoff cites unknown thread '{ref}'")
    return issues


def table(outline: list[dict], words: dict[str, int] | None = None) -> list[dict]:
    pos = positions(outline, words)
    rows = []
    for c in outline:
        s = get(c)
        rows.append({"id": c["id"], "title": c.get("title", ""), "act": s["act"],
                     "percent": s["percent"] if s["percent"] is not None else pos.get(c["id"]),
                     "stc": s["save_the_cat"], "hj": s["heros_journey"], "type": s["chapter_type"],
                     "strand": s["strand"], "turn": turn_text(s), "ending": s["ending"],
                     "function": s["plot_function"]})
    return rows
