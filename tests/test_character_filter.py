"""Chapter-cast filter for characters.md. Fake LLM only. No network."""
from click.testing import CliRunner

from bookforge import config
from bookforge.cli import cli
from bookforge.core import pack, progress
from bookforge.steps import write_chapter


CHARACTERS = """
## Ada One
A pilot. aliases: Ada

## Bea Two
A navigator.

## Cal Three
A locksmith.
aliases: Skip

## Dee Four
A cook who is not in this chapter.

## Eve Fifth
The point of view who must stay.

## Finn Cooper
A name from another chapter.
"""


def _setup(project):
    for s in ("brainstorm", "outline", "initialize_cast_and_world"):
        progress.run_step(project, s)


def _cast_chapter(project):
    raw_path = project.state_dir / "outline.json"
    import json
    raw = json.loads(raw_path.read_text())
    ch = next(c for c in raw if c["id"] == "ch01")
    ch["summary"] = "Ada and Bea meet Skip."
    ch["scenes"] = [{
        "scene_number": 1,
        "pov": "Ada One",
        "location": "Harbor",
        "characters_present": ["Ada One", "Bea Two"],
        "action": "Ada and Bea wait.",
        "beats": ["Ada One waits", "Bea Two answers"],
    }]
    from bookforge.core.io import atomic_write_json
    atomic_write_json(raw_path, raw)
    (project.project_dir / "style_bible.md").write_text("Plain speech.\n", encoding="utf-8")
    (project.project_dir / "characters.md").write_text(CHARACTERS, encoding="utf-8")
    voices = project.project_dir / "voices"
    voices.mkdir(exist_ok=True)
    (voices / "ada.md").write_text("VOICEADAUNIQUE quick and plain.\n", encoding="utf-8")
    (voices / "dee.md").write_text("VOICEDEEUNIQUE ornate and absent.\n", encoding="utf-8")
    plan = project.project_dir / "plan"
    plan.mkdir(exist_ok=True)
    (plan / "ch01-plan.txt").write_text("Skip brings the key.\n", encoding="utf-8")
    project._style = None


def _names(text):
    return {line[3:].strip() for line in text.splitlines() if line.startswith("## ")}


def test_chapter_cast_keeps_cards_plan_alias_and_always_list(project, fake):
    _setup(project)
    _cast_chapter(project)
    chapter = project.chapter("ch01")
    config.set_config({
        "context": {"characters": "chapter_cast", "always_characters": ["Eve Fifth"],
                    "prior_text": "full"},
        "plan": {"plan_file": "plan/{chapter_id}-plan.txt"},
        "write": {"require_human_seed": False},
    })
    filtered = pack.system_for(project, chapter)
    assert _names(filtered) == {"Ada One", "Bea Two", "Cal Three", "Eve Fifth"}
    assert "Dee Four" not in filtered and "Finn Cooper" not in filtered

    # The summaries mode turns the same filter on without context.characters.
    config.set_config({
        "context": {"prior_text": "summaries_plus_prev_plan", "always_characters": ["Eve Fifth"]},
        "plan": {"plan_file": "plan/{chapter_id}-plan.txt"},
        "write": {"require_human_seed": False},
    })
    via_mode = pack.system_for(project, chapter)
    assert _names(via_mode) == {"Ada One", "Bea Two", "Cal Three", "Eve Fifth"}

    scene = chapter["scenes"][0]
    _system, user = pack.build_write(project, chapter, scene, scene["beats"], "", 400)
    assert "VOICEADAUNIQUE" in user
    assert "VOICEDEEUNIQUE" not in user

    config.set_config({"write": {"require_human_seed": False}})
    whole = pack.system_for(project, chapter)
    assert _names(whole) == {"Ada One", "Bea Two", "Cal Three", "Dee Four", "Eve Fifth", "Finn Cooper"}


def test_preview_prints_both_character_filter_estimates(project, fake):
    _setup(project)
    _cast_chapter(project)
    config.set_config({
        "context": {"characters": "chapter_cast", "always_characters": ["Eve Fifth"]},
        "plan": {"plan_file": "plan/{chapter_id}-plan.txt"},
        "write": {"require_human_seed": False},
    })
    before = len(fake.calls)
    runner = CliRunner()
    args = ["pack", "preview", "--project", str(project.project_dir), "--chapter", "ch01",
            "--scene", "1", "--role", "write"]
    first = runner.invoke(cli, args)
    second = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output
    assert first.output == second.output
    lines = dict(line.split(" ", 1) for line in first.output.splitlines() if line.strip())
    assert int(lines["prompt_tokens_est"]) > 0
    assert int(lines["with_filter"]) < int(lines["without_filter"])
    sent, on, off = pack.preview_token_counts(project, "ch01", 1, "write")
    assert (int(lines["prompt_tokens_est"]), int(lines["with_filter"]), int(lines["without_filter"])) == (sent, on, off)
    assert len(fake.calls) == before
    # write_chapter still drafts; the filter must not be required for that path.
    write_chapter.run(project, "ch01")


def test_honorific_alone_does_not_match_another_entry():
    labels = ["Lord Ashby"]
    assert pack._label_hit("Lord Ashby", labels)
    assert pack._label_hit("Ashby", labels)
    assert not pack._label_hit("Lord Brandt", labels)
    assert not pack._label_in_blob("Lord Ashby", "lord brandt spoke.")
