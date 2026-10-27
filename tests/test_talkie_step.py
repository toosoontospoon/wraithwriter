"""Pipeline no-op when talkie is off, CLI file mode, and chapter-summary math."""
import json
import re

from click.testing import CliRunner

from bookforge.cli import cli
from bookforge.core import jev, llm, talkie, talkie_roll as R
from bookforge.steps import talkie_chapter

SRC = "Nia locked the gate and pocketed the iron key."
SWAP = {
    "locked": "shut", "gate": "wicket", "and": "then", "pocketed": "kept",
    "the": "that", "iron": "metal", "key": "token",
}


def reword(text):
    def sub(match):
        word = match.group(0)
        repl = SWAP.get(word.lower())
        if not repl:
            return word
        if word[:1].isupper():
            return repl[:1].upper() + repl[1:]
        return repl
    return re.sub(r"[A-Za-z']+", sub, text)


def _boom(*a, **k):
    raise AssertionError("model called while talkie is disabled")


def test_disabled_makes_no_model_calls(project, fake):
    revised = project.chapter_path("ch01", "revised")
    revised.write_text(SRC + "\n")
    before = len(fake.calls)
    talkie.set_fake(_boom)
    jev.set_fake(_boom)
    llm.set_fake(_boom)
    out = talkie_chapter.run(project, "ch01")
    assert out == {"skipped": True}
    assert not project.chapter_path("ch01", "talkie").exists()
    assert revised.read_text() == SRC + "\n"
    assert len(fake.calls) == before
    assert project.progress["chapter_status"].get("ch01") == "talkie"


def test_cli_file_writes_out_and_logs(tmp_path):
    src = tmp_path / "in.txt"
    out = tmp_path / "out.txt"
    src.write_text(SRC + "\n")

    def talk(prompt, n_predict, seed, stop, ban_nl=False, **kw):
        body = prompt.split("Pupil's version:\n\n")[-1].split("\n\nMaster's version:")[0]
        return reword(body)

    def jev_ok(state, questions, model):
        return {"answers": {qid: {"noul": 0.1} for qid in questions},
                "usage": {"input_tokens": 1}, "model": model}

    talkie.set_fake(talk)
    jev.set_fake(jev_ok)
    llm.set_fake(_boom)
    result = CliRunner().invoke(cli, ["talkie", "run", "--file", str(src), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert src.read_text() == SRC + "\n"
    written = out.read_text().strip()
    assert written == reword(SRC)
    log_dir = tmp_path / "out_talkie"
    rows = [json.loads(line) for line in (log_dir / "talkie.jsonl").read_text().splitlines()]
    assert rows and rows[0]["final"] == reword(SRC)
    summary = json.loads((log_dir / "talkie_summary.json").read_text())
    assert summary["paragraphs"] == 1
    assert summary["passes"] == 1


def test_summary_math():
    rows = [
        {"final": "one two three four", "passed": True, "kept": 1.0, "clean": 0.5,
         "talkie_word_share": 0.8, "sol_word_share": 0.2, "sol_4gram_share": 0.1,
         "style_flags": [], "talkie_wall": 60.0,
         "targeted": {"kept": True, "talkie_wall": 0, "sol_usd": 0.02}},
        {"skipped": "marker", "final": "* * *"},
        {"final": "five six", "passed": False, "kept": 0.5, "clean": 1.0,
         "talkie_word_share": 0.6, "sol_word_share": 0.4, "sol_4gram_share": 0.3,
         "style_flags": ["mean"], "talkie_wall": 30.0,
         "targeted": {"kept": False, "talkie_wall": 0, "sol_usd": 0.01}},
    ]
    summary = R.summarize(rows, jev_sol_usd=0.12)
    assert summary["paragraphs"] == 2
    assert summary["passes"] == 1
    assert summary["facts_kept"] == 0.75
    assert summary["sentences_clean"] == 0.75
    assert summary["talkie_word_share"] == 0.7
    assert summary["sol_word_share"] == 0.3
    assert summary["mean_sol_4gram_share"] == 0.2
    assert summary["style_flagged"] == 1
    assert summary["targeted_pass_counts"] == {"attempted": 2, "kept": 1, "reverted": 1}
    assert summary["jev_sol_usd"] == 0.12
    assert summary["talkie_minutes"] == 1.5
    assert summary["minutes_per_1k_words"] == 250.0


def test_deai_ignores_talkie_file_when_disabled_or_stale(project):
    import copy
    import os
    from bookforge import config as C
    revised = project.chapter_path("ch01", "revised")
    talk = project.chapter_path("ch01", "talkie")
    revised.write_text("revised\n")
    talk.write_text("talkie\n")
    base = copy.deepcopy(C.load_config())
    try:
        off = copy.deepcopy(base)
        off.setdefault("talkie", {})["enabled"] = False
        C.set_config(off)
        assert project.deai_source("ch01") == revised
        assert "talkie" not in project.best_chapter_text("ch01")
        on = copy.deepcopy(base)
        on.setdefault("talkie", {})["enabled"] = True
        C.set_config(on)
        st = revised.stat().st_mtime
        os.utime(talk, (st + 5, st + 5))
        assert project.deai_source("ch01") == talk
        os.utime(talk, (st - 5, st - 5))
        assert project.deai_source("ch01") == revised
    finally:
        C.set_config(base)


def test_gen_many_keeps_job_order(monkeypatch):
    import time as _t
    def fake_generate(prompt, src, seed, cfg=None, log_dir=None):
        _t.sleep(0.01 * (5 - seed))
        return {"text": str(seed), "wall": 1.0}
    monkeypatch.setattr(talkie, "generate", fake_generate)
    out = R._gen_many([("p", "s", k) for k in range(5)], {"parallel": 4}, None)
    assert [g["text"] for g in out] == ["0", "1", "2", "3", "4"]
    assert R._wall(out, {"parallel": 4}) == 5.0 / 4
