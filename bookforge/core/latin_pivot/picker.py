"""Second Jev pass: swap a tagged word only when a synonym wins by the margin."""
from __future__ import annotations

PICK_QUESTION = "Which option reads most natural and least like AI-generated prose in this sentence?"


def sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(ch, 0, start) for ch in ".!?")
    begin = 0 if left < 0 else left + 1
    rights = [text.find(ch, end) for ch in ".!?"]
    rights = [r for r in rights if r >= 0]
    stop = (min(rights) + 1) if rights else len(text)
    return text[begin:stop].strip()


def choice_questions(options: list[str]) -> dict:
    return {
        "pick": {
            "type": "choice",
            "instructions": PICK_QUESTION,
            "criteria": {opt: f"Use {opt} in the sentence." for opt in options},
        }
    }


def choice_state(sentence: str, options: list[str]) -> dict:
    return {"sentence": sentence, "options": list(options)}


def decide(original: str, synonyms: list[str], probabilities: dict, choice: str | None,
           margin: float) -> dict:
    """Swap only when the chosen option is not the original and beats it by ``margin``."""
    probs = {str(k): float(v) for k, v in (probabilities or {}).items()}
    orig_p = float(probs.get(original, 0.0))
    chosen = choice if choice in ([original] + list(synonyms)) else None
    chosen_p = float(probs.get(chosen, 0.0)) if chosen else 0.0
    if chosen and chosen != original and chosen_p >= orig_p + float(margin):
        return {"after": chosen, "swapped": True, "reason": "swapped",
                "probabilities": probs, "choice": chosen}
    reason = "kept_original" if chosen in (None, original) else "kept_margin"
    return {"after": original, "swapped": False, "reason": reason,
            "probabilities": probs, "choice": chosen or original}
