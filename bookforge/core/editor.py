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


def _draft_text(ctx, chapter_id: str, text: str | None) -> str:
    if text:
        return text
    path_fn = getattr(ctx, "chapter_path", None)
    if path_fn is None:
        return ""
    draft = path_fn(chapter_id, "draft")
    if draft.exists():
        return draft.read_text()
    best = getattr(ctx, "best_chapter_text", None)
    return best(chapter_id) if best else ""


def _format_located(hit: dict) -> str:
    """One editor line: severity, scene, paragraph, rule id, evidence, fix."""
    ev = (hit.get("span") or "").replace('"', "'").replace("\n", " ").strip()
    if len(ev) < 8:
        ev = (hit.get("paragraph") or ev).replace('"', "'").replace("\n", " ").strip()[:160]
    label = "major" if int(hit.get("severity") or 2) >= 3 else "minor"
    fix = (hit.get("fix") or "Fix this narration tell. Leave quoted dialogue alone.").replace("\n", " ")
    return (f"[{label}] scene {hit.get('scene', 1)} paragraph {hit.get('para', 0)} "
            f'RULE {hit["rule"]} AT "{ev}" FIX: {fix}')


def _same_spot(hit: dict, row: dict) -> bool:
    if hit.get("para") != row.get("para"):
        return False
    span = (hit.get("span") or "").strip()
    other = (row.get("span") or "").strip()
    return bool(span and other and (span in other or other in span))


def _located_lines(ctx, text: str) -> list[str]:
    if not text or not text.strip():
        return []
    from bookforge.core.deai import located_hits
    from bookforge.core.jev_tells import hits_for
    names = ctx.cast_names() if hasattr(ctx, "cast_names") else None
    allow = None
    style = getattr(ctx, "style", None)
    if callable(style):
        try:
            allow = style().allowlist()
        except Exception:
            allow = None
    rows = located_hits(text, names, allow)
    lines = [_format_located(h) for h in rows]
    for hit in hits_for(ctx, text):
        if any(_same_spot(hit, row) for row in rows):
            continue
        lines.append(_format_located(hit))
    return lines


def findings(ctx, chapter_id: str, crit: dict, chapter: dict | None = None, text: str | None = None) -> list[str]:
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
    out.extend(_located_lines(ctx, _draft_text(ctx, chapter_id, text)))
    return list(dict.fromkeys(out))


def _guard_cfg() -> tuple[str, float]:
    from bookforge.config import load_config
    d = load_config().get("deai") or {}
    try:
        drop = float(d.get("max_tell_drop", 0.5))
    except (TypeError, ValueError):
        drop = 0.5
    return str(d.get("edit_guard", "section") or "section"), drop


def _tell_score(text: str) -> float:
    from bookforge.core.detector import detect
    return float(detect(text or "")["score"])


def note_full_edit_guard(before: str, after: str, rep: dict) -> None:
    """Full-chapter edits are not reverted. A score drop past max_tell_drop is a warning.

    ``deai.edit_guard`` "off" disables the warning. "section" still only reverts inside a packet.
    """
    mode, drop = _guard_cfg()
    if mode == "off" or before == after:
        return
    b, a = _tell_score(before), _tell_score(after)
    if a < b - drop:
        rep["edit_guard_warning"] = (
            f"detector score {b} -> {a} (drop > {drop}); full-edit mode does not revert the chapter")


def build(ctx, chapter: dict, text: str, crit: dict) -> tuple[str, str]:
    from bookforge.core import threads
    from bookforge.core import protect
    fx = findings(ctx, chapter["id"], crit, chapter, text)
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
           "findings": len(findings(ctx, chapter["id"], crit, chapter, text))}
    if not (0.5 * n <= m <= RUNAWAY * n):
        rep["accepted"], rep["reason"] = False, f"runaway length {m} vs {n}"
        return text, rep
    rep["accepted"] = True
    from bookforge.core import protect
    final, back = protect.restore(text, SCENE_BREAK.join(scenes), protect.protected(chapter))
    rep["protected_restored"] = back
    note_full_edit_guard(text, final, rep)
    if rep.get("edit_guard_warning"):
        print("   [warn] " + rep["edit_guard_warning"])
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
        reason = ("edit guard reverted the edited sections" if sp.get("edit_guard_reverted")
                  else "no edited sections applied")
        rep["accepted"], rep["reason"] = False, reason
        if sp.get("edit_guard_reverted"):
            rep["edit_guard_reverted"] = sp["edit_guard_reverted"]
        return text, rep
    rep["accepted"] = True
    final, back = protect.restore(text, spliced, protect.protected(chapter))
    rep["protected_restored"] = back
    return final, rep
