"""Story-first length (report-only).

Length is never a driver. The planner sizes each beat by what it has to do; scene and chapter
sizes are the sums of their beats and are used only for reporting and as a generous safety
ceiling on generation. Nothing in the pipeline asks for more words to hit a number.

Beat format: "(~180w, reveal) Meemaw tells ..." -> 180. Untagged beats fall back to a size
by type keyword, else DEFAULT_BEAT.
"""
from __future__ import annotations

import re

# typical size (words) by beat type; guidance for planners, fallback for untagged beats
BEAT_TYPES = {
    "peak": 550, "action": 450, "reveal": 350, "legend": 350, "dialogue": 280,
    "character": 220, "setting": 180, "transition": 90,
}
DEFAULT_BEAT = 220
CEILING_FACTOR = 1.5
CEILING_PAD = 200
NO_FILLER_RULE = ("NO FILLER: never invent business, chores, small talk, scenery or recap to reach a length. "
                  "Length follows the story: a chapter with little to tell is short, and that is fine. "
                  "If a beat or chapter is too thin to stand on its own, say so (merge it) instead of padding it.")
BEAT_TYPE_GUIDE = ("Tag each beat with a size estimate and type, like \"(~350w, reveal) ...\". Typical sizes: "
                   + ", ".join(f"{k} ~{v}w" for k, v in BEAT_TYPES.items())
                   + ". Size each beat by what it must do, not to fill a quota.")

_TAG = re.compile(r"^\s*\(\s*~?\s*(\d{2,5})\s*(?:w|words?)\b[^)]*\)", re.I)


def beat_words(beat: str) -> int:
    m = _TAG.match(beat or "")
    if m:
        return int(m.group(1))
    low = (beat or "").lower()[:60]
    for k, v in BEAT_TYPES.items():
        if k in low:
            return v
    return DEFAULT_BEAT


def scene_estimate(scene: dict) -> int:
    beats = scene.get("beats") or []
    if beats:
        return sum(beat_words(b) for b in beats)
    return int(scene.get("target_words") or 0) or DEFAULT_BEAT * 3


def chapter_estimate(chapter: dict) -> int:
    return sum(scene_estimate(s) for s in chapter.get("scenes") or [])


def ceiling(estimate: int) -> int:
    """Generous upper bound: a limit, never a goal."""
    return int(max(estimate, DEFAULT_BEAT) * CEILING_FACTOR) + CEILING_PAD


CRISIS_RULE = ("TEMPO AND CRISES: a crisis runs to its resolution inside the chapter; never end a chapter mid-crisis "
               "unless it is a deliberate cliffhanger the beat sheet asks for. Urgency lives in the characters, not in rushed prose: "
               "plan fights and survival moments as several vivid, moment-by-moment visual beats (give them room), with "
               "panicked characters who never pause, set things down or make speeches (fragments at most); peaks are told "
               "through what the POV character sees, with minimal dialogue.")
