"""Chapter craft-rule checks (LLM editor, pass/fail with quotes).

Rules (each can be disabled in config  rules_check: {disabled: [ids]}):
  clarity           - a 16-year-old can follow it: things introduced plainly, strange events described directly, clear chronology
  dialogue_variety  - no predictable say/reply/qualify rhythm, no "not A but B" volleys, no reused sample lines
  as_you_know       - nobody asks about or explains what they already know; such facts go to narration
  scene_closure     - every tension/fear/setup raised in a scene pays off or is visibly carried forward
  alternation       - narration and dialogue alternate; no long unbroken narration blocks
  grounding         - era, home, means, landscape, flora, weather, season and senses are concrete and era-true
  variety           - the chapter doesn't repeat the previous chapter's central beat or setting focus
"""
from __future__ import annotations

import json

from bookforge.config import load_config
from bookforge.core.llm import llm_call

RULES = {
    "physical_clarity": "Physical action is always clear. Before a struggle everyone is placed (who stands where, facing what, what is between them). Every blow or grab names the actor, the body part or object used, the direction, what it hits and the result. Every change of position (falls, backs up, gets dragged, stands) is stated. FAIL quoting each spot where a reader could not say who hit whom, where someone is, or what moved.",
    "tempo": "Urgency lives in the characters, not in rushed prose. A fight or survival crisis is painted moment by moment in rich, concrete description, while the panicked characters never set things down, tidy, talk sentimentally, trade movie-style lines or make speeches as anyone dies (fragments only). FAIL quoting any line that stalls a crisis with business or talk, or a fight summarized too fast to see.",
    "visual_peaks": "Dramatic peaks are told visually, through what the POV character sees, in vivid, concrete, lingering images (size, speed, wrongness, fear in the body), with minimal dialogue. FAIL quoting a peak carried by dialogue, or told flatly without vivid images.",
    "continuity": "Nothing contradicts the story pack (the continuity sheet, the bible, and the earlier chapters' full text): places and routes, who lives where and owns what, injuries, objects, what each character knows, who is present, time of day. FAIL quoting each contradiction and citing the pack fact it breaks.",
    "introductions": "INTRODUCE means first appearance in the book. FAIL quoting any person, place, object or bit of magic named in this chapter that has not appeared in an earlier chapter and is not plainly introduced here at first mention (what or who it is). Also FAIL if something the earlier chapters already introduced is re-explained at length as if new.",
    "narrator_voice": "The narration carries the narrator voice of the style bible (idiom, rhythm, wry warmth) and never names the narrator or says he was there. FAIL quoting stretches of flat, generic narration where the voice is lost, and any line that reveals the narrator's identity.",
    "speaker_tags": "Whenever two or more people could be talking, every line's speaker is clear, and every tag names the right person. FAIL quoting a line whose speaker is unclear or mis-tagged.",
    "clarity": "Clarity for a 16-year-old: every place, person, object and magical thing is introduced plainly the first time (the reader is told what it is), strange events are described directly, and the order of events is clear. FAIL quoting each spot where a 16-year-old would be confused: a name used before saying what it is (e.g. a creek named without saying it is a creek), an unclear who/where/when, or a strange event described too vaguely to picture.",
    "dialogue_variety": "Exchanges vary in shape (interruptions, non-answers, silence, action instead of reply, one-sided runs). FAIL if several exchanges follow the same statement -> response -> qualification rhythm, if there are 'not A but B' volleys, or if voice-sheet sample lines are reused.",
    "as_you_know": "No character asks about, or explains, something they and the listener already know (family objects, shared history, their own home). FAIL with the quote if any line does.",
    "scene_closure": "Every tension, fear, or setup raised in a scene pays off in that scene or is visibly carried forward. FAIL naming anything raised and then dropped (for example, hiding from someone who then simply disappears from the story).",
    "alternation": "Narration and dialogue alternate in a lively rhythm. FAIL if there are long unbroken narration stretches (more than about 4 paragraphs or 350 words) that aren't deliberately solitary.",
    "grounding": "The chapter is grounded in its stated era and place: concrete period objects, the household's exact means, the landscape and plants, the weather and season, and the senses. FAIL if it's generic or anachronistic (quote the anachronism).",
    "quip_clusters": "Wisecracks and made-for-TV zingers are used in moderation. FAIL if three or more quip lines cluster in one exchange, or if banter replaces real feeling or information.",
    "line_purpose": "Every line reveals personality, values or motive, or moves the plot. FAIL quoting lines (dialogue or narration) that do neither and exist only to sound clever.",
    "dangling_events": "No event or threat is left hanging unless it's clearly a deliberate hook. FAIL naming anything raised (a threat, a mystery, an injury, a promise) that the chapter drops without consequence or carry-forward.",
    "backstory_by_deed": "Backstory and character come through concrete deeds and anecdotes (a specific thing someone did), not trait summaries. FAIL quoting summary passages that tell traits or history without a specific incident.",
    "threat_consequence": "Threats carry real consequences and a resolution (something is lost, hurt, changed, or escaped). FAIL if a confrontation fizzles without cost or outcome.",
    "power_leaks": "If the protagonist's latent power appears, it leaks through physical things (electronics, still water, animals) and nobody explains it. FAIL if a character confidently explains or names the power as magic too early, or if a leak happens with no clear physical trace. Plainly describing what physically happened is required, not a failure.",
    "variety": "The chapter doesn't repeat the previous chapter's central beat or setting focus. FAIL if it leans on the same set piece (same place, same activity, same image) as the previous chapter.",
}


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


def _disabled():
    return set((load_config().get("rules_check") or {}).get("disabled") or [])


def check(ctx, chapter: dict, text: str, raw_dir=None) -> list[dict]:
    rules = {k: v for k, v in RULES.items() if k not in _disabled()}
    outline = ctx.load_outline()
    ids = [c["id"] for c in outline]
    prev = outline[ids.index(chapter["id"]) - 1] if chapter["id"] in ids and ids.index(chapter["id"]) > 0 else None
    prev_focus = json.dumps({"title": prev.get("title"), "summary": prev.get("summary", "")[:400],
                             "scenes": [{"location": s.get("location"), "action": (s.get("action") or "")[:160]}
                                        for s in prev.get("scenes", [])]}, ensure_ascii=False) if prev else "(none)"
    res = llm_call(_pack_prefix(ctx, chapter) + f"""You are a strict line and structure editor. Check this chapter against each rule. Each "pass" needs a short verbatim quote showing compliance where that makes sense; each fail needs a verbatim quote of the offending passage (5-30 words) and a concrete fix.

RULES: {json.dumps(rules, ensure_ascii=False)}
GROUNDING NOTES FOR THIS CHAPTER: {chapter.get('grounding', '') or '(none given)'}
PREVIOUS CHAPTER FOCUS: {prev_focus}

TEXT:
{text[:60000]}

Output ONLY JSON: {{"results": [{{"rule": "id", "pass": true, "quote": "...", "note": "...", "fix": ""}}]}}""",
                   system=_pack_system(ctx, chapter), role="critique", json_mode=True, max_tokens=4000, temperature=0.1, raw_dir=raw_dir)
    out = [r for r in (res.get("results") or []) if isinstance(r, dict) and r.get("rule") in rules]
    return out


def failed(results):
    return [r for r in results if not r.get("pass")]


def enforce_first_line(chapter: dict, text: str) -> str:
    """Retired: openings are guided by `opening_intent`, never forced verbatim.
    Kept as a no-op so existing callers stay valid; a legacy first_line is read as intent only."""
    return text
