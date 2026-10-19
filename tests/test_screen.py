"""Post-draft screen: code checks, Jev gate (faked), revision packet, splice, fallbacks, facts file."""
import json

import pytest

from bookforge import config
from bookforge.core import facts, jev, screen, screen_checks
from bookforge.steps import revise_chapter

FACTS = {
    "chapter": "ch01",
    "bible": {"pov": "Close third person, limited to Finn.",
              "timetable": ["6:45 Morning Class: Logic.", "4:30-6:00 Evening Class: Alchemy, Music, Theology.",
                            "1:30-3:30 Arms, by form.", "9:15 Vespers in the court."]},
    "known_characters": {"before_this_chapter": ["Finn Cooper"], "planned_new_in_this_chapter": ["Zara Blackwind", "Elias Thorne", "Mira Vale"]},
    "plan_scenes": {"1": {"scene": "Morning", "beats": {"b1_1": "Finn wakes", "b1_2": "Finn meets Zara"}, "rules": []}},
    "default_rules": ["No tricolons."],
    "count_rules": [{"id": "finn_answers", "speaker": "Finn", "plan_scene": 1, "max": 1,
                     "question": "In `item.line`, is Finn answering Zara?", "true": "yes", "false": "no"}],
    "screen": {"event_times": {"vespers": ["9:15"], "evening class": ["4:30"]},
               "subjects": ["grammar", "alchemy", "music", "theology", "arms"],
               "session_subjects": {"evening class": ["alchemy", "music", "theology"]},
               "afternoon_only": ["arms"], "non_pov_names": ["Zara", "Elias"], "max_new_per_scene": 2},
    "_manual": ["count_rules", "bible", "known_characters", "plan_scenes", "default_rules", "screen"],
}

P_CLEAN = "Finn walked to the yard and looked at the sky. It was grey. He went in for bread."
CHAPTER_TEXT = "\n\n".join([
    P_CLEAN,
    "At nine the bell rang for Vespers, and Finn went down to the hall.",
    "Evening Class was Grammar again. Zara felt tired.",
    "Zara Blackwind came in. Elias Thorne followed. Mira Vale stood by the door.",
    '"You came," Zara said.', '"I did," Finn said.', '"Why?"', '"Because."',
    "The boys came out of the Arms passage, still loud from the morning.",
]) + "\n\n* * *\n\n" + P_CLEAN


@pytest.fixture
def chapter(project):
    ch = {"id": "ch01", "title": "T", "scenes": [{"scene_number": 1, "pov": "Finn Cooper", "beats": ["Finn wakes"]},
                                                 {"scene_number": 2, "pov": "Finn Cooper"}]}
    project.save_outline([ch])
    config.set_config({"llm": {"json_retries": 1}, "screen": {"enabled": True, "facts_dir": "facts", "section_words": 40}})
    p = facts.path(project, "ch01")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(FACTS))
    return project.chapter("ch01")


@pytest.fixture
def gen_chapter(chapter, project):
    p = facts.path(project, "ch01")
    p.write_text(json.dumps({**FACTS, "_manual": ["count_rules"]}))
    return chapter


def _sections(text=CHAPTER_TEXT, words=40):
    return screen.split_sections(text, words)


def test_split_join_roundtrip():
    secs = _sections()
    assert len(secs) >= 3 and secs[-1]["scene"] == 2
    assert screen.join_sections(secs).split() == CHAPTER_TEXT.replace("* * *", " * * * ").split()


def test_code_checks_catch_rule_checkable_problems():
    secs = [{"sid": "S1", "scene": 1, "text": CHAPTER_TEXT.split("* * *")[0]}]
    checks = {f["check"] for f in screen_checks.run(secs, FACTS)}
    assert {"time_vs_timetable", "session_subject", "afternoon_only_in_morning", "pov_other_thoughts"} <= checks
    newp = screen_checks.new_people_per_scene(secs, FACTS)
    assert newp[0]["over"] and len(newp[0]["new_named"]) == 3
    lines = screen_checks.character_lines(secs, "Finn")
    assert [l["line"] for l in lines] == ['"I did," Finn said.', '"Because."']


def test_no_key_skips_jev_keeps_code(chapter, project, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    jev.set_fake(None)
    pk = screen.run(project, chapter, CHAPTER_TEXT, {})
    assert pk["jev"].startswith("skipped") and pk["flagged"]
    assert all(f["source"] != "jev" for fl in pk["flags"].values() for f in fl)
    assert "REVISION PACKET" in pk["prompt"] and "skipped" in pk["prompt"]


def test_jev_flags_at_thresholds_and_counts(chapter, project):
    seen = []

    def fake(state, qs, model):
        seen.append(model)
        if "items" in state:
            return {"model": model, "answers": {k: {"type": "noul", "noul": 0.9} for k in qs}, "usage": {"input_tokens": 10}}
        txt = state["section_text"]
        ans = {q: {"type": "noul", "noul": 0.05} for q in qs if qs[q]["type"] == "noul"}
        ans["ya_appropriate"] = {"type": "noul", "noul": 0.97}
        if "felt tired" in txt:
            ans["pov_head_hop"] = {"type": "noul", "noul": 0.65}       # below the 0.7 bar
            ans["session_content_conflict"] = {"type": "noul", "noul": 0.8}
        ans["plan_beat"] = {"type": "choice", "choice": "b1_1", "probabilities": {"b1_1": 0.9, "none": 0.1}, "confidence": 0.9}
        return {"model": "jev-1.13.0", "answers": ans, "usage": {"input_tokens": 100}}

    jev.set_fake(fake)
    try:
        pk = screen.run(project, chapter, CHAPTER_TEXT, {})
    finally:
        jev.set_fake(None)
    assert pk["jev"] == "ok" and set(seen) == {"jev-1.13.0"}
    allf = [f for fl in pk["flags"].values() for f in fl]
    assert any(f["source"] == "jev" and f["check"] == "session_content_conflict" for f in allf)
    assert not any(f["source"] == "jev" and f["check"] == "pov_head_hop" for f in allf)
    assert any(f["check"] == "count:finn_answers" for f in allf)
    clean_sid = [s["sid"] for s in pk["sections"] if s["scene"] == 2][0]
    assert clean_sid not in pk["flagged"] and pk["beat_map"][clean_sid]["beat"] == "b1_1"
    rows = [json.loads(l) for l in (project.state_dir / "jev_calls.jsonl").read_text().splitlines()]
    assert rows and all(r["ok"] and r["requested"] == "jev-1.13.0" for r in rows)


def test_jev_error_falls_back(chapter, project):
    jev.set_fake(lambda s, q, m: (_ for _ in ()).throw(RuntimeError("HTTP 500")))
    try:
        pk = screen.run(project, chapter, CHAPTER_TEXT, {})
    finally:
        jev.set_fake(None)
    assert pk["jev"].startswith("skipped: Jev call failed") and pk["flagged"]


def test_packet_editor_only_touches_flagged(chapter, project, fake, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    step = project.new_step_folder("t")

    def edit(prompt, system):
        assert "REVISION PACKET" in prompt and "CHAPTER DRAFT" not in prompt
        import re
        secs = dict(re.findall(r"TEXT OF (S\d+) \(edit this\):\n(.*?)(?=\nCONTEXT AFTER|\n===== S|\nYOUR JOB)", prompt, re.S))
        return {"sections": {k: v.replace("Evening Class was Grammar again.", "Evening Class was Alchemy.")
                                 .replace("At nine the bell", "At a quarter past nine the bell") for k, v in secs.items()},
                "unfixed": []}
    fake.hooks["edit"] = edit
    out, rep = revise_chapter._edit(project, chapter, CHAPTER_TEXT, {}, step, config.load_config())
    assert rep["mode"] == "packet" and rep["accepted"]
    assert "Evening Class was Alchemy." in out and "quarter past nine" in out
    assert out.count(P_CLEAN) == 2   # unflagged sections untouched
    assert (step / "revision_packet.md").exists() and (step / "screen_estimate.json").exists()
    est = json.loads((step / "screen_estimate.json").read_text())
    assert est["packet_input_tokens"] > 0 and est["full_input_tokens"] > 0


def test_clean_chapter_skips_editor(chapter, project, fake, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    step = project.new_step_folder("t")
    out, rep = revise_chapter._edit(project, chapter, P_CLEAN + "\n\n* * *\n\n" + P_CLEAN, {}, step, config.load_config())
    assert rep["reason"].startswith("screen: nothing flagged") and fake.count("edit") == 0


def test_bad_packet_reply_falls_back_to_full_edit(chapter, project, fake, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    step = project.new_step_folder("t")
    fake.hooks["edit"] = lambda p, s: ({"nope": 1} if "REVISION PACKET" in p else
                                       p.split("CHAPTER DRAFT:\n", 1)[1].split("\n\nYOUR JOB:", 1)[0])
    out, rep = revise_chapter._edit(project, chapter, CHAPTER_TEXT, {}, step, config.load_config())
    assert fake.count("edit") == 2 and rep.get("mode") != "packet"


def test_screen_disabled_uses_full_editor(chapter, project, fake):
    config.set_config({"llm": {"json_retries": 1}, "screen": {"enabled": False}})
    step = project.new_step_folder("t")
    fake.hooks["edit"] = lambda p, s: p.split("CHAPTER DRAFT:\n", 1)[1].split("\n\nYOUR JOB:", 1)[0]
    out, rep = revise_chapter._edit(project, chapter, CHAPTER_TEXT, {}, step, config.load_config())
    assert fake.count("edit", "CHAPTER DRAFT") == 1 and not (step / "revision_packet.md").exists()


def test_facts_regenerate_keeps_manual(gen_chapter, project):
    chapter = gen_chapter
    p = facts.path(project, "ch01")
    f = facts.load_or_generate(project, chapter)
    assert f["count_rules"] == FACTS["count_rules"]          # manual key kept
    assert f["plan_scenes"]["1"]["beats"] == {"b1_1": "Finn wakes"}  # generated keys rebuilt from the outline
    assert json.loads(p.read_text())["_manual"] == ["count_rules"]
