"""Canon extract -> integrate -> deconflict -> resolve, feed-forward, Chekhov ledger, audit."""
import re

from click.testing import CliRunner

from bookforge.cli import cli
from bookforge.core import canon_pipeline, progress
from bookforge.core.canon import Canon


def setup_plan(ctx):
    for s in ("brainstorm", "outline", "initialize_cast_and_world"):
        progress.run_step(ctx, s)


def scene_of(prompt, rx):
    m = re.search(rx, prompt)
    return (m.group(1), int(m.group(2))) if m else (None, None)


def test_seeded_canon_and_entities(project, fake):
    setup_plan(project)
    c = project.canon()
    assert {"finn_cooper", "zara_blackwind", "elias_thorne", "harbor"} <= set(c.entities)
    assert any(f["type"] == "secret" and f["status"] == "planned" for f in c.facts.values())
    assert any(t["description"] == "the brass key under the floorboard" and t.get("payoff_by") == "ch03"
               for t in c.data["threads"].values())


def test_code_checks_flag_unknown_names_and_pov(project, fake):
    setup_plan(project)
    text = ("I walked to the dock. I saw Elara there. I said hello to Elara. I waved at Elara and I left. "
            "I ran. I hid. I waited. I slept. I woke. I ate.")
    chk = canon_pipeline.code_checks(text, {"person": "third"}, project.canon())
    assert [u["name"] for u in chk["unknown_names"]] == ["Elara"]
    assert chk["pov_issue"]


def test_conflict_plan_wins_triggers_targeted_revision(project, fake):
    setup_plan(project)
    def write(p, s):
        m = re.search(r"of scene (\d+) of chapter (ch\d\d)", p); ch, sc = (m.group(2), int(m.group(1))) if m else (None, None)
        if (ch, sc) == ("ch01", 1):
            return "Zara Blackwind worked as the ship's cook and stirred the pot of beans.\n\nFinn Cooper checked the boiler."

    def extract(p, s):
        ch, sc = scene_of(p, r"SCENE TEXT \((ch\d\d) scene (\d+)\)")
        if (ch, sc) != ("ch01", 1):
            return None
        job = "mechanic" if "ship's mechanic" in p else "cook"
        return {"facts": [{"type": "character_trait", "entities": ["zara_blackwind"], "claim": f"Zara is the ship's {job}",
                           "quote": f"Zara Blackwind worked as the ship's {job}", "confidence": 0.95, "importance": "major"}]}

    def deconflict(p, s):
        if "Zara is the ship's cook" in p:
            return {"verdicts": [{"fact_index": 0, "verdict": "conflict", "conflicts_with": ["plan:characters"],
                                  "explanation": "Plan makes Zara the engineer who teaches Finn",
                                  "fix": "Make Zara the ship's mechanic, not the cook", "confidence": 0.9}]}

    fake.hooks.update(write=write, extract=extract, deconflict=deconflict)
    progress.run_step(project, "write", "ch01")
    c = project.canon()
    tasks = c.open_revision_tasks("ch01")
    assert len(tasks) == 1 and tasks[0]["source"]["scene"] == 1 and "mechanic" in tasks[0]["fix"]
    assert any(f["claim"] == "Zara is the ship's cook" and f["status"] == "contradicted" for f in c.facts.values())

    progress.run_step(project, "critique", "ch01")
    progress.run_step(project, "revise", "ch01")
    revised = project.chapter_path("ch01", "revised").read_text()
    assert "ship's mechanic" in revised and "ship's cook" not in revised
    assert "Finn Cooper checked the boiler." in revised            # untouched paragraph kept verbatim
    c = Canon(project.state_dir)
    assert not c.open_revision_tasks("ch01")
    assert [t["status"] for t in c.data["revision_tasks"]] == ["applied"]
    assert not any(f["claim"] == "Zara is the ship's cook" for f in c.facts.values())   # retracted on re-extract
    assert any(f["claim"] == "Zara is the ship's mechanic" and f["status"] == "established" for f in c.facts.values())


def test_conflict_canon_wins_amends_plan_and_feeds_forward(project, fake):
    setup_plan(project)
    c0 = project.canon()
    old = c0.add_fact("character_trait", ["elias_thorne"], "Elias Thorne has two flesh arms", "planned", "major",
                      origin="plan")
    c0.save()

    def extract(p, s):
        ch, sc = scene_of(p, r"SCENE TEXT \((ch\d\d) scene (\d+)\)")
        if (ch, sc) == ("ch01", 2):
            return {"facts": [{"type": "character_trait", "entities": ["elias_thorne"],
                               "claim": "Elias Thorne has a mechanical left arm", "quote": "the deck creaked under every step",
                               "confidence": 0.9, "importance": "major"}]}

    def deconflict(p, s):
        if "mechanical left arm" in p.split("NEW FACTS:")[1]:
            return {"verdicts": [{"fact_index": 0, "verdict": "conflict", "conflicts_with": [old["id"]],
                                  "explanation": "A mechanical arm gives the antagonist a vivid weakness",
                                  "new_is_better": True, "confidence": 0.9,
                                  "plan_updates": [{"chapter": "ch02", "action": "add_beat",
                                                    "new": "Elias's mechanical arm seizes in the storm"},
                                                   {"chapter": "ch01", "action": "add_beat", "new": "ignored: past"}]}]}

    fake.hooks.update(extract=extract, deconflict=deconflict)
    progress.run_step(project, "write", "ch01")
    c = project.canon()
    amend = [a for a in c.data["amendments"] if a["kind"] == "canon_wins"]
    assert len(amend) == 1 and amend[0]["supersedes"] == [old["id"]]
    assert c.facts[old["id"]]["status"] == "retired" and c.facts[old["id"]]["superseded_by"] == amend[0]["fact"]
    assert not c.open_revision_tasks("ch01")
    ch2 = project.chapter("ch02")
    assert "Elias's mechanical arm seizes in the storm" in ch2["key_events"] and ch2["amendments"]
    assert "ignored: past" not in project.chapter("ch01")["key_events"]
    assert any("mechanical left arm" in n for n in project.load_characters()["elias_thorne"]["canon_notes"])
    # feed-forward: the new canon reaches the writer for ch02's Elias scene
    progress.run_step(project, "write", "ch02")
    ch2_prompts = [p for r, p, _ in fake.calls if r == "write" and "of chapter ch02" in p]
    assert any("mechanical left arm" in p and "the story pack wins" in p for p in ch2_prompts)  # demoted, still fed forward


def test_high_stakes_conflict_goes_to_review_queue_and_cli(project, fake):
    setup_plan(project)

    def extract(p, s):
        ch, sc = scene_of(p, r"SCENE TEXT \((ch\d\d) scene (\d+)\)")
        if (ch, sc) == ("ch01", 1):
            return {"facts": [{"type": "secret", "entities": ["elias_thorne"], "claim": "Elias is Finn's father",
                               "quote": "the deck creaked", "confidence": 0.8, "importance": "critical"}]}

    def deconflict(p, s):
        if "Finn's father" in p:
            return {"verdicts": [{"fact_index": 0, "verdict": "conflict", "conflicts_with": [],
                                  "explanation": "Not in plan; changes the whole arc", "new_is_better": True,
                                  "confidence": 0.7}]}

    fake.hooks.update(extract=extract, deconflict=deconflict)
    progress.run_step(project, "write", "ch01")
    q = project.canon().pending_reviews()
    assert len(q) == 1 and q[0]["why_review"] == "high-stakes conflict"
    r = CliRunner().invoke(cli, ["review", "list", "--project", str(project.project_dir)], obj={})
    assert "Finn's father" in r.output
    r = CliRunner().invoke(cli, ["review", "resolve", "--project", str(project.project_dir), q[0]["id"],
                                 "--decision", "plan", "--fix", "Remove the paternity hint"], obj={})
    assert r.exit_code == 0, r.output
    c = project.canon()
    assert not c.pending_reviews() and c.open_revision_tasks("ch01")[0]["fix"] == "Remove the paternity hint"


def test_extract_is_idempotent(project, fake):
    setup_plan(project)
    ch = project.chapter("ch01")
    sc = ch["scenes"][0]
    text = "Finn Cooper checks the boiler. The wind came hard off the river and the deck creaked under every step."
    canon_pipeline.process_scene(project, "ch01", sc, text)
    n1 = len(project.canon().facts)
    canon_pipeline.process_scene(project, "ch01", sc, text)
    c = project.canon()
    assert len(c.facts) == n1
    assert c.data["continuity"]["finn_cooper"]["location"] == "deck ch01"
    assert len(c.data["continuity"]["finn_cooper"]["history"]) == 1
    assert c.data["version"] >= 3 and (project.state_dir / "canon_history").exists()


def test_quote_not_in_text_lowers_confidence(project, fake):
    setup_plan(project)
    fake.hooks["extract"] = lambda p, s: {"facts": [{"claim": "x", "quote": "never written", "confidence": 1.0}]}
    res = canon_pipeline.extract("some text", {}, {"chapter": "ch01", "scene": 1}, project.canon(),
                                 {"unknown_names": []})
    assert res.facts[0].confidence == 0.6


def test_chekhov_ledger_and_audit(project, fake):
    progress.run_all_with_progress(project)
    c = project.canon()
    t = next(t for t in c.data["threads"].values() if "brass key" in t["description"])
    assert t["status"] == "paid_off" and t["payoff_source"]["chapter"] == "ch03"
    assert project.load_summaries()["chapters"].keys() == {"ch01", "ch02", "ch03"}


def test_audit_blocks_finalize_on_unpaid_critical_setup(project, fake):
    setup_plan(project)
    fake.hooks["extract"] = lambda p, s: ({"facts": [], "threads_opened": [
        {"description": "the bomb in the hold", "importance": "critical"}]} if "(ch01 scene 1)" in p else None)
    progress.run_all_with_progress(project)
    audit = progress.read_json(project.state_dir / "audit.json")
    assert not audit["passed"] and any("bomb in the hold" in i["description"] for i in audit["issues"])
    assert not (project.project_dir / "final_manuscript.txt").exists()
