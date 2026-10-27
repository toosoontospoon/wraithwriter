"""Human seed gate: verbatim opening, refusal, override, raw versioning.

Drives write_chapter.run with the fake LLM. No network.
"""
import io

import pytest

from bookforge import config
from bookforge.core import progress
from bookforge.core.context import BookContext
from bookforge.core.human_seed import HumanSeedError, raw_family, seed_body, strip_front_matter
from bookforge.steps import write_chapter

BODY = (
    "She said \u201cHello\u201d  \u2014 then\u00a0waited.  \n"
    "The dock  held."
)
FRONT = (
    "---\n"
    "chapter: 1\n"
    "scene: 1\n"
    "beat_id: open\n"
    "author: human\n"
    "date: 2026-10-27\n"
    "---\n"
)


def _setup(project):
    for s in ("brainstorm", "outline", "initialize_cast_and_world"):
        progress.run_step(project, s)


def _enable(raw=None):
    write = {"require_human_seed": True}
    if raw is not None:
        write["raw_dir"] = str(raw)
    config.set_config({
        "llm": {"json_retries": 1, "roles": {"write": {"tier": "gemma"}}},
        "write": write,
    })


def _plant(project, chapter_id, text, scene_folder=None):
    root = project.manuscript_dir / chapter_id
    if scene_folder:
        path = root / scene_folder / "human_seed.md"
    else:
        path = root / "human_seed.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _clear_draft(project, chapter_id="ch01"):
    project.progress["scenes"].pop(chapter_id, None)
    project.save_progress()
    for p in (project.manuscript_dir / chapter_id).glob("scene*_draft.txt"):
        p.unlink()
    chapter = project.chapter_path(chapter_id, "draft")
    if chapter.exists():
        chapter.unlink()
    project.progress["hashes"].pop(f"write:{chapter_id}", None)
    project.progress["chapter_status"].pop(chapter_id, None)
    project.save_progress()


def test_front_matter_and_end_whitespace_only():
    body = strip_front_matter(FRONT + BODY + "\n\n")
    assert body == BODY + "\n\n"
    # One trailing end-of-line only. An earlier newline stays.
    assert seed_body(FRONT + BODY + "\n") == BODY
    assert seed_body(FRONT + BODY + "\n\n") == BODY + "\n"
    assert "author: human" not in seed_body(FRONT + BODY)
    # Unclosed front matter is left alone.
    assert strip_front_matter("---\nchapter: 1\nno closer\n") .startswith("---\n")
    assert raw_family("nbeerbower/Gemma4-Gutenberg-31B-Heretic:featherless-ai") == "gemma"
    assert raw_family("deepseek/deepseek-v4.1-flash-1017") == "deepseek-v4-1-flash-1017"


def test_byte_exact_seed_in_scene_chapter_and_raw(project, fake, tmp_path):
    _setup(project)
    _enable(tmp_path / "raw")
    _plant(project, "ch01", FRONT + BODY + "\n")
    fake.hooks["write"] = lambda p, s: "\nThe boiler held."
    write_chapter.run(project, "ch01")
    opening = seed_body(FRONT + BODY + "\n")
    assert opening == BODY
    scene = project.scene_path("ch01", 1, "draft").read_text(encoding="utf-8")
    chapter = project.chapter_path("ch01", "draft").read_text(encoding="utf-8")
    raws = list((tmp_path / "raw").glob("ch01-v*-gemma-raw.txt"))
    assert len(raws) == 1 and raws[0].name == "ch01-v1-gemma-raw.txt"
    raw = raws[0].read_text(encoding="utf-8")
    for text in (scene, chapter, raw):
        assert text.startswith(opening), text[:80]
        assert "author: human" not in text
        assert "beat_id:" not in text
    assert "\u201c" in scene and "\u2014" in scene and "\u00a0" in scene and "  " in scene
    prompt = next(p for r, p, _ in fake.calls if r == "write" and "of scene 1 of chapter ch01" in p)
    assert "do not repeat or rewrite it" in prompt and opening in prompt
    assert "The boiler held." in scene


def test_missing_and_empty_seed_refuse(project, fake, monkeypatch):
    _setup(project)
    _enable()
    monkeypatch.setattr("sys.stdin", io.StringIO("should not be read\n"))
    with pytest.raises(HumanSeedError, match="human_seed.md") as missing:
        write_chapter.run(project, "ch01")
    assert "plain UTF-8 markdown" in str(missing.value)
    assert "scene01" in str(missing.value)
    assert not (project.manuscript_dir / "ch01" / "human_seed.md").exists()
    _plant(project, "ch01", FRONT + "   \n\n")
    with pytest.raises(HumanSeedError, match="human_seed.md"):
        write_chapter.run(project, "ch01")


def test_scene_override_wins_for_both_folder_names(project, fake):
    _setup(project)
    _enable()
    chapter_seed = "Chapter opening stays off this scene."
    _plant(project, "ch01", chapter_seed + "\n")
    override = "Scene one \u201copens\u201d  here."
    _plant(project, "ch01", override + "\n", scene_folder="scene1")
    fake.hooks["write"] = lambda p, s: "\nContinued."
    write_chapter.run(project, "ch01")
    scene = project.scene_path("ch01", 1, "draft").read_text(encoding="utf-8")
    assert scene.startswith(override)
    assert not scene.startswith(chapter_seed)

    _clear_draft(project)
    (project.manuscript_dir / "ch01" / "scene1" / "human_seed.md").unlink()
    padded = "Padded \u2014 folder  wins."
    _plant(project, "ch01", padded + "\n", scene_folder="scene01")
    write_chapter.run(project, "ch01")
    scene = project.scene_path("ch01", 1, "draft").read_text(encoding="utf-8")
    assert scene.startswith(padded)
    assert chapter_seed not in scene.split("\n", 1)[0]


def test_raw_versions_v1_then_v2(project, fake, tmp_path):
    _setup(project)
    raw = tmp_path / "raw"
    _enable(raw)
    _plant(project, "ch01", BODY + "\n")
    fake.hooks["write"] = lambda p, s: "\nFirst pass."
    write_chapter.run(project, "ch01")
    assert (raw / "ch01-v1-gemma-raw.txt").exists()
    _clear_draft(project)
    fake.hooks["write"] = lambda p, s: "\nSecond pass."
    write_chapter.run(project, "ch01")
    assert (raw / "ch01-v2-gemma-raw.txt").read_text(encoding="utf-8").startswith(BODY)
    assert "Second pass." in (raw / "ch01-v2-gemma-raw.txt").read_text(encoding="utf-8")
    assert "First pass." in (raw / "ch01-v1-gemma-raw.txt").read_text(encoding="utf-8")


def test_tty_paste_saves_chapter_seed_and_drafts(project, fake, monkeypatch):
    _setup(project)
    _enable()
    pasted = "Pasted \u201copening\u201d  with  spaces.\n"
    stream = io.StringIO(pasted + ".\n")
    stream.isatty = lambda: True
    monkeypatch.setattr("sys.stdin", stream)
    fake.hooks["write"] = lambda p, s: "\nAfter the paste."
    write_chapter.run(project, "ch01")
    saved = (project.manuscript_dir / "ch01" / "human_seed.md").read_text(encoding="utf-8")
    assert saved == pasted
    opening = seed_body(saved)
    scene = project.scene_path("ch01", 1, "draft").read_text(encoding="utf-8")
    assert scene.startswith(opening)
    assert "After the paste." in scene


def test_open_quote_joins_directly_and_seed_skips_cleaners(project, fake, monkeypatch):
    from bookforge.core import deai, protect, rules_check
    from bookforge.core.human_seed import ends_inside_open_quote, join_opening
    _setup(project)
    _enable()
    # Body ends with a newline and an unmatched opening quote. An earlier line keeps its trailing space.
    body = "She waited. \n\""
    assert body.endswith("\n\"") and ends_inside_open_quote(body)
    _plant(project, "ch01", FRONT + body + "\n")
    assert seed_body(FRONT + body + "\n") == body
    seen = []

    def watch(name, fn):
        def wrapped(*args, **kwargs):
            seen.append((name, args))
            return fn(*args, **kwargs)
        return wrapped

    monkeypatch.setattr(write_chapter, "scrub", watch("scrub", write_chapter.scrub))
    monkeypatch.setattr(write_chapter, "clean_prose", watch("clean", write_chapter.clean_prose))
    monkeypatch.setattr(deai, "scrub", watch("deai.scrub", deai.scrub))
    monkeypatch.setattr(rules_check, "enforce_first_line", watch("enforce", rules_check.enforce_first_line))
    monkeypatch.setattr(protect, "restore", watch("protect", protect.restore))
    fake.hooks["write"] = lambda p, s: "still inside"
    write_chapter.run(project, "ch01")
    scene = project.scene_path("ch01", 1, "draft").read_text(encoding="utf-8")
    chapter = project.chapter_path("ch01", "draft").read_text(encoding="utf-8")
    assert scene.startswith(body) and chapter.startswith(body)
    assert scene[len(body):].startswith("still inside")
    assert chapter[len(body):].startswith("still inside")
    assert " " not in scene[len(body):len(body) + 1]
    prompt = next(p for r, p, _s in fake.calls if r == "write" and "of scene 1 of chapter ch01" in p)
    assert "continuing inside that spoken line" in prompt
    for _name, args in seen:
        for arg in args:
            assert not (isinstance(arg, str) and body in arg)

    # A closed opening uses the paragraph separator on the real write step.
    _clear_draft(project)
    (project.manuscript_dir / "ch01" / "human_seed.md").write_text("She stopped.\n", encoding="utf-8")
    fake.hooks["write"] = lambda p, s: "Next sentence."
    write_chapter.run(project, "ch01")
    closed_scene = project.scene_path("ch01", 1, "draft").read_text(encoding="utf-8")
    assert closed_scene.startswith("She stopped.\n\nNext sentence.")
    assert join_opening("She stopped.", "Next.") == "She stopped.\n\nNext."


def test_library_seed_default_is_required():
    assert config.DEFAULT_CONFIG["write"]["require_human_seed"] is True


def test_cli_raw_dir_twice(project, fake, tmp_path):
    from click.testing import CliRunner
    from bookforge.cli import cli
    _setup(project)
    _enable()
    _plant(project, "ch01", BODY + "\n")
    fake.hooks["write"] = lambda p, s: "\nHeld."
    raw = tmp_path / "raw"
    runner = CliRunner()
    args = ["run", "--project", str(project.project_dir), "--step", "write",
            "--chapter", "ch01", "--raw-dir", str(raw)]
    res = runner.invoke(cli, args)
    assert res.exit_code == 0, res.output
    _clear_draft(project)
    res = runner.invoke(cli, args)
    assert res.exit_code == 0, res.output
    opening = seed_body(BODY + "\n")
    chapter = project.chapter_path("ch01", "draft").read_text(encoding="utf-8")
    assert chapter.startswith(opening)
    assert (raw / "ch01-v1-gemma-raw.txt").read_text(encoding="utf-8").startswith(opening)
    assert (raw / "ch01-v2-gemma-raw.txt").read_text(encoding="utf-8").startswith(opening)


def test_open_quote_seed_drops_a_reopened_quote_from_the_continuation():
    from bookforge.core.human_seed import join_opening
    seed = 'Caleb looked up.\n\n"'
    assert join_opening(seed, '"Go on then," Caleb said.') == seed + 'Go on then," Caleb said.'
    assert join_opening(seed, '\u201cGo on.') == seed + "Go on."
    assert join_opening(seed, 'Go on then."') == seed + 'Go on then."'
    out = join_opening(seed, '\n"Go on."')
    assert out.startswith(seed) and not out.startswith(seed + '"')
