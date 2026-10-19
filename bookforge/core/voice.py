"""Narrator re-voice stage (configurable).

Drafters tend to drift into flat close-third. This pass re-voices a whole chapter in the
narrator voice the project's style bible defines (its "Narrator voice" section and
example passages), keeping every event, fact, name and line of meaningful dialogue.
It runs inside the revise step after the keep/rollback decision. Output is accepted only
if the scene count matches, length stays within bounds, and the AI-tell score doesn't drop.

Config (bookforge config.yaml):
  voice:
    enabled: true            # off by default in the package; turn on per project
    role: revision           # LLM role (tier) to use
    min_ratio: 0.0           # no length floor: tightening is always allowed
    max_ratio: 1.0           # capped at the input length: never pad
    max_tell_drop: 0.3       # reject if the detector score drops by more than this
    instructions: ""         # extra project-specific voice notes appended to the prompt
"""
from __future__ import annotations

import json
import re

from bookforge.config import load_config
from bookforge.core import detector
from bookforge.core.llm import llm_call

SEP = "* * *"

DEFAULTS = {"enabled": False, "role": "revision", "min_ratio": 0.0, "max_ratio": 1.0,
            "max_tell_drop": 0.3, "instructions": ""}


def cfg() -> dict:
    return {**DEFAULTS, **(load_config().get("voice") or {})}


def _narrator_section(ctx) -> str:
    sb = ctx.style()
    if sb.empty:
        return ""
    return sb.block(3500, prefer=["narrator-opening"], for_drafter=True)


def _desired(chapter: dict, text: str) -> int:
    """Length cap: the input's own length. No chapter target is ever applied (story-first)."""
    return len(text.split())


def prompt_for(ctx, chapter: dict, scenes: list[str], extra: str = "") -> str:
    c = cfg()
    joined = f"\n\n{SEP}\n\n".join(s.strip() for s in scenes)
    briefs = json.dumps([{k: s.get(k) for k in ("scene_number", "closing_image", "tone_target")}
                         for s in chapter.get("scenes", [])], ensure_ascii=False)
    return f"""Re-voice this chapter ("{chapter.get('title', '')}") in the book's NARRATOR VOICE exactly as the style bible below defines it. This is a voice pass, not a plot revision.

KEEP: the chapter's first sentence exactly as written; every event in its order, every fact, name and number; all dialogue that carries character (light trims allowed); each scene's final image; the {len(scenes)} scenes separated by lines containing only {SEP}. Each event happens once.
CHANGE: narration that has gone flat close-third or neutral. Make the storyteller audible in the narration roughly every paragraph: the idiom, rhythm, asides, comic exaggeration, and fondness the style bible calls for, modeled on its example passages (stance and rhythm, never their sentences). Uncanny moments are described plainly and clearly so the reader sees exactly what happened (the narrator may say he didn't know the cause then). CLARITY for a reader around 16: keep or add plain introductions of places, people, objects and magic (say what each thing is the first time), keep chronology clear, and never cut an explanation the reader needs. Obey every LOCKED narrator rule in the style bible, including any identity that must stay hidden.
LENGTH: at most {_desired(chapter, joined)} words (the input's length). Shorter is welcome: cut repetition and idle business, never events, obligations or needed explanations. Never add filler. No em dashes. Use straight quotes. Scenes end on an image, action, or line of dialogue, never a moral.
{('PROJECT VOICE NOTES: ' + c['instructions']) if c.get('instructions') else ''}
{extra}

STYLE BIBLE:
{_narrator_section(ctx)}

SCENE BRIEFS: {briefs}

CHAPTER:
{joined}

Return ONLY the re-voiced prose."""


def revoice(ctx, chapter: dict, scenes: list[str], step_dir=None, extra: str = "") -> tuple[list[str], dict]:
    """Return (scenes, report). On any failed check the input scenes come back unchanged."""
    c = cfg()
    rep = {"enabled": bool(c["enabled"]), "accepted": False}
    if not c["enabled"] or not any(s.strip() for s in scenes):
        return scenes, rep
    src = f"\n\n{SEP}\n\n".join(scenes)
    allow = ctx.style().allowlist() if hasattr(ctx.style(), "allowlist") else []
    before = detector.detect(src, allow).get("score", 10)
    out = llm_call(prompt_for(ctx, chapter, scenes, extra), role=c["role"],
                   max_tokens=int(len(src.split()) * 2.4) + 1500, temperature=0.7, raw_dir=step_dir)
    out = detector.autofix(out)[0].strip()
    from bookforge.core.rules_check import enforce_first_line
    out = enforce_first_line(chapter, out)
    parts = [p.strip() for p in re.split(r"\n\s*\*\s*\*\s*\*\s*\n", "\n" + out + "\n") if p.strip()]
    ratio = len(out.split()) / max(1, _desired(chapter, src))
    after = detector.detect(out, allow).get("score", 0)
    rep.update({"ratio": round(ratio, 3), "score_before": before, "score_after": after,
                "scenes_in": len(scenes), "scenes_out": len(parts)})
    if len(parts) != len(scenes):
        rep["reason"] = "scene count changed"
    elif not (c["min_ratio"] <= ratio <= c["max_ratio"]):
        rep["reason"] = "length out of bounds"
    elif after < before - c["max_tell_drop"]:
        rep["reason"] = "tell score dropped"
    else:
        rep["accepted"] = True
        if step_dir is not None:
            (step_dir / "voice.txt").write_text(out)
        return parts, rep
    return scenes, rep
