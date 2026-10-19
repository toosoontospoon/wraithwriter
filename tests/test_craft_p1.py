"""P1 craft: mystery + who-knows-what, subplots, magic (Sanderson's laws), rolling refinement."""
import json

from bookforge.core import progress
from bookforge.core.canon_pipeline import resolve_review
from bookforge.core.context import BookContext
from bookforge.craft import REGISTRY, engine
from craft_fake import P1_PLANS, install
from craft_helpers import check, checks_named, ev, plan, setup

MYS = {"applies": True, "main_truth": "t1", "culprit": "elias",
       "truths": [{"id": "t1", "statement": "Elias poisoned the well", "reveal_ch": "ch08", "revealed_to": ["finn"]},
                  {"id": "t2", "statement": "Zara is Finn's sister", "reveal_ch": "ch06"}],
       "clues": [{"id": "c1", "points_to": "t1", "description": "bitter smell", "plant_ch": "ch02"},
                 {"id": "c2", "points_to": "t1", "description": "missing key", "plant_ch": "ch07"}],
       "red_herrings": [{"id": "h1", "false_claim": "Zara did it", "plant_ch": "ch03", "cleared_ch": "ch09"}],
       "suspects": [{"entity": "elias", "first_appear_ch": "ch05"}],
       "knowledge": [{"who": "zara", "truth": "t1", "learns_ch": "ch05"}]}


def _mys(tmp_path):
    P, L = setup(tmp_path, n=10)
    m, data, notes = plan("mystery", MYS, P, L)
    return P, L, m, data, notes


# ---------------------------------------------------------------- mystery planning
def test_mystery_plan_autofix_fair_play(tmp_path):
    P, L, m, data, notes = _mys(tmp_path)
    c2 = next(c for c in data["clues"] if c["id"] == "c2")
    assert c2["plant_ch"] == "ch06"                                   # >= 2 chapters before ch08 reveal
    assert data["red_herrings"][0]["cleared_ch"] == "ch08"            # cleared by the main reveal
    assert data["suspects"][0]["first_appear_ch"] <= "ch03"           # culprit in first quarter
    assert not [i for i in check("mystery", m.planned_events(data, P), P, L, "plan") if i["severity"] != "minor"]


def test_no_mystery_means_no_checks(tmp_path):
    P, L = setup(tmp_path)
    m, data, _ = plan("mystery", {"applies": False}, P, L)
    assert data == {} and m.planned_events(data, P) == [] and check("mystery", [], P, L) == []


def test_audit_clue_before_reveal_culprit_early_herring_cleared(tmp_path):
    P, L, m, data, _ = _mys(tmp_path)
    good = m.planned_events(data, P)
    assert not [i for i in check("mystery", good, P, L) if i["severity"] != "minor"]
    late_clue = [e if not (e["kind"] == "clue" and e["ref"] == "c2") else {**e, "ch": "ch09"} for e in good]
    assert checks_named(check("mystery", late_clue, P, L), "fair_play")[0]["severity"] == "critical"
    late_culprit = [e if e["kind"] != "appear" else {**e, "ch": "ch07"} for e in good]
    assert any("first quarter" in i["description"] for i in check("mystery", late_culprit, P, L))
    uncleared = [e for e in good if e["kind"] != "herring_cleared"]
    assert checks_named(check("mystery", uncleared, P, L), "red_herring")


def test_who_knows_what_leaks(tmp_path):
    P, L, m, data, _ = _mys(tmp_path)
    good = m.planned_events(data, P)
    early = good + [ev("reveal", "t2", "ch03")]
    assert any(i["check"] == "knowledge_leak" and "t2" in i["description"] for i in check("mystery", early, P, L))
    acts = good + [ev("knows", "t1", "ch03", 1, who="zara", mode="acts_on")]
    assert any(i["check"] == "knowledge_leak" and "zara acts on t1" in i["description"] for i in check("mystery", acts, P, L))
    later = good + [ev("knows", "t1", "ch06", 1, who="zara", mode="acts_on")]   # learned ch05 per plan
    assert not checks_named(check("mystery", later, P, L), "knowledge_leak")


def test_mystery_observe_actions(tmp_path):
    P, L, m, data, _ = _mys(tmp_path)
    src = {"chapter": "ch04", "scene": 1}
    out = m.observe(data, [ev("reveal", "t1", "ch04")], src, L, P)
    assert out[0]["check"] == "reveal_before_setup" and out[0]["action"] == "revise"
    L.add_realized(ev("clue", "c1", "ch02"))
    L.add_realized(ev("clue", "c2", "ch03"))
    out = m.observe(data, [ev("reveal", "t1", "ch04")], src, L, P)
    assert out[0]["check"] == "early_leak" and out[0]["action"] == "review" and out[0]["amend"]["move"]["ch"] == "ch04"
    out = m.observe(data, [ev("knows", "t1", "ch04", 1, who="finn", mode="acts_on")], src, L, P)
    assert out[0]["check"] == "knowledge_leak"


def test_brief_has_must_not_reveal_and_pov_ignorance(tmp_path):
    P, L, m, data, _ = _mys(tmp_path)
    P.book.chapters[3]["scenes"][0]["pov"] = "Finn"
    lines = m.brief(data, [], "ch04", 1, L, P)
    assert any(x.startswith("MUST NOT REVEAL") and "Elias poisoned the well" in x for x in lines)
    assert any("Finn DOES NOT KNOW" in x for x in lines)


def test_early_leak_canon_wins_moves_planned_reveal(project, fake):
    install(fake, project).plans.update(P1_PLANS)
    progress.run_all_with_progress(project, until="outline")
    ctx = BookContext(project.project_dir)
    cn = ctx.canon()
    q = cn.enqueue_review({"fact": {"type": "plot_point", "entities": [], "claim": "leak", "quote": "", "confidence": 0.5,
                                    "importance": "critical"}, "source": {"chapter": "ch02", "scene": 1}, "explanation": "leak",
                           "conflicts_with": [], "why_review": "craft: early_leak",
                           "craft": {"module": "mystery", "check": "early_leak", "ref": "t1",
                                     "move": {"kind": "reveal", "ref": "t1", "ch": "ch02", "scene": 1}}})
    cn.save("t")
    resolve_review(ctx, q["id"], "canon")
    L = engine.ledger(ctx)
    assert next(e for e in L.data["planned"] if e["kind"] == "reveal" and e["ref"] == "t1")["ch"] == "ch02"
    assert L.data["amendments"]


# ---------------------------------------------------------------- subplots
SUB = {"threads": [{"id": "a", "label": "A", "mice_type": "E", "description": "war", "open_ch": "ch01", "close_ch": "ch10",
                    "max_gap": 2, "convergences": [{"id": "cv1", "ch": "ch05", "with": "b", "supplies": "army"}]},
                   {"id": "b", "label": "B", "mice_type": "C", "description": "love", "open_ch": "ch02", "close_ch": "ch09",
                    "parent": "a", "max_gap": 3}]}


def test_subplot_plan_braids_within_gap(tmp_path):
    P, L = setup(tmp_path, n=10)
    m, data, _ = plan("subplots", SUB, P, L)
    assert not check("subplots", m.planned_events(data, P), P, L, "plan")


def test_subplot_gap_unresolved_convergence_nesting(tmp_path):
    P, L = setup(tmp_path, n=10)
    m, data, _ = plan("subplots", SUB, P, L)
    evs = m.planned_events(data, P)
    gappy = [e for e in evs if not (e["ref"] == "a" and e["kind"] == "thread_touch" and "ch02" <= e["ch"] <= "ch08")]
    assert checks_named(check("subplots", gappy, P, L), "subplot_gap")
    unres = [e for e in evs if not (e["ref"] == "b" and e["kind"] == "thread_resolved")]
    assert checks_named(check("subplots", unres, P, L), "subplot_unresolved")
    noconv = [e for e in evs if e["kind"] != "convergence"]
    assert checks_named(check("subplots", noconv, P, L), "convergence")
    badnest = [e if not (e["ref"] == "b" and e["kind"] == "thread_resolved") else {**e, "ch": "ch10", "scene": 2} for e in evs]
    assert checks_named(check("subplots", badnest, P, L), "mice_nesting")[0]["severity"] == "minor"


# ---------------------------------------------------------------- magic
MAG = {"applies": True, "hardness": 0.9,
       "rules": [{"id": "r1", "kind": "power", "text": "bind metal", "shown_ch": "ch09"},
                 {"id": "r2", "kind": "cost", "text": "burns memory", "applies_to": ["r1"], "shown_ch": "ch02"},
                 {"id": "r3", "kind": "power", "text": "fly", "shown_ch": "ch08"}],
       "solutions": [{"id": "s1", "ch": "ch10", "description": "bind the gate", "rules": ["r1"], "cost": "r2"}]}


def test_magic_plan_law1_autofix_and_law2_law3(tmp_path):
    P, L = setup(tmp_path, n=10)
    m, data, notes = plan("magic", MAG, P, L)
    assert m._rule(data, "r1")["shown_ch"] == "ch09" or notes      # ch09 is already 1 chapter before ch10
    issues = check("magic", m.planned_events(data, P), P, L, "plan")
    assert checks_named(issues, "magic_law2")[0]["ref"] == "r3"      # power with no limit
    assert any(i["check"] == "magic_law3" and i["ref"] in ("r1", "r3") for i in issues)   # late powers


def test_magic_audit_deus_ex_and_costs(tmp_path):
    P, L = setup(tmp_path, n=10)
    m, data, _ = plan("magic", MAG, P, L)
    use = ev("magic_use", "s1", "ch05", 1, rules=["r1"], at_conflict=True, cost_paid=False)
    issues = check("magic", [ev("rule_shown", "r2", "ch02"), use], P, L)
    assert checks_named(issues, "magic_law1")[0]["severity"] == "critical"
    assert any("costs never bite" in i["description"] for i in issues)


def test_magic_observe_deus_ex_goes_to_review_and_soft_magic(tmp_path):
    P, L = setup(tmp_path, n=10)
    m, data, _ = plan("magic", MAG, P, L)
    out = m.observe(data, [ev("magic_use", "x", "ch04", 1, rules=["r1"], at_conflict=True)], {"chapter": "ch04", "scene": 1}, L, P)
    assert out[0]["check"] == "magic_law1" and out[0]["action"] == "review"
    data["hardness"] = 0.2
    L.add_realized(ev("rule_shown", "r1", "ch02"))
    out = m.observe(data, [ev("magic_use", "x", "ch04", 1, rules=["r1"], at_conflict=True)], {"chapter": "ch04", "scene": 1}, L, P)
    assert [o["check"] for o in out] == ["magic_law1"] and out[0]["severity"] == "major"
    out = m.observe(data, [ev("magic_use", "x", "ch09", 1, new_power=True)], {"chapter": "ch09", "scene": 1}, L, P)
    assert out[0]["check"] == "magic_law3"


# ---------------------------------------------------------------- end to end with P1 ledgers
def test_e2e_with_mystery_subplots_magic_passes(project, fake):
    install(fake, project).plans.update(P1_PLANS)
    progress.run_all_with_progress(project)
    ctx = BookContext(project.project_dir)
    audit = json.loads((ctx.state_dir / "audit.json").read_text())
    serious = [i for i in audit["issues"] if i.get("craft") and i["severity"] != "minor"]
    assert serious == [] and audit["passed"]
    prompts = [p for r, p, _ in fake.calls if r == "write"]
    assert any("PLANT CLUE: grease on Elias's cuff" in p for p in prompts)
    assert any("MUST NOT REVEAL" in p for p in prompts)
    assert any("MAGIC SOLUTION" in p for p in prompts)
    assert any(f["claim"] == "Elias sabotaged the engine" for f in ctx.canon().facts.values())   # seeded secret


def test_e2e_early_leak_queues_review_and_fails_audit(project, fake):
    cs = install(fake, project)
    cs.plans.update(P1_PLANS)
    cs.extra["ch01:1"] = [{"kind": "reveal", "ref": "t1", "quote": ""}]
    progress.run_all_with_progress(project, until="audit")
    ctx = BookContext(project.project_dir)
    assert any(q.get("craft") for q in ctx.canon().pending_reviews()) or \
        any(t["reason"] == "craft: reveal_before_setup" for t in ctx.canon().data["revision_tasks"])
    audit = json.loads((ctx.state_dir / "audit.json").read_text())
    assert not audit["passed"]


def test_rolling_refine_updates_next_chapter_once(project, fake):
    cs = install(fake, project)

    def refine(p, s):
        if "ROLLING REFINE" in p:
            nxt = p.split("next chapter ", 1)[1].split(" ", 1)[0]
            return {"cards": [{"scene": 1, "goal": f"refined goal for {nxt}"}]}
        return cs.plan(p, s)
    fake.hooks["craft_plan"] = refine
    progress.run_all_with_progress(project, until="write")
    L = engine.ledger(BookContext(project.project_dir))
    assert L.card("ch02", 1)["goal"] == "refined goal for ch02" and "ch02" in L.data["refined"]
    n = fake.count("craft_plan", "ROLLING REFINE")
    assert engine.refine_next(BookContext(project.project_dir), "ch01") is False and fake.count("craft_plan", "ROLLING REFINE") == n
