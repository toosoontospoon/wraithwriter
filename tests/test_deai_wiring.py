"""De-AI wiring: prose rules, located findings, screen candidates, edit guard, residual rewrite, Pangram stub."""
import json
from pathlib import Path

from bookforge import config
from bookforge.core import deai, detector, detector_hooks, editor, jev, pack, screen, screen_checks
from bookforge.steps import deai_chapter, revise_chapter

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = (ROOT / "tests" / "fixtures" / "deai" / "narration_sample.txt").read_text()
DEAI_QS = ("ai_packed_noun", "ai_hedge_stack", "ai_soft_contrast", "ai_unclear_agency",
           "ai_polished_intro", "ai_parallel_list_ending")


def test_polished_intro_and_parallel_list_positive_and_negative():
    intro = ("Millbrook was the kind of town that kept its secrets in the churchyard and never wrote them down. "
             "The streets stayed quiet. The clocks stayed slow. The doors stayed shut.")
    concrete = "Finn pulled the rope and the bell rang once over the yard. He wiped his hands and went in for bread."
    quoted = '"Come inside," Mara said. The hall was the kind of place that swallowed sound.'
    anaphora = "Shops opened at dawn. Shops closed at dusk. Shops locked at nine."
    preamble = "Three habits kept the watch. Men rose early. Dogs slept late. Lamps burned low."
    tricolon = "They left the yard, the road, and the town."
    assert detector.polished_intro_evidence(intro)
    assert detector.polished_intro_evidence(concrete) is None
    assert detector.polished_intro_evidence(quoted) is None
    assert detector.parallel_list_ending_evidence(anaphora)
    assert detector.parallel_list_ending_evidence(preamble)
    assert detector.parallel_list_ending_evidence(tricolon)
    assert detector.parallel_list_ending_evidence("Shops opened at dawn. Shops closed at dusk.") is None
    assert detector.parallel_list_ending_evidence('"We left," she said. "Then we locked the door."') is None

    mid = "Finn walked to the yard and looked at the sky. It was grey. He went in for bread."
    both = detector.detect(intro + "\n\n" + mid + "\n\n" + anaphora)
    assert both["counts"].get("polished_intro") == 1
    assert both["counts"].get("parallel_list_ending") == 1
    buried = detector.detect(mid + "\n\n" + intro + "\n\n" + mid)
    assert "polished_intro" not in buried["counts"]
    assert "parallel_list_ending" not in buried["counts"]
    scene2 = detector.detect(concrete + "\n\n* * *\n\n" + intro)
    assert any(h["rule"] == "polished_intro" and h["para"] == 2 for h in scene2["hits"])


def test_prose_rules_block_is_in_system_prompts_and_byte_stable(project):
    ch1 = {"id": "ch01", "title": "One", "scenes": []}
    ch2 = {"id": "ch02", "title": "Two", "scenes": []}
    a, b = pack.system_for(project, ch1), pack.system_for(project, ch2)
    assert a == b
    assert deai.PROSE_RULES in a
    assert screen.lean_system(project, ch1) == screen.lean_system(project, ch2)
    assert deai.PROSE_RULES in screen.lean_system(project, ch1)
    assert "PROSE RULES:" in project.stable_prefix()
    config.set_config({"deai": {"prompt_rules": False}, "llm": {"json_retries": 1}})
    off = pack.system_for(project, ch1)
    assert "PROSE RULES:" not in off
    assert "PROSE RULES:" not in screen.lean_system(project, ch1)
    assert off == pack.system_for(project, ch2)


def test_editor_findings_keep_narration_hits_and_drop_dialogue(project):
    project.save_outline([{"id": "ch01", "title": "T", "scenes": [{"scene_number": 1, "pov": "Finn"}]}])
    project.save_characters([{"name": "Finn Cooper"}])
    project.save_world({})
    project.write_text(project.chapter_path("ch01", "draft"), SAMPLE)
    chapter = project.chapter("ch01")
    lines = editor.findings(project, "ch01", {}, chapter, SAMPLE)
    blob = "\n".join(lines)
    assert "RULE skill_banlist" in blob and "tapestry" in blob
    assert "RULE not_x_but_y" in blob
    assert "RULE polished_intro" in blob and "RULE parallel_list_ending" in blob
    assert "scene 1 paragraph 0" in blob
    assert "delve" not in blob.lower()
    config.set_config({"deai": {"max_findings": 1}, "llm": {"json_retries": 1}})
    assert len(deai.located_hits(SAMPLE)) == 1


def _chapter(project):
    ch = {"id": "ch01", "title": "T", "scenes": [{"scene_number": 1, "pov": "Finn Cooper",
                                                 "characters_present": ["Finn Cooper"], "beats": ["Finn walks"]}]}
    project.save_outline([ch])
    project.save_characters([{"name": "Finn Cooper"}, {"name": "Elias Ward"}])
    project.save_world({})
    return project.chapter("ch01")


def _jev_ans(qs, scores):
    ans = {}
    for q, spec in qs.items():
        if spec["type"] == "noul":
            ans[q] = {"type": "noul", "noul": scores.get(q, 0.05)}
    ans["ya_appropriate"] = {"type": "noul", "noul": 0.99}
    ans["plan_beat"] = {"type": "choice", "choice": "b1_1", "probabilities": {"b1_1": 0.9, "none": 0.1},
                        "confidence": 0.9}
    return {"model": "jev-1.13.0", "answers": ans, "usage": {"input_tokens": 10}}


def test_revision_packet_contains_narration_hits_not_dialogue(project, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    chapter = _chapter(project)
    config.set_config({"llm": {"json_retries": 1}, "screen": {"enabled": True, "section_words": 80}})
    asked = []

    def fake(state, qs, model):
        asked.append(set(qs))
        return _jev_ans(qs, {q: 0.9 for q in DEAI_QS})

    jev.set_fake(fake)
    try:
        pk = screen.run(project, chapter, SAMPLE, {})
    finally:
        jev.set_fake(None)
    flags = [f for fl in pk["flags"].values() for f in fl]
    ev = " ".join((f.get("evidence") or "") + " " + (f.get("detail") or "") + " " + f.get("check", "") for f in flags).lower()
    assert "tapestry" in ev and "not fear, but" in ev
    assert "kind of town" in ev and "shops opened at dawn" in ev
    assert "delve" not in ev
    assert pk["jev"] == "ok"
    assert any("ai_polished_intro" in qs for qs in asked)
    assert any("ai_parallel_list_ending" in qs for qs in asked)


def test_deai_questions_positions_thresholds_and_switch(project, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    chapter = _chapter(project)
    config.set_config({"llm": {"json_retries": 1}, "screen": {"enabled": True, "deai_questions": True,
                                                              "section_words": 500}})
    neutral = "Finn walked down to the yard, pulled the gate shut, and went in for bread before the rain."

    def run(text, scores, words=500):
        config.set_config({"llm": {"json_retries": 1}, "screen": {"enabled": True, "deai_questions": True,
                                                                  "section_words": words}})
        seen = []

        def fake(state, qs, model):
            seen.append((state["section_text"][:48], set(qs)))
            return _jev_ans(qs, scores)

        jev.set_fake(fake)
        try:
            pk = screen.run(project, chapter, text, {})
        finally:
            jev.set_fake(None)
        flags = [f for fl in pk["flags"].values() for f in fl if f.get("source") == "jev"]
        return pk, seen, flags

    _, seen, flags = run(neutral, {q: 0.49 for q in DEAI_QS})
    assert seen and set(DEAI_QS) <= seen[0][1]
    assert not any(f["check"] in DEAI_QS for f in flags)
    _, _, flags = run(neutral, {q: 0.50 for q in DEAI_QS})
    assert {f["check"] for f in flags} >= set(DEAI_QS)

    cases = [
        ("ai_hedge_stack", "Finn might possibly wait beside the gate until the evening bell rings over the empty yard.",
         "hedge_stack"),
        ("ai_packed_noun", "The crew remember the voyage and forget the streak before the lamps are lit tonight.",
         "packed_noun"),
        ("ai_soft_contrast", "Finn took the oar, not the rope, and rowed across the quiet harbor before dark.",
         "soft_contrast"),
        ("ai_unclear_agency",
         "Finn met Elias at the dock and they talked for an hour about the tide and the broken lamp.\n\n"
         "He turned away and walked home along the wall before the rain started.",
         "unclear_referent"),
        ("ai_polished_intro",
         "Millbrook was the kind of town that kept its secrets in the churchyard and never wrote them down. "
         "The streets stayed quiet. The clocks stayed slow. The doors stayed shut.",
         "polished_intro"),
        ("ai_parallel_list_ending", "Shops opened at dawn. Shops closed at dusk. Shops locked at nine.",
         "parallel_list_ending"),
    ]
    for qid, text, code in cases:
        pk, _, flags = run(text, {qid: 0.39})
        assert not any(f["check"] == qid for f in flags), qid
        # Non-structural code hits stay in the packet. Structural ones are dropped until Jev agrees,
        # so a 0.40 flag (below the 0.5 bar) is what shows the code candidate lowered the threshold.
        if code not in ("polished_intro", "parallel_list_ending"):
            assert any(f.get("check") == code for fl in pk["flags"].values() for f in fl), (qid, code)
        _, _, flags = run(text, {qid: 0.40})
        assert any(f["check"] == qid and f["p"] == 0.4 for f in flags), qid

    intro = cases[4][1]
    mid = "Finn walked to the yard and looked at the sky until the grey light thinned and the bread was gone from the shelf."
    end = cases[5][1]
    _, seen, _ = run(intro + "\n\n" + mid + "\n\n" + end, {}, words=20)
    assert len(seen) >= 3
    assert "ai_polished_intro" in seen[0][1] and "ai_parallel_list_ending" not in seen[0][1]
    assert "ai_polished_intro" not in seen[1][1] and "ai_parallel_list_ending" not in seen[1][1]
    assert "ai_parallel_list_ending" in seen[-1][1] and "ai_polished_intro" not in seen[-1][1]

    config.set_config({"llm": {"json_retries": 1}, "screen": {"enabled": True, "deai_questions": False,
                                                              "section_words": 500}})
    seen = []

    def fake_off(state, qs, model):
        seen.append(set(qs))
        return _jev_ans(qs, {})

    jev.set_fake(fake_off)
    try:
        screen.run(project, chapter, neutral, {})
    finally:
        jev.set_fake(None)
    assert seen and all(not (set(DEAI_QS) & qs) for qs in seen)
    assert "pov_head_hop" in seen[0]


def test_screen_runs_code_checks_without_typesafe_key(project, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    jev.set_fake(None)
    assert config.DEFAULT_CONFIG["screen"]["enabled"] is True
    assert screen.DEFAULTS["enabled"] is True
    assert screen.cfg()["enabled"] is True
    chapter = _chapter(project)
    pk = screen.run(project, chapter, SAMPLE, {})
    assert pk["jev"].startswith("skipped") and "TYPESAFE_API_KEY" in pk["jev"]
    flags = [f for fl in pk["flags"].values() for f in fl]
    assert flags and all(f["source"] != "jev" for f in flags)
    assert any(f["check"] == "skill_banlist" for f in flags)
    assert any(f["check"] == "polished_intro" and f.get("severity", 0) >= 2 for f in flags)
    assert not any("delve" in (f.get("evidence") or "").lower() for f in flags)


def test_edit_guard_reverts_a_section_that_adds_tells(project, fake, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    jev.set_fake(None)
    chapter = _chapter(project)
    config.set_config({"llm": {"json_retries": 1}, "screen": {"enabled": True, "section_words": 400,
                                                              "facts_dir": "facts"},
                       "deai": {"edit_guard": "section", "max_tell_drop": 0.5}})
    facts = {"chapter": "ch01", "bible": {"pov": "Finn.", "timetable": ["9:15 Vespers in the court."]},
             "known_characters": {"before_this_chapter": ["Finn Cooper"], "planned_new_in_this_chapter": []},
             "plan_scenes": {"1": {"beats": {"b1_1": "Finn walks"}, "rules": []}}, "default_rules": [],
             "screen": {"event_times": {"vespers": ["9:15"]}, "non_pov_names": [], "max_new_per_scene": 0},
             "_manual": ["screen", "bible", "plan_scenes"]}
    path = __import__("bookforge.core.facts", fromlist=["path"]).path(project, "ch01")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts))
    text = "At nine the bell rang for Vespers, and Finn went down to the hall and shut the door behind him."
    fake.hooks["edit"] = lambda prompt, system: {
        "sections": {"S1": "At nine the bell rang for a tapestry of Vespers, and Finn went down to the hall and shut the door."},
        "unfixed": []}
    step = project.new_step_folder("guard")
    out, rep = revise_chapter._edit(project, chapter, text, {}, step, config.load_config())
    assert "tapestry" not in out and "At nine the bell rang for Vespers" in out
    assert rep.get("edit_guard_reverted") == ["S1"]

    config.set_config({"llm": {"json_retries": 1}, "screen": {"enabled": True, "section_words": 400,
                                                              "facts_dir": "facts"},
                       "deai": {"edit_guard": "off"}})
    out_off, rep_off = revise_chapter._edit(project, chapter, text, {}, step, config.load_config())
    assert "tapestry" in out_off and not rep_off.get("edit_guard_reverted")


def test_full_edit_guard_warns_without_reverting(project, fake):
    config.set_config({"llm": {"json_retries": 1}, "screen": {"enabled": False},
                       "deai": {"edit_guard": "section", "max_tell_drop": 0.5}})
    chapter = _chapter(project)
    text = "Finn walked to the yard and looked at the sky. It was grey. He went in for bread and sat down."
    project.write_text(project.chapter_path("ch01", "draft"), text)

    def edit(prompt, system):
        body = prompt.split("CHAPTER DRAFT:\n", 1)[1].split("\n\nYOUR JOB", 1)[0]
        return body.replace("the sky", "a tapestry of smoke")

    fake.hooks["edit"] = edit
    step = project.new_step_folder("fullguard")
    out, rep = editor.edit_chapter(project, chapter, text, {}, step)
    assert "tapestry" in out
    assert "full-edit mode does not revert" in rep.get("edit_guard_warning", "")


def test_rewrite_residual_off_skips_llm_and_on_keeps_better(project, fake):
    project.save_outline([{"id": "ch01", "title": "T", "scenes": [{"scene_number": 1}]}])
    project.save_characters([{"name": "Finn Cooper"}])
    project.save_world({})
    project.write_text(project.chapter_path("ch01", "revised"), SAMPLE)
    deai_chapter.run(project, "ch01")
    assert fake.count("deai") == 0
    assert "tapestry" in project.chapter_path("ch01", "final").read_text()

    config.set_config({"deai": {"rewrite_residual": True}, "llm": {"json_retries": 1}})
    deai_chapter.run(project, "ch01")
    final = project.chapter_path("ch01", "final").read_text()
    assert fake.count("deai") >= 1
    assert "tapestry" not in final and "delve" in final
    gate = json.loads(sorted(project.steps_dir.glob("*deai_ch01/final_gate.json"))[-1].read_text())
    assert gate["residual_rewrite"]["rewritten_paragraphs"]


def test_pangram_stub_skips_without_a_key_and_does_not_change_text(project, monkeypatch):
    monkeypatch.delenv("PANGRAM_API_KEY", raising=False)
    text = "Finn walked to the yard and shut the gate behind him before dark."
    project.save_outline([{"id": "ch01", "title": "T", "scenes": [{"scene_number": 1}]}])
    project.save_characters({})
    project.save_world({})
    project.write_text(project.chapter_path("ch01", "revised"), text)
    config.set_config({"deai": {"external_detector": {"provider": "pangram", "api_key_env": "PANGRAM_API_KEY",
                                                      "cap_usd": 1.0, "mode": "report"}},
                       "llm": {"json_retries": 1}})
    deai_chapter.run(project, "ch01")
    final = project.chapter_path("ch01", "final").read_text()
    assert final.strip() == text.strip()
    report = next(project.steps_dir.glob("*deai_ch01/detector_report.txt")).read_text()
    assert '"provider": "pangram"' in report and '"status": "skipped: no key"' in report
    assert "PANGRAM_API_KEY=" not in report
    monkeypatch.setenv("PANGRAM_API_KEY", "super-secret-token")
    row = detector_hooks.external_check("same words " + text)
    assert row["status"] == "skipped: not implemented"
    assert row["cost_usd"] == 0.0 and row["spans"] == []
    assert "super-secret-token" not in json.dumps(row)
    assert "requests" not in Path(detector_hooks.__file__).read_text()


def test_screen_checks_expose_structural_candidates():
    sections = screen.split_sections(SAMPLE, 80)
    hits = screen_checks.deai_candidates(SAMPLE, sections, ["Finn"])
    rules = {h["check"] for h in hits}
    assert {"polished_intro", "parallel_list_ending", "skill_banlist", "not_x_but_y"} <= rules
    assert all("delve" not in (h.get("evidence") or "").lower() for h in hits)
