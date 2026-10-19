"""Thread ledger: every chapter must open, advance, pay off, or plant story threads.

Project file: <project>/threads.yaml
  threads:
    - id: mentor_clock
      title: "The mentor's clock (the binding wears down)"
      type: main|decoy|side|arc|romance|plant|series
      status: planned|open|advancing|paid_off
      open_chapter: ch06
      payoff_chapter: "book5"            # a chapter id, or a later book
      beats: {ch06: "...", ch12: "..."}  # planned beats per chapter
      notes: "..."
      history: [{chapter, action, result, quote, needs_review}]

Flow: plan_obligations (planner) -> chapter.thread_obligations, why_matters, opening_hook ->
scenes inherit obligations -> the drafter sees them first -> check_obligations (critique role)
returns pass/fail with quotes -> revise fixes the failures -> update_ledger records the results
with needs_review=True for a human to confirm.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from bookforge.core.io import atomic_write_text
from bookforge.core.llm import llm_call

ACTIONS = ("open", "advance", "payoff", "plant")
STATUS_AFTER = {"open": "open", "advance": "advancing", "plant": "open", "payoff": "paid_off"}


def _pack_system(ctx, chapter):
    from bookforge.core import pack
    try:
        return pack.system_for(ctx, chapter)
    except Exception:
        return ""


def _pack_prefix(ctx, chapter):
    from bookforge.core import pack
    try:
        return pack.chapter_pack(ctx, chapter)
    except Exception:
        return ""


def path(ctx) -> Path:
    return Path(ctx.project_dir) / "threads.yaml"


def load(ctx) -> dict:
    p = path(ctx)
    if not p.exists():
        return {"threads": []}
    d = yaml.safe_load(p.read_text()) or {}
    d.setdefault("threads", [])
    return d


def save(ctx, data: dict):
    atomic_write_text(path(ctx), yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110))


def enabled(ctx) -> bool:
    return bool(load(ctx)["threads"])


def by_id(data) -> dict:
    return {t["id"]: t for t in data["threads"]}


def _ledger_view(data, chapter_id) -> list[dict]:
    out = []
    for t in data["threads"]:
        out.append({k: t.get(k) for k in ("id", "title", "type", "status", "open_chapter", "payoff_chapter")}
                   | {"beat_here": (t.get("beats") or {}).get(chapter_id, ""),
                      "last": (t.get("history") or [])[-1:] or None})
    return out


def plan_obligations(ctx, chapter: dict, outline: list[dict], raw_dir=None) -> dict:
    """Planner: assign thread obligations + why_matters + opening_hook for one chapter."""
    data = load(ctx)
    ids = [c["id"] for c in outline]
    i = ids.index(chapter["id"]) if chapter["id"] in ids else 0
    neighbors = [{k: c.get(k) for k in ("id", "title", "summary")} for c in outline[max(0, i - 2): i + 3]]
    prev = outline[i - 1] if i > 0 else None
    prev_focus = ({"title": prev.get("title"), "locations": [s.get("location") for s in prev.get("scenes", [])],
                   "beats": [(s.get("action") or "")[:140] for s in prev.get("scenes", [])],
                   "obligations": [o.get("thread") for o in prev.get("thread_obligations", [])]} if prev else None)
    sb = ctx.style()
    style = sb.block(1200, for_drafter=False) if not sb.empty else ""
    res = llm_call(_pack_prefix(ctx, chapter) + f"""You are the series' story architect. Every chapter must promote the plot by OPENING, ADVANCING, PAYING OFF, or PLANTING threads that develop character or the larger story. Assign this chapter's thread obligations.

CHAPTER: {json.dumps({k: chapter.get(k) for k in ('id', 'title', 'act', 'summary', 'key_events', 'ends_with')}, ensure_ascii=False)}
NEIGHBORING CHAPTERS: {json.dumps(neighbors, ensure_ascii=False)}
THREAD LEDGER (status and the beat planned here, if any): {json.dumps(_ledger_view(data, chapter['id']), ensure_ascii=False)}
{style}

Rules:
- Give 3-6 obligations. Every thread with a planned beat for this chapter must appear. Never pay off a thread before its payoff chapter, and never open one that is already open.
- "how" names a CONCRETE on-page moment (an action, line, object, or image), playable in this chapter's events and true to canon. Plants stay light (no heavy signposting), but are described plainly.
- CLARITY (reader is about 16): plans must never tell the writer to withhold basics. Every place, person, object and magical thing gets introduced plainly the first time (say what it is). Strange events are described directly so the reader can picture them. Keep chronology clear. Telling is fine for facts, setup and context. Never write directives like "without explaining it", "leave it unexplained" or "don't say what it is"; the only things held back are story secrets the ledger says aren't due yet, and even those are shown plainly as events.
- "scene" is the scene number (1-based, given the chapter's events in order) where it lands, or 0 if it spans the chapter.
- "why_matters" is one line on what changes for the story or the character because of this chapter.
- "opening_hook": what the FIRST PARAGRAPH must show. It must reveal character through action and foreshadow something; never open on weather or exposition.
- Only thread ids from the ledger.
- PLANNING RULE: give tone and intent, never dialogue or exact lines. Wording belongs to the writer.
- VARIETY: don't repeat the previous chapter's central beat or setting focus (PREVIOUS CHAPTER below). Move the story to new ground; a callback is fine, a reprise isn't.
- "grounding": concrete notes for the drafter. Era (with era-true objects), the home, exactly how poor or comfortable, landscape, plants, weather, season, and 3-5 specific sensory details. Follow the style bible's setting rules.

PREVIOUS CHAPTER: {json.dumps(prev_focus, ensure_ascii=False)}

Output ONLY JSON: {{"why_matters": "...", "opening_hook": "...", "grounding": "...", "thread_obligations": [{{"thread": "id", "action": "open|advance|payoff|plant", "how": "...", "scene": 1}}]}}""",
                   system=_pack_system(ctx, chapter), role="outline", json_mode=True, max_tokens=5000, temperature=0.3, raw_dir=raw_dir)
    known = by_id(data)
    obs = [o for o in (res.get("thread_obligations") or []) if o.get("thread") in known and o.get("action") in ACTIONS]
    return {"why_matters": res.get("why_matters", ""), "opening_hook": res.get("opening_hook", ""),
            "grounding": res.get("grounding", ""), "thread_obligations": obs}


def opening_intent(chapter: dict) -> str:
    """The chapter's opening intent. Back-compat: a legacy verbatim `first_line` is migrated and read as intent."""
    if not chapter:
        return ""
    if not chapter.get("opening_intent") and chapter.get("first_line"):
        chapter["opening_intent"] = chapter["first_line"]
    return (chapter.get("opening_intent") or "").strip()


def apply_to_chapter(chapter: dict, plan: dict) -> dict:
    chapter.update({k: plan[k] for k in ("why_matters", "opening_hook", "thread_obligations", "grounding", "opening_intent")
                    if k in plan})
    legacy = plan.get("first_line")
    if legacy and not chapter.get("opening_intent"):  # back-compat: an old verbatim first line is read as intent
        chapter["opening_intent"] = legacy
    opening_intent(chapter)
    inherit_to_scenes(chapter)
    return chapter


def inherit_to_scenes(chapter: dict):
    scenes = chapter.get("scenes") or []
    for s in scenes:
        n = s.get("scene_number")
        s["thread_obligations"] = [o for o in chapter.get("thread_obligations", [])
                                   if int(o.get("scene") or 0) in (0, n)]
    if scenes:  # anything aimed at a scene that doesn't exist lands in the last one
        nums = {s.get("scene_number") for s in scenes}
        stray = [o for o in chapter.get("thread_obligations", []) if int(o.get("scene") or 0) not in nums | {0}]
        scenes[-1]["thread_obligations"] += stray


def render(obligations: list[dict], data: dict | None = None) -> str:
    if not obligations:
        return ""
    t = by_id(data) if data else {}
    lines = [f"- [{o['action'].upper()}] {t.get(o['thread'], {}).get('title', o['thread'])}: {o.get('how', '')}"
             for o in obligations]
    return "\n".join(lines)


def chapter_block(ctx, chapter: dict, scene: dict | None = None) -> str:
    """Prominent drafter block: why it matters, opening hook, and this scene's obligations."""
    if not chapter.get("thread_obligations"):
        return ""
    data = load(ctx)
    obs = scene.get("thread_obligations") if scene is not None else chapter["thread_obligations"]
    first = scene is not None and scene.get("scene_number") == (chapter.get("scenes") or [{}])[0].get("scene_number")
    out = ["STORY OBLIGATIONS (MUST be visible on the page; this is why the chapter exists):",
           f"Why this chapter matters: {chapter.get('why_matters', '')}"]
    if chapter.get("grounding"):
        out.append(f"GROUNDING (era, home, means, land, weather, senses): {chapter['grounding']}")
    if first and opening_intent(chapter):
        out.append("OPENING INTENT (guidance for what the opening should do or convey; NOT wording to copy, "
                   f"write your own first sentence): {opening_intent(chapter)}")
    if first and chapter.get("opening_hook"):
        out.append(f"OPENING HOOK (first paragraph must deliver this, showing character in action and foreshadowing): {chapter['opening_hook']}")
    if obs:
        out.append("Thread obligations for this scene:\n" + render(obs, data))
    return "\n".join(out)


def check_obligations(ctx, chapter: dict, text: str, raw_dir=None) -> list[dict]:
    """Critique: pass/fail per obligation, each with a verbatim quote."""
    obs = chapter.get("thread_obligations") or []
    if not obs:
        return []
    data = load(ctx)
    res = llm_call(_pack_prefix(ctx, chapter) + f"""You are a strict story editor. For EACH obligation, decide whether the chapter text actually delivers it on the page. Judge the SUBSTANCE: the thread is opened, advanced, paid off, or planted as intended. The "how" is suggested staging, so different staging that does the same story work still passes (say so in the note). "pass" needs a verbatim quote (5-30 words) that shows it. It fails if the thread's story work is missing, only gestured at, contradicted, or a plant is stated so loudly it signposts. Also judge the opening hook against the first paragraph.

OBLIGATIONS: {json.dumps([dict(o, title=by_id(data).get(o['thread'], {}).get('title', '')) for o in obs], ensure_ascii=False)}
OPENING HOOK REQUIRED: {chapter.get('opening_hook', '')}

TEXT:
{text[:60000]}

Output ONLY JSON: {{"results": [{{"thread": "id", "action": "...", "pass": true, "quote": "verbatim or empty", "note": "what's missing or how it lands", "fix": "if failed: a concrete revision instruction"}}], "opening_hook": {{"pass": true, "note": "...", "fix": ""}}}}""",
                   system=_pack_system(ctx, chapter), role="critique", json_mode=True, max_tokens=4000, temperature=0.1, raw_dir=raw_dir)
    results = [r for r in (res.get("results") or []) if isinstance(r, dict)]
    hook = res.get("opening_hook") or {}
    if chapter.get("opening_hook"):
        results.append({"thread": "_opening_hook", "action": "hook", "pass": bool(hook.get("pass")),
                        "quote": text.strip().split("\n\n")[0][:200], "note": hook.get("note", ""),
                        "fix": hook.get("fix", "")})
    # verify quotes really appear (a pass without a real quote is a fail)
    norm = _norm(text)
    for r in results:
        if r.get("pass") and r["thread"] != "_opening_hook":
            q = _norm(r.get("quote") or "")
            if len(q) < 10 or not _quote_in(q, norm):
                r["pass"], r["note"] = False, (r.get("note", "") + " [quote not found in text]").strip()
                r.setdefault("fix", "")
    return results


def _norm(s: str) -> str:
    import re
    s = s.translate(str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\u2014": " ", "\u2013": " "}))
    s = re.sub(r"[^\w' ]+", " ", s.lower())
    return " ".join(s.split())


def _quote_in(q: str, norm: str) -> bool:
    """True if the quote (or any 6-word window covering most of it) appears in the text.
    Quotes are often stitched across sentences or lightly paraphrased at the edges."""
    if q in norm:
        return True
    w = q.split()
    if len(w) < 6:
        return False
    wins = [" ".join(w[i:i + 6]) for i in range(0, len(w) - 5)]
    hits = sum(1 for x in wins if x in norm)
    return hits >= max(1, len(wins) // 2)


def failed(results: list[dict]) -> list[dict]:
    return [r for r in results if not r.get("pass")]


def update_ledger(ctx, chapter: dict, results: list[dict]):
    """Record what the chapter actually did. Every entry is flagged for human review."""
    data = load(ctx)
    t = by_id(data)
    for r in results:
        th = t.get(r.get("thread"))
        if not th:
            continue
        hist = th.setdefault("history", [])
        hist[:] = [h for h in hist if h.get("chapter") != chapter["id"] or h.get("action") != r.get("action")]
        hist.append({"chapter": chapter["id"], "action": r.get("action"), "result": "met" if r.get("pass") else "MISSED",
                     "quote": r.get("quote", ""), "note": r.get("note", ""), "needs_review": True})
        if r.get("pass"):
            new = STATUS_AFTER.get(r.get("action"))
            if new and not (th.get("status") == "paid_off" and new != "paid_off"):
                th["status"] = new
            if r.get("action") in ("open", "plant") and not th.get("open_chapter"):
                th["open_chapter"] = chapter["id"]
    save(ctx, data)
    return data
