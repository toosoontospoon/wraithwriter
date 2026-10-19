"""Former bug repros from REVIEW.md, now asserting the fixed behaviour."""
import json
import re
from pathlib import Path

import pytest

from bookforge.core import progress
from bookforge.core.context import BookContext, NullEmbeddingStore
from bookforge.core import state_updater as su

PKG = Path(__file__).resolve().parent.parent / "bookforge"


def mk(tmp_path):
    return BookContext(tmp_path / "p", embedding_store=NullEmbeddingStore())


def test_no_hardcoded_keys():
    for f in PKG.rglob("*.py"):
        t = f.read_text()
        assert not re.search(r"[s]k-or-v1-[0-9a-f]{20}|[x]ai-[A-Za-z0-9]{30}|[s]k-[A-Za-z0-9]{32}", t), f


def test_import_has_no_side_effects(tmp_path):
    import bookforge.core.llm  # noqa: F401
    assert not (tmp_path / "home" / ".bookforge").exists()


def test_dict_characters_visible_to_writer(tmp_path):
    ctx = mk(tmp_path)
    ctx.save_json("state/characters.json", {"finn": {"name": "Finn", "role": "protagonist"}})
    assert list(ctx.load_characters()) == ["finn"]
    ctx.save_json("state/characters.json", [{"name": "Finn Cooper"}])
    assert list(ctx.load_characters()) == ["finn_cooper"]


def test_state_update_does_not_wipe_characters(tmp_path):
    ctx = mk(tmp_path)
    ctx.save_json("state/characters.json", {"finn": {"name": "Finn"}})
    su.merge_character_deltas(ctx, {"finn": {"current_state": {"location": "deck", "knowledge": ["a"]}}})
    c = ctx.load_characters()["finn"]
    assert c["current_state"]["location"] == "deck" and c["current_state"]["knowledge"] == ["a"]


def test_world_merge_handles_dicts_and_keeps_order(tmp_path):
    ctx = mk(tmp_path)
    ctx.save_json("state/world.json", {"key_locations": {"Port": "x"}, "core_rules": ["b", "a"]})
    su.merge_world_deltas(ctx, {"new_locations": [{"name": "Y"}], "rule_changes": [{"rule": "x"}, "c"],
                                "lore_updates": "myth"})
    w = ctx.load_world()
    assert [l["name"] for l in w["key_locations"]] == ["Port", "Y"]
    assert w["core_rules"] == ["b", "a", "rule: x", "c"] and w["lore"] == ["myth"]


def test_location_and_rules_reach_writer(tmp_path):
    ctx = mk(tmp_path)
    ctx.save_world({"key_locations": [{"name": "Harbor", "description": "sooty skyport"}],
                    "supernatural_rules": "No magic."})
    ctx.save_characters([{"name": "Finn Cooper", "voice": "eager and quick"}])
    ctx.save_outline([{"id": "ch01", "title": "T", "scenes": [{"location": "Harbor"}]}])
    out = ctx.get_scene_context(ctx.chapter("ch01"), {"location": "Harbor", "characters_present": ["Finn Cooper"],
                                                      "scene_number": 1}, ["b"], "")
    assert "sooty skyport" in out and "eager and quick" in out
    assert "No magic." in ctx.stable_prefix()


def test_run_all_skips_finished_chapters(project, fake):
    progress.run_all_with_progress(project)
    n = len(fake.calls)
    progress.run_all_with_progress(BookContext(project.project_dir))
    assert len(fake.calls) == n


def test_restart_from_init_resets_chapters_and_archives(project, fake):
    progress.run_all_with_progress(project, until="revise")
    progress.reset_progress_after_step(project, "initialize_cast_and_world")
    assert all(v == "" for v in project.progress["chapter_status"].values())
    assert not project.chapter_path("ch01", "draft").exists()
    assert any((project.project_dir / "archive").rglob("ch01_draft.txt"))
    assert not any(f["provenance"] for f in project.canon().facts.values())


def test_budget_keeps_characters_when_tail_is_huge(tmp_path):
    ctx = mk(tmp_path)
    ctx.total_context_tokens = 1500
    ctx.save_characters([{"name": "Finn Cooper"}])
    ctx.save_outline([{"id": "ch01", "title": "T"}])
    out = ctx.get_scene_context(ctx.chapter("ch01"), {"characters_present": ["Finn Cooper"], "scene_number": 1},
                                ["b"], "x " * 50000)
    assert "CHARACTERS IN SCENE" in out and "TEXT SO FAR" in out


def test_unknown_role_falls_back_to_configured_default(capsys):
    from bookforge.config import resolve_role
    prov, model = resolve_role("no_such_role")
    assert "claude" not in model and "not configured" in capsys.readouterr().out


def test_outline_ids_are_normalized(project, fake):
    progress.run_step(project, "brainstorm")
    progress.run_step(project, "outline")
    o = project.load_outline()
    assert [c["id"] for c in o] == ["ch01", "ch02", "ch03"]
    assert o[0]["slug"] == "whispers_from_the_tide"


def test_clean_prose_keeps_time_skip_markers():
    from bookforge.steps._common import clean_prose
    assert "==========" in clean_prose("a\n\n---\n\nb")
    assert clean_prose("x - y") == "x - y"


def test_revise_preserves_paragraphs(project, fake):
    progress.run_all_with_progress(project, until="revise")
    assert "\n\n" in project.chapter_path("ch01", "revised").read_text()


def test_finalize_requires_fresh_audit_and_uses_title(project, fake):
    progress.run_all_with_progress(project, until="deai")
    assert progress.run_step(project, "finalize") is None
    progress.run_all_with_progress(project)
    text = (project.project_dir / "final_manuscript.txt").read_text()
    assert text.startswith("THE BRASS KEY") and "A BOY JOINS" not in text


def test_manual_edit_makes_downstream_stale(project, fake):
    progress.run_all_with_progress(project, until="critique")
    assert progress.is_current(project, "critique", "ch01")
    p = project.chapter_path("ch01", "draft")
    p.write_text(p.read_text() + "\n\nA new paragraph the author typed by hand.")
    assert not progress.is_current(project, "critique", "ch01")


def test_cost_log_written(project, fake):
    progress.run_step(project, "brainstorm")
    rows = [json.loads(l) for l in (project.project_dir / "costs.jsonl").read_text().splitlines()]
    assert rows and {"role", "prompt_tokens", "cost_usd"} <= set(rows[0])
