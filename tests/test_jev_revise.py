"""Opt-in Jev reviser. Fakes only: no network, no keys."""
import json

import pytest

from bookforge import config
from bookforge.core import jev, jev_revise, llm

AFRAID = "She was afraid of the dark hall."


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    jev.set_fake(None)
    yield
    jev.set_fake(None)
    llm.set_fake(None)


def _enable():
    config.set_config({"llm": {"json_retries": 1},
                       "deai": {"jev_revise": {"enabled": True, "max_rounds": 1},
                                "edit_guard": "section", "max_tell_drop": 0.5}})


def _arm(new_text, *, fid=0.95, cut=0.0, emotion="afraid"):
    """``emotion`` is 'afraid' (score only while that word remains), 'always', or 'never'."""
    roles = []

    def jfake(state, qs, model):
        doc = state.get("document") or ""
        ans = {}
        for k in qs:
            if "edits" in state:
                p = fid
            elif "document" not in state and str(k).startswith("c"):
                p = cut
            elif str(k).startswith("emotion_explained__"):
                if emotion == "always":
                    p = 0.9
                elif emotion == "never":
                    p = 0.0
                else:
                    p = 0.9 if "afraid" in doc else 0.0
            else:
                p = 0.0
            ans[k] = {"type": "noul", "noul": p}
        return {"model": model, "answers": ans, "usage": {"input_tokens": 4}}

    def lfake(role, system, prompt, json_mode):
        roles.append(role)
        return {"replacements": [{"id": "s01", "text": new_text}]}

    jev.set_fake(jfake)
    llm.set_fake(lfake)
    return roles


def test_disabled_by_default_makes_no_llm_call():
    roles = _arm("Gone.")
    out, rep = jev_revise.revise(AFRAID)
    assert out == AFRAID
    assert rep["enabled"] is False and rep["jev"] == "skipped: disabled"
    assert roles == []


def test_fidelity_rejection_leaves_the_line():
    _enable()
    _arm("She wanted a ship she had never seen.", fid=0.2)
    out, rep = jev_revise.revise(AFRAID)
    assert out == AFRAID
    assert any("fidelity" in r["reason"] for r in rep["rejected"])
    assert rep["kept"] == []


def test_cut_gate_rejection_leaves_the_line():
    _enable()
    _arm("", cut=0.95)
    out, rep = jev_revise.revise(AFRAID)
    assert out == AFRAID
    assert any(r["reason"].startswith("cut gate") for r in rep["rejected"])


def test_word_cut_cap_rejects_a_large_deletion():
    _enable()
    _arm("", cut=0.0)
    out, rep = jev_revise.revise(AFRAID)
    assert out == AFRAID
    assert any(r["reason"] == "word-cut cap" for r in rep["rejected"])
    assert rep["words_cut"] == 0


def test_undo_if_the_category_does_not_improve():
    _enable()
    _arm("She was afraid, plainly, of the dark hall.", emotion="always")
    out, rep = jev_revise.revise(AFRAID)
    assert out == AFRAID
    assert any("did not fall" in r["reason"] for r in rep["rejected"])
    assert rep["kept"] == []


def test_edit_guard_reverts_when_the_detector_score_falls():
    _enable()
    _arm("The harbor was a tapestry of ropes and smoke.")
    out, rep = jev_revise.revise(AFRAID)
    assert out == AFRAID
    assert any("edit guard" in r["reason"] for r in rep["rejected"])
    assert rep["words_cut"] == 0


def test_jev_unavailable_changes_nothing(monkeypatch):
    _enable()
    roles = []
    llm.set_fake(lambda role, system, prompt, json_mode: roles.append(role) or {"replacements": []})
    jev.set_fake(None)
    out, rep = jev_revise.revise(AFRAID)
    assert out == AFRAID and roles == []
    assert rep["jev"].startswith("skipped:")


def test_dialogue_in_a_line_must_survive():
    _enable()
    text = 'She said "Stop." Then she left the yard.'
    _arm("She shouted.", emotion="always")
    out, rep = jev_revise.revise(text)
    assert out == text
    assert any(r["reason"] == "dialogue not preserved" for r in rep["rejected"])


def test_deai_chapter_records_a_disabled_report_and_skips_the_writer(project):
    from bookforge.steps import deai_chapter
    project.save_outline([{"id": "ch01", "title": "T", "scenes": [{"scene_number": 1}]}])
    project.save_characters([{"name": "Nia Cole"}])
    project.save_world({})
    project.write_text(project.chapter_path("ch01", "revised"), AFRAID)
    deai_chapter.run(project, "ch01")
    gate = json.loads(sorted(project.steps_dir.glob("*deai_ch01/final_gate.json"))[-1].read_text())
    assert gate["jev_revise"]["enabled"] is False
    assert project.chapter_path("ch01", "final").read_text().strip() == AFRAID

    config.set_config({"llm": {"json_retries": 1}, "deai": {"jev_revise": {"enabled": True}}})
    project.write_text(project.chapter_path("ch01", "revised"), AFRAID)
    deai_chapter.run(project, "ch01")
    gate = json.loads(sorted(project.steps_dir.glob("*deai_ch01/final_gate.json"))[-1].read_text())
    assert str(gate["jev_revise"]["jev"]).startswith("skipped:")
    assert project.chapter_path("ch01", "final").read_text().strip() == AFRAID
