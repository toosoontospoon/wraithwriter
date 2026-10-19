"""Draft a chapter scene by scene: plan beats -> write -> summarize -> canon pass.

Resumable at scene granularity: each scene's text lives in manuscript/chNN/sceneNN_draft.txt
and progress.scenes[ch][n] records which sub-steps finished for which scene card.
"""
import json

from pydantic import Field

from bookforge.core import canon_pipeline, progress
from bookforge.craft import engine as craft
from bookforge.core.deai import scrub
from bookforge.core.io import stable_hash
from bookforge.core.llm import llm_call
from bookforge.core.schema import _Loose
from bookforge.steps._common import clean_prose, join_scenes, summarize

from bookforge.core.rules_card import RULE_CARD as WRITE_RULES  # the one rule card (v1 rules deleted)


class BeatSheet(_Loose):
    beats: list[str] = Field(default_factory=list)


def _scene_card_hash(chapter, scene):
    return stable_hash([chapter.get("id"), scene, chapter.get("ends_with")])


def _plan_beats(ctx, chapter, scene, step_dir):
    covered = ctx.load_summaries()["scenes"].get(chapter["id"], {})
    canon = ctx.canon()
    threads = [t["description"] for t in canon.open_threads()]
    from bookforge.core.length import BEAT_TYPE_GUIDE, CRISIS_RULE, NO_FILLER_RULE
    from bookforge.core import pack
    res = llm_call(f"""{pack.chapter_pack(ctx, chapter)}{pack.chapter_plan_block(chapter, scene)}

Turn this scene card into an ordered beat sheet of 1-8 concrete beats: exactly as many as the scene's events need. {BEAT_TYPE_GUIDE} {NO_FILLER_RULE} {CRISIS_RULE}

SCENE CARD: {json.dumps({k: v for k, v in scene.items() if k != 'craft_brief'})}
{scene.get('craft_brief', '')}
CHAPTER: {chapter['title']} | ends with: {chapter.get('ends_with', '')}
CHAPTER SETUPS TO PLANT: {chapter.get('setups', [])}
CHAPTER PAYOFFS DUE: {chapter.get('payoffs', [])}
ALREADY COVERED EARLIER IN THIS CHAPTER: {json.dumps(covered)}
OPEN THREADS: {threads[:15]}

Each beat is a concrete, clearly described event. CLARITY (reader is about 16): plans must never tell the writer to withhold basics. Every place, person, object and magical thing gets introduced plainly the first time (say what it is). Strange events are described directly so the reader can picture them. Keep chronology clear. Telling is fine for facts, setup and context. Never write directives like "without explaining it", "leave it unexplained" or "don't say what it is"; the only things held back are story secrets the ledger says aren't due yet, and even those are shown plainly as events.

SELF-CHECK before answering: re-read the continuity sheet and the earlier chapters' text in the story pack. Every beat must agree with them (geography, who lives where, who owns what, injuries, what each character knows, time of day). Use the word "introduce" only for a first appearance in the book.

Output ONLY JSON: {{"beats": ["(~280w, dialogue) ..."]}}""", system=pack.system_for(ctx, chapter), role="plan",
                   schema=BeatSheet, max_tokens=6000, temperature=0.3, raw_dir=step_dir)
    return res.beats or scene.get("beats") or [scene.get("action", "Write the scene.")]


def write_scene(ctx, chapter, scene, chapter_text_so_far, step_dir):
    sp = ctx.progress["scenes"].setdefault(chapter["id"], {}).setdefault(str(scene["scene_number"]), {})
    card = _scene_card_hash(chapter, scene)
    if sp.get("card") != card:
        sp.clear()
        sp["card"] = card
    path = ctx.scene_path(chapter["id"], scene["scene_number"], "draft")
    if not (sp.get("written") and path.exists()):
        brief = craft.brief(ctx, chapter, scene)
        if not sp.get("beats"):
            sp["beats"] = _plan_beats(ctx, chapter, {**scene, "craft_brief": brief}, step_dir)
            ctx.save_progress()
        sc = {**scene, "beats": sp["beats"], "craft_brief": brief}
        parts = ctx.plan_parts(sc)
        text = ""
        from bookforge.core import length as _len, pack
        for i, beats in enumerate(parts, 1):
            words = _len.ceiling(sum(_len.beat_words(b) for b in beats))
            system, prompt = pack.build_write(ctx, chapter, sc, beats, (chapter_text_so_far + "\n\n" + text).strip(),
                                              words, i, len(parts))
            (step_dir / f"prompt_s{scene['scene_number']:02d}_p{i}.txt").write_text(f"=== SYSTEM ===\n{system}\n\n=== USER ===\n{prompt}")
            out = llm_call(prompt, system=system, role="write", temperature=0.8, max_tokens=int(words * 2.2) + 8000)
            if i == len(parts):
                out = craft_rerank(ctx, chapter, scene, prompt, system, out, words, step_dir)
            text += ("\n\n" if text else "") + scrub(clean_prose(out))
        if scene.get("scene_number") == (chapter.get("scenes") or [{}])[0].get("scene_number"):
            from bookforge.core.rules_check import enforce_first_line
            text = enforce_first_line(chapter, text)
        ctx.write_text(path, text)
        sp["written"] = True
        sp.pop("summarized", None)
        sp.pop("canon", None)
        sp.pop("craft", None)
        ctx.save_progress()
    text = path.read_text()
    if not sp.get("summarized"):
        s = ctx.load_summaries()
        s["scenes"].setdefault(chapter["id"], {})[str(scene["scene_number"])] = summarize(text, "scene", 80)
        ctx.save_summaries(s)
        ctx.embeddings.replace(chapter["id"], scene["scene_number"], text, list(ctx.canon().entities))
        sp["summarized"] = True
        ctx.save_progress()
    if not sp.get("canon"):
        rep = canon_pipeline.process_scene(ctx, chapter["id"], scene, text, step_dir)
        sp["canon"] = {"facts": rep["facts_extracted"], "revision_tasks": len(rep["revision_tasks"]),
                       "review": len(rep["review"]), "amendments": len(rep["amendments"])}
        ctx.save_progress()
    if not sp.get("craft"):
        rep = craft.process_scene(ctx, chapter["id"], scene, text, step_dir)
        if rep["text"] != text:  # verbatim autofixes (e.g. prophecy wording)
            text = rep["text"]
            ctx.write_text(path, text)
        sp["craft"] = {"events": rep["events"], "issues": len(rep["issues"])}
        ctx.save_progress()
    return text


def craft_rerank(ctx, chapter, scene, prompt, system, first, words, step_dir):
    """Optional (craft.rerank, off by default): for high-tension scenes draw a second ending and keep the
    one a judge says better fits the scene card. Costs one extra write call plus one judge call."""
    from bookforge.craft.base import cfg
    c = cfg()
    card = craft.ledger(ctx).card(chapter["id"], scene.get("scene_number"))
    if not c["rerank"] or not card or card.get("tension", 0) < c["rerank_min_tension"]:
        return first
    second = llm_call(prompt, system=system, role="write", temperature=0.95, max_tokens=int(words * 2.2))
    verdict = llm_call(f"""Two candidate versions of the end of a scene. Pick the one that better delivers this scene card (shape, disaster or decision, tension {card.get('tension')}/10) with stronger, plainer prose.
CARD: {json.dumps(card)}
A:
{first[-3000:]}
B:
{second[-3000:]}
Output ONLY JSON: {{"choice": "A|B", "why": "..."}}""", role="rerank", json_mode=True, max_tokens=300, raw_dir=step_dir)
    return second if isinstance(verdict, dict) and verdict.get("choice") == "B" else first


def update_chapter_summaries(ctx, chapter_id):
    s = ctx.load_summaries()
    scenes = s["scenes"].get(chapter_id, {})
    s["chapters"][chapter_id] = summarize("\n".join(f"Scene {k}: {v}" for k, v in sorted(
        scenes.items(), key=lambda x: int(x[0]))), "chapter (given as scene summaries)", 200)
    ids = [c["id"] for c in ctx.load_outline()]
    if chapter_id in ids and (ids.index(chapter_id) + 1) % 3 == 0:
        prev = s.get("book", {}).get("text", "")
        recent = "\n".join(f"{c}: {s['chapters'][c]}" for c in ids[:ids.index(chapter_id) + 1]
                           if c in s["chapters"] and c > s.get("book", {}).get("through", ""))
        s["book"] = {"through": chapter_id,
                     "text": summarize(f"EARLIER SUMMARY:\n{prev}\n\nNEW CHAPTERS:\n{recent}", "book so far", 350)}
    ctx.save_summaries(s)


def run(ctx, chapter_id: str):
    inputs = progress.input_hash(ctx, "write", chapter_id)
    chapter = ctx.chapter(chapter_id)
    if not chapter:
        raise ValueError(f"Chapter {chapter_id} not found in outline")
    if not chapter.get("scenes"):
        raise ValueError(f"No scenes for {chapter_id}; run outline first")
    step_dir = ctx.new_step_folder(f"write_{chapter_id}")
    (step_dir / "input_snapshot.json").write_text(json.dumps(chapter, indent=2))
    print(f"Writing {chapter_id}: {chapter['title']} ({len(chapter['scenes'])} scenes)")
    texts = []
    for scene in chapter["scenes"]:
        texts.append(write_scene(ctx, chapter, scene, "\n\n".join(texts), step_dir))
        print(f"   scene {scene['scene_number']}: {len(texts[-1].split())} words")
    draft = join_scenes(texts)
    ctx.write_text(ctx.chapter_path(chapter_id, "draft"), draft)
    (step_dir / "draft.txt").write_text(draft)
    update_chapter_summaries(ctx, chapter_id)
    craft.refine_next(ctx, chapter_id)
    canon = ctx.canon()
    (step_dir / "canon_summary.json").write_text(json.dumps({
        "open_revision_tasks": canon.open_revision_tasks(chapter_id),
        "pending_reviews": canon.pending_reviews(), "open_threads": canon.open_threads()}, indent=2))
    progress.record(ctx, "write", chapter_id, inputs=inputs)
    print(f"{chapter_id} drafted: {len(draft.split())} words, "
          f"{len(canon.open_revision_tasks(chapter_id))} revision tasks, {len(canon.pending_reviews())} pending reviews")
