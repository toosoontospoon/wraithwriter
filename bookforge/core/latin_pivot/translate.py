"""Talkie translation prompt and sampling for one Latin paragraph."""
from __future__ import annotations

import json
import re
from pathlib import Path

STOPS = ["\n\n", "\nLatin:", "Latin:"]
REPEAT_PENALTY = 1.05
TEMPERATURE = 0.0
RETRY_TEMPERATURE = 0.3
TOP_K = 1

LATIN_DRAFT_INSTRUCTION = (
    "Write the prose in classical-style Latin. Plain narrative. "
    "One paragraph per blank-line block. Keep proper names unchanged.\n"
    "Human seed lines stay in English. Wrap each human seed line as "
    "<<SEED>>...<</SEED>>. Carry those spans through untouched. "
    "Do not translate anything inside <<SEED>>...<</SEED>>."
)


def package_fewshot_path() -> Path:
    return Path(__file__).resolve().parent / "fewshot.json"


def load_fewshot(path: str | Path | None = None) -> dict:
    src = Path(path) if path else package_fewshot_path()
    data = json.loads(src.read_text(encoding="utf-8"))
    if "header" not in data or "pairs" not in data:
        raise ValueError(f"fewshot file {src} needs header and pairs")
    return data


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]


def latin_word_count(text: str) -> int:
    return len((text or "").split())


def n_predict_for(latin: str) -> int:
    """clamp(2.2 × Latin words + 40, 80, 600), truncated toward zero."""
    raw = 2.2 * latin_word_count(latin) + 40
    return int(max(80, min(600, raw)))


def build_prompt(target: str, fewshot: dict | None = None, fewshot_path: str | Path | None = None) -> str:
    """Header, the example pairs, the target paragraph, and a trailing ``English:\\n``.

    A blank line separates the header from the first ``Latin:`` and each pair from the next.
    """
    data = fewshot if fewshot is not None else load_fewshot(fewshot_path)
    chunks = [str(data["header"]).rstrip("\n"), ""]
    for pair in data["pairs"]:
        chunks.append(f"Latin:\n{pair['latin']}")
        chunks.append("")
        chunks.append(f"English:\n{pair['english']}")
        chunks.append("")
    chunks.append(f"Latin:\n{str(target).strip()}")
    chunks.append("")
    chunks.append("English:\n")
    return "\n".join(chunks)


def draft_instruction(seed_lines: list[str] | None = None) -> str:
    """Latin-draft addendum. Seed lines are shown already wrapped, in English."""
    lines = [LATIN_DRAFT_INSTRUCTION]
    for line in seed_lines or []:
        if line:
            lines.append("HUMAN SEED (English, wrap exactly and do not translate):\n"
                         f"<<SEED>>{line}<</SEED>>")
    return "\n\n".join(lines)
