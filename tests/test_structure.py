"""Story-structure blocks: schema coercion, prompt block, lint (FAIL/WARN), CLI."""
from click.testing import CliRunner

from bookforge.core import structure as S
from bookforge.core.schema import normalize_outline


WORDS = ["apple", "river", "candle", "harbor", "violin", "meadow", "tunnel", "lantern", "falcon", "quarry"]


def ch(n, **st):
    base = {"act": "1", "save_the_cat": "setup", "heros_journey": "ordinary_world", "chapter_type": "discovery",
            "strand": "A", "a_purpose": "x", "plot_function": f"{WORDS[n]} {WORDS[n - 1]}x changes {WORDS[n]}y",
            "value_turn": {"from": f"low{n}", "to": f"high{n}"}, "scene_sequel": "scene", "ending": "reveal"}
    base.update(st)
    return {"id": f"ch{n:02d}", "title": f"T{n}", "structure": base}


def checks(issues, sev=None):
    return [(i["chapter"], i["check"]) for i in issues if sev is None or i["severity"] == sev]


def test_coercion_aliases_and_string_turn():
    s = S.Structure.model_validate({"act": "Act 2", "save_the_cat": "Dark Night", "heros_journey": "Mentor",
                                    "chapter_type": "set-piece", "strand": "both", "ending": "image",
                                    "value_turn": "safe -> hunted", "percent": "52%", "setups": "thread:x"})
    assert (s.act, s.save_the_cat, s.heros_journey, s.chapter_type, s.strand, s.ending) == \
        ("2a", "dark_night_of_the_soul", "meeting_the_mentor", "set_piece", "AB", "resonant_image")
    assert s.value_turn == {"from": "safe", "to": "hunted"} and s.percent == 52.0 and s.setups == ["thread:x"]


def test_schema_keeps_structure_and_drops_defaults():
    out = normalize_outline([{"id": "x", "title": "A", "structure": {"plot_function": "p", "act": "3"}}])
    assert out[0]["structure"] == {"plot_function": "p", "act": "3"}
    assert normalize_outline([{"id": "x", "title": "A"}])[0]["structure"] == {}


def test_block_in_writer_plan_block():
    from bookforge.core import pack
    c = ch(1, color="talking sheep", try_fail="try 1/3: fails", setups=["thread:fold"], percent=10)
    b = pack.chapter_plan_block(c)
    assert "STRUCTURAL FUNCTION" in b and "Plot function" in b and "low1 -> high1" in b
    assert "talking sheep" in b and "try 1/3" in b and "thread:fold" in b and "10%" in b
    assert S.block({"id": "ch01"}) == ""


def test_fail_on_missing_function_and_flat_turn():
    o = [ch(1, plot_function=""), ch(2, value_turn={"from": "calm", "to": "calm"}), ch(3, value_turn={})]
    f = checks(S.check(o), "FAIL")
    assert ("ch01", "plot_function") in f and ("ch02", "value_turn") in f and ("ch03", "value_turn") in f


def test_fail_on_duplicate_neighbour_function():
    o = [ch(1, plot_function="Ada learns the Dictates and chooses to stay with Marlow"),
         ch(2, plot_function="Ada learns the Dictates and chooses to stay with Marlow house"),
         ch(3)]
    assert ("ch02", "duplicate_function") in checks(S.check(o), "FAIL")
    assert not [x for x in checks(S.check([ch(1), ch(2)]), "FAIL")]


def test_missing_fields_and_enums_warn():
    o = [{"id": "ch01", "structure": {"plot_function": "p", "value_turn": "a -> b", "chapter_type": "musical"}}]
    w = checks(S.check(o), "WARN")
    assert ("ch01", "missing") in w and ("ch01", "enum") in w


def test_runs_of_type_and_ending():
    o = [ch(i, chapter_type="reaction", ending="resonant_image") for i in range(1, 5)]
    w = checks(S.check(o), "WARN")
    assert ("ch01", "chapter_type_run") in w and ("ch01", "ending_run") in w
    o2 = [ch(1, chapter_type="reaction"), ch(2, chapter_type="action"), ch(3, chapter_type="reaction")]
    assert ("ch01", "chapter_type_run") not in checks(S.check(o2))


def test_act_balance_and_order():
    o = [ch(1, act="1"), ch(2, act="1"), ch(3, act="1"), ch(4, act="2a"), ch(5, act="2b"), ch(6, act="3")]
    w = [i["description"] for i in S.check(o) if i["check"] == "act_balance"]
    assert any("Act 1 is 50%" in d for d in w)
    assert ("", "act_order") in checks(S.check([ch(1, act="2a"), ch(2, act="1")]))


def test_beat_position_uses_words():
    o = [ch(1, save_the_cat="midpoint"), ch(2), ch(3), ch(4)]
    assert ("ch01", "beat_position") in checks(S.check(o, words={"ch01": 1000, "ch02": 1000, "ch03": 1000, "ch04": 1000}))
    assert S.positions(o, {"ch01": 100, "ch02": 300})["ch02"] == round(100 * 250 / 800, 1)  # unknowns use the mean (200)


def test_setups_and_payoffs_link_to_threads():
    threads = [{"id": "fold", "payoff_chapter": "ch03"}, {"id": "later", "payoff_chapter": "book4"},
               {"id": "orphan", "payoff_chapter": ""}]
    o = [ch(1, setups=["thread:fold", "thread:later", "thread:orphan", "thread:nope", "a loose gun"]), ch(2),
         ch(3, payoffs=[])]
    iss = S.check(o, threads)
    c = checks(iss)
    assert ("ch01", "payoff_unmarked") in c          # thread says ch03 but ch03 doesn't list it
    assert ("ch01", "setup_no_payoff") in c          # orphan
    assert ("ch01", "unknown_thread") in c and ("ch01", "unlinked_setup") in c
    assert not any("thread:later" in i["description"] for i in iss if i["check"] == "setup_no_payoff")
    o[2]["structure"]["payoffs"] = ["thread:fold"]
    assert not any("thread:fold" in i["description"] for i in S.check(o, threads))


def test_cli_check_exit_codes(monkeypatch):
    from bookforge import cli
    good = [ch(1), ch(2)]
    monkeypatch.setattr(cli, "_structure_inputs", lambda p: (None, good, [], {}))
    r = CliRunner().invoke(cli.cli, ["structure", "check", "--project", "."])
    assert r.exit_code == 0 and "0 fail" in r.output
    bad = [ch(1, plot_function=""), ch(2)]
    monkeypatch.setattr(cli, "_structure_inputs", lambda p: (None, bad, [], {}))
    r = CliRunner().invoke(cli.cli, ["structure", "check", "--project", "."])
    assert r.exit_code == 1 and "[FAIL] ch01" in r.output
    r = CliRunner().invoke(cli.cli, ["structure", "show", "--project", "."])
    assert r.exit_code == 0 and "ch02" in r.output


def test_rules_mention_plot_function():
    from bookforge.core.rules_card import RULE_CARD
    assert "PLOT FUNCTION" in RULE_CARD and "NO CHAPTER" in S.PLANNER_RULE.upper() or "no chapter" in S.PLANNER_RULE


def test_outline_planner_prompt_carries_structure_rule(monkeypatch, tmp_path):
    from bookforge.steps import outline as O
    seen = {}

    def fake(prompt, **kw):
        seen["p"] = prompt
        return {"chapters": [{"id": "ch01", "title": "A", "structure": {"plot_function": "p", "value_turn": "a -> b"}}]}
    monkeypatch.setattr(O, "llm_call", fake)
    out = O._phase1("premise", 0, {}, tmp_path)
    assert S.PLANNER_RULE in seen["p"] and '"structure"' in seen["p"] and "plot_function" in seen["p"]
    assert out[0]["structure"]["value_turn"] == {"from": "a", "to": "b"}


def test_undeclared_payoff_and_series_exemption():
    threads = [{"id": "s", "type": "series", "payoff_chapter": None},
               {"id": "u", "type": "arc", "payoff_chapter": None, "beats": {"ch01": "a", "ch03": "b"}}]
    o = [ch(1, setups=["thread:s", "thread:u"]), ch(2), ch(3)]
    c = [(i["check"], i["description"][:8]) for i in S.check(o, threads)]
    assert ("payoff_undeclared", "thread:u") in c
    assert not any(d.startswith("thread:s") or "thread:s" in d for _, d in c)
