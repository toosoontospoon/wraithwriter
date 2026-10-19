"""P2 craft: relationships, iceberg pacing, prophecy verbatim, mentor, fair trope subversion, CLI/views, full e2e."""
import json

from click.testing import CliRunner

from bookforge.cli import cli
from bookforge.core import progress
from bookforge.core.context import BookContext
from bookforge.craft import REGISTRY, engine
from craft_fake import P1_PLANS, P2_PLANS, install
from craft_helpers import check, checks_named, ev, plan, setup


def test_relationship_trajectory_and_jump(tmp_path):
    P, L = setup(tmp_path, n=6)
    m, data, _ = plan("relationships", {"relationships": [{"id": "fz", "a": "finn", "b": "zara", "stages": [
        {"name": "friction", "chapter": "ch02", "trust_delta": -1}, {"name": "bond", "chapter": "ch05", "trust_delta": 3}]}]}, P, L)
    assert checks_named(check("relationships", m.planned_events(data, P), P, L, "plan"), "relationship_jump")
    assert not check("relationships", m.planned_events(data, P), P, L)
    off = [ev("trust_shift", "fz", "ch02", 1, delta=-1)]
    assert checks_named(check("relationships", off, P, L), "relationship_trajectory")
    out = m.observe(data, [ev("trust_shift", "fz", "ch03", 1, delta=4)], {"chapter": "ch03", "scene": 1}, L, P)
    assert out[0]["action"] == "revise"


def test_iceberg_hint_before_explain_and_scene_cap(tmp_path):
    P, L = setup(tmp_path, n=6)
    m, data, notes = plan("world_reveal", {"elements": [{"id": "w1", "element": "the Veil", "first_hint_ch": "ch04",
                                                         "first_explain_ch": "ch03"}]}, P, L)
    assert data["elements"][0]["first_hint_ch"] == "ch02" and notes
    assert checks_named(check("world_reveal", [ev("explain", "w1", "ch03")], P, L), "iceberg")
    dump = [ev("explain", f"x{i}", "ch05", 1) for i in range(4)]
    assert checks_named(check("world_reveal", dump, P, L), "info_dump")
    assert m.observe(data, dump, {"chapter": "ch05", "scene": 1}, L, P)[0]["check"] == "info_dump"


def test_prophecy_wording_kept_verbatim(tmp_path):
    P, L = setup(tmp_path, n=6)
    m, data, _ = plan("prophecy", P2_PLANS["prophecy, oracle"], P, L)
    canon_text = data["prophecies"][0]["text"]
    e = L.add_realized(ev("prophecy_quote", "pr1", "ch01") | {"quote": "When iron sings the sky will fall"})
    out = m.observe(data, [e], {"chapter": "ch01", "scene": 1}, L, P)
    assert out[0]["action"] == "autofix" and out[0]["replace"] == ("When iron sings the sky will fall", canon_text)
    drift = [ev("prophecy_quote", "pr1", "ch02") | {"quote": "When iron weeps, the sky shall fall."},
             ev("false_reading", "pr1", "ch02"), ev("fulfillment", "pr1", "ch05")]
    assert checks_named(check("prophecy", drift, P, L), "prophecy_wording")[0]["severity"] == "critical"
    nofalse = [ev("fulfillment", "pr1", "ch05")]
    assert any("false reading" in i["description"] for i in check("prophecy", nofalse, P, L))


def test_mentor_lessons_and_mentor_solves(tmp_path):
    P, L = setup(tmp_path, n=6)
    m, data, _ = plan("mentor", P2_PLANS["mentor figure"], P, L)
    assert checks_named(check("mentor", [ev("lesson_applied", "l1", "ch01")], P, L), "mentor_lesson")
    assert checks_named(check("mentor", [ev("mentor_solves", None, "ch03")], P, L), "mentor_solves")


def test_fair_subversion(tmp_path):
    P, L = setup(tmp_path, n=9)
    m, data, _ = plan("tropes", {"tropes": [{"id": "t", "trope": "Chosen One", "treatment": "subvert",
                                             "setup_ch": "ch02", "turn_ch": "ch07"}]}, P, L)
    assert not checks_named(check("tropes", [ev("trope_setup", "t", "ch02"), ev("trope_turn", "t", "ch07")], P, L),
                            "trope_subversion")
    assert checks_named(check("tropes", [ev("trope_turn", "t", "ch07")], P, L), "trope_subversion")
    assert any("act(s)" in i["description"] for i in check("tropes", m.planned_events(data, P), P, L, "plan"))
    _, d2, _ = plan("tropes", {"tropes": [{"id": "s", "trope": "Dark Lord", "treatment": "straight"}]}, P, L)
    assert checks_named(check("tropes", [], P, L, "plan"), "trope_homogeneity")


def test_e2e_all_tiers_pass_and_prophecy_autofix_in_manuscript(project, fake):
    cs = install(fake, project)
    cs.plans.update(P1_PLANS)
    cs.plans.update(P2_PLANS)
    base_write = fake.r_write

    def write(p, s):
        if "of scene 1 of chapter ch01" in p:
            return base_write(p, s, False) + '\n\nThe old words: "When iron sings the sky will fall."'
        return None
    fake.hooks["write"] = write
    cs.extra["ch01:1"] = [{"kind": "prophecy_quote", "ref": "pr1", "quote": "When iron sings the sky will fall."}]
    progress.run_all_with_progress(project)
    ctx = BookContext(project.project_dir)
    assert "When iron sings, the sky shall fall." in ctx.scene_path("ch01", 1).read_text()
    assert "When iron sings the sky will fall" not in ctx.scene_path("ch01", 1).read_text()
    audit = json.loads((ctx.state_dir / "audit.json").read_text())
    serious = [i for i in audit["issues"] if i.get("craft") and i["severity"] != "minor"]
    assert serious == [], serious
    assert audit["passed"]
    L = engine.ledger(ctx)
    assert set(L.data["modules"]) >= {"structure", "promises", "arcs", "foreshadow", "mystery", "subplots", "magic",
                                      "relationships", "world_reveal", "prophecy", "mentor", "tropes"}


def test_cli_craft_commands(project, fake):
    install(fake, project).plans.update(P1_PLANS)
    progress.run_all_with_progress(project, until="write")
    r = CliRunner()
    p = str(project.project_dir)
    out = r.invoke(cli, ["craft", "show", "--project", p])
    assert out.exit_code == 0 and "framework: save_the_cat" in out.output
    out = r.invoke(cli, ["craft", "show", "--project", p, "--ledger", "mystery"])
    assert "grease" in out.output
    out = r.invoke(cli, ["craft", "timeline", "--project", p])
    assert out.exit_code == 0 and "clue:c1" in out.output and "X" in out.output
    out = r.invoke(cli, ["craft", "timeline", "--project", p, "--tension"])
    assert "ch01:1" in out.output
    out = r.invoke(cli, ["craft", "check", "--project", p])
    assert out.exit_code == 0 and "PLAN CHECKS" in out.output and "REALIZED" in out.output
    out = r.invoke(cli, ["craft", "plan", "--project", p, "--framework", "three_act"])
    assert out.exit_code == 0 and engine.ledger(BookContext(project.project_dir)).data["framework"] == "three_act"


def test_disabled_craft_is_a_no_op(project, fake):
    from bookforge import config
    config.set_config({"llm": {"json_retries": 1}, "craft": {"enabled": False}})
    progress.run_all_with_progress(project)
    assert fake.count("craft_plan") == 0 and fake.count("craft_extract") == 0, [p[:80] for r, p, _ in fake.calls if r.startswith("craft")]
    assert not engine.ledger(BookContext(project.project_dir)).has_plan


def test_modules_can_be_limited_by_config(project, fake):
    from bookforge import config
    config.set_config({"llm": {"json_retries": 1}, "craft": {"modules": ["structure", "foreshadow"]}})
    install(fake, project)
    progress.run_all_with_progress(project, until="outline")
    assert set(engine.ledger(BookContext(project.project_dir)).data["modules"]) == {"structure", "foreshadow"}
