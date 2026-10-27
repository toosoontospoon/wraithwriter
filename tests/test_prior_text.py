"""context.prior_text: summaries and plans instead of prior-chapter prose.

Drives pack.build_write, write_chapter._plan_beats, critique_chapter.critique_text,
and the pack preview CLI. Fake LLM only.
"""
import json

from click.testing import CliRunner

from bookforge import config
from bookforge.cli import cli
from bookforge.core import pack, progress
from bookforge.core.io import atomic_write_json
from bookforge.steps import critique_chapter, write_chapter


def _setup(project):
    for s in ("brainstorm", "outline", "initialize_cast_and_world"):
        progress.run_step(project, s)


def _plant(project):
    raw = json.loads((project.state_dir / "outline.json").read_text())
    for ch in raw:
        if ch["id"] == "ch01":
            ch["scenes"][0].setdefault("beats", []).append("CH01CARDTOKEN the glass fogs")
        if ch["id"] == "ch03":
            ch["summary"] = "CH03PLANTOKEN Finn comes home"
            ch["scenes"][0]["beats"] = ["CH03PLANTOKEN opens the lock", "Finn stays"]
    atomic_write_json(project.state_dir / "outline.json", raw)
    for cid, token in (("ch01", "FULLCH01UNIQUE"), ("ch02", "FULLCH02UNIQUE")):
        project.write_text(project.chapter_path(cid, "draft"), (token + " ") * 2500)
    summaries = project.load_summaries()
    summaries.setdefault("chapters", {})
    summaries["chapters"]["ch01"] = "SUMCH01 the hire"
    summaries["chapters"]["ch02"] = "SUMCH02 the storm"
    project.save_summaries(summaries)
    plan = project.project_dir / "plan" / "ch02-plan.txt"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("CH02PLANTOKEN the storm plan in full detail.\n", encoding="utf-8")
    notes = project.project_dir / "notes" / "ch03-beat_notes.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("CH03NOTESTOKEN the lock is brass.\n", encoding="utf-8")
    return plan


def _mode(budget=None):
    write = {"require_human_seed": False, "prior_words": 1500}
    if budget is not None:
        write["context_budget_tokens"] = budget
    config.set_config({
        "context": {"prior_text": "summaries_plus_prev_plan"},
        "plan": {"plan_file": "plan/{chapter_id}-plan.txt",
                 "notes_files": ["notes/{chapter_id}-beat_notes.md"]},
        "write": write,
        "llm": {"json_retries": 1},
    })


def test_three_chapters_use_summaries_and_plans_not_full_text(project, fake, tmp_path):
    _setup(project)
    _plant(project)
    _mode()
    chapter = project.chapter("ch03")
    scene = chapter["scenes"][0]
    so_far = ("HEADWORD " * 3000) + ("TAILWORD " * 1600)
    system, user = pack.build_write(project, chapter, scene, scene["beats"], so_far, 400)
    whole = system + "\n\n" + user
    for token in ("SUMCH01", "SUMCH02", "CH02PLANTOKEN", "CH03PLANTOKEN", "CH03NOTESTOKEN", "TAILWORD"):
        assert token in whole, token
    assert "HEADWORD" not in whole
    assert "FULLCH01UNIQUE" not in whole and "FULLCH02UNIQUE" not in whole
    assert len([w for w in user.split() if w == "TAILWORD"]) <= 1500

    write_chapter._plan_beats(project, chapter, scene, tmp_path)
    planned = next(p for r, p, _ in fake.calls if r == "plan" and "Turn this scene card" in p)
    for token in ("SUMCH01", "SUMCH02", "CH02PLANTOKEN", "CH03PLANTOKEN"):
        assert token in planned
    assert "FULLCH01UNIQUE" not in planned and "FULLCH02UNIQUE" not in planned

    critique_chapter.critique_text(project, chapter, "Finn came home and opened the lock.")
    crit = [p for r, p, _ in fake.calls if r == "critique"][-1]
    for token in ("SUMCH01", "SUMCH02", "CH02PLANTOKEN", "CH03PLANTOKEN"):
        assert token in crit
    assert "FULLCH01UNIQUE" not in crit and "FULLCH02UNIQUE" not in crit

    # full / unset still carries recent chapter prose and the whole chapter-so-far.
    config.set_config({"write": {"require_human_seed": False}, "llm": {"json_retries": 1}})
    assert config.load_config()["context"]["prior_text"] == "full"
    system_full, user_full = pack.build_write(project, chapter, scene, scene["beats"], so_far, 400)
    full = system_full + "\n\n" + user_full
    assert "FULLCH02UNIQUE" in full and "HEADWORD" in full
    assert "FULL TEXT OF ch02" in full


def test_budget_trims_after_the_prior_text_selection(project, fake):
    _setup(project)
    plan = _plant(project)
    plan.write_text("CH02PLANTOKEN " + ("storm " * 30000), encoding="utf-8")
    _mode(budget=4000)
    chapter = project.chapter("ch03")
    scene = chapter["scenes"][0]
    system, user = pack.build_write(project, chapter, scene, scene["beats"], "TAILWORD " * 2000, 400)
    whole = system + "\n\n" + user
    assert pack.prompt_token_estimate(whole) <= 4000
    assert "FULLCH01UNIQUE" not in whole and "FULLCH02UNIQUE" not in whole
    assert "CH03PLANTOKEN opens the lock" in user


def test_pack_preview_prints_the_write_estimate_without_an_llm(project, fake):
    _setup(project)
    _plant(project)
    _mode()
    before = len(fake.calls)
    runner = CliRunner()
    args = ["pack", "preview", "--project", str(project.project_dir), "--chapter", "ch03",
            "--scene", "1", "--role", "write"]
    first = runner.invoke(cli, args)
    second = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert first.output == second.output
    line = first.output.splitlines()[0]
    assert line.startswith("prompt_tokens_est ")
    assert "with_filter " in first.output and "without_filter " in first.output
    count = int(line.split()[-1])
    assert count > 0
    system, user = pack.write_prompt_for_scene(project, "ch03", 1)
    assert count == pack.prompt_token_estimate(system + "\n\n" + user)
    assert len(fake.calls) == before
