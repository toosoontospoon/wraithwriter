"""Fakes for latin-pivot tests. No network."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("block_network")


def ping_up(*_a, **_k):
    return True, "ok"


def jev_noul(p=0.95, *, low_unless_fidelity=True):
    def fake(state, questions, model):
        answers = {}
        for key, question in questions.items():
            if question.get("type") == "choice":
                options = list((question.get("criteria") or {}))
                answers[key] = {"type": "choice", "choice": options[0] if options else None,
                                "probabilities": {}, "confidence": 0.0}
                continue
            instructions = question.get("instructions") or ""
            score = p
            if low_unless_fidelity and "every event" not in instructions:
                score = 0.1
            answers[key] = {"type": "noul", "noul": score}
        return {"model": model, "answers": answers, "usage": {"input_tokens": 12}}

    return fake


def install_ping(monkeypatch):
    monkeypatch.setattr(
        "bookforge.core.latin_pivot.talkie_lifecycle.ping_endpoint", ping_up)


LATIN_BOY = "Puer in agro ambulabat et canem parvum prope viam longam vidit hodie."
ENGLISH_BOY = "A boy walked in the field and saw a small dog near the long road today."
SEED = '<<SEED>>"Not a word," he told the boy.<</SEED>>'
SEED_INNER = '"Not a word," he told the boy.'
