"""Keep stored scene plans and canon facts in step with the chapter text that actually exists.

When a chapter is revised heavily (voice pass, manual rewrite, targeted fixes), the stored
scene briefs and the canon facts extracted from the old draft go stale. The critic then
"enforces" things the current text deliberately dropped. resync() fixes that:
  1. re-derives each scene's action/beats/closing_image/characters/location from the text
     (thread obligations, why_matters, and the opening hook are kept: they're intent, not description);
  2. retracts the chapter's old canon provenance and re-extracts facts from the current text;
  3. marks the chapter plan_source="realized" and records a text hash so callers can tell it's fresh.
"""
from __future__ import annotations

import difflib
import hashlib
import json

from bookforge.core.llm import llm_call
from bookforge.steps._common import split_scenes


def text_hash(text: str) -> str:
    return hashlib.sha1(" ".join(text.split()).encode()).hexdigest()[:16]


def changed_a_lot(a: str, b: str, threshold: float = 0.9) -> bool:
    return difflib.SequenceMatcher(None, a.split(), b.split(), autojunk=False).ratio() < threshold


def is_stale(chapter: dict, text: str) -> bool:
    return chapter.get("plan_text_hash") != text_hash(text)


def resync(ctx, chapter_id: str, text: str | None = None, step_dir=None, reextract: bool = True) -> dict:
    outline = ctx.load_outline()
    idx = next(i for i, c in enumerate(outline) if c["id"] == chapter_id)
    ch = outline[idx]
    if text is None:
        for kind in ("final", "revised", "draft"):
            p = ctx.chapter_path(chapter_id, kind)
            if p.exists():
                text = p.read_text()
                break
    scenes_txt = split_scenes(text)
    old = {s.get("scene_number"): s for s in ch.get("scenes", [])}
    res = llm_call(f"""Describe what ACTUALLY happens in each scene of this chapter, as a scene plan a writer could have used. Be exact and literal; invent nothing.

For each of the {len(scenes_txt)} scenes give: location, characters_present (names), pov, action (<=80 words), beats (1-8 short ordered events, as many as actually happen), closing_image (the last image or action), emotional_turn ("start -> end"), sensory_anchor.

{chr(10).join(f'=== SCENE {i} ==={chr(10)}{t}' for i, t in enumerate(scenes_txt, 1))}

Output ONLY JSON: {{"scenes": [{{"scene_number": 1, "location": "...", "characters_present": [], "pov": "...", "action": "...", "beats": [], "closing_image": "...", "emotional_turn": "...", "sensory_anchor": "..."}}]}}""",
                   role="summary", json_mode=True, max_tokens=6000, temperature=0.1, raw_dir=step_dir)
    new_scenes = []
    for i, s in enumerate((res.get("scenes") or [])[:len(scenes_txt)], 1):
        base = dict(old.get(i, old.get(max(old) if old else 1, {})) or {})
        base.update({k: v for k, v in s.items() if v})
        base["scene_number"] = i
        base["target_words"] = len(scenes_txt[i - 1].split())
        new_scenes.append(base)
    ch["scenes"] = new_scenes
    ch["plan_source"] = "realized"
    ch["plan_text_hash"] = text_hash(text)
    from bookforge.core import threads
    threads.inherit_to_scenes(ch)
    outline[idx] = ch
    ctx.save_outline(outline)
    # write scene files so per-scene steps (deai, re-extract) read the current text
    for i, t in enumerate(scenes_txt, 1):
        for kind in ("revised", "final"):
            if ctx.scene_path(chapter_id, i, kind).exists() or kind == "revised":
                ctx.write_text(ctx.scene_path(chapter_id, i, kind), t)
    if reextract:
        from bookforge.core import canon_pipeline
        ctx.canon().retract_source(chapter_id)
        for sc, t in zip(new_scenes, scenes_txt):
            try:
                canon_pipeline.process_scene(ctx, chapter_id, sc, t, step_dir)
            except Exception as e:  # extraction is best-effort; a bad JSON reply must not lose the resync
                print(f"   re-extract failed for scene {sc.get('scene_number')}: {e}")
    return ch
