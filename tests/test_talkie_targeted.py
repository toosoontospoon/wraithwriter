"""Targeted pass: only paragraphs that still drop a fact, 25% budget, revert when worse."""
import json
import re

import pytest

from bookforge.core import jev, llm, talkie, talkie_roll as R

CLEAN = "Nia locked the gate and pocketed the iron key."
WORSE_SRC = "She set the lantern on the stone step beside the green door and then waited there quietly."
FAIL_SRC = "Otto hid the candle under the bench before the rain came in through the open window."
KEEP_SRC = "Bram left the lantern by the gate before the night watch came around the quiet yard."
SWAP = {
    "the": "that", "and": "then", "lantern": "lamp", "candle": "taper",
    "locked": "shut", "gate": "wicket", "pocketed": "kept", "iron": "metal", "key": "token",
}


def reword(text):
    def sub(match):
        word = match.group(0)
        repl = SWAP.get(word.lower())
        if not repl:
            return word
        if word[:1].isupper():
            return repl[:1].upper() + repl[1:]
        return repl
    return re.sub(r"[A-Za-z']+", sub, text)


def cfg(**over):
    base = dict(n_rerolls=1, history_window=2, edit_budget=0.15, sol_tries=1, copy_max=0.35,
                splice_copy_max=0.5, splice_seeds=1, dedupe_jaccard=0.4, jev_threshold=0.5,
                cap_usd=1.0, targeted={"enabled": True, "edit_budget": 0.25, "rounds": 1})
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clear():
    talkie.set_fake(None)
    jev.set_fake(None)
    llm.set_fake(None)
    yield
    talkie.set_fake(None)
    jev.set_fake(None)
    llm.set_fake(None)


def _jev():
    def fn(state, questions, model):
        answers = {}
        if "rewrites" in state:
            text = "\n".join(state["rewrites"])
            dropped = 0.2
            invented = 0.1
            if "WORSE" in text:
                dropped = 0.8
            elif "BETTER" in text:
                dropped = 0.05
            if "INVENTED" in text:
                invented = 0.9
            for qid in questions:
                cat = qid.split("__")[0]
                score = dropped if cat == "dropped" else invented if cat == "invented" else 0.1
                answers[qid] = {"noul": score}
        else:
            src, rewrite = state.get("source") or "", state.get("rewrite") or ""
            for qid in questions:
                if qid.startswith("miss__"):
                    idx = int(qid.split("__")[1])
                    line = src.splitlines()[idx] if idx < len(src.splitlines()) else ""
                    missing = ("lantern" in line or "candle" in line) and not (
                        ("lantern" in line and "lantern" in rewrite) or ("candle" in line and "candle" in rewrite))
                    answers[qid] = {"noul": 0.9 if missing else 0.1}
                else:
                    answers[qid] = {"noul": 0.1}
        return {"answers": answers, "usage": {"input_tokens": 1}, "model": model}
    return fn


def test_targeted_only_on_drops_budget_and_revert(tmp_path):
    prompts = []

    def talk(prompt, n_predict, seed, stop, ban_nl=False, **kw):
        if prompt.rstrip().endswith("Master:"):
            return "Zarquon moved the object."
        src = prompt.split("Pupil's version:\n\n")[-1].split("\n\nMaster's version:")[0]
        return reword(src)

    def sol(**kw):
        prompts.append(kw["prompt"])
        line = re.search(r"\[1\] (.+)", kw["prompt"]).group(1).split("\n")[0].strip()
        body = line[:-1] if line.endswith(".") else line
        if "candle" in kw["prompt"]:
            text = body + " BETTER INVENTED."
        elif "night watch" in kw["prompt"]:
            text = body + " BETTER."
        else:
            text = body + " WORSE."
        return {"edits": [{"n": 1, "text": text}], "delete": []}

    talkie.set_fake(talk)
    jev.set_fake(_jev())
    llm.set_fake(sol)
    paras = [CLEAN, WORSE_SRC, FAIL_SRC, KEEP_SRC]
    finals, summary = R.run_chapter(paras, cfg(), "Nia, Bram, Otto", "", tmp_path)
    assert len(prompts) == 3
    assert all("pocketed" not in p for p in prompts)
    assert all("at most 4" in p for p in prompts)
    assert all("at most 3" not in p for p in prompts)
    logged = [json.loads(line) for line in (tmp_path / "talkie.jsonl").read_text().splitlines()
              if '"phase": "targeted"' in line]
    assert [row["i"] for row in logged] == [1, 2, 3]
    assert [row["budget"] for row in logged] == [4, 4, 4]
    for src, row in zip(paras[1:], logged):
        assert row["budget"] == max(3, int(0.25 * len(src.split())))
        assert row["budget"] > max(3, int(0.15 * len(src.split())))
    assert logged[0]["kept"] is False and logged[0]["jev_after"]["dropped"] > logged[0]["jev_before"]["dropped"]
    assert logged[1]["kept"] is False and logged[1]["jev_after"]["invented"] > 0.5
    assert logged[2]["kept"] is True and logged[2]["jev_after"]["dropped"] < logged[2]["jev_before"]["dropped"]
    assert "WORSE" not in finals[1]
    assert "INVENTED" not in finals[2]
    assert "BETTER" in finals[3]
    assert summary["targeted_pass_counts"] == {"attempted": 3, "kept": 1, "reverted": 2}


def test_targeted_disabled_does_not_call_sol(tmp_path):
    talkie.set_fake(lambda prompt, n_predict, seed, stop, ban_nl=False, **kw: reword(
        prompt.split("Pupil's version:\n\n")[-1].split("\n\nMaster's version:")[0]))
    jev.set_fake(_jev())
    calls = []
    llm.set_fake(lambda **kw: calls.append(kw["prompt"]) or {"edits": [], "delete": []})
    R.run_chapter([WORSE_SRC], cfg(targeted={"enabled": False}), "Nia", "", tmp_path)
    assert calls == []
    assert '"phase": "targeted"' not in (tmp_path / "talkie.jsonl").read_text()
