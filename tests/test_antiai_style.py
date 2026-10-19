"""Detector catalog, anti-AI rewrite loop, style bible / voice sheets, rich scene briefs."""
from pathlib import Path

from bookforge import config
from bookforge.core import deai, detector, style_bible
from bookforge.core.schema import Scene

SLOP = ('The air crackled with tension. Somewhere, a dog barked. She let out a breath she didn\'t know '
        'she was holding.\n\n"I hear you," Kit said softly. "And that\'s valid."\n\n'
        'It wasn\'t fear. It was something older\u2014a testament to the weight of everything they had lost.\n\n'
        'Maybe, just maybe, that was enough.')
CLEAN = ('Ada pulled the trotline hand over hand until the catfish came up thrashing. Tom held the lantern '
         'and told him he was doing it wrong, which he said every Saturday.\n\n'
         '"You want it or not?" Ada said.\n\n"I want the truck," Tom said. "The fish is your problem."\n\n'
         'They walked home along the creek with the fish in a feed sack and the lantern swinging.')


def test_catalog_loads_and_is_comprehensive():
    cat = detector.load_catalog()
    ids = {r["id"] for r in cat}
    for need in ("ai_vocab_core", "not_x_but_y", "a_beat", "lands_landed", "something_shifted", "abstract_weight",
                 "whisper_volume", "held_breath", "therapy_speak", "said_bookism", "said_adverb", "em_dash",
                 "semicolon_heavy", "rule_of_three", "rhetorical_opener", "one_line_punch", "scene_end_moral",
                 "sensory_list_opening", "emotion_after_show", "and_yet", "somewhere_dog", "air_atmosphere",
                 "eyes_cliche", "quip_density", "antislop_phrase"):
        assert need in ids, need
    for r in cat:
        assert r["severity"] in (1, 2, 3) and r.get("fix"), r["id"]
    assert sum(len(r.get("patterns", [])) for r in cat) > 500


def test_detector_flags_slop_with_reasons():
    rep = detector.detect(SLOP)
    rules = set(rep["counts"])
    for r in ("air_atmosphere", "somewhere_dog", "held_breath", "therapy_speak", "said_adverb", "wasnt_it_was",
              "em_dash", "abstract_weight", "maybe_just_maybe", "scene_end_moral"):
        assert r in rules, r
    assert rep["score"] < 3
    h = rep["hits"][0]
    assert {"rule", "severity", "para", "start", "end", "span", "reason", "fix"} <= set(h)
    assert all(v >= 0 for v in rep["per_1k"].values())


def test_detector_clean_text_scores_high():
    rep = detector.detect(CLEAN)
    assert rep["score"] >= 8, detector.format_report(rep)
    assert not any(h["severity"] >= 3 for h in rep["hits"])


def test_dialogue_vs_narration_scoping():
    # therapy-speak only counts inside dialogue; said_adverb only in narration
    assert "therapy_speak" not in detector.detect("The clinic had a sign about boundaries.")["counts"]
    assert "therapy_speak" in detector.detect('"You need boundaries," she said.')["counts"]


def test_allowlist_and_known_names():
    t = "Once upon a time there was a girl named Lyra."
    rep = detector.detect(t, allow=["once upon a time"], known_names=["Lyra"])
    assert "stock_names" not in rep["counts"]
    assert not any("once upon a time" in h["span"].lower() for h in rep["hits"])


def test_autofix_only_tokens():
    out, applied = detector.autofix("\u201cWait\u2014\u201d she said\u2026 It was a tapestry.")
    assert "\u2014" not in out and "\u201c" not in out and "tapestry" in out
    assert applied == ["token_scrub"]


def test_rewrite_loop_uses_flag_instructions_and_reports(fake):
    seen = []

    def hook(prompt, system):
        seen.append(prompt)
        para = prompt.split("PARAGRAPH TO FIX:\n", 1)[1].split("\n\nPROBLEMS FOUND", 1)[0]
        return para.replace("The air crackled with tension.", "The stove ticked.")

    fake.hooks["deai"] = hook
    text = "The air crackled with tension. Ada set the bucket down by the door and wiped his hands.\n\nHe went out."
    out, rep = deai.rewrite_flagged(text)
    assert "crackled" not in out and out.endswith("He went out.")
    assert "FIX:" in seen[0] and "air_atmosphere" in seen[0]
    assert rep["rewritten_paragraphs"] == [0] and rep["score_after"] >= rep["score_before"]
    assert 1 <= rep["loops"] <= 2 and "residual" in rep


def test_rewrite_loop_caps_and_rejects_worse(fake):
    fake.hooks["deai"] = lambda p, s: "A tapestry, a symphony, a kaleidoscope of ozone. Something shifted in him."
    config.set_config({"llm": {"json_retries": 1}, "deai": {"max_loops": 2}})
    text = "The air crackled with tension near the old mill by the water."
    out, rep = deai.rewrite_flagged(text)
    assert out == text and rep["rewritten_paragraphs"] == []
    assert fake.count("deai") == 2  # two loops, both rejected


def _write_bible(root: Path):
    (root / "voices").mkdir(parents=True)
    (root / "style_bible.md").write_text(
        "# SB\n\n## Example passages\n\n### storyteller\nOnce there was a creek that ran backward on Sundays.\n\n"
        "### todo-one\nTODO: fill me\n\n## Narrator voice\nWarm and wry.\n\n## Allowed phrases\n- once upon a time\n")
    (root / "voices" / "ada.yaml").write_text(
        "name: Ada Lane\naliases: [Ada]\nspeech_patterns: [deadpan]\nnever_says: [boundaries]\n"
        "wants: to belong\nsample_lines: [\"I reckon.\", TODO]\n")
    (root / "voices" / "marlow.yaml").write_text("name: Lord Henry Marlow\naliases: [Marlow]\n")


def test_style_bible_parse_and_budget(tmp_path):
    _write_bible(tmp_path)
    sb = style_bible.load(tmp_path)
    assert sb.sample_ids() == ["storyteller"]          # TODO sample skipped
    assert sb.allowlist() == ["once upon a time"]
    b = sb.block(400)
    assert b.index("EXAMPLE PASSAGES") < b.index("STYLE GUIDANCE") and "creek that ran backward" in b
    from bookforge.core.context import count_tokens
    assert count_tokens(sb.block(60)) <= 60
    v = sb.voices_block(["Ada"], 500)
    assert "NEVER says: boundaries" in v and "I reckon." in v and "TODO" not in v
    assert sb.voice_for("Lord Edmund Maltravers") is None   # no false match on title words
    assert sb.voice_for("Marlow")["name"] == "Lord Henry Marlow"


def test_style_and_voices_reach_drafter_context(project, tmp_path):
    _write_bible(project.project_dir)
    project.save_json("outline.json", [{"id": "ch01", "title": "Creek", "scenes": []}])
    project.save_characters({})
    project.save_world({"key_locations": [], "core_rules": []})
    prefix = project.stable_prefix()
    assert "creek that ran backward" in prefix and "imitate" in prefix.lower()
    scene = Scene.model_validate({"scene_number": 1, "pov": "Ada", "characters_present": ["Ada"],
                                  "wants": ["Ada: wants the fish"], "obstacle": "the line is snagged",
                                  "emotional_turn": "bored -> proud", "sensory_anchor": "cold mud",
                                  "closing_image": "the fish in the sack", "voice_notes": "Ada: deadpan",
                                  "tone_target": "warm", "style_samples": ["storyteller"]}).model_dump()
    ctxt = project.get_scene_context({"id": "ch01", "title": "Creek"}, scene, ["Ada pulls the line"], "")
    for s in ("Who wants what: Ada: wants the fish", "Obstacle: the line is snagged", "bored -> proud",
              "cold mud", "Closing image", "storyteller", "VOICE SHEETS", "NEVER says: boundaries"):
        assert s in ctxt, s


def test_scene_brief_schema_coerces():
    s = Scene.model_validate({"wants": "Ada: fish", "voice_notes": ["a", "b"], "style_samples": "storyteller",
                              "closing_image": ["x", "y"]}).model_dump()
    assert s["wants"] == ["Ada: fish"] and s["style_samples"] == ["storyteller"]
    assert isinstance(s["closing_image"], str)


def test_deai_role_on_utility_tier():
    assert config.resolve_role("deai")[1] == "google/gemini-2.5-flash"


def test_cli_detect(tmp_path):
    from click.testing import CliRunner
    from bookforge.cli import cli
    f = tmp_path / "s.txt"
    f.write_text(SLOP)
    r = CliRunner().invoke(cli, ["detect", str(f)])
    assert r.exit_code == 0 and "score=" in r.output and "therapy_speak" in r.output


def test_curly_quote_dialogue_not_fragments_or_punches():
    t = ("Ada threw a husk at him. Tom let it land across his knee.\n\n"
         + "\n\n".join(["\u201cMustang,\u201d Ada said.", "\u201cTruck.\u201d", "\u201cTake the paved road.\u201d",
                        "\u201cSheriff takes the paved road.\u201d", "\u201cFor the fence.\u201d",
                        "\u201cThat we just drove through?\u201d", "\u201cI live up our road.\u201d"]))
    rules = set(detector.detect(t)["counts"])
    assert not rules & {"one_line_punch", "fragment", "lands_landed"}, rules
    assert detector.detect(SLOP)["score"] < 3


def test_voice_pass_rejects_scene_count_change(monkeypatch, tmp_path):
    from bookforge.core import voice
    monkeypatch.setattr(voice, "cfg", lambda: {**voice.DEFAULTS, "enabled": True})
    monkeypatch.setattr(voice, "llm_call", lambda *a, **k: "One merged scene with no separator at all.")
    class SB:
        empty = True
        def allowlist(self): return []
    class Ctx:
        def style(self): return SB()
    scenes = ["First scene text here.", "Second scene text here."]
    out, rep = voice.revoice(Ctx(), {"title": "t", "scenes": []}, scenes)
    assert out == scenes and not rep["accepted"] and rep["reason"] == "scene count changed"


def test_voice_pass_accepts_good_output(monkeypatch):
    from bookforge.core import voice
    monkeypatch.setattr(voice, "cfg", lambda: {**voice.DEFAULTS, "enabled": True})
    monkeypatch.setattr(voice, "llm_call", lambda *a, **k: "Then first scene ran on.\n\n* * *\n\nThe second scene too.")
    class SB:
        empty = True
        def allowlist(self): return []
    class Ctx:
        def style(self): return SB()
    out, rep = voice.revoice(Ctx(), {"title": "t", "scenes": []}, ["The first scene ran on.", "The second scene too."])
    assert rep["accepted"] and len(out) == 2


class _TCtx:
    def __init__(self, d):
        self.project_dir = d


def test_threads_inherit_render_and_ledger(tmp_path, monkeypatch):
    import yaml
    from bookforge.core import threads
    (tmp_path / "threads.yaml").write_text(yaml.safe_dump({"threads": [
        {"id": "ring", "title": "Nell's ring", "type": "plant", "status": "planned"},
        {"id": "clock", "title": "Tom's clock", "type": "main", "status": "planned"}]}))
    ctx = _TCtx(tmp_path)
    ch = {"id": "ch02", "scenes": [{"scene_number": 1}, {"scene_number": 2}],
          "thread_obligations": [{"thread": "ring", "action": "plant", "how": "chain glints", "scene": 1},
                                 {"thread": "clock", "action": "open", "how": "knife", "scene": 0},
                                 {"thread": "clock", "action": "advance", "how": "x", "scene": 9}]}
    threads.inherit_to_scenes(ch)
    assert [o["thread"] for o in ch["scenes"][0]["thread_obligations"]] == ["ring", "clock"]
    assert len(ch["scenes"][1]["thread_obligations"]) == 2  # span + stray
    assert "[PLANT] Nell's ring" in threads.render(ch["thread_obligations"], threads.load(ctx))
    threads.update_ledger(ctx, ch, [{"thread": "ring", "action": "plant", "pass": True, "quote": "q"},
                                    {"thread": "clock", "action": "open", "pass": False}])
    d = threads.by_id(threads.load(ctx))
    assert d["ring"]["status"] == "open" and d["ring"]["open_chapter"] == "ch02"
    assert d["clock"]["status"] == "planned" and d["clock"]["history"][0]["result"] == "MISSED"
    assert all(h["needs_review"] for h in d["ring"]["history"])


def test_threads_check_rejects_fake_quote(tmp_path, monkeypatch):
    import yaml
    from bookforge.core import threads
    (tmp_path / "threads.yaml").write_text(yaml.safe_dump({"threads": [{"id": "ring", "title": "r"}]}))
    monkeypatch.setattr(threads, "llm_call", lambda *a, **k: {"results": [
        {"thread": "ring", "action": "plant", "pass": True, "quote": "this sentence is not in the chapter at all"}],
        "opening_hook": {"pass": True}})
    res = threads.check_obligations(_TCtx(tmp_path), {"id": "ch01", "thread_obligations": [
        {"thread": "ring", "action": "plant", "how": "x"}]}, "Nell touched the ring on its chain.")
    assert res[0]["pass"] is False and "quote not found" in res[0]["note"]


def test_plan_sync_change_detection():
    from bookforge.core import plan_sync
    a = "one two three four five six seven eight nine ten " * 5
    assert not plan_sync.changed_a_lot(a, a)
    assert plan_sync.changed_a_lot(a, "completely different words now " * 10)
    assert plan_sync.is_stale({"plan_text_hash": "x"}, a)
    assert not plan_sync.is_stale({"plan_text_hash": plan_sync.text_hash(a)}, a)


def test_detector_dialogue_rhythm_and_narration_block():
    from bookforge.core import detector
    pingpong = "\n\n".join(['"You coming?" he said.', '"Maybe," Ada said.', '"Maybe ain\'t yes."', '"It ain\'t no."',
                            '"Then what is it?"', '"It\'s maybe."', '"Well, fine then."'])
    rep = detector.detect(pingpong)
    assert any(h["rule"] == "dialogue_rhythm" for h in rep["hits"])
    block = "\n\n".join(["The hill went up and the road went with it, past the tipple and the church and the store. " * 4] * 6)
    rep2 = detector.detect(block)
    assert any(h["rule"] == "narration_block" for h in rep2["hits"])


def test_first_line_is_intent_not_wording():
    from bookforge.core.rules_check import enforce_first_line
    from bookforge.core import threads
    ch = {"first_line": "It was the last summer the boys were alive together."}
    assert enforce_first_line(ch, "Ada dove.") == "Ada dove."  # never forced verbatim
    assert threads.opening_intent(ch) == "It was the last summer the boys were alive together."
    ch2 = threads.apply_to_chapter({"scenes": [{"scene_number": 1}]},
                                   {"opening_intent": "Open on the boys mid-dare; foreshadow the creek."})
    assert ch2["opening_intent"].startswith("Open on the boys")
    ch3 = threads.apply_to_chapter({"scenes": [{"scene_number": 1}]}, {"first_line": "Legacy line."})
    assert ch3["opening_intent"] == "Legacy line."


def test_detector_quip_cluster():
    from bookforge.core import detector
    t = "\n\n".join(['"Nice shoes."', '"Nice face."', '"Nicer than yours."', '"Low bar."', "Ada walked on down the road toward the store where the log trucks used to idle."])
    rep = detector.detect(t)
    assert any(h["rule"] == "quip_cluster" for h in rep["hits"])


def test_rules_check_has_ben_lessons():
    from bookforge.core import rules_check
    for k in ("quip_clusters", "line_purpose", "dangling_events", "backstory_by_deed", "threat_consequence", "power_leaks"):
        assert k in rules_check.RULES


def test_clarity_rules_present():
    import bookforge.steps.write_chapter as wc
    from bookforge.core.rules_card import RULE_CARD
    from bookforge.core.rules_check import RULES
    from bookforge.core.schema import Critique
    from bookforge.core import pack
    import inspect
    assert not hasattr(wc, "WRITE_RULES_V1")
    assert "CLARITY" in RULE_CARD and "INTRODUCE means" in RULE_CARD
    assert "subtext" not in RULE_CARD and "never explained" not in RULE_CARD
    assert 12 <= len([l for l in RULE_CARD.splitlines() if l[:3].strip().rstrip(".").isdigit()]) <= 16
    assert "clarity" in RULES and "continuity" in RULES and "introductions" in RULES
    assert "clarity_score" in Critique.model_fields
    src = inspect.getsource(pack.build_write)
    assert "write each beat to completion, then stop" in src and "never a goal" in src
