"""Rolling loop with fakes: order, budget trim, deletions, quotes, dedupe, skip-Sol, resume."""
import json
import re

import pytest

from bookforge.core import jev, llm, talkie, talkie_roll as R

P0 = "Nia locked the gate and pocketed the iron key."
P1 = "The yard was dark and the dog barked once."
OPENING = "The ship had already left the quay."
SWAP = {
    "locked": "shut", "gate": "wicket", "and": "then", "pocketed": "kept",
    "the": "that", "iron": "metal", "key": "token", "yard": "court",
    "was": "stood", "dark": "dim", "dog": "hound", "barked": "called",
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
    base = dict(n_rerolls=2, history_window=2, edit_budget=0.15, sol_tries=2, copy_max=0.35,
                splice_copy_max=0.5, splice_seeds=1, dedupe_jaccard=0.4, jev_threshold=0.5,
                cap_usd=1.0, fewshot_path=None, targeted={"enabled": False})
    base.update(over)
    return base


def jev_scores(paragraph=0.1, err0=0.1, miss=None):
    def fn(state, questions, model):
        fn.calls.append(list(questions))
        answers = {}
        if "rewrites" in state:
            for qid in questions:
                cat = qid.split("__")[0]
                answers[qid] = {"noul": paragraph if cat == "dropped" else 0.1}
        else:
            for qid in questions:
                if qid.startswith("err__0"):
                    answers[qid] = {"noul": err0}
                elif qid.startswith("miss__") and miss is not None:
                    idx = int(qid.split("__")[1])
                    answers[qid] = {"noul": miss(idx)}
                else:
                    answers[qid] = {"noul": 0.1}
        return {"answers": answers, "usage": {"input_tokens": 1}, "model": model}
    fn.calls = []
    return fn


@pytest.fixture(autouse=True)
def _clear():
    talkie.set_fake(None)
    jev.set_fake(None)
    llm.set_fake(None)
    yield
    talkie.set_fake(None)
    jev.set_fake(None)
    llm.set_fake(None)


def test_loop_order_fixed_history_and_no_sol_when_talkie_passes(tmp_path):
    prompts = []

    def talk(prompt, n_predict, seed, stop, ban_nl=False, **kw):
        prompts.append(prompt)
        if "The yard was dark" in prompt and prompt.strip().endswith("Master's version:"):
            rows = [json.loads(line) for line in (tmp_path / "talkie.jsonl").read_text().splitlines()]
            assert rows[0]["i"] == 0 and rows[0]["final"]
        src = prompt.split("Pupil's version:\n\n")[-1].split("\n\nMaster's version:")[0]
        return reword(src)

    sol_calls = []
    fake = jev_scores(0.1)
    talkie.set_fake(talk)
    jev.set_fake(fake)
    llm.set_fake(lambda **kw: sol_calls.append(kw["prompt"]) or {"edits": [], "delete": []})
    finals, summary = R.run_chapter([P0, P1], cfg(), "Nia, Bram", OPENING, tmp_path)
    assert sol_calls == []
    assert finals[0] == reword(P0)
    assert finals[1] == reword(P1)
    assert OPENING in prompts[0]
    assert "The master's version of the passage before" not in prompts[0]
    para1 = prompts[2]
    pupil_at = para1.rfind("The pupil's version of the passage before:\n\n")
    master_at = para1.rfind("The master's version of the passage before:\n\n")
    current_at = para1.rfind("Pupil's version:\n\n")
    pupil = para1[pupil_at:master_at]
    master = para1[master_at:current_at]
    assert P0 in pupil
    assert reword(P0) in master
    assert P0 not in master
    assert any("dropped__0" in qs and "dropped__1" in qs for qs in fake.calls)
    rows = R.load_rows(tmp_path / "talkie.jsonl")
    assert [r["i"] for r in rows] == [0, 1]
    assert rows[1]["chosen_seed"] == 1100
    assert rows[0]["sol_words"] == 0 and rows[0]["budget"] == 0
    assert summary["paragraphs"] == 2
    assert summary["passes"] == 2


def test_candidate_jev_is_one_batch(tmp_path):
    talkie.set_fake(lambda prompt, n_predict, seed, stop, ban_nl=False, **kw: reword(
        prompt.split("Pupil's version:\n\n")[-1].split("\n\nMaster's version:")[0]))
    fake = jev_scores(0.1)
    jev.set_fake(fake)
    llm.set_fake(lambda **kw: (_ for _ in ()).throw(AssertionError("sol")))
    R.run_chapter([P0], cfg(n_rerolls=2), "Nia", OPENING, tmp_path)
    batched = [qs for qs in fake.calls if "dropped__0" in qs and "dropped__1" in qs]
    assert len(batched) == 1


def test_budget_trim_and_retry(tmp_path):
    src = "Ada held the brass key. The door stayed shut."
    prompts = []

    def talk(prompt, n_predict, seed, stop, ban_nl=False, **kw):
        return src

    def sol(**kw):
        prompts.append(kw["prompt"])
        if "Those edits write" in kw["prompt"]:
            return {"edits": [{"n": 1, "text": "Ada held the old key."}], "delete": []}
        return {"edits": [
            {"n": 1, "text": "Ada held a wholly different brass object today indeed."},
            {"n": 2, "text": "Something else happened beside the heavy door at once now."},
        ], "delete": []}

    talkie.set_fake(talk)
    jev.set_fake(jev_scores(paragraph=0.9, err0=0.9))
    llm.set_fake(sol)
    finals, _summary = R.run_chapter([src], cfg(n_rerolls=1, sol_tries=2), "Ada", "", tmp_path)
    assert len(prompts) == 2
    assert "the budget is" in prompts[1]
    row = R.load_rows(tmp_path / "talkie.jsonl")[0]
    assert row["sol_words"] <= row["budget"]
    assert row["budget"] == max(3, int(0.15 * len(src.split())))
    assert "old key" in finals[0]
    assert "wholly" not in finals[0]


def test_apply_trims_over_budget_edit():
    sentences = ["Ada held the brass key.", "The door stayed shut."]
    edits = [
        {"n": 1, "text": "Ada held the old key."},
        {"n": 2, "text": "The heavy iron door stayed shut tight now please."},
    ]
    text, used, took, skipped, deleted = R.apply_edits(sentences, edits, [], set(), budget=3, threshold=0.4)
    assert took == [1]
    assert skipped == [2]
    assert used <= 3
    assert "old key" in text and "heavy" not in text and deleted == []


def test_deletion_only_flagged_or_duplicate():
    sentences = ["Ada held the brass key.", "The door stayed shut.", "Rain hit the slate roof."]
    text, _used, _took, _skipped, deleted = R.apply_edits(
        sentences, [], [1, 2], flagged={1}, budget=5, threshold=0.4)
    assert deleted == [2]
    assert "Ada held" in text and "Rain hit" in text and "door" not in text
    untouched, *_rest, deleted_unique = R.apply_edits(
        sentences, [], [2], flagged=set(), budget=5, threshold=0.4)
    assert deleted_unique == []
    assert "door" in untouched
    dup = ["Ada held the brass key.", "Rain hit the slate roof.", "Ada held the brass key tightly."]
    text, *_r, deleted_dup = R.apply_edits(dup, [], [3], flagged=set(), budget=5, threshold=0.4)
    assert deleted_dup == [3]
    assert "tightly" not in text and "Rain hit" in text


def test_edit_keeps_edge_quotes():
    sentences = ['"I forgot it."', "Her aunt nodded."]
    text, *_ = R.apply_edits(
        sentences, [{"n": 1, "text": "I forgot the whole thing."}], [], set(), budget=20, threshold=0.4)
    assert text.startswith('"I forgot the whole thing."')
    assert "Her aunt nodded." in text


def test_dedupe_drops_splice_only():
    sentences = ["Ada held the brass key.", "Rain hit the slate roof.", "Ada held the brass key tightly."]
    kept, dropped = R.dedupe_splices(sentences, {2}, 0.4)
    assert dropped == [2]
    assert kept == sentences[:2]
    kept_all, dropped_none = R.dedupe_splices(sentences, set(), 0.4)
    assert dropped_none == [] and kept_all == sentences


def test_loop_dedupes_a_spliced_repeat(tmp_path):
    src = "Ada held the brass key. Ada held the brass key tightly."

    def talk(prompt, n_predict, seed, stop, ban_nl=False, **kw):
        if prompt.rstrip().endswith("Master:"):
            return "Ada held the brass key tightly."
        return "Ada held the brass key."

    def miss(idx):
        return 0.9 if idx == 1 else 0.1

    talkie.set_fake(talk)
    jev.set_fake(jev_scores(paragraph=0.9, err0=0.1, miss=miss))
    llm.set_fake(lambda **kw: {"edits": [], "delete": []})
    finals, _s = R.run_chapter(
        [src], cfg(n_rerolls=1, splice_seeds=1, splice_copy_max=1.0), "Ada", "", tmp_path)
    row = R.load_rows(tmp_path / "talkie.jsonl")[0]
    assert row["splices"]
    assert "tightly" not in finals[0]


def test_resume_reloads_fixed_history(tmp_path):
    calls = []

    def talk(prompt, n_predict, seed, stop, ban_nl=False, **kw):
        calls.append(prompt)
        src = prompt.split("Pupil's version:\n\n")[-1].split("\n\nMaster's version:")[0]
        return reword(src)

    talkie.set_fake(talk)
    jev.set_fake(jev_scores(0.1))
    llm.set_fake(lambda **kw: (_ for _ in ()).throw(AssertionError("sol")))
    first, _s = R.run_chapter([P0], cfg(), "Nia", OPENING, tmp_path)
    n_after_first = len(calls)
    second, _s = R.run_chapter([P0, P1], cfg(), "Nia", OPENING, tmp_path)
    assert second[0] == first[0]
    assert len(calls) == n_after_first + 2
    master_at = calls[-1].rfind("The master's version of the passage before:\n\n")
    current_at = calls[-1].rfind("Pupil's version:\n\n")
    assert first[0] in calls[-1][master_at:current_at]
    assert P0 not in calls[-1][master_at:current_at]
