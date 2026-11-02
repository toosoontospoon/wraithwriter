"""Write-step gating and the latin-pivot Click commands. Offline."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from bookforge import config
from bookforge.cli import cli
from bookforge.core import jev, llm, progress, talkie
from bookforge.core.latin_pivot.pipeline import enabled
from bookforge.core.latin_pivot.translate import LATIN_DRAFT_INSTRUCTION
from bookforge.steps import write_chapter
from latin_pivot_fakes import ENGLISH_BOY, LATIN_BOY, SEED, SEED_INNER, jev_noul

pytestmark = pytest.mark.usefixtures("block_network")


def _setup(project):
    for step in ("brainstorm", "outline", "initialize_cast_and_world"):
        progress.run_step(project, step)


def _talk(prompt, n_predict, seed, stop, **kw):
    target = prompt.rsplit("Latin:\n", 1)[-1].split("\n\nEnglish:", 1)[0].strip()
    if target == SEED or target.startswith("<<SEED>>") and target.endswith("<</SEED>>"):
        return target
    return ENGLISH_BOY


def test_standard_write_does_not_enter_latin_pivot(project, fake):
    _setup(project)
    assert enabled(project) is False

    def boom(*_a, **_k):
        raise AssertionError("talkie called during a standard write")

    talkie.set_fake(boom)
    jev.set_fake(boom)
    write_chapter.run(project, "ch01")
    assert not any(LATIN_DRAFT_INSTRUCTION.split(".")[0] in (prompt or "") for _role, prompt, _sys in fake.calls)
    assert not (project.project_dir / "runs" / "latin_pivot").exists()


def test_config_mode_and_cli_flag_turn_it_on(project, fake, monkeypatch):
    _setup(project)
    monkeypatch.setattr("bookforge.core.latin_pivot.talkie_lifecycle.ping_endpoint", lambda *_a, **_k: (True, "ok"))
    talkie.set_fake(_talk)
    jev.set_fake(jev_noul())

    def hook(prompt, system):
        if "classical-style Latin" in (prompt or ""):
            return LATIN_BOY
        return None

    fake.hooks["write"] = hook
    config.set_config({"draft": {"mode": "latin_pivot"}, "latin_pivot": {"fidelity": {"jev": True}}})
    write_chapter.run(project, "ch01")
    prompts = [prompt for role, prompt, _sys in fake.calls if role == "write"]
    assert prompts and all("classical-style Latin" in prompt for prompt in prompts)
    assert (project.project_dir / "runs" / "latin_pivot").exists()
    assert enabled(cfg=config.load_config()) is True

    # Flag path, mode left at the library default. A fresh chapter cache so write runs again.
    project.progress["scenes"].pop("ch01", None)
    project.progress["hashes"].pop("write:ch01", None)
    project.save_progress()
    for path in (project.manuscript_dir / "ch01").glob("scene*_draft.txt"):
        path.unlink()
    draft = project.chapter_path("ch01", "draft")
    if draft.exists():
        draft.unlink()
    config.set_config({})
    assert config.load_config()["draft"]["mode"] == "standard"
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--project", str(project.project_dir), "--step", "write",
                                  "--chapter", "ch01", "--latin-pivot"])
    assert result.exit_code == 0, result.output
    flagged = [prompt for role, prompt, _sys in fake.calls if role == "write" and "classical-style Latin" in prompt]
    assert flagged


def test_write_step_keeps_an_em_dash_inside_a_mid_paragraph_seed(project, fake, monkeypatch):
    """Craft scrub runs on the scene after markers are stripped. A mid-paragraph seed must survive."""
    _setup(project)
    monkeypatch.setattr("bookforge.core.latin_pivot.talkie_lifecycle.ping_endpoint", lambda *_a, **_k: (True, "ok"))
    seed = 'She waited\u2014"Not a word," she told the boy.'
    seed_path = project.manuscript_dir / "ch01" / "human_seed.md"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(seed + "\n", encoding="utf-8")
    wrapped = "<<SEED>>" + seed + "<</SEED>>"
    latin = (
        "Puer in agro ambulabat et canem parvum prope viam longam vidit hodie. "
        + wrapped + " Deinde tacuit in camera quieta."
    )
    english = (
        "A boy walked in the field and saw a small dog near the long road today. "
        + wrapped + " Then the room stayed quiet."
    )

    def hook(prompt, system):
        if "classical-style Latin" not in (prompt or ""):
            return None
        if "HUMAN SEED" in prompt:
            return latin
        return LATIN_BOY

    def talk(prompt, n_predict, seed_n, stop, **kw):
        target = prompt.rsplit("Latin:\n", 1)[-1].split("\n\nEnglish:", 1)[0]
        if "<<SEED>>" in target:
            return english
        return ENGLISH_BOY

    fake.hooks["write"] = hook
    talkie.set_fake(talk)
    jev.set_fake(jev_noul())
    config.set_config({"draft": {"mode": "latin_pivot"}, "latin_pivot": {"fidelity": {"jev": False}}})
    write_chapter.run(project, "ch01")
    scene = project.scene_path("ch01", 1, "draft").read_text(encoding="utf-8")
    chapter = project.chapter_path("ch01", "draft").read_text(encoding="utf-8")
    assert seed in scene
    assert seed in chapter
    assert "\u2014" in scene
    assert "<<SEED>>" not in scene
    assert "<<SEED>>" not in chapter


def test_cli_smoke_latin_file_and_dry_run(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "metadata.json").write_text("{}\n", encoding="utf-8")
    latin = tmp_path / "sample.txt"
    latin.write_text(LATIN_BOY + "\n\n" + SEED + "\n", encoding="utf-8")
    calls = {"talk": 0, "jev": 0, "llm": 0}

    def talk(prompt, n_predict, seed, stop, **kw):
        calls["talk"] += 1
        assert kw["temperature"] == 0
        assert kw["top_k"] == 1
        assert kw["cache_prompt"] is True
        assert prompt.endswith("English:\n")
        return ENGLISH_BOY

    def boom(*_a, **_k):
        calls["llm"] += 1
        raise AssertionError("gemma called")

    talkie.set_fake(talk)
    jev.set_fake(jev_noul())
    llm.set_fake(boom)
    from bookforge.core.latin_pivot import talkie_lifecycle
    talkie_lifecycle.set_ping_fake(lambda *_a, **_k: (True, "ok"))
    runner = CliRunner()
    dry = runner.invoke(cli, ["latin-pivot", "run", "--project", str(proj), "--chapter", "ch01",
                               "--latin-file", str(latin), "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "paragraphs: 2" in dry.output
    assert "stages: latin, translate, fidelity, tag, synonyms, pick, scrub" in dry.output
    assert "cost_usd_est:" in dry.output
    assert calls == {"talk": 0, "jev": 0, "llm": 0}

    est = runner.invoke(cli, ["latin-pivot", "estimate", "--project", str(proj), "--chapter", "ch01",
                               "--latin-file", str(latin)])
    assert est.exit_code == 0, est.output
    assert "talkie_usd: 0" in est.output
    assert calls == {"talk": 0, "jev": 0, "llm": 0}

    out = tmp_path / "out"
    result = runner.invoke(cli, ["latin-pivot", "run", "--project", str(proj), "--chapter", "ch01",
                                  "--latin-file", str(latin), "--out", str(out), "--no-jev"])
    assert result.exit_code == 0, result.output
    final = (out / "final.txt").read_text(encoding="utf-8")
    assert SEED_INNER in final
    assert "<<SEED>>" not in final
    assert (out / "latin.txt").exists()
    assert (out / "english_raw.txt").exists()
    assert (out / "fidelity.jsonl").exists()
    assert (out / "tags.jsonl").exists()
    assert (out / "swaps.jsonl").exists()
    assert (out / "run.json").exists()
    assert (out / "detect.json").exists()
    report = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert report["aborted_reason"] is None
    assert calls["llm"] == 0
    assert calls["talk"] == 1
    talkie_lifecycle.set_ping_fake(None)
