"""Story-first length: no word target drives planning, writing, revise or re-voice."""
import inspect

from bookforge.core import length, voice
from bookforge.core.schema import Chapter, Scene
from bookforge.steps import outline, revise_chapter, write_chapter


def test_beat_words_tag_and_fallback():
    assert length.beat_words("(~350w, reveal) Meemaw tells it") == 350
    assert length.beat_words("(~120 words) walk home") == 120
    assert length.beat_words("transition: they walk home") == length.BEAT_TYPES["transition"]
    assert length.beat_words("something happens") == length.DEFAULT_BEAT


def test_estimates_are_sums_of_beats():
    s1 = {"beats": ["(~300w, action) a", "(~90w, transition) b"]}
    s2 = {"beats": ["(~200w) c"]}
    assert length.scene_estimate(s1) == 390
    assert length.chapter_estimate({"scenes": [s1, s2]}) == 590
    assert length.ceiling(390) > 390


def test_schema_defaults_are_not_targets():
    assert Scene.model_validate({"scene_number": 1}).target_words == 0
    assert Chapter.model_validate({"id": "ch01"}).target_words == 0
    assert len(Scene.model_validate({"scene_number": 1, "beats": ["one"]}).beats) == 1


def test_no_division_from_word_targets():
    src = inspect.getsource(outline) + inspect.getsource(write_chapter)
    for bad in ("/ 3500", "/ 1400", "4-8 concrete", "about {words} words", "per chapter)"):
        assert bad not in src, bad
    assert "1-8 concrete" in src and "NO_FILLER_RULE" in src


def test_writer_gets_ceiling_not_target():
    from bookforge.core import pack
    src = inspect.getsource(pack.build_write)
    assert "write each beat to completion, then stop" in src and "never a goal" in src


def test_revise_is_one_editor_pass_without_102_cap():
    from bookforge.core import editor
    assert not hasattr(revise_chapter, "polish_scene")
    src = inspect.getsource(editor)
    assert "1.02" not in src and "Cut filler" in src and editor.RUNAWAY >= 1.3


def test_voice_caps_at_input_and_has_no_floor():
    assert voice.DEFAULTS["min_ratio"] == 0.0 and voice.DEFAULTS["max_ratio"] == 1.0
    assert voice._desired({"target_words": 100}, "word " * 500) == 500
