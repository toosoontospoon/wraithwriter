"""Draft a chapter scene by scene: plan beats -> write -> summarize -> canon pass.

Resumable at scene granularity: each scene's text lives in manuscript/chNN/sceneNN_draft.txt
and progress.scenes[ch][n] records which sub-steps finished for which scene card.
"""
import json
from pathlib import Path

from pydantic import Field

from bookforge.core import canon_pipeline, progress
from bookforge.craft import engine as craft
from bookforge.core.deai import scrub, scrub_obj
from bookforge.core.io import stable_hash
from bookforge.config import resolve_role, tight_context_model
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
    beats = res.beats or scene.get("beats") or [scene.get("action", "Write the scene.")]
    return scrub_obj(beats)


def write_max_tokens(words: int, role: str = "write") -> int:
    """Requested completion tokens before tier headroom.

    The library drafter adds ``int(words * 2.2) + 8000`` and the tier adds 6000 more.
    A 32k model (Gemma on the HF router) skips that 8000 pad; headroom is clamped in
    ``adjusted_role_params`` so the call does not ask for 8000+ extra tokens.
    """
    base = int(words * 2.2)
    provider, model = resolve_role(role)
    if tight_context_model(provider, model):
        return base
    return base + 8000


def _append_continuation(text: str, opening: str, continuation: str, first_chunk: bool) -> str:
    """Append a continuation. Cleaners have already run on ``continuation`` only."""
    from bookforge.core.human_seed import join_opening
    if first_chunk and opening and not text:
        return join_opening(opening, continuation)
    return text + (("\n\n" if text else "") + continuation)


def _raw_scene(opening: str, chunks: list[str]) -> str:
    """Seed prefix plus raw model continuations, joined the same way as the draft."""
    from bookforge.core.human_seed import join_opening
    text = ""
    for i, chunk in enumerate(chunks):
        if opening and i == 0:
            text = join_opening(opening, chunk)
        else:
            text += ("\n\n" if text else "") + chunk
    return text


def _join_pieces(opening: str, pieces: list[str]) -> str:
    text = ""
    for i, piece in enumerate(pieces, 1):
        text = _append_continuation(text, opening, piece, i == 1)
    return text


def beat_by_beat_enabled() -> bool:
    """Gemma writes one call per beat group. Other tiers do only when the flag is set."""
    from bookforge.config import load_config, resolve_role
    write = load_config().get("write") or {}
    if write.get("beat_by_beat"):
        return True
    try:
        provider, model = resolve_role("write")
    except Exception:
        return False
    return provider == "huggingface" and "gemma" in (model or "").lower()


def beats_per_call() -> int:
    """1 or 2 beats per write call. Any other value is treated as 1."""
    from bookforge.config import load_config
    try:
        n = int((load_config().get("write") or {}).get("beats_per_call") or 1)
    except (TypeError, ValueError):
        return 1
    return 2 if n == 2 else 1


def _beat_groups(ctx, scene) -> list[list[str]]:
    if not beat_by_beat_enabled():
        return ctx.plan_parts(scene)
    beats = list(scene.get("beats") or []) or ["Write the scene."]
    per = beats_per_call()
    return [beats[i:i + per] for i in range(0, len(beats), per)]


def _clean_continuation(out: str, chapter, scene, opening: str, part: int) -> str:
    """Scrub and clean one continuation. The seed is not an argument."""
    piece = scrub(clean_prose(out))
    first = (chapter.get("scenes") or [{}])[0].get("scene_number")
    if not opening and scene.get("scene_number") == first and part == 1:
        from bookforge.core.rules_check import enforce_first_line
        piece = enforce_first_line(chapter, piece)
    return piece


def _parse_drift(res):
    """A plan reply is a verdict only when it carries drift, covered, or missing.

    A beat sheet (``{"beats": [...]}``) is not a verdict. Do not treat a missing
    ``covered`` list as a miss: that is what a default-filled schema would invent.
    """
    if not isinstance(res, dict):
        return None
    if not any(k in res for k in ("drift", "covered", "missing")):
        return None
    missing = res.get("missing") or []
    if not isinstance(missing, list):
        missing = [str(missing)]
    reason = str(res.get("reason") or "").strip()
    if res.get("drift") or missing:
        return {"fail": True, "reason": reason or "missed or extra beats", "missing": missing}
    return {"fail": False, "reason": reason}


def _drift_verdict(scene, beats, piece, chunk, step_dir):
    card = {k: scene.get(k) for k in ("scene_number", "pov", "location", "time", "characters_present",
                                       "purpose", "conflict", "sensory_anchor", "closing_image") if scene.get(k)}
    card["beats"] = list(beats)
    prompt = (
        "BEAT CHECK: does this chunk do its beat(s) and only them?\n"
        f"SCENE CARD: {json.dumps(card)}\n"
        f"SCENE: {scene.get('scene_number')} CHUNK INDEX: {chunk}\n"
        f"CHUNK:\n{piece}\n"
        'Output ONLY JSON: {"drift": false, "covered": [], "missing": [], "reason": ""}'
    )
    res = llm_call(prompt, role="plan", json_mode=True, temperature=0.1, max_tokens=800, raw_dir=step_dir)
    return _parse_drift(res)


def _flag_drift(step_dir, scene, chunk, beats, reason):
    path = Path(step_dir) / "drift_report.json"
    rows = json.loads(path.read_text()) if path.exists() else []
    rows.append({"scene": scene.get("scene_number"), "chunk": chunk, "beats": list(beats), "reason": reason})
    path.write_text(json.dumps(rows, indent=2))


def write_scene(ctx, chapter, scene, chapter_text_so_far, step_dir, raw_scenes=None):
    from bookforge.core.human_seed import seed_for_scene
    sp = ctx.progress["scenes"].setdefault(chapter["id"], {}).setdefault(str(scene["scene_number"]), {})
    card = _scene_card_hash(chapter, scene)
    if sp.get("card") != card:
        sp.clear()
        sp["card"] = card
    path = ctx.scene_path(chapter["id"], scene["scene_number"], "draft")
    opening = seed_for_scene(ctx, chapter, scene)
    fresh_raw = None
    if not (sp.get("written") and path.exists()):
        brief = craft.brief(ctx, chapter, scene)
        if not sp.get("beats"):
            sp["beats"] = _plan_beats(ctx, chapter, {**scene, "craft_brief": brief}, step_dir)
            ctx.save_progress()
        sc = {**scene, "beats": sp["beats"], "craft_brief": brief}
        parts = _beat_groups(ctx, sc)
        pieces, raw_chunks = [], []
        beat_mode = beat_by_beat_enabled()
        from bookforge.core import length as _len, pack
        for i, beats in enumerate(parts, 1):
            words = _len.ceiling(sum(_len.beat_words(b) for b in beats))

            def _so_far():
                return (chapter_text_so_far + "\n\n" + _join_pieces(opening, pieces)).strip()

            def _produce(reason=None, rerank=False):
                system, prompt = pack.build_write(ctx, chapter, sc, beats, _so_far(), words, i, len(parts))
                provider, model = resolve_role("write")
                system, prompt = pack.fit_write_context(ctx, system, prompt, provider, model)
                if opening and i == 1:
                    from bookforge.core.human_seed import continuation_note
                    prompt += "\n\n" + continuation_note(opening)
                if reason:
                    prompt += (f"\n\nThe previous chunk drifted ({reason}). "
                               "Rewrite so this chunk does its beat(s) and only them.")
                (step_dir / f"prompt_s{scene['scene_number']:02d}_p{i}.txt").write_text(
                    f"=== SYSTEM ===\n{system}\n\n=== USER ===\n{prompt}")
                out = llm_call(prompt, system=system, role="write", temperature=0.8,
                               max_tokens=write_max_tokens(words))
                if rerank:
                    out = craft_rerank(ctx, chapter, scene, prompt, system, out, words, step_dir)
                piece = _clean_continuation(out, chapter, scene, opening, i)
                if beat_mode:
                    canon_pipeline.process_scene(
                        ctx, chapter["id"], scene, _join_pieces(opening, pieces + [piece]), step_dir, chunk=i)
                return out, piece

            out, piece = _produce(rerank=(i == len(parts)))
            if beat_mode:
                verdict = _drift_verdict(sc, beats, piece, i, step_dir)
                if verdict and verdict["fail"]:
                    out, piece = _produce(reason=verdict["reason"])
                    again = _drift_verdict(sc, beats, piece, i, step_dir)
                    if again and again["fail"]:
                        _flag_drift(step_dir, scene, i, beats, again["reason"])
            raw_chunks.append(out)
            pieces.append(piece)
        text = _join_pieces(opening, pieces)
        ctx.write_text(path, text)
        fresh_raw = _raw_scene(opening, raw_chunks)
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
        # Craft scrubs its argument. Give it the continuation so the seed is not an argument.
        craft_src = text[len(opening):] if opening and text.startswith(opening) else text
        rep = craft.process_scene(ctx, chapter["id"], scene, craft_src, step_dir)
        if opening and text.startswith(opening):
            if rep["text"] != craft_src:
                text = opening + rep["text"]
                ctx.write_text(path, text)
        elif rep["text"] != text:  # verbatim autofixes (e.g. prophecy wording)
            text = scrub(rep["text"])
            ctx.write_text(path, text)
        sp["craft"] = {"events": rep["events"], "issues": len(rep["issues"])}
        ctx.save_progress()
    if raw_scenes is not None:
        raw_scenes.append(fresh_raw if fresh_raw is not None else text)
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


def _raw_dir_for(ctx, raw_dir):
    from bookforge.config import load_config
    if raw_dir:
        return Path(raw_dir)
    hooked = getattr(ctx, "write_raw_dir", None)
    if hooked:
        return Path(hooked)
    configured = (load_config().get("write") or {}).get("raw_dir")
    return Path(configured) if configured else None


def _write_raw_file(ctx, chapter_id, raw_scenes, raw_dir):
    from bookforge.core.human_seed import next_raw_path, raw_family
    directory = _raw_dir_for(ctx, raw_dir)
    if not directory or not raw_scenes:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    _provider, model = resolve_role("write")
    path = next_raw_path(directory, chapter_id, raw_family(model))
    path.write_text(join_scenes(raw_scenes), encoding="utf-8")
    return path


def run(ctx, chapter_id: str, raw_dir=None):
    from bookforge.core.human_seed import ensure_chapter_seed
    inputs = progress.input_hash(ctx, "write", chapter_id)
    chapter = ctx.chapter(chapter_id)
    if not chapter:
        raise ValueError(f"Chapter {chapter_id} not found in outline")
    if not chapter.get("scenes"):
        raise ValueError(f"No scenes for {chapter_id}; run outline first")
    ensure_chapter_seed(ctx, chapter)
    step_dir = ctx.new_step_folder(f"write_{chapter_id}")
    (step_dir / "input_snapshot.json").write_text(json.dumps(chapter, indent=2))
    print(f"Writing {chapter_id}: {chapter['title']} ({len(chapter['scenes'])} scenes)")
    texts, raw_scenes = [], []
    for scene in chapter["scenes"]:
        texts.append(write_scene(ctx, chapter, scene, "\n\n".join(texts), step_dir, raw_scenes))
        print(f"   scene {scene['scene_number']}: {len(texts[-1].split())} words")
    draft = join_scenes(texts)
    ctx.write_text(ctx.chapter_path(chapter_id, "draft"), draft)
    (step_dir / "draft.txt").write_text(draft)
    _write_raw_file(ctx, chapter_id, raw_scenes, raw_dir)
    update_chapter_summaries(ctx, chapter_id)
    craft.refine_next(ctx, chapter_id)
    canon = ctx.canon()
    (step_dir / "canon_summary.json").write_text(json.dumps({
        "open_revision_tasks": canon.open_revision_tasks(chapter_id),
        "pending_reviews": canon.pending_reviews(), "open_threads": canon.open_threads()}, indent=2))
    progress.record(ctx, "write", chapter_id, inputs=inputs)
    print(f"{chapter_id} drafted: {len(draft.split())} words, "
          f"{len(canon.open_revision_tasks(chapter_id))} revision tasks, {len(canon.pending_reviews())} pending reviews")
