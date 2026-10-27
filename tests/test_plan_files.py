"""Author plan and notes files in beat planning, and beat-cache invalidation.

Drives write_chapter.run with the fake LLM. No network.
"""
from bookforge import config
from bookforge.core import pack, progress
from bookforge.steps import write_chapter


def _setup(project):
    for s in ("brainstorm", "outline", "initialize_cast_and_world"):
        progress.run_step(project, s)


def _templates():
    return {"plan_file": "book1/{chapter_id}-plan.txt",
            "notes_files": ["notes/{chapter_id}-beat_notes.md"]}


def _plant(project, plan_text, notes_text):
    plan = project.project_dir / "book1" / "ch01-plan.txt"
    notes = project.project_dir / "notes" / "ch01-beat_notes.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    notes.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(plan_text, encoding="utf-8")
    notes.write_text(notes_text, encoding="utf-8")
    return plan, notes


def _beat_prompts(fake):
    return [p for r, p, _ in fake.calls if r == "plan" and "Turn this scene card" in p]


def test_plan_and_notes_enter_beat_planning_and_invalidate_the_cache(project, fake):
    _setup(project)
    config.set_config({"plan": _templates(), "write": {"require_human_seed": False}})
    plan, notes = _plant(project, "PLANTOKEN Finn opens the hatch at dawn.\n",
                         "NOTESTOKEN the hatch is already open.\n")
    write_chapter.run(project, "ch01")
    prompts = _beat_prompts(fake)
    assert prompts
    assert "PLANTOKEN Finn opens the hatch at dawn." in prompts[0]
    assert "NOTESTOKEN the hatch is already open." in prompts[0]
    assert "notes override the plan file" in prompts[0]
    first_hash = pack.chapter_plan_sources(project, "ch01")[2]
    notes.write_text("NOTESTOKEN the hatch stays shut.\n", encoding="utf-8")
    assert pack.chapter_plan_sources(project, "ch01")[2] != first_hash
    plan.write_text("PLANTOKEN Finn opens the hatch at dusk.\n", encoding="utf-8")
    assert pack.chapter_plan_sources(project, "ch01")[2] != first_hash
    fake.calls.clear()
    write_chapter.run(project, "ch01")
    again = _beat_prompts(fake)
    assert again, "editing the plan files must plan beats again"
    assert "NOTESTOKEN the hatch stays shut." in again[0]
    assert "PLANTOKEN Finn opens the hatch at dusk." in again[0]


def test_unset_or_missing_plan_files_are_not_injected(project, fake):
    _setup(project)
    _plant(project, "PLANTOKEN should stay out.\n", "NOTESTOKEN should stay out.\n")
    write_chapter.run(project, "ch01")
    blob = "\n".join(p for r, p, _ in fake.calls if r == "plan")
    assert "PLANTOKEN" not in blob and "NOTESTOKEN" not in blob
    assert "notes override the plan file" not in blob

    project.progress["scenes"].pop("ch01", None)
    project.progress["hashes"].pop("write:ch01", None)
    project.save_progress()
    for path in (project.manuscript_dir / "ch01").glob("scene*_draft.txt"):
        path.unlink()
    draft = project.chapter_path("ch01", "draft")
    if draft.exists():
        draft.unlink()
    fake.calls.clear()
    config.set_config({"plan": _templates(), "write": {"require_human_seed": False}})
    (project.project_dir / "book1" / "ch01-plan.txt").unlink()
    (project.project_dir / "notes" / "ch01-beat_notes.md").unlink()
    write_chapter.run(project, "ch01")
    blob = "\n".join(_beat_prompts(fake))
    assert "notes override the plan file" not in blob
    assert "AUTHOR PLAN FILE" not in blob
