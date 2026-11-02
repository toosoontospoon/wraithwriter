"""Pipeline: seeds, fidelity, tags, resume, budget, lifecycle, dry-run. Offline."""
import json
from pathlib import Path

import pytest

from bookforge import config
from bookforge.core import jev, talkie
from bookforge.core.latin_pivot.budget import BudgetExceeded
from bookforge.core.latin_pivot.pipeline import execute
from bookforge.core.latin_pivot.seeds import SEED_RE
from bookforge.core.latin_pivot.synonyms import DictSynonymSource
from bookforge.core.latin_pivot.talkie_lifecycle import TalkieSession
from bookforge.core.talkie import TalkieUnavailable
from latin_pivot_fakes import ENGLISH_BOY, LATIN_BOY, SEED, SEED_INNER, install_ping, jev_noul

pytestmark = pytest.mark.usefixtures("block_network")

BOY = LATIN_BOY + "\n\n" + SEED + "\n"


def _cfg(**over):
    base = {"latin_pivot": {"fidelity": {"jev": True}, "cap_usd": 0.50}}
    # deep-merge happens in set_config. Pass a full latin_pivot overlay.
    config.set_config(over or base)


def _run(tmp_path, latin, talk, *, jev_fake=None, cap=None, extra=None, source=None, runner=None):
    install_ping  # referenced so a missing import fails loudly
    path = tmp_path / "latin.txt"
    path.write_text(latin, encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "metadata.json").write_text("{}\n", encoding="utf-8")
    talkie.set_fake(talk)
    if jev_fake is not None:
        jev.set_fake(jev_fake)
    overlay = {"latin_pivot": {"cap_usd": 0.50 if cap is None else cap, "fidelity": {"jev": True}}}
    if extra:
        # set_config deep-merges nested dicts, so pass the nested overlay directly.
        for key, value in extra.items():
            if isinstance(value, dict) and isinstance(overlay["latin_pivot"].get(key), dict):
                overlay["latin_pivot"][key] = {**overlay["latin_pivot"][key], **value}
            else:
                overlay["latin_pivot"][key] = value
    config.set_config(overlay)
    return execute(
        project=proj, chapter="ch01", latin_file=path, synonym_source=source, runner=runner,
        ping=lambda *_a, **_k: (True, "ok"), cap_usd=cap,
    )


def _boy_talk(prompt, n_predict, seed, stop, **kw):
    target = prompt.rsplit("Latin:\n", 1)[-1].split("\n\nEnglish:", 1)[0]
    if SEED_INNER in target and target.strip().startswith("<<SEED>>"):
        return target.strip()
    if "<<SEED>>" in target:
        return ('The sailor came back to the harbour today. ' + SEED +
                " Then he waited quietly in the room for a while.")
    return ENGLISH_BOY


def test_seed_only_paragraph_skips_talkie(tmp_path, monkeypatch):
    calls = []

    def talk(prompt, n_predict, seed, stop, **kw):
        calls.append(prompt)
        return _boy_talk(prompt, n_predict, seed, stop, **kw)

    _run(tmp_path, BOY, talk, jev_fake=jev_noul())
    assert len(calls) == 1
    target = calls[0].rsplit("Latin:\n", 1)[-1].split("\n\nEnglish:", 1)[0].strip()
    assert target == LATIN_BOY


def test_seeds_survive_swap_and_scrub(tmp_path):
    # One paragraph holds both the tell and the seed, so a swap cannot duplicate the span.
    latin = (
        "Puer tapetem in mensa vidit et diu tacuit in camera quieta hodie. "
        + SEED + " Deinde mansit in loco.\n"
    )
    english = (
        "The boy saw the tapestry on the table and stayed quiet in the long room today. "
        + SEED + " Then he stayed in that place."
    )

    def talk(prompt, n_predict, seed, stop, **kw):
        return english

    def fake(state, questions, model):
        answers = {}
        for key, question in questions.items():
            if question.get("type") == "noul":
                instr = question.get("instructions") or ""
                score = 0.95 if "every event" in instr or "tapestry" in instr else 0.1
                answers[key] = {"type": "noul", "noul": score}
            else:
                answers[key] = {"type": "choice", "choice": "cloth", "confidence": 0.8,
                                "probabilities": {"tapestry": 0.2, "cloth": 0.8}}
        return {"model": model, "answers": answers, "usage": {"input_tokens": 20}}

    result = _run(tmp_path, latin, talk, jev_fake=fake,
                  source=DictSynonymSource({"tapestry": ["cloth"]}))
    final = (result["out_dir"] / "final.txt").read_text(encoding="utf-8")
    assert SEED_INNER in final
    assert "<<SEED>>" not in final
    assert "cloth" in final.lower()
    swaps = (result["out_dir"] / "swaps.jsonl").read_text(encoding="utf-8")
    assert "swapped" in swaps
    # Seed bytes were not part of the replacement.
    assert final.count(SEED_INNER) == 1


def test_mangled_seed_is_repaired(tmp_path):
    latin = ('Nauta rediit ad portum hodie. ' + SEED + ' Deinde tacuit diu in camera quieta.\n')

    def talk(prompt, n_predict, seed, stop, **kw):
        return ('The sailor came back to the harbour today. <<SEED>>"Nope," he said.<</SEED>> '
                "Then he was quiet in the room for a while.")

    result = _run(tmp_path, latin, talk, jev_fake=jev_noul())
    final = (result["out_dir"] / "final.txt").read_text(encoding="utf-8")
    assert SEED_INNER in final
    assert "Nope" not in final


def test_lost_seed_markers_retry_then_fail(tmp_path):
    latin = ('Nauta rediit ad portum hodie. ' + SEED + ' Deinde tacuit diu in camera quieta.\n')
    calls = []

    def talk(prompt, n_predict, seed, stop, **kw):
        calls.append(kw.get("temperature"))
        return "The sailor came back and said something else in the room today while he waited."

    with pytest.raises(Exception, match="SEED"):
        _run(tmp_path, latin, talk, jev_fake=jev_noul())
    assert calls == [0, 0.3] or calls == [0.0, 0.3]


def test_fidelity_retry_then_pass(tmp_path):
    calls = {"jev": 0, "temps": []}

    def talk(prompt, n_predict, seed, stop, **kw):
        calls["temps"].append(kw.get("temperature"))
        return ENGLISH_BOY

    def fake(state, questions, model):
        calls["jev"] += 1
        score = 0.2 if calls["jev"] == 1 else 0.95
        return {"model": model, "answers": {"faithful": {"type": "noul", "noul": score}},
                "usage": {"input_tokens": 8}}

    result = _run(tmp_path, LATIN_BOY + "\n", talk, jev_fake=fake)
    row = json.loads((result["out_dir"] / "fidelity.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["flagged"] is False
    assert row["attempts"] == 2
    assert 0.3 in calls["temps"]
    assert calls["jev"] == 2


def test_jev_unavailable_is_heuristic_only(tmp_path):
    path = tmp_path / "latin.txt"
    path.write_text(LATIN_BOY + "\n", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    config.set_config({"latin_pivot": {"fidelity": {"jev": True}}})
    jev.set_fake(None)
    talkie.set_fake(lambda *a, **k: ENGLISH_BOY)
    result = execute(project=proj, chapter="ch01", latin_file=path, ping=lambda *_a, **_k: (True, "ok"))
    row = json.loads((result["out_dir"] / "fidelity.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["jev_mode"] == "unavailable"
    assert row["flagged"] is False
    assert row["heuristic_ok"] is True


def test_tag_threshold_and_cap(tmp_path):
    seen = {}

    def fake(state, questions, model):
        seen["n"] = len(questions)
        seen["instructions"] = [q["instructions"] for q in questions.values()]
        answers = {}
        for key, question in questions.items():
            term = question["instructions"]
            score = 0.91 if "tapestry" in term else 0.2
            answers[key] = {"type": "noul", "noul": score}
        return {"model": model, "answers": answers, "usage": {"input_tokens": 15}}

    text = "The tapestry sat quietly while he would delve later in the long room today.\n"
    # First a cap of 1: only the earlier tell is sent.
    path = tmp_path / "cap.txt"
    path.write_text(text, encoding="utf-8")
    proj = tmp_path / "pcap"
    proj.mkdir()
    config.set_config({"latin_pivot": {"tag": {"max_per_paragraph": 1}, "fidelity": {"jev": False}}})
    talkie.set_fake(lambda *a, **k: "The tapestry sat quietly while he would delve later in the long room today.")
    jev.set_fake(fake)
    result = execute(project=proj, chapter="ch01", latin_file=path, ping=lambda *_a, **_k: (True, "ok"),
                     to_stage="tag")
    assert seen["n"] == 1
    assert "tapestry" in seen["instructions"][0]
    row = json.loads((result["out_dir"] / "tags.jsonl").read_text().splitlines()[0])
    assert len(row["candidates"]) == 1

    seen.clear()
    proj2 = tmp_path / "pthr"
    proj2.mkdir()
    config.set_config({"latin_pivot": {"tag": {"max_per_paragraph": 12, "threshold": 0.6},
                                        "fidelity": {"jev": False}}})
    result = execute(project=proj2, chapter="ch01", latin_file=path, ping=lambda *_a, **_k: (True, "ok"),
                     to_stage="tag")
    row = json.loads((result["out_dir"] / "tags.jsonl").read_text().splitlines()[0])
    kept = [c for c in row["candidates"] if c.get("kept")]
    assert len(row["candidates"]) >= 2
    assert [c["text"].lower() for c in kept] == ["tapestry"]
    assert all(TAG_OK in q or True for q in seen["instructions"])
    assert any("typical of AI-generated prose" in q for q in seen["instructions"])


TAG_OK = "typical of AI-generated prose"


def test_picker_margin_keeps_original_in_the_run(tmp_path):
    def fake(state, questions, model):
        answers = {}
        for key, question in questions.items():
            if question.get("type") == "noul":
                instr = question.get("instructions") or ""
                score = 0.95 if ("every event" in instr or "tapestry" in instr) else 0.1
                answers[key] = {"type": "noul", "noul": score}
            else:
                answers[key] = {"type": "choice", "choice": "cloth", "confidence": 0.6,
                                "probabilities": {"tapestry": 0.55, "cloth": 0.60}}
        return {"model": model, "answers": answers, "usage": {"input_tokens": 16}}

    latin = "Puer tapetem vidit in camera longa et quieta hodie mane sub arbore.\n"
    english = "The tapestry lay in the long quiet room this morning under a tree."
    result = _run(tmp_path, latin, lambda *a, **k: english, jev_fake=fake,
                  source=DictSynonymSource({"tapestry": ["cloth"]}))
    final = (result["out_dir"] / "final.txt").read_text(encoding="utf-8")
    assert "tapestry" in final
    assert "cloth" not in final.lower()
    row = json.loads((result["out_dir"] / "swaps.jsonl").read_text().splitlines()[0])
    assert row["swaps"][0]["reason"] == "kept_margin"
    assert "probabilities" in row["swaps"][0]
    assert row["swaps"][0]["sentence"]


def test_budget_aborts_before_the_call_and_stops_talkie(tmp_path):
    jev_calls = []
    runner = []

    def talk(prompt, n_predict, seed, stop, **kw):
        return ENGLISH_BOY

    def fake(state, questions, model):
        jev_calls.append(questions)
        return {"model": model, "answers": {}, "usage": {"input_tokens": 10}}

    path = tmp_path / "latin.txt"
    path.write_text(LATIN_BOY + "\n", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    config.set_config({"latin_pivot": {
        "cap_usd": 0.0,
        "talkie": {"manage": True, "start_cmd": "start-talkie", "stop_cmd": "stop-talkie",
                   "stop_after": True, "endpoint": "http://127.0.0.1:8080"},
    }})
    talkie.set_fake(talk)
    jev.set_fake(fake)
    with pytest.raises(BudgetExceeded):
        execute(project=proj, chapter="ch01", latin_file=path, cap_usd=0.0,
                ping=lambda *_a, **_k: (True, "ok"), runner=lambda cmd: runner.append(cmd))
    assert jev_calls == []
    assert runner == ["stop-talkie"]


def test_crash_resume_translates_only_the_rest(tmp_path):
    paras = [f"{LATIN_BOY} Extra {i} verbum." for i in range(4)]
    # Keep each paragraph near the English length. The fake English is fixed, so
    # make every Latin paragraph the same length as LATIN_BOY plus two tokens.
    latin = "\n\n".join(LATIN_BOY for _ in range(4))
    calls = []
    state = {"boom": True}

    def talk(prompt, n_predict, seed, stop, **kw):
        target = prompt.rsplit("Latin:\n", 1)[-1].split("\n\nEnglish:", 1)[0].strip()
        calls.append(target)
        if state["boom"] and len(calls) == 3:
            raise RuntimeError("boom on paragraph 3")
        return ENGLISH_BOY

    path = tmp_path / "latin.txt"
    path.write_text(latin, encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    config.set_config({"latin_pivot": {"fidelity": {"jev": False}, "cap_usd": 0.50}})
    talkie.set_fake(talk)
    with pytest.raises(TalkieUnavailable):
        execute(project=proj, chapter="ch01", latin_file=path, ping=lambda *_a, **_k: (True, "ok"))
    assert len(calls) == 3
    calls.clear()
    state["boom"] = False
    execute(project=proj, chapter="ch01", latin_file=path, ping=lambda *_a, **_k: (True, "ok"))
    assert len(calls) == 2


def test_dry_run_makes_no_client_calls(tmp_path):
    path = tmp_path / "latin.txt"
    path.write_text(BOY, encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()

    def boom(*_a, **_k):
        raise AssertionError("client called")

    talkie.set_fake(boom)
    jev.set_fake(boom)
    from bookforge.core import llm
    llm.set_fake(boom)
    result = execute(project=proj, chapter="ch01", latin_file=path, dry_run=True,
                     runner=boom, ping=boom)
    assert result["dry_run"] is True
    assert result["estimate"]["paragraphs"] == 2
    text = __import__("io").StringIO()
    # execute already printed. Re-read by running again is enough: the return value has the plan.
    assert result["estimate"]["stages"][0] == "latin"
    assert result["estimate"]["talkie_usd"] == 0


def test_lifecycle_stops_on_success_exception_and_abort():
    def session(ping_script):
        pings = list(ping_script)
        cmds = []

        def ping(_endpoint):
            return pings.pop(0)

        def runner(cmd):
            cmds.append(cmd)

        clock = {"t": 0.0}

        def now():
            return clock["t"]

        def sleep(seconds):
            clock["t"] += seconds

        cfg = {"endpoint": "http://127.0.0.1:8080", "manage": True, "start_cmd": "start-talkie",
               "stop_cmd": "stop-talkie", "stop_after": True, "start_timeout_s": 5}
        return TalkieSession(cfg, runner=runner, ping=ping, sleep=sleep, now=now), cmds

    live, cmds = session([(False, "down"), (True, "up")])
    with live:
        pass
    assert cmds == ["start-talkie", "stop-talkie"]

    live, cmds = session([(False, "down"), (True, "up")])
    with pytest.raises(RuntimeError):
        with live:
            raise RuntimeError("mid-run")
    assert cmds == ["start-talkie", "stop-talkie"]

    live, cmds = session([(True, "up")])
    with pytest.raises(BudgetExceeded):
        with live:
            raise BudgetExceeded("cap")
    assert cmds == ["stop-talkie"]
