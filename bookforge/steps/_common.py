"""Helpers shared by chapter steps."""
from __future__ import annotations

import json
import re

from bookforge.core.llm import llm_call

SCENE_BREAK = "\n\n* * *\n\n"


def clean_prose(text: str) -> str:
    """Remove wrapper tags/markdown the model may add. Keeps scene/time-skip markers."""
    text = re.sub(r"</?(draft|revised|prose|text|scene)>", "", text, flags=re.I)
    text = text.replace("```", "")
    text = re.sub(r"^\s*#+\s.*$", "", text, flags=re.M)          # markdown headers
    text = re.sub(r"^\s*-{3,}\s*$", "==========", text, flags=re.M)  # hr -> time-skip marker
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def summarize(text: str, what: str, words: int = 120) -> str:
    prompt = (f"You are a continuity editor. Summarize this {what} in about {words} words for the writing team. "
              "Cover key events in order (names, places, actions), what each character learned or changed, "
              "new information, and where everyone is at the end. Plain factual prose.\n\nTEXT:\n" + text)
    return llm_call(prompt, role="summary", max_tokens=words * 3, temperature=0.2).strip()


def join_scenes(texts: list[str]) -> str:
    return SCENE_BREAK.join(t.strip() for t in texts if t.strip())


def split_scenes(text: str) -> list[str]:
    return [t.strip() for t in text.split("* * *") if t.strip()]


def dump(path, data):
    path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False))
