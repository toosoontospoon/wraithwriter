"""P0 craft: framework, scene/sequel, tension, try/fail, promises, arcs, foreshadow windows, scheduler cap."""
from bookforge import config
from bookforge.craft import REGISTRY, engine
from bookforge.craft.base import Events
from craft_helpers import check, checks_named, ev, plan, setup


# ---------------------------------------------------------------- framework + cards
def test_default_framework_is_save_the_cat_and_maps_beats(tmp_path):
    P, L = setup(tmp_path, n=11)
    _, data, _ = plan("structure", {}, P, L)
    assert data["framework"] == "save_the_cat"
    beats = {b["name"]: b["chapter"] for b in data["beats"]}
    assert beats["midpoint"] == "ch06" and beats["all_is_lost"] in ("ch08", "ch09") and beats["opening_image"] == "ch01"


def test_framework_is_configurable(tmp_path):
    config.set_config({"craft": {"framework": "seven_point"}})
    from bookforge.craft.base import cfg
    P, L = setup(tmp_path, framework=cfg()["framework"])
    _, data, _ = plan("structure", {}, P, L)
    assert [b["name"] for b in data["beats"]][:2] == ["hook", "plot_turn_1"]


def test_default_cards_scene_sequel_and_valid_tension_plan(tmp_path):
    P, L = setup(tmp_path, n=10, scenes=3)
    plan("structure", {}, P, L)
    cards = L.data["scene_cards"]
    assert all(cards[f"ch{i:02d}:3"]["scene_type"] == "scene" for i in range(1, 11))  # chapter ends on a disaster
    assert any(c["scene_type"] == "sequel" for c in cards.values())
    events = REGISTRY["structure"].planned_events(L.module("structure"), P)
    assert not checks_named(check("structure", events, P, L, "plan"), "tension_curve")


def test_tension_audit_flags_flat_run_and_early_peak(tmp_path):
    P, L = setup(tmp_path, n=6, scenes=1)
    plan("structure", {}, P, L)
    flat = [ev("tension", None, f"ch{i:02d}", 1, value=v) for i, v in zip(range(1, 7), [5, 5, 5, 9, 4, 3])]
    issues = checks_named(check("structure", flat, P, L), "tension_curve")
    descr = " ".join(i["description"] for i in issues)
    assert "Three scenes in a row" in descr and "peaks at scene #4" in descr


def test_try_fail_needs_two_failures(tmp_path):
    P, L = setup(tmp_path, n=4, scenes=1)
    raw = {"cards": [{"chapter": "ch01", "scene": 1, "try_fail": {"goal": "main", "attempt": 1, "outcome": "no_and"}},
                     {"chapter": "ch03", "scene": 1, "try_fail": {"goal": "main", "attempt": 2, "outcome": "yes"}}]}
    plan("structure", raw, P, L)
    assert checks_named(check("structure", [], P, L, "plan"), "try_fail")
    L.data["scene_cards"]["ch02:1"]["try_fail"] = {"goal": "main", "attempt": 2, "outcome": "no"}
    assert not checks_named(check("structure", [], P, L, "plan"), "try_fail")


def test_scene_that_should_end_in_disaster_but_succeeds_gets_revision(tmp_path):
    P, L = setup(tmp_path, n=8, scenes=1)
    plan("structure", {}, P, L)
    out = REGISTRY["structure"].observe(L.module("structure"), [ev("scene_outcome", None, "ch02", 1, result="yes")],
                                        {"chapter": "ch02", "scene": 1}, L, P)
    assert out and out[0]["action"] == "revise" and out[0]["check"] == "scene_shape"


# ---------------------------------------------------------------- foreshadow
def test_payoff_window_autofix_and_checks(tmp_path):
    P, L = setup(tmp_path, n=10)
    _, data, notes = plan("foreshadow", {"items": [{"id": "gun", "element": "the brass key", "plant_ch": "ch05",
                                                     "payoff_ch": "ch06", "importance": "critical"}]}, P, L)
    assert data["items"][0]["plant_ch"] == "ch04" and notes        # moved earlier to respect min gap 2
    f = data["items"][0]
    ok = [ev("plant", "gun", "ch04"), ev("payoff", "gun", "ch06")]
    assert not check("foreshadow", ok, P, L)
    late = [ev("plant", "gun", "ch04"), ev("payoff", "gun", "ch10")]
    assert checks_named(check("foreshadow", late, P, L), "payoff_window")
    nosetup = [ev("payoff", "gun", "ch06")]
    assert checks_named(check("foreshadow", nosetup, P, L), "payoff_without_setup")[0]["severity"] == "critical"
    unpaid = [ev("plant", "gun", "ch04")]
    assert "never paid off" in check("foreshadow", unpaid, P, L)[0]["description"]
    assert f["window"][0] <= "ch06" <= f["window"][1]


def test_outline_setups_become_foreshadow_items(tmp_path):
    P, L = setup(tmp_path, n=4)
    P.book.chapters[0]["setups"] = ["the brass key under the floorboard"]
    P.book.chapters[3]["payoffs"] = ["the brass key under the floorboard"]
    _, data, _ = plan("foreshadow", {}, P, L)
    assert data["items"][0]["plant_ch"] == "ch01" and data["items"][0]["payoff_ch"] == "ch04"


def test_spotlighted_clue_and_unset_payoff_observed(tmp_path):
    P, L = setup(tmp_path, n=6)
    plan("foreshadow", {"items": [{"id": "gun", "element": "rifle", "plant_ch": "ch01", "payoff_ch": "ch05"}]}, P, L)
    m = REGISTRY["foreshadow"]
    out = m.observe(L.module("foreshadow"), [ev("plant", "gun", "ch01", 1, overt=True)], {"chapter": "ch01", "scene": 1}, L, P)
    assert out[0]["check"] == "spotlight" and out[0]["action"] == "revise"
    out = m.observe(L.module("foreshadow"), [ev("payoff", "gun", "ch03")], {"chapter": "ch03", "scene": 1}, L, P)
    assert out[0]["check"] == "payoff_without_setup" and out[0]["action"] == "review"


# ---------------------------------------------------------------- arcs
ARC = {"arcs": [{"character": "finn", "lie": "I am only worth what I fix", "truth": "I am enough", "arc_type": "positive"}]}


def test_arc_stage_order_plan_and_audit(tmp_path):
    P, L = setup(tmp_path, n=10)
    raw = {"arcs": [{**ARC["arcs"][0], "stages": [{"name": "first_doubt", "chapter": "ch07"},
                                                  {"name": "midpoint_glimpse", "chapter": "ch03"}]}]}
    _, data, notes = plan("arcs", raw, P, L)
    chs = [s["chapter"] for s in data["arcs"][0]["stages"]]
    assert chs == sorted(chs) and notes                               # autofix reorders
    stages = [s["name"] for s in data["arcs"][0]["stages"]]
    events = [ev("arc_stage", f"finn:{s}", c) for s, c in zip(stages, chs)]
    assert not check("arcs", events, P, L)
    swapped = list(events)
    swapped[1], swapped[2] = ev("arc_stage", "finn:first_doubt", chs[2]), ev("arc_stage", "finn:midpoint_glimpse", chs[1])
    assert checks_named(check("arcs", swapped, P, L), "arc_order")
    missing = [e for e in events if not e["ref"].endswith("tested_choice")]
    assert checks_named(check("arcs", missing, P, L), "arc_stage")[0]["severity"] == "major"


def test_arc_out_of_order_scene_gets_revision_task(tmp_path):
    P, L = setup(tmp_path, n=10)
    plan("arcs", ARC, P, L)
    out = REGISTRY["arcs"].observe(L.module("arcs"), [ev("arc_stage", "finn:dark_night", "ch03")],
                                   {"chapter": "ch03", "scene": 1}, L, P)
    assert out and out[0]["check"] == "arc_order" and out[0]["action"] == "revise"


# ---------------------------------------------------------------- promises
def test_promise_progress_per_act_and_genre_in_opening(tmp_path):
    P, L = setup(tmp_path, n=12)
    m, data, _ = plan("promises", {"promises": [{"id": "g", "kind": "genre", "statement": "a heist", "made_ch": "ch05",
                                                 "payoff_ch": "ch12"},
                                                {"id": "q", "kind": "plot", "statement": "will they escape",
                                                 "made_ch": "ch01", "payoff_ch": "ch12"}]}, P, L)
    assert data["promises"][0]["made_ch"] == "ch01"            # genre forced to the opening
    planned = m.planned_events(data, P)
    assert not check("promises", planned, P, L, "plan")
    no_act2 = [e for e in planned if not (e["kind"] == "promise_progress" and P.book.act_of(e["ch"]) == 2 and e["ref"] == "q")]
    assert any("act 2" in i["description"] for i in check("promises", no_act2, P, L))
    late_genre = [e if not (e["ref"] == "g" and e["kind"] == "promise_made") else {**e, "ch": "ch04"} for e in planned]
    assert any("not the opening" in i["description"] for i in check("promises", late_genre, P, L))


# ---------------------------------------------------------------- scheduler cap
def test_scheduler_respects_configurable_cap(tmp_path):
    P, L = setup(tmp_path, n=3, scenes=2)
    evs = [{"kind": "payoff", "ref": f"x{i}", "ch": "ch02"} for i in range(7)]
    w = engine.schedule([dict(e) for e in evs], P, L)
    assert w and w[0]["check"] == "scene_cap"
    config.set_config({"craft": {"max_key_items_per_scene": 4}})
    assert not engine.schedule([dict(e) for e in evs], P, L)
    s = [dict(e) for e in evs[:4]]
    engine.schedule(s, P, L)
    assert sorted(e["scene"] for e in s) == [1, 1, 2, 2]       # spread, not stacked


def test_setups_scheduled_before_payoffs_within_chapter(tmp_path):
    P, L = setup(tmp_path, n=2, scenes=3)
    s = [{"kind": "payoff", "ref": "a", "ch": "ch01"}, {"kind": "plant", "ref": "b", "ch": "ch01"}]
    engine.schedule(s, P, L)
    E = Events(s, P.book)
    assert E.first("plant")["scene"] < E.first("payoff")["scene"]
