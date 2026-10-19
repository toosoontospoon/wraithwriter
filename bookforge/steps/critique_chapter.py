"""Critique: deterministic checks (names, POV, AI tells) + canon-aware LLM editor pass."""
import json

from bookforge.core import canon_pipeline, deai, progress
from bookforge.core.io import atomic_write_json
from bookforge.core.llm import llm_call
from bookforge.core.schema import Critique


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


def _protect_block(chapter):
    from bookforge.core import protect
    b = protect.block(protect.protected(chapter), protect.facts(chapter))
    return (b + "\n") if b else ""


def critique_text(ctx, chapter, text, step_dir=None) -> dict:
    canon = ctx.canon()
    checks = canon_pipeline.code_checks(text, (chapter.get("scenes") or [{}])[0], canon)
    tells = deai.lint(text, ctx.cast_names())
    facts = [f for f in canon.active_facts() if f["importance"] == "critical"
             or any(p.get("chapter") == chapter["id"] for p in f["provenance"])][:80]
    tasks = canon.open_revision_tasks(chapter["id"])
    from bookforge.craft import engine as craft
    briefs = "\n".join(b for b in (craft.brief(ctx, chapter, s) for s in chapter.get("scenes") or []) if b)
    excerpt = text if len(text) < 60000 else text[:60000] + "\n[...truncated...]"
    prompt = f"""You are a tough, practical editor of upper-YA fantasy for readers around 16. Reward clear, gripping, funny, moving storytelling. Do not reward withholding, vagueness or literary showing-off; penalize anything a 16-year-old would find confusing (unexplained places, people, objects or magic; unclear chronology; strange events not plainly described). Cite exact phrases.

CHAPTER {chapter['id']}: {chapter.get('title', '')}
DRAMATIC QUESTION: {chapter.get('dramatic_question', '')}
MUST END WITH: {chapter.get('ends_with', '')}
SETUPS TO PLANT: {chapter.get('setups', [])}  PAYOFFS DUE: {chapter.get('payoffs', [])}

MACHINE-EXTRACTED NOTES (may be wrong; the story pack in the system prompt is the source of truth):
{chr(10).join('- ' + f['claim'] for f in facts) or '(none)'}

{('CRAFT PLAN (judge whether the draft delivers it):' + chr(10) + briefs + chr(10)) if briefs else ''}
AUTOMATED FINDINGS:
- names not in cast: {[u['name'] for u in checks['unknown_names']][:15]}
- POV: {checks['pov_issue'] or 'ok'}
- AI-tell counts: {tells['counts']}
- open continuity fixes: {[t['fix'] for t in tasks][:10]}

{_protect_block(chapter)}
DRAFT:
{excerpt}

Score 0-10 each: clarity (could a 16-year-old always follow where we are, who is who, what things are and what happened?), style, engagement, pacing, arc_integration, mode_balance, world_integration, narration_balance, consistency (agreement with the story pack: continuity sheet, bible, earlier chapters' text), economy (10 = every paragraph moves plot, character, setting the reader needs, or a needed explanation; low = filler). Length itself is never a fault: a short chapter that tells its story fully is ideal. Also flag as weak spots: legends named without a short explanation; unclear staging or physical action (who stands where; every blow or grab must name actor, body part or object, direction, target and result; every change of position stated); unexplained motives for pointed actions; strange events without a clear witness and vantage, or shrugged off without a reason; effects without a physical cause; continuity slips in chores, food or objects; routine business that runs long without moving plot or character; padding or filler (chores, small talk, scenery, recap or repeated beats that exist only to take up space); buildup that only delays what the next chapter delivers. For each weak spot give "location" as a verbatim quote of 5-20 words from the draft.

Output ONLY JSON: {{"clarity_score": 0, "style_score": 0, "engagement_score": 0, "pacing_score": 0, "arc_integration_score": 0, "mode_balance_score": 0, "world_integration_score": 0, "narration_balance_score": 0, "consistency_score": 0, "economy_score": 0, "consistency_issues": [], "weak_spots": [{{"location": "verbatim quote", "problem": "...", "severity": "minor|major|critical"}}], "suggested_fixes": [], "strongest_moments": []}}"""
    prompt = _pack_prefix(ctx, chapter) + prompt
    c = llm_call(prompt, system=_pack_system(ctx, chapter), role="critique", schema=Critique, max_tokens=4000,
                 raw_dir=step_dir).model_dump()
    from bookforge.core import protect
    c["weak_spots"], c["protected_dropped"] = protect.filter_spots(c.get("weak_spots", []), protect.protected(chapter))
    c["tells_score"] = deai.tells_score(text)
    c["automated"] = {"unknown_names": checks["unknown_names"][:20], "pov_issue": checks["pov_issue"],
                      "tells": tells["counts"]}
    c["total"] = Critique.model_validate(c).total() + c["tells_score"]
    return c


def run(ctx, chapter_id: str):
    inputs = progress.input_hash(ctx, "critique", chapter_id)
    chapter = ctx.chapter(chapter_id)
    draft = ctx.chapter_path(chapter_id, "draft")
    if not chapter or not draft.exists():
        raise FileNotFoundError(f"Need outline entry and draft for {chapter_id}")
    step_dir = ctx.new_step_folder(f"critique_{chapter_id}")
    (step_dir / "input_snapshot.json").write_text(json.dumps(chapter, indent=2))
    from bookforge.core import plan_sync, threads
    text = draft.read_text()
    c = critique_text(ctx, chapter, text, step_dir)
    if chapter.get("thread_obligations"):
        tc = threads.check_obligations(ctx, chapter, text, raw_dir=step_dir)
        c["thread_checks"] = tc
        for r in threads.failed(tc):
            c.setdefault("weak_spots", []).append({"location": "", "severity": "major", "thread": r.get("thread"),
                                                   "problem": f"Thread obligation not met ({r.get('action')} {r.get('thread')}): "
                                                              f"{r.get('note', '')} FIX: {r.get('fix', '')}"})
        print(f"   thread obligations: {len(tc) - len(threads.failed(tc))}/{len(tc)} met")
    from bookforge.core import rules_check
    rc = rules_check.check(ctx, chapter, text, raw_dir=step_dir)
    c["rule_checks"] = rc
    from bookforge.core import protect
    _prot = protect.protected(chapter)
    for r in rules_check.failed(rc):
        if protect.hits(r.get("quote", ""), _prot):
            c.setdefault("protected_dropped", []).append(r)
            continue
        c.setdefault("weak_spots", []).append({"location": r.get("quote", "")[:200], "severity": "major", "rule": r.get("rule"),
                                               "problem": f"Craft rule failed ({r.get('rule')}): {r.get('note', '')} FIX: {r.get('fix', '')}"})
    print(f"   craft rules: {len(rc) - len(rules_check.failed(rc))}/{len(rc)} pass "
          f"{[r['rule'] for r in rules_check.failed(rc)]}")
    (step_dir / "critique.json").write_text(json.dumps(c, indent=2))
    atomic_write_json(ctx.state_dir / "critiques" / f"{chapter_id}.json", c)
    progress.record(ctx, "critique", chapter_id, inputs=inputs)
    print(f"{chapter_id} critiqued: total {c['total']:.1f}, consistency {c['consistency_score']}, tells {c['tells_score']}")
