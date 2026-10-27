"""Beat-by-beat scene writes and the one-retry drift flag.

Drives write_chapter.run with the fake LLM. No network.
"""
import json

from bookforge import config
from bookforge.core import canon_pipeline, progress
from bookforge.steps import write_chapter


def _setup(project):
    for s in ("brainstorm", "outline", "initialize_cast_and_world"):
        progress.run_step(project, s)


def _scene_writes(fake, scene=1, chapter="ch01"):
    needle = f"of scene {scene} of chapter {chapter}"
    return [p for r, p, _ in fake.calls if r == "write" and needle in p]


def _watch_chunks(monkeypatch):
    seen = []
    real = canon_pipeline.process_scene

    def wrapped(ctx, chapter_id, scene, text, step_dir=None, chunk=None):
        seen.append((scene.get("scene_number"), chunk))
        return real(ctx, chapter_id, scene, text, step_dir, chunk)

    monkeypatch.setattr(canon_pipeline, "process_scene", wrapped)
    return seen


def test_parse_drift_ignores_a_beat_sheet():
    assert write_chapter._parse_drift({"beats": ["Finn checks the boiler"]}) is None
    assert write_chapter._parse_drift({"drift": False, "covered": ["a"], "missing": []})["fail"] is False
    failed = write_chapter._parse_drift({"drift": True, "reason": "left the harbor", "missing": ["the order"]})
    assert failed["fail"] and failed["reason"] == "left the harbor"


def test_gemma_one_write_call_per_beat(project, fake, monkeypatch):
    _setup(project)
    config.set_config({"llm": {"json_retries": 1, "roles": {"write": {"tier": "gemma"}}},
                        "write": {"beat_by_beat": False, "beats_per_call": 1, "require_human_seed": False}})
    seen = _watch_chunks(monkeypatch)
    write_chapter.run(project, "ch01")
    beats = project.progress["scenes"]["ch01"]["1"]["beats"]
    prompts = _scene_writes(fake)
    assert len(beats) >= 2
    assert len(prompts) == len(beats)
    assert "The wind came hard off the river" not in prompts[0]
    assert "The wind came hard off the river" in prompts[1]
    scene1 = [chunk for n, chunk in seen if n == 1]
    assert scene1 == list(range(1, len(beats) + 1)) + [None]
    sp = project.progress["scenes"]["ch01"]["1"]
    assert sp.get("summarized") and sp.get("canon") and sp.get("craft")
    assert list(project.steps_dir.glob("*/drift_report.json")) == []


def test_flag_splits_other_tiers_and_pairs_beats(project, fake):
    _setup(project)
    config.set_config({"llm": {"json_retries": 1},
                        "write": {"beat_by_beat": True, "beats_per_call": 1, "require_human_seed": False}})
    write_chapter.run(project, "ch01")
    beats = project.progress["scenes"]["ch01"]["1"]["beats"]
    assert len(_scene_writes(fake)) == len(beats)

    # A fresh chapter cache, two beats per call: one write, not one per beat.
    project.progress["scenes"].pop("ch01", None)
    project.progress["hashes"].pop("write:ch01", None)
    project.save_progress()
    for p in (project.manuscript_dir / "ch01").glob("scene*_draft.txt"):
        p.unlink()
    draft = project.chapter_path("ch01", "draft")
    if draft.exists():
        draft.unlink()
    fake.calls.clear()
    config.set_config({"llm": {"json_retries": 1},
                        "write": {"beat_by_beat": True, "beats_per_call": 2, "require_human_seed": False}})
    write_chapter.run(project, "ch01")
    beats = project.progress["scenes"]["ch01"]["1"]["beats"]
    prompts = _scene_writes(fake)
    assert len(beats) == 2 and len(prompts) == 1
    assert all(b in prompts[0] for b in beats)


def test_default_write_role_is_not_one_call_per_beat(project, fake, monkeypatch):
    _setup(project)
    seen = _watch_chunks(monkeypatch)
    write_chapter.run(project, "ch01")
    beats = project.progress["scenes"]["ch01"]["1"]["beats"]
    assert len(beats) >= 2
    assert len(_scene_writes(fake)) == 1
    scene1 = [chunk for n, chunk in seen if n == 1]
    assert scene1 == [None]


def test_drifting_chunk_is_retried_once_then_flagged(project, fake, monkeypatch):
    _setup(project)
    config.set_config({"llm": {"json_retries": 1},
                        "write": {"beat_by_beat": True, "beats_per_call": 1, "require_human_seed": False}})
    seen = _watch_chunks(monkeypatch)

    def plan_hook(prompt, system):
        if "BEAT CHECK" not in prompt:
            return None
        if "SCENE: 1 CHUNK INDEX: 1\n" in prompt:
            return {"drift": True, "covered": [], "missing": ["the order"], "reason": "left the harbor"}
        return {"drift": False, "covered": ["ok"], "missing": [], "reason": ""}

    fake.hooks["plan"] = plan_hook
    write_chapter.run(project, "ch01")
    prompts = _scene_writes(fake)
    beats = project.progress["scenes"]["ch01"]["1"]["beats"]
    # One extra write: the single retry of chunk 1. Later beats are not retried.
    assert len(prompts) == len(beats) + 1
    drifted = [p for p in prompts if "left the harbor" in p]
    assert len(drifted) == 1
    assert "The previous chunk drifted (left the harbor)" in drifted[0]
    reports = list(project.steps_dir.glob("*/drift_report.json"))
    assert len(reports) == 1
    rows = json.loads(reports[0].read_text())
    assert len(rows) == 1
    assert rows[0]["scene"] == 1 and rows[0]["chunk"] == 1
    assert rows[0]["reason"] == "left the harbor"
    scene1 = [chunk for n, chunk in seen if n == 1]
    # Chunk 1 is checked twice (the call and its retry); the scene pass has chunk None.
    assert scene1.count(1) == 2
    assert scene1.count(None) == 1
    sp = project.progress["scenes"]["ch01"]["1"]
    assert sp.get("summarized") and sp.get("canon") and sp.get("craft")
