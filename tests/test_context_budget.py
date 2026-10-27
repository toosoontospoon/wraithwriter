"""Write-prompt budget: priority order, trim, and prompt_tokens_est.

Drives pack.build_write and write_chapter.run with the fake LLM. No network.
"""
import json

from bookforge import config
from bookforge.core import pack, progress
from bookforge.core.io import atomic_write_json
from bookforge.steps import write_chapter


def _setup(project):
    for s in ("brainstorm", "outline", "initialize_cast_and_world"):
        progress.run_step(project, s)


def _write_rows(project):
    path = project.state_dir / "usage.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if r["role"] == "write"]


def test_unset_budget_keeps_the_full_chapter_so_far(project, fake):
    _setup(project)
    assert config.DEFAULT_CONFIG["write"]["context_budget_tokens"] is None
    assert config.DEFAULT_CONFIG["write"]["prior_words"] == 1500
    chapter = project.chapter("ch01")
    scene = chapter["scenes"][0]
    huge = "ALPHAUNIQUE " * 3000
    system, user = pack.build_write(project, chapter, scene, scene["beats"], huge, 400)
    assert "ALPHAUNIQUE" in user
    assert user.index("CHAPTER SO FAR") < user.index("SCENE CARD:")
    assert "BEATS TO WRITE NOW" in user
    write_chapter.run(project, "ch01")
    rows = _write_rows(project)
    assert rows and "prompt_tokens" in rows[0]
    assert isinstance(rows[0]["prompt_tokens_est"], int) and rows[0]["prompt_tokens_est"] > 0


def test_gemma_budget_trims_a_huge_pack_and_keeps_beats_first(project, fake):
    _setup(project)
    raw = json.loads((project.state_dir / "outline.json").read_text())
    raw.insert(0, {"id": "ch00", "title": "Before", "summary": "The harbor slept.",
                   "ends_with": "dawn", "scenes": [
                       {"scene_number": 1, "pov": "Finn Cooper", "location": "Harbor",
                        "characters_present": ["Finn Cooper"], "action": "Finn waits.",
                        "beats": ["Finn waits for the tide"]}]})
    atomic_write_json(project.state_dir / "outline.json", raw)
    huge = ("ALPHAUNIQUE " * 8000) + ("OMEGAUNIQUE " * 2000)
    project.write_text(project.chapter_path("ch00", "draft"), huge)
    summaries = project.load_summaries()
    summaries.setdefault("chapters", {})["ch00"] = "SUMMARYCH00 the harbor slept"
    project.save_summaries(summaries)
    (project.project_dir / "style_bible.md").write_text("STYLEBIBLEUNIQUE plain speech.\n" + ("guidance " * 200))
    voice = project.project_dir / "voices"
    voice.mkdir()
    (voice / "finn.md").write_text("VOICEFINNUNIQUE quick and eager, never ornate.")
    project._style = None
    canon = project.canon()
    canon.add_fact("scene_detail", ["finn_cooper"],
                   "FACTUNIQUE Finn counted the rivets. " + ("brass lamp " * 40000),
                   "established", "local")
    canon.save()
    budget = 24000
    config.set_config({
        "llm": {"json_retries": 1, "roles": {"write": {"tier": "gemma"}}},
        "write": {"context_budget_tokens": budget, "prior_words": 1500, "require_human_seed": False},
    })
    assert pack.prompt_token_estimate(huge) > budget
    chapter = project.chapter("ch01")
    tail = pack.prior_tail(project, chapter, "", 1500)
    assert "OMEGAUNIQUE" in tail and "ALPHAUNIQUE" not in tail
    assert len(tail.split()) <= 1500
    write_chapter.run(project, "ch01")
    prompts = [(p, s) for r, p, s in fake.calls if r == "write" and "of scene 1 of chapter ch01" in p]
    assert prompts
    user, system = prompts[0]
    whole = system + "\n\n" + user
    estimate = pack.prompt_token_estimate(whole)
    assert estimate <= budget
    assert "ALPHAUNIQUE" not in whole
    assert "Finn Cooper checks the boiler" in user
    first = min(user.index("Finn Cooper checks the boiler"), user.index("SCENE CARD:"))
    for marker in ("VOICE SHEETS", "CONTINUITY AND FACTS", "PRIOR TEXT", "EARLIER CHAPTERS", "STYLE BIBLE"):
        if marker in user:
            assert first < user.index(marker), marker
    rows = _write_rows(project)
    assert rows
    assert "prompt_tokens" in rows[0] and rows[0]["prompt_tokens_est"] <= budget
    assert rows[0]["prompt_tokens_est"] == pack.prompt_token_estimate(
        prompts[0][1] + "\n\n" + prompts[0][0])


def test_oversized_facts_are_condensed_once_per_scene(project, fake):
    _setup(project)
    canon = project.canon()
    canon.add_fact("scene_detail", ["finn_cooper"],
                   "FACTUNIQUE " + ("rivet " * 30000), "established", "local")
    canon.save()
    budget = 4000
    config.set_config({
        "llm": {"json_retries": 1},
        "write": {"context_budget_tokens": budget, "beat_by_beat": True, "beats_per_call": 1,
                  "require_human_seed": False},
    })
    write_chapter.run(project, "ch01")
    condensed = [p for r, p, _ in fake.calls if r == "plan" and "CONDENSE FACTS" in p]
    assert sum("SCENE: 1\n" in p for p in condensed) == 1
    assert sum("SCENE: 2\n" in p for p in condensed) == 1
    for _r, prompt, system in ((r, p, s) for r, p, s in fake.calls if r == "write"):
        assert pack.prompt_token_estimate(system + "\n\n" + prompt) <= budget
