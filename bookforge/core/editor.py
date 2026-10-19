"""One full-context editing pass per chapter (Sol, high reasoning).

Replaces the old polish, re-voice and de-AI LLM rewrite. The editor sees exactly what the writer
saw (story pack, recent chapters, voices, plan, obligations, rule card) plus the critique's findings,
and EDITS the chapter: it fixes every listed problem and any continuity slip, and leaves working
prose alone. No length cap: clarity may add a sentence; filler may not.
"""
from __future__ import annotations

import json

from bookforge.core import pack
from bookforge.core.deai import scrub
from bookforge.core.llm import llm_call
from bookforge.steps._common import SCENE_BREAK, clean_prose, split_scenes

RUNAWAY = 1.5   # sanity guard only (a runaway rewrite), not a length target


def findings(ctx, chapter_id: str, crit: dict, chapter: dict | None = None) -> list[str]:
    from bookforge.core import protect
    prot = protect.protected(chapter or ctx.chapter(chapter_id) or {})
    out = []
    for w in protect.filter_spots(crit.get("weak_spots", []), prot)[0]:
        loc = f' AT "{w["location"]}"' if w.get("location") else ""
        out.append(f"[{w.get('severity', 'minor')}]{loc}: {w.get('problem', '')}")
    for r in crit.get("thread_checks", []):
        if not r.get("pass"):
            out.append(f"[major] STORY OBLIGATION NOT MET ({r.get('thread')}): {r.get('note', '')} FIX: {r.get('fix', '')}")
    for r in crit.get("rule_checks", []):
        if not r.get("pass") and not protect.hits(r.get("quote", ""), prot) and not any(r.get("rule") and r.get("rule") in x for x in out):
            out.append(f"[major] RULE {r.get('rule')}: {r.get('note', '')} AT \"{r.get('quote', '')}\" FIX: {r.get('fix', '')}")
    for t in ctx.canon().open_revision_tasks(chapter_id):
        out.append(f"[possible continuity issue, verify against the story pack first] {t.get('fix', '')}")
    return list(dict.fromkeys(out))


def build(ctx, chapter: dict, text: str, crit: dict) -> tuple[str, str]:
    from bookforge.core import threads
    from bookforge.core import protect
    fx = findings(ctx, chapter["id"], crit, chapter)
    blocks = [pack.chapter_plan_block(chapter), protect.block(protect.protected(chapter), protect.facts(chapter)), pack.notes_block(ctx, chapter), threads.chapter_block(ctx, chapter),
              "EDITOR'S FINDINGS ON THIS DRAFT (fix each one; a finding may be wrong, so check it against the story pack "
              "and skip it only if the pack proves it wrong):\n" + ("\n".join(f"- {f}" for f in fx) or "(none)"),
              "CHAPTER DRAFT:\n" + text,
              f"""YOUR JOB: you are the book's line and continuity editor. EDIT this draft; do not rewrite it.
1. Fix every finding above.
2. Read the draft against the continuity sheet, the bible and the earlier chapters' text. Fix every slip: places, who lives or owns what, injuries, objects, what each character knows, who is present, time of day, anyone named before being introduced, wrong speaker tags.
3. Enforce the RULE CARD in the system prompt, especially clarity, the narrator's folksy voice in the narration, each speaker's voice, and staging.
4. Leave every sentence that already works exactly as it is. Cut filler. Add only what clarity needs.
5. Keep all story events and the chapter's first line. Keep the scene breaks: the output must have exactly {len(split_scenes(text))} scenes separated by a line containing only * * *
Output ONLY the full edited chapter text."""]
    return pack.system_for(ctx, chapter), pack.chapter_pack(ctx, chapter) + "\n\n".join(b for b in blocks if b)


def edit_chapter(ctx, chapter: dict, text: str, crit: dict, step_dir=None) -> tuple[str, dict]:
    system, prompt = build(ctx, chapter, text, crit)
    if step_dir:
        (step_dir / "editor_prompt.txt").write_text(f"=== SYSTEM ===\n{system}\n\n=== USER ===\n{prompt}")
    n = len(text.split())
    out = llm_call(prompt, system=system, role="edit", temperature=0.4, max_tokens=int(n * 2.4) + 2000, raw_dir=step_dir)
    out = scrub(clean_prose(out))
    scenes = split_scenes(out)
    m = len(out.split())
    rep = {"words_in": n, "words_out": m, "scenes_in": len(split_scenes(text)), "scenes_out": len(scenes),
           "findings": len(findings(ctx, chapter["id"], crit))}
    if not (0.5 * n <= m <= RUNAWAY * n):
        rep["accepted"], rep["reason"] = False, f"runaway length {m} vs {n}"
        return text, rep
    rep["accepted"] = True
    from bookforge.core import protect
    final, back = protect.restore(text, SCENE_BREAK.join(scenes), protect.protected(chapter))
    rep["protected_restored"] = back
    return final, rep


def edit_packet(ctx, chapter: dict, text: str, packet: dict, step_dir=None) -> tuple[str, dict]:
    """Packet-mode editor (core/screen.py): the expensive model reads only the flagged sections, the
    governing bible lines and the beat map, returns edited flagged sections as JSON, and code splices
    them back into the untouched chapter. Raises LLMError/ValueError on a bad reply (caller falls back)."""
    from bookforge.core import protect, screen
    system, prompt = screen.lean_system(ctx, chapter), packet["prompt"]
    if step_dir:
        (step_dir / "editor_prompt.txt").write_text(f"=== SYSTEM ===\n{system}\n\n=== USER (revision packet) ===\n{prompt}")
    flagged_words = sum(len(s["text"].split()) for s in packet["sections"] if s["sid"] in packet["flagged"])
    out = llm_call(prompt, system=system, role="edit", json_mode=True, temperature=0.4,
                   max_tokens=int(flagged_words * 2.4) + 2000, raw_dir=step_dir)
    if not isinstance(out, dict) or not isinstance(out.get("sections"), dict):
        raise ValueError("packet editor returned no 'sections' object")
    spliced, sp = screen.splice(packet, out["sections"])
    n, m = len(text.split()), len(spliced.split())
    rep = {"mode": "packet", "words_in": n, "words_out": m, "scenes_in": len(split_scenes(text)),
           "scenes_out": len(split_scenes(spliced)), "findings": sum(len(v) for v in packet["flags"].values()),
           "flagged": packet["flagged"], "splice": sp, "unfixed": out.get("unfixed", []), "jev": packet.get("jev")}
    if not sp["applied"]:
        rep["accepted"], rep["reason"] = False, "no edited sections applied"
        return text, rep
    rep["accepted"] = True
    final, back = protect.restore(text, spliced, protect.protected(chapter))
    rep["protected_restored"] = back
    return final, rep
