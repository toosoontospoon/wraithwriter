"""Skill banlist, assistant-voice regressions, fragment budget, and the prose linter gate."""
from pathlib import Path

import pytest
from click.testing import CliRunner

from bookforge import config
from bookforge.cli import cli
from bookforge.core import deai, detector
from bookforge.core import structure as S

ROOT = Path(__file__).resolve().parents[1]
BANLIST = ROOT / "bookforge/style/generated/deai_skill_banlist.txt"

NINETEEN = (
    "landscape", "pivotal", "crucial", "robust", "leverage", "utilize", "facilitate",
    "comprehensive", "holistic", "nuanced", "underscore", "foster", "paradigm",
    "cutting-edge", "seamless", "transformative", "game-changer", "unlock", "empower",
)


def _rows():
    rows = []
    for line in BANLIST.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("GENERATED") or line.startswith("#"):
            continue
        kind, rest = line.split(None, 1)
        qual = ""
        if kind == "word":
            parts = rest.rsplit(None, 1)
            if len(parts) == 2 and parts[1] in ("figurative", "verb"):
                rest, qual = parts
        rows.append((kind, rest, qual))
    return rows


def _narration(kind, term, qual):
    if qual == "figurative":
        return "The political landscape changed before the evening vote in town."
    if qual == "verb":
        return "They leverage the old winch before the tide turns at dusk."
    if kind == "phrase":
        return f"{term} The crew finished the work before dark and went home."
    return f"The crew will {term} the gate before dark and then walk home."


def test_generated_banlist_contains_the_missing_words_and_phrases():
    text = BANLIST.read_text()
    for word in NINETEEN:
        assert word in text
    for phrase in ("Of course!", "Certainly!", "It's important to note", "I'd be happy to",
                   "Happy to help", "Hope this helps", "Let me know if you need anything else"):
        assert phrase in text


def test_every_banlist_entry_flags_in_narration_not_in_dialogue():
    for kind, term, qual in _rows():
        sentence = _narration(kind, term, qual)
        hit = detector.detect(sentence)
        assert "skill_banlist" in hit["counts"], sentence
        quoted = f'"{sentence}" she said and turned back to the winch.'
        assert "skill_banlist" not in detector.detect(quoted)["counts"], quoted


def test_landscape_and_leverage_qualifiers_and_allowlist():
    literal = "The landscape rolled out below the ridge and the sheep kept walking home."
    assert "skill_banlist" not in detector.detect(literal)["counts"]
    assert "skill_banlist" in detector.detect(
        "The political landscape shifted before the vote and nobody slept.")["counts"]
    assert "skill_banlist" in detector.detect(
        "A landscape of grief spread through the town before dawn.")["counts"]
    assert "skill_banlist" not in detector.detect(
        "The leverage on the winch held the load steady all night.")["counts"]
    assert "skill_banlist" in detector.detect(
        "She leveraged the winch and the crate rose before dawn.")["counts"]
    assert "skill_banlist" not in detector.detect(
        "They utilize the winch before dark and walk home.", allow=["utilize"])["counts"]
    words = [h for h in detector.detect("The crew will utilize the gate before dark.")["hits"]
             if h["rule"] == "skill_banlist"]
    assert words and words[0]["severity"] == 2
    phrases = [h for h in detector.detect("Of course! The boat left the harbor before dawn.")["hits"]
               if h["rule"] == "skill_banlist"]
    assert phrases and phrases[0]["severity"] == 3


def test_assistant_voice_matches_punctuation_and_skips_quotes():
    for sentence in (
        "Of course! I will go to the dock.",
        "Certainly! The boat left the harbor.",
        "Ultimately, the boat left the harbor before dawn.",
        "It is important to note the tide turned before dawn.",
    ):
        assert "assistant_voice" in detector.detect(sentence)["counts"], sentence
    for sentence in (
        '"Of course!" she said.',
        '"Certainly!" he said.',
        '"Ultimately," she said, "we leave on the tide."',
        '"It is important to note," she said.',
    ):
        assert "assistant_voice" not in detector.detect(sentence)["counts"], sentence


def test_lint_stock_words_and_fragments_skip_dialogue():
    quoted = '"A tapestry of light," she said.\n\nShe ran home along the ridge before dark.'
    assert "stock_word" not in deai.lint(quoted)["counts"]
    assert "fragment" not in deai.lint('"Cold."\n\nShe ran home before the rain started over the ridge.')["counts"]
    bare = deai.lint("A tapestry of light.")
    assert bare["counts"].get("stock_word") == 1


def test_fragment_ratio_and_budget_exempts_dialogue():
    fragments = "No soundtrack. Cold lake. Grey sky."
    rep = detector.detect(fragments)
    assert rep["fragment_ratio"] == 1
    assert "fragment_budget" in rep["flags"]
    assert "fragment" in rep["counts"]
    dialogue = '"Cold lake."\n\n"No feed."\n\nShe ran home before the rain started over the east ridge.'
    clean = detector.detect(dialogue)
    assert clean["fragment_ratio"] == 0
    assert "fragment_budget" not in clean["flags"]
    assert "fragment" not in clean["counts"]
    config.set_config({"deai": {"fragment_max_ratio": 1}})
    assert "fragment_budget" not in detector.detect(fragments)["flags"]


def test_deai_rules_number_eight_before_nine():
    text = (ROOT / "bookforge/style/deai_rules.md").read_text()
    assert text.index("\n8.") < text.index("\n9.")


def test_deai_config_keys():
    block = config.DEFAULT_CONFIG["deai"]
    assert block["fail_on_tokens"] is True
    assert block["fragment_max_ratio"] == 0.02
    assert block["dash_policy"] == "fiction"
    for key in ("max_flag_ratio", "max_loops", "min_severity", "cluster"):
        assert key in block


CLEAN = (
    "Ada pulled the trotline until the catfish came up thrashing in the lantern light.\n\n"
    "Tom held the lantern and told her the knot was wrong, which he said every Saturday night.\n\n"
    "\"You want the fish or not?\" Ada said.\n\n"
    "They walked home along the creek with the fish in a feed sack and the lantern swinging."
)
DIRTY = (
    "Of course! It is important to note the political landscape was a tapestry of smoke.\n\n"
    "He paused \u2014 then the crew began to utilize the winch.\n\n"
    "No soundtrack. No feed. Cold lake."
)


def test_detect_fail_on_exit_codes(tmp_path):
    dirty = tmp_path / "dirty.txt"
    clean = tmp_path / "clean.txt"
    dirty.write_text(DIRTY)
    clean.write_text(CLEAN)
    runner = CliRunner()
    bad = runner.invoke(cli, ["detect", str(dirty), "--fail-on", "3"])
    assert bad.exit_code == 1
    for name in ("word choice", "stock phrases", "grammar tells", "sentences", "tokens"):
        assert name in bad.output
    good = runner.invoke(cli, ["detect", str(clean), "--fail-on", "3"])
    assert good.exit_code == 0, good.output
    for name in ("word choice", "stock phrases", "grammar tells", "sentences", "tokens"):
        assert name in good.output
    plain = runner.invoke(cli, ["detect", str(dirty)])
    assert plain.exit_code == 0 and "score=" in plain.output


def _outline_with(plot_function):
    return [{"id": "ch01", "title": "Dock", "summary": "Finn rows home before dark.",
             "structure": {"act": "1", "save_the_cat": "setup", "heros_journey": "ordinary_world",
                           "chapter_type": "discovery", "strand": "A", "a_purpose": "rows home",
                           "plot_function": plot_function,
                           "value_turn": {"from": "lost", "to": "found"},
                           "scene_sequel": "scene", "ending": "reveal"}}]


def test_structure_check_warns_on_banned_plot_function(monkeypatch):
    outline = _outline_with("They utilize the old chart to cross the marsh before dark.")
    issues = S.check(outline)
    assert any(i["severity"] == "WARN" and i["check"] == "prose_tell" and "utilize" in i["description"]
               and "skill_banlist" in i["description"] for i in issues)
    from bookforge import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_structure_inputs", lambda p: (None, outline, [], {}))
    result = CliRunner().invoke(cli, ["structure", "check", "--project", "."])
    assert result.exit_code == 0, result.output
    assert "[WARN]" in result.output and "utilize" in result.output and "skill_banlist" in result.output
    contrast = _outline_with("It was not fear, but hunger that moved the plot forward today.")
    assert any(i["check"] == "prose_tell" and "not_x_but_y" in i["description"] for i in S.check(contrast))


def test_deai_chapter_token_gate_scrubs_and_records(project, monkeypatch):
    monkeypatch.setattr("bookforge.core.deai.scrub", lambda text: text)
    project.save_outline([{"id": "ch01", "title": "Dock", "summary": "Finn runs.",
                           "scenes": [{"scene_number": 1, "action": "runs"}]}])
    project.save_characters({})
    project.save_world({})
    project.write_text(project.chapter_path("ch01", "revised"),
                       "He paused \u2014 then ran to the dock before the bell.")
    from bookforge.steps import deai_chapter
    deai_chapter.run(project, "ch01")
    final = project.chapter_path("ch01", "final").read_text()
    assert "\u2014" not in final and "paused, then ran" in final
    report = next(project.steps_dir.glob("*deai_ch01/detector_report.txt")).read_text()
    assert "token_tells_before:" in report and "scrub_counts:" in report and "token_tells_after:" in report
    assert "em_dash" in report


def test_deai_chapter_token_gate_raises_when_tells_remain(project, monkeypatch):
    monkeypatch.setattr("bookforge.core.deai.scrub", lambda text: text)
    monkeypatch.setattr("bookforge.core.deai.scrub_report", lambda text: (text, {"skipped": 1}))
    project.save_outline([{"id": "ch01", "title": "Dock", "summary": "Finn runs.",
                           "scenes": [{"scene_number": 1, "action": "runs"}]}])
    project.save_characters({})
    project.save_world({})
    project.write_text(project.chapter_path("ch01", "revised"),
                       "He paused \u2014 then ran to the dock before the bell.")
    from bookforge.steps import deai_chapter
    with pytest.raises(RuntimeError, match="token tells remain"):
        deai_chapter.run(project, "ch01")
    report = next(project.steps_dir.glob("*deai_ch01/detector_report.txt")).read_text()
    assert "token gate failed" in report and "token_tells_after:" in report and "em_dash" in report


def test_deai_chapter_token_gate_respects_fail_on_tokens_false(project, monkeypatch):
    config.set_config({"deai": {"fail_on_tokens": False}})
    monkeypatch.setattr("bookforge.core.deai.scrub", lambda text: text)
    monkeypatch.setattr("bookforge.core.deai.scrub_report", lambda text: (text, {}))
    project.save_outline([{"id": "ch01", "title": "Dock", "summary": "Finn runs.",
                           "scenes": [{"scene_number": 1, "action": "runs"}]}])
    project.save_characters({})
    project.save_world({})
    project.write_text(project.chapter_path("ch01", "revised"),
                       "He paused \u2014 then ran to the dock before the bell.")
    from bookforge.steps import deai_chapter
    deai_chapter.run(project, "ch01")
    assert "\u2014" in project.chapter_path("ch01", "final").read_text()


def test_leverage_after_modal_verbs():
    from bookforge.core.detector import detect
    for modal in ("would", "could", "should", "will", "can", "might not"):
        text = f"The captain {modal} leverage the old winch before the tide turns."
        assert detect(text)["counts"].get("skill_banlist", 0) == 1, modal


def test_vendored_manifest_has_no_local_paths_or_personal_examples():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "bookforge/style/vendor/VENDORED.json").read_text())
    for src in manifest["sources"].values():
        assert not src["path"].startswith("/"), src["path"]
    extract = (root / "bookforge/style/vendor/de-ai-revision/rules_extract.md").read_text()
    assert "girls" not in extract.lower()
