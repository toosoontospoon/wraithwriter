"""End-to-end fake runs with the craft layer, plus resumability."""
import json

import pytest

from bookforge.core import progress
from bookforge.core.context import BookContext
from bookforge.craft import engine
from craft_fake import install


def test_full_fake_run_with_craft_passes_audit(project, fake):
    install(fake, project)
    progress.run_all_with_progress(project)
    ctx = BookContext(project.project_dir)
    L = engine.ledger(ctx)
    assert L.has_plan and L.data["framework"] == "save_the_cat" and L.data["scene_cards"]
    assert any("CRAFT BRIEF" in p and "SHAPE:" in p for r, p, _ in fake.calls if r == "write")
    assert any("ARC BEAT" in p for r, p, _ in fake.calls if r == "write")
    audit = json.loads((ctx.state_dir / "audit.json").read_text())
    craft_serious = [i for i in audit["issues"] if i.get("craft") and i["severity"] != "minor"]
    assert craft_serious == [], craft_serious
    assert audit["passed"] and (ctx.project_dir / "final_manuscript.txt").exists()
    assert L.data["realized"]


def test_writer_that_skips_arc_beat_fails_craft_audit(project, fake):
    cs = install(fake, project)
    cs.drop.add(("arc_stage", "finn_cooper:tested_choice"))
    progress.run_all_with_progress(project, until="audit")
    audit = json.loads((BookContext(project.project_dir).state_dir / "audit.json").read_text())
    assert any(i.get("craft") and i["check"] == "arc_stage" and "tested_choice" in i["description"] for i in audit["issues"])


def test_scene_shape_violation_becomes_revision_task(project, fake):
    cs = install(fake, project)
    cs.outcome["ch01:1"] = "yes"
    progress.run_all_with_progress(project, until="write")
    ctx = BookContext(project.project_dir)
    card = engine.ledger(ctx).card("ch01", 1)
    tasks = [t for t in ctx.canon().data["revision_tasks"] if t["reason"].startswith("craft:")]
    if card["scene_type"] == "scene":
        assert any(t["reason"] == "craft: scene_shape" for t in tasks)


def test_resume_after_crash_in_craft_pass(project, fake):
    cs = install(fake, project)
    state = {"boom": True}
    real = cs.extract

    def flaky(p, s):
        if "(ch01 scene 2)" in p and state["boom"]:
            state["boom"] = False
            raise RuntimeError("craft judge down")
        return real(p, s)

    fake.hooks["craft_extract"] = flaky
    with pytest.raises(RuntimeError):
        progress.run_all_with_progress(project, until="write")
    n_write = fake.count("write", "of chapter ch01")
    planners = lambda: fake.count("craft_plan") - fake.count("craft_plan", "ROLLING REFINE")  # noqa: E731
    n_plan = planners()
    progress.run_all_with_progress(BookContext(project.project_dir), until="write")
    assert fake.count("write", "of chapter ch01") == n_write          # no scene rewritten
    assert planners() == n_plan                                         # plan cached
    L = engine.ledger(BookContext(project.project_dir))
    assert any(e["ch"] == "ch01" and e["scene"] == 2 for e in L.data["realized"])


def test_craft_plan_is_cached_and_idempotent(project, fake):
    install(fake, project)
    progress.run_all_with_progress(project, until="outline")
    n = fake.count("craft_plan")
    ctx = BookContext(project.project_dir)
    v = engine.plan_all(ctx)
    assert fake.count("craft_plan") == n and v == engine.ledger(ctx).data["plan_issues"]


def test_restart_retracts_craft_events(project, fake):
    install(fake, project)
    progress.run_all_with_progress(project, until="write")
    ctx = BookContext(project.project_dir)
    assert any(e["ch"] == "ch02" for e in engine.ledger(ctx).data["realized"])
    progress.reset_progress_after_step(ctx, "write", "ch02")
    assert not any(e["ch"] == "ch02" for e in engine.ledger(ctx).data["realized"])


def test_rerank_off_by_default_and_on_when_flagged(project, fake):
    from bookforge import config
    install(fake, project)
    progress.run_all_with_progress(project, until="write")
    assert fake.count("rerank") == 0
    config.set_config({"llm": {"json_retries": 1}, "craft": {"rerank": True, "rerank_min_tension": 0}})
    fake.hooks["rerank"] = lambda p, s: {"choice": "B"}
    progress.reset_progress_after_step(BookContext(project.project_dir), "write", "ch01")
    progress.run_all_with_progress(BookContext(project.project_dir), until="write")
    assert fake.count("rerank") >= 1
