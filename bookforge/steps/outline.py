"""Outline: structure -> cast -> world -> scene expansion -> assessment.

Each phase is cached under state/outline_work/ keyed by its inputs, so a crash
resumes at the failed phase. Chapter ids are always ch01..chNN (code-enforced).
"""
import json

from pydantic import BaseModel
import math

from pydantic import Field

from bookforge.core import progress
from bookforge.craft import engine as craft
from bookforge.core.io import atomic_write_json, read_json, stable_hash
from bookforge.core.deai import scrub_obj
from bookforge.core.llm import llm_call
from bookforge.core.schema import (Chapter, _Loose, normalize_characters, normalize_outline,
                                   normalize_world)


class ChapterList(_Loose):
    chapters: list[Chapter] = Field(default_factory=list)


class Assessment(_Loose):
    overall_score: float = 0
    ready_to_write: bool = False
    blocking_issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


def _cached(ctx, name, key, fn):
    path = ctx.state_dir / "outline_work" / f"{name}.json"
    rec = read_json(path, None)
    if rec and rec.get("key") == key:
        return rec["data"]
    data = fn()
    atomic_write_json(path, {"key": key, "data": data})
    return data


def _unwrap_list(result):
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        if "id" in result and ("title" in result or "name" in result):
            return [result]
        lists = [v for v in result.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if lists:
            return max(lists, key=len)
    return []


def run(ctx):
    inputs = progress.input_hash(ctx, "outline")
    step_dir = ctx.new_step_folder("outline")
    _STYLE_CTX["ctx"] = ctx
    prompt = ctx.metadata["prompt"]
    target = None  # story-first: length is never an input (see core/length.py)
    bs = ctx.metadata.get("brainstorm", {})
    (step_dir / "input_snapshot.json").write_text(json.dumps({"prompt": prompt, "brainstorm": bs}, indent=2))
    k1 = stable_hash([prompt, bs])
    print("Phase 1: structure...")
    outline = _cached(ctx, "phase1", k1, lambda: _phase1(prompt, target, bs, step_dir))
    print(f"   -> {len(outline)} chapters")
    k2 = stable_hash([k1, outline])
    print("Phase 2: cast...")
    chars = _cached(ctx, "phase2", k2, lambda: _phase2(prompt, bs, outline, step_dir))
    k3 = stable_hash([k2, chars])
    print("Phase 3: world...")
    world = _cached(ctx, "phase3", k3, lambda: _phase3(prompt, bs, outline, chars, step_dir))
    print("Phase 4: scene expansion...")
    expanded = []
    for i, ch in enumerate(outline):
        k4 = stable_hash([k3, world, ch, outline[i - 1]["id"] if i else None])
        expanded.append(_cached(ctx, f"phase4_{ch['id']}", k4,
                                lambda ch=ch, i=i: _expand(prompt, outline, i, chars, world, step_dir)))
    expanded = normalize_outline(expanded)
    from bookforge.core import length as _len
    for ch in expanded:  # report-only sizes derived from the beats
        for s in ch["scenes"]:
            s["target_words"] = _len.scene_estimate(s)
        ch["target_words"] = _len.chapter_estimate(ch)
    print(f"   estimated length (report only): ~{sum(c['target_words'] for c in expanded)} words")
    print("Phase 5: assessment...")
    assessment = _phase5(prompt, target, expanded, chars, world, step_dir)
    for name, data in (("expanded_outline.json", expanded), ("characters.json", chars), ("world.json", world),
                       ("assessment.json", assessment)):
        (step_dir / name).write_text(json.dumps(data, indent=2))
    atomic_write_json(ctx.state_dir / "outline.json", expanded)
    ctx.save_characters(chars)
    ctx.save_world(world)
    print("Phase 6: craft plan (framework, scene cards, arcs, promises, foreshadowing, ...)")
    craft_issues = craft.plan_all(ctx, step_dir)
    (step_dir / "craft_plan_issues.json").write_text(json.dumps(craft_issues, indent=2))
    serious = [i for i in craft_issues if i["severity"] in ("critical", "major")]
    if serious:
        print(f"   craft plan: {len(serious)} issue(s) to review: bookforge craft check --project {ctx.project_dir}")
    progress.record(ctx, "outline", inputs=inputs)
    print(f"Outline complete -> {step_dir} ({len(expanded)} chapters, {len(chars)} characters, "
          f"readiness {assessment.get('overall_score')}/10)")
    if not assessment.get("ready_to_write"):
        print(f"   Issues: {assessment.get('blocking_issues')}")


def _phase1(prompt, target, bs, step_dir):
    from bookforge.core.length import NO_FILLER_RULE
    from bookforge.core.structure import CHAPTER_TYPES, ENDINGS, PLANNER_RULE as STRUCTURE_RULE
    TYPES, ENDINGS = ", ".join(CHAPTER_TYPES), ", ".join(ENDINGS)
    res = llm_call(f"""You are a story architect. Design the outline this story needs, with a clear 3-act structure. Build the story first and keep adding chapters until the story is complete; the chapter count follows from the events, never from a word count.

PREMISE: {prompt}
GENRE: {bs.get('genre', '')}
THEMES: {json.dumps(bs.get('themes', []))}

Rules:
- A chapter is one story movement: something happens that changes the situation. Short chapters are fine; a chapter with only one thin event should be merged into its neighbor instead.
- Every chapter ends on a hook: a question, reversal, or revelation.
- 1-6 concrete key_events per chapter, as many as the movement really has.
- {NO_FILLER_RULE}
- Schedule introductions of any fantastical element.
- Track Chekhov's guns: "setups" lists details planted in this chapter that must pay off later; "payoffs" lists earlier setups resolved here (use the same wording as the setup).
- Every setup needs a later payoff and every payoff an earlier setup.
- {STRUCTURE_RULE} Give every chapter a "structure" block: act (1, 2a, 2b or 3), save_the_cat beat, heros_journey stage, chapter_type ({TYPES}), strand (A, B or AB) with a_purpose/b_purpose, plot_function, value_turn {{"from","to"}}, scene_sequel, try_fail (if any), ending ({ENDINGS}), optional color.

Output ONLY JSON: {{"chapters": [{{"id": "ch01", "act": "Act 1", "title": "...", "summary": "<=100 words", "dramatic_question": "...", "key_events": ["..."], "ends_with": "...", "setups": ["..."], "payoffs": ["..."], "structure": {{"act": "1", "save_the_cat": "opening_image", "heros_journey": "ordinary_world", "chapter_type": "discovery", "strand": "A", "a_purpose": "...", "b_purpose": "", "plot_function": "...", "value_turn": {{"from": "...", "to": "..."}}, "scene_sequel": "scene+sequel", "try_fail": "", "ending": "reveal", "color": ""}}}}]}}""",
                   role="outline", max_tokens=16000, json_mode=True, raw_dir=step_dir)
    res = scrub_obj(res)
    (step_dir / "high_level_outline_raw.json").write_text(json.dumps(res, indent=2))
    return normalize_outline(_unwrap_list(res))


def _phase2(prompt, bs, outline, step_dir):
    res = llm_call(f"""You are a character psychologist. Build the cast: 2-3 main and 3-5 supporting characters.

PREMISE: {prompt}
GENRE: {bs.get('genre', '')}
OUTLINE: {json.dumps([{k: c[k] for k in ('id', 'title', 'summary')} for c in outline])}

Each character has contradictions, a concrete want, a wound that predates the story, and a change that costs them. Give each a stable snake_case id.

Output ONLY JSON: {{"characters": [{{"id": "snake_case", "name": "Full Name", "aliases": ["nickname"], "role": "protagonist|antagonist|love interest|supporting|minor", "age": 30, "gender": "...", "physical": "...", "personality": "how they act", "backstory": "...", "motivation": "want vs need", "secret": "what they hide, or empty", "arc": "start -> catalyst -> cost -> end", "voice": "cadence, vocabulary, tics", "relationships": {{"other_id": "dynamic"}}, "chapters_present": ["ch01"]}}]}}""",
                   role="outline", max_tokens=8000, json_mode=True, raw_dir=step_dir)
    return normalize_characters(scrub_obj(_unwrap_list(res) or res))


def _phase3(prompt, bs, outline, chars, step_dir):
    res = llm_call(f"""You are a worldbuilder. Create a story-serving world bible with hard rules.

PREMISE: {prompt}
GENRE: {bs.get('genre', '')}
OUTLINE: {json.dumps([{"id": c["id"], "title": c["title"], "summary": c["summary"][:150]} for c in outline])}
CHARACTERS: {json.dumps([{"id": k, "name": c["name"], "role": c["role"]} for k, c in chars.items()])}

Output ONLY JSON: {{"genre_tone": "...", "core_rules": ["rule: cost or limit"], "key_locations": [{{"name": "...", "description": "senses", "significance": "..."}}], "history_timeline": ["..."], "culture_society": {{"government": "...", "religion": "...", "daily_life": "...", "taboos": "..."}}, "economy_resources": "...", "sensory_palette": {{"colors": "...", "sounds": "...", "textures": "...", "smells": "...", "weather": "..."}}, "unique_elements": ["..."], "consistency_flags": ["NEVER ...", "ALWAYS ..."], "lore": ["..."]}}""",
                   role="worldbuild", max_tokens=8000, json_mode=True, raw_dir=step_dir)
    return normalize_world(scrub_obj(res))


_STYLE_CTX = {"ctx": None}


def _style_for_planner() -> str:
    """Style bible guidance + compact voice sheets for planner prompts (budgeted)."""
    ctx = _STYLE_CTX["ctx"]
    if ctx is None:
        return ""
    from bookforge.config import load_config
    c = load_config().get("style", {}) or {}
    sb = ctx.style()
    if sb.empty:
        return ""
    parts = [sb.block(int(c.get("planner_tokens", 2000)), for_drafter=False),
             sb.all_voices_block(int(c.get("planner_voice_tokens", 1500)))]
    ids = sb.sample_ids()
    if ids:
        parts.append("STYLE SAMPLE IDS available for style_samples: " + ", ".join(ids))
    return "\n\n".join(p for p in parts if p) + "\n"


class PlanCheck(BaseModel):
    problems: list[str] = []
    plan: dict = {}


def selfcheck_plan(ctx, plan: dict, step_dir=None) -> dict | None:
    """The planner re-reads its own chapter plan against the full story pack and returns a corrected plan."""
    from bookforge.core import pack as _pack
    res = llm_call(f"""{_pack.chapter_pack(ctx, plan)}PLAN TO CHECK (yours, for chapter {plan.get('id')}):
{json.dumps(plan, ensure_ascii=False)}

You wrote this chapter plan. Check it line by line against the story pack (continuity sheet, bible, beat sheet, decisions log) and the earlier chapters' full text, then fix it. Look for:
- continuity errors: geography and routes, who lives where and owns what, injuries, objects, time of day and how much time has passed, what each character knows;
- anyone or anything that a beat treats as known but that has never been introduced (INTRODUCE = first appearance in the book), and anything re-introduced that the reader already met;
- story obligations not carried by a named scene's beats; the chapter's ends_with not delivered;
- beats that are vague, filler, or repeat the previous chapter's central beat; any scene that does not serve the chapter's structure.plot_function or value turn (keep colour only where it rides on a scene that does);
- a crisis that stops before its resolution (the chapter must not end mid-crisis unless the beat sheet asks for a cliffhanger); fights planned as a rushed summary instead of vivid moment-by-moment visual beats; characters pausing, setting objects down, or making speeches as someone dies; peaks planned as dialogue instead of what the POV character sees;
- staging a writer would have to guess (who is where, how they get there, who sees what).
Keep everything that is right. Output ONLY JSON: {{"problems": ["what you found and fixed"], "plan": {{...the full corrected plan, same keys...}}}}""",
                   system=_pack.system_for(ctx, plan), role="outline", schema=PlanCheck, max_tokens=14000, raw_dir=step_dir)
    fixed = scrub_obj(res.plan or {})
    if step_dir is not None:
        dumped = res.model_dump()
        dumped["plan"] = fixed
        (step_dir / f"plan_selfcheck_{plan.get('id')}.json").write_text(json.dumps(dumped, indent=2, ensure_ascii=False))
    return fixed if fixed.get("scenes") else None


def _expand(prompt, outline, i, chars, world, step_dir):
    ch = outline[i]
    from bookforge.core.length import BEAT_TYPE_GUIDE, CRISIS_RULE, NO_FILLER_RULE
    prev = f"{outline[i - 1]['title']}: {outline[i - 1].get('ends_with', '')}" if i else "N/A (opening)"
    nxt = outline[i + 1]["title"] if i < len(outline) - 1 else "N/A (finale)"
    cast = json.dumps([{"id": k, "name": c["name"], "role": c["role"], "voice": c.get("voice", "")}
                       for k, c in chars.items()])
    sb = _style_for_planner()
    obl = {}
    tctx = _STYLE_CTX.get("ctx")
    if tctx is not None:
        from bookforge.core import threads as _threads
        if _threads.enabled(tctx):
            obl = _threads.plan_obligations(tctx, ch, outline, raw_dir=step_dir)
            sb += ("\nCHAPTER STORY OBLIGATIONS (every one must land in a named scene's beats; give each scene a "
                   "'thread_obligations' list copying the ones it carries):\nWhy this chapter matters: "
                   + obl["why_matters"] + "\nOpening hook (scene 1's first beat must deliver it): " + obl["opening_hook"]
                   + "\nGrounding: " + obl.get("grounding", "")
                   + (("\nOpening intent (guidance for what scene 1's opening should do or convey; not wording): "
                      + (obl.get("opening_intent") or obl.get("first_line")))
                     if (obl.get("opening_intent") or obl.get("first_line")) else "")
                   + "\n" + _threads.render(obl["thread_obligations"], _threads.load(tctx)) + "\n")
    from bookforge.core import structure as _structure
    sb += "\n" + _structure.PLANNER_RULE + "\n"
    _sblock = _structure.block(ch)
    if _sblock:
        sb += _sblock + "\n(Every scene's beats must serve this function and deliver the value turn.)\n"
    psys, ppack = None, ""
    if tctx is not None:
        from bookforge.core import pack as _pack
        psys = _pack.system_for(tctx, ch)
        ppack = _pack.chapter_pack(tctx, ch)
    res = llm_call(f"""{ppack}You are a scene architect. Expand this chapter into the scenes a writer can execute without guessing. Start a new scene only at a change of location, a jump in time, or a change of POV; one scene is fine if the chapter is continuous.

PREMISE: {prompt}
{sb}
CAST (use these names exactly): {cast}
WORLD RULES: {json.dumps(world.get('core_rules', []))}
LOCATIONS: {json.dumps([l['name'] for l in world.get('key_locations', [])])}
CHAPTER: {json.dumps({k: ch.get(k) for k in ('id', 'title', 'act', 'summary', 'key_events', 'dramatic_question', 'ends_with', 'setups', 'payoffs')})}
PREVIOUS CHAPTER ENDED: {prev}
NEXT CHAPTER: {nxt}
LENGTH: there is no word target. Each beat takes the words it needs; the chapter's size is the sum of its beats. {NO_FILLER_RULE} {CRISIS_RULE}

Rules: every scene is a rich brief: POV; "wants" = who wants what in this scene (one entry per character with a goal); the obstacle; "emotional_turn" = the POV character's value at start -> end (it must change); exactly one concrete "sensory_anchor"; the "closing_image" the scene ends on (an image or action, never a moral or summary); "voice_notes" for each present character (how they talk here, drawn from their voice sheet); a "tone_target"; and "style_samples" = ids of style-bible example passages to imitate (empty if none). Every scene has a turning point; "beats" are 1-8 concrete, ordered events, as many as the scene really has (never invent beats or business to fill it out); {BEAT_TYPE_GUIDE}; the final scene delivers ends_with; setups listed for the chapter must be planted in some scene's beats and payoffs resolved; lock "person" (first|third) and "tense" (past|present) for the book.\n\nCLARITY (reader is about 16): plans must never tell the writer to withhold basics. Every place, person, object and magical thing gets introduced plainly the first time (say what it is). Strange events are described directly so the reader can picture them. Keep chronology clear. Telling is fine for facts, setup and context. Never write directives like \"without explaining it\", \"leave it unexplained\" or \"don't say what it is\"; the only things held back are story secrets the ledger says aren't due yet, and even those are shown plainly as events.

PLANNING RULE: give tone and intent, never dialogue or exact lines. Beats, voice_notes and every other field describe what happens and how it should feel; no quoted speech, no scripted sentences. Dialogue and phrasing belong to the writer.

Output ONLY JSON: {{"id": "{ch['id']}", "title": "...", "act": "...", "summary": "...", "dramatic_question": "...", "key_events": [...], "ends_with": "...", "setups": [...], "payoffs": [...], "scenes": [{{"scene_number": 1, "pov": "character name", "person": "third", "tense": "past", "location": "...", "characters_present": ["name"], "action": "summary", "beats": ["(~350w, reveal) ..."], "emotional_arc": "FROM -> TO", "sensory_details": "...", "purpose": "...", "prose_modes": [], "character_moments": [], "world_reveals": [], "wants": ["Name: wants ..."], "obstacle": "...", "emotional_turn": "wary -> trusting", "sensory_anchor": "...", "closing_image": "...", "voice_notes": ["Name: ..."], "tone_target": "...", "style_samples": []}}]}}""",
                   system=psys, role="outline", max_tokens=12000, json_mode=True, raw_dir=step_dir)
    if not isinstance(res, dict):
        res = {}
    if tctx is not None and res.get("scenes"):
        res = selfcheck_plan(tctx, {**ch, **res}, step_dir) or res
    merged = {**ch, **{k: v for k, v in res.items() if v}}
    merged["id"] = ch["id"]
    from bookforge.core import length as _len
    for s in merged.get("scenes") or []:
        if isinstance(s, dict):
            s["target_words"] = _len.scene_estimate(s)  # report-only
    merged["target_words"] = sum(s.get("target_words", 0) for s in merged.get("scenes") or [] if isinstance(s, dict))
    if obl:
        from bookforge.core import threads as _threads
        _threads.apply_to_chapter(merged, obl)
        merged["plan_source"] = "planned"
    return scrub_obj(Chapter.model_validate(merged).model_dump())


def _phase5(prompt, target, expanded, chars, world, step_dir):
    brief = [{"id": c["id"], "title": c["title"], "ends_with": c["ends_with"], "setups": c["setups"],
              "payoffs": c["payoffs"], "scenes": [s["action"][:150] for s in c["scenes"]]} for c in expanded]
    res = llm_call(f"""You are a ruthless developmental editor. Find every weakness in this plan.

PREMISE: {prompt}
CHAPTERS: {json.dumps(brief, indent=1)}
ARCS: {json.dumps([{"name": c["name"], "arc": c["arc"], "motivation": c["motivation"]} for c in chars.values()])}
RULES: {json.dumps(world.get('core_rules', []))}

Check structure, pacing, arcs, plot holes, stakes, and that every setup has a payoff. Flag thin chapters (one slight event, or buildup to something the next chapter delivers) as candidates to merge; never suggest adding material just for length.
Output ONLY JSON: {{"overall_score": 0-10, "ready_to_write": true, "blocking_issues": [], "suggestions": []}}""",
                    role="critique", schema=Assessment, max_tokens=4000, raw_dir=step_dir)
    return scrub_obj(res.model_dump())
