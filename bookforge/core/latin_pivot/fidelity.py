"""Per-paragraph fidelity: a cheap heuristic, then one Jev yes/no when it passes."""
from __future__ import annotations

import re
from typing import Callable

from bookforge.core import jev
from bookforge.core.latin_pivot.seeds import visible

FIDELITY_QUESTION = (
    "Does the English keep every event and statement of the Latin, in the same order, "
    "with nothing added or dropped?"
)

_QUOTE = re.compile(r'"[^"]*"|“[^”]*”|«[^»]*»')
_SENT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_DIGIT = re.compile(r"\d+")


def _sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT.split((text or "").strip()) if p.strip()]
    return parts or ([] if not (text or "").strip() else [text.strip()])


def proper_names(latin: str) -> list[str]:
    """Capitalised Latin tokens that are not the first word of a sentence."""
    names: list[str] = []
    for sent in _sentences(visible(latin)):
        words = _WORD.findall(sent)
        for i, word in enumerate(words):
            if i == 0:
                continue
            if word[:1].isupper() and any(ch.islower() for ch in word[1:]):
                names.append(word)
            elif word[:1].isupper() and word[1:].isupper() and len(word) > 1:
                names.append(word)
    return names


def quote_count(text: str) -> int:
    return len(_QUOTE.findall(visible(text)))


def heuristic(latin: str, english: str, *, ratio_min: float = 0.9, ratio_max: float = 2.2) -> dict:
    """Code checks. ``ok`` is false when any check fails. No model call."""
    lat = visible(latin)
    eng = visible(english)
    lw = max(1, len(lat.split()))
    ew = len(eng.split())
    ratio = ew / lw
    lat_s = len(_sentences(lat))
    eng_s = len(_sentences(eng))
    allowed = max(1, 0.3 * lat_s)
    names = proper_names(latin)
    missing = [n for n in names if not re.search(r"\b" + re.escape(n) + r"\b", eng)]
    lat_digits = _DIGIT.findall(lat)
    eng_digits = _DIGIT.findall(eng)
    checks = {
        "ratio": ratio,
        "ratio_ok": ratio_min <= ratio <= ratio_max,
        "quotes_latin": quote_count(latin),
        "quotes_english": quote_count(english),
        "quotes_ok": quote_count(latin) == quote_count(english),
        "missing_names": missing,
        "names_ok": not missing,
        "digits_ok": lat_digits == eng_digits,
        "sentences_latin": lat_s,
        "sentences_english": eng_s,
        "sentences_ok": abs(eng_s - lat_s) <= allowed + 1e-9,
    }
    checks["ok"] = all(checks[k] for k in (
        "ratio_ok", "quotes_ok", "names_ok", "digits_ok", "sentences_ok"))
    return checks


def fidelity_questions() -> dict:
    return {
        "faithful": {
            "type": "noul",
            "instructions": FIDELITY_QUESTION,
            "criteria": {
                "true": "The English keeps every event and statement of the Latin, in the same order, with nothing added or dropped.",
                "false": "The English adds, drops, or reorders an event or statement from the Latin.",
            },
        }
    }


def fidelity_state(latin: str, english: str) -> dict:
    return {"latin": latin, "english": english}


def _score(attempt: dict) -> tuple:
    jev_p = attempt.get("jev_p")
    return (1 if attempt.get("heuristic_ok") else 0, -1.0 if jev_p is None else float(jev_p))


def evaluate(latin: str, english: str, *, ratio_min: float, ratio_max: float, jev_on: bool,
             threshold: float, retries: int, ask: Callable, retranslate: Callable | None,
             model: str, log_dir, cap_usd: float, tag: str = "latin_pivot:fidelity") -> dict:
    """Score ``english``. Retry translation when a check fails, up to ``retries``.

    ``ask`` is ``jev.ask``. A ``JevUnavailable`` (or a disabled Jev) keeps the run
    heuristic-only and records that. The best attempt is returned. ``flagged`` is
    true when the kept attempt still fails its bar.
    """
    current = english
    attempts: list[dict] = []
    mode = "off"
    for n in range(int(retries) + 1):
        heur = heuristic(latin, current, ratio_min=ratio_min, ratio_max=ratio_max)
        jev_p = None
        passed = bool(heur["ok"])
        if heur["ok"] and jev_on:
            try:
                res = ask(fidelity_state(latin, current), fidelity_questions(), model=model,
                          tag=tag, log_dir=log_dir, cap_usd=cap_usd)
                raw = jev.noul(res, "faithful")
                jev_p = None if raw is None else float(raw)
                mode = "noul"
                passed = jev_p is not None and jev_p >= threshold
            except jev.JevUnavailable:
                mode = "unavailable"
                passed = bool(heur["ok"])
        elif not jev_on:
            mode = "off"
        attempts.append({
            "n": n,
            "text": current,
            "heuristic": heur,
            "heuristic_ok": bool(heur["ok"]),
            "jev_p": jev_p,
            "passed": passed,
            "jev_mode": mode,
        })
        if passed or n >= int(retries) or retranslate is None:
            break
        if mode == "unavailable" and heur["ok"]:
            break
        current = retranslate(n + 1)
    best = max(attempts, key=_score)
    # Prefer a passing attempt over a higher score that still failed.
    passing = [a for a in attempts if a["passed"]]
    chosen = passing[-1] if passing else best
    return {
        "text": chosen["text"],
        "heuristic": chosen["heuristic"],
        "heuristic_ok": chosen["heuristic_ok"],
        "jev_p": chosen["jev_p"],
        "jev_mode": chosen["jev_mode"],
        "attempts": len(attempts),
        "flagged": not chosen["passed"],
        "passed": chosen["passed"],
    }
