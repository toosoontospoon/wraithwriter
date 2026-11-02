"""Motif ban, always-flag list, and density. Offline, fakes only."""
import json
import re

import pytest
from click.testing import CliRunner

from bookforge import config
from bookforge.cli import cli
from bookforge.config import DEFAULT_CONFIG
from bookforge.core import jev, llm, talkie
from bookforge.core.latin_pivot.budget import BudgetExceeded, gemma_cost
from bookforge.core.latin_pivot.motifs import (
    LATIN_PATTERNS,
    is_stillness_sense,
    latin_hits,
    match_always_flag,
    motif_density,
    motif_instruction,
)
from bookforge.core.latin_pivot.pipeline import execute
from bookforge.core.latin_pivot.synonyms import DictSynonymSource
from bookforge.core.latin_pivot.tagger import TAG_QUESTION
from latin_pivot_fakes import ENGLISH_BOY, LATIN_BOY

pytestmark = pytest.mark.usefixtures("block_network")

_STILL = re.compile(r"\bstill\b", re.I)

ADVERBS = [
    "He was still waiting by the door.",
    "She is still running down the lane.",
    "He was still not ready to leave.",
    "There was still more light on the water.",
    "Still, she went down to the harbour.",
    "They were still there when the bell rang.",
    "It could still be true in the morning.",
    "She had still hoped for a letter.",
    "Still less did the boy understand the map.",
    "He would still be gone at dusk.",
]
STILLNESS = [
    "The still air hung over the field.",
    "A still pond lay under the trees.",
    "This still room smelled of dust.",
    "That still hour before dawn.",
    "The boy stood still beside the gate.",
    "She sat still and watched the road.",
    "The house was still.",
    "The house was still, and the fire was out.",
    "In the still of the night a dog barked.",
    "His still face gave nothing away.",
    "The dog lay still on the step.",
    "They kept still until the cart passed.",
]
AMBIGUOUS = "The boy still likes the long river road."


def _sense(sentence: str):
    match = _STILL.search(sentence)
    assert match, sentence
    return is_stillness_sense(sentence, (match.start(), match.end()))


def _proj(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "metadata.json").write_text("{}\n", encoding="utf-8")
    return proj


def _ping(*_a, **_k):
    return True, "ok"


def test_latin_true_and_false_positives_and_seed_spans():
    hits = latin_hits("Silva tactus mutare silentium tacuit quieti susurrans.")
    assert [hit["word"] for hit in hits] == ["silentium", "tacuit", "quieti", "susurrans"]
    assert latin_hits("silva tactus mutare mutatus mutual silver quite") == []
    assert latin_hits("<<SEED>>silentium tacuit quieti<</SEED>> silva") == []
    seeded = 'Before <<SEED>>the silence was still.<</SEED>> the road stayed open.'
    assert match_always_flag(seeded) == []
    folded = match_always_flag("He said   nothing, then left without  a   word. A hushing. The silences. A quieter lane.")
    motifs = [hit["motif"] for hit in folded]
    assert "said nothing" in motifs
    assert "without a word" in motifs
    assert "hush" in motifs
    assert "silence" in motifs
    assert "quiet" in motifs
    assert match_always_flag("distill the install") == []


def test_still_heuristic_adverb_stillness_and_ambiguous():
    assert len(ADVERBS) >= 8 and len(STILLNESS) >= 8
    assert [_sense(sentence) for sentence in ADVERBS] == [False] * len(ADVERBS)
    assert [_sense(sentence) for sentence in STILLNESS] == [True] * len(STILLNESS)
    assert _sense(AMBIGUOUS) is None
    assert match_always_flag(ADVERBS[0]) == []
    flagged = match_always_flag(STILLNESS[0])
    assert len(flagged) == 1 and flagged[0]["motif"] == "still" and flagged[0]["ambiguous"] is False
    amb = match_always_flag(AMBIGUOUS)
    assert amb[0]["ambiguous"] is True and amb[0]["sense"] is None


def test_motif_density_rounds_eight_hits_in_570_words():
    text = ("silence " * 8 + "road " * 562).strip()
    assert motif_density(text, match_always_flag) == {"hits": 8, "words": 570, "per_1k": 14.0}
    assert motif_density("", match_always_flag) == {"hits": 0, "words": 0, "per_1k": 0.0}
    # Adverb "still" is not a hit, so it does not inflate the rate.
    adverb = "He was still waiting by the door."
    assert motif_density(adverb, match_always_flag)["hits"] == 0


def test_defaults_carry_tunable_patterns_and_the_phrase_lists():
    ban = DEFAULT_CONFIG["latin_pivot"]["motif_ban"]
    flag = DEFAULT_CONFIG["latin_pivot"]["always_flag"]
    assert ban["enabled"] is True and ban["action"] == "regenerate" and ban["max_regens"] == 1
    assert "said nothing" in ban["english"] and "without a word" in ban["english"]
    for lemma in ("silentium", "silens", "silere", "tacere", "tacitus", "taciturnus",
                  "quies", "quietus", "quiescere", "susurrus", "susurrare",
                  "conticescere", "obticescere", "mutus"):
        assert lemma in ban["latin"]
    assert ban["latin_patterns"] == list(LATIN_PATTERNS)
    assert flag["enabled"] is True and flag["still_classifier"] == "jev"
    for phrase in ("silence", "silently", "quieted", "still", "words left unsaid",
                   "kept silent", "fell silent", "went quiet"):
        assert phrase in flag["phrases"]
    note = motif_instruction(ban["english"], ban["latin"])
    assert "silence or quiet as a motif" in note
    assert "silentium" in note and "said nothing" in note


def test_flag_path_writes_latin_motif_hits_without_a_draft_call(tmp_path):
    latin = LATIN_BOY + "\n\nDeinde tacuit in camera longa hodie sub arbore magno.\n"
    path = tmp_path / "latin.txt"
    path.write_text(latin, encoding="utf-8")
    proj = _proj(tmp_path)

    def boom(*_a, **_k):
        raise AssertionError("gemma called for a supplied latin file")

    def talk(prompt, n_predict, seed, stop, **kw):
        target = prompt.rsplit("Latin:\n", 1)[-1]
        if "tacuit" in target:
            return "Then he stayed in the long room under a tree."
        return ENGLISH_BOY

    llm.set_fake(boom)
    talkie.set_fake(talk)
    jev.set_fake(lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("jev")))
    config.set_config({"latin_pivot": {
        "fidelity": {"jev": False},
        "motif_ban": {"action": "flag"},
    }})
    result = execute(project=proj, chapter="ch01", latin_file=path, ping=_ping, no_jev=True)
    rows = json.loads((result["out_dir"] / "latin_motif_hits.json").read_text(encoding="utf-8"))
    assert rows == [{
        "paragraph_index": 1,
        "word": "tacuit",
        "sentence": "Deinde tacuit in camera longa hodie sub arbore magno.",
    }]
    report = json.loads((result["out_dir"] / "run.json").read_text(encoding="utf-8"))
    assert report["latin_motif_hits"] == rows
    assert report["motif_density"]["latin"]["hits"] == 1
    assert report["motif_density"]["english_raw"]["hits"] == 0
    assert report["motif_density"]["final"]["hits"] == 0
    assert "tacuit" in (result["out_dir"] / "latin.txt").read_text(encoding="utf-8")


def _outline(project):
    from bookforge.core import progress
    for step in ("brainstorm", "outline", "initialize_cast_and_world"):
        progress.run_step(project, step)


def _first_write_estimate(ctx, chapter, scene):
    """The cost the latin stage will estimate for the first write call of this scene."""
    from bookforge.config import resolve_role
    from bookforge.core import pack
    from bookforge.core.deai import scrub_obj
    from bookforge.core.human_seed import continuation_note, seed_for_scene
    from bookforge.core.latin_pivot.translate import draft_instruction
    from bookforge.craft import engine as craft
    from bookforge.core import length as length_mod
    from bookforge.steps import write_chapter as wc

    cfg = config.load_config()
    opening = seed_for_scene(ctx, chapter, scene) or ""
    brief = craft.brief(ctx, chapter, scene) or ""
    card = {**scene, "craft_brief": brief} if brief else dict(scene)
    beats = scrub_obj(list(card.get("beats") or [card.get("action") or "Write the scene."]))
    scene_for_write = {**scene, "beats": beats, "craft_brief": brief}
    groups = wc._beat_groups(ctx, scene_for_write)
    ban = cfg["latin_pivot"]["motif_ban"]
    extra = draft_instruction([ln for ln in opening.splitlines() if ln.strip()] if opening else [])
    extra = extra + "\n\n" + motif_instruction(ban.get("english"), ban.get("latin"))
    group = groups[0]
    words = length_mod.ceiling(sum(length_mod.beat_words(b) for b in group))
    system, prompt = pack.build_write(ctx, chapter, scene_for_write, group, "", words, 1, len(groups))
    if opening:
        prompt += "\n\n" + continuation_note(opening)
    prompt += "\n\n" + extra
    provider, model = resolve_role("write", cfg)
    system, prompt = pack.fit_write_context(ctx, system, prompt, provider, model)
    return gemma_cost(system, prompt, cfg)


def test_regenerate_names_the_hit_and_stays_inside_the_cap(project, fake, monkeypatch):
    _outline(project)
    monkeypatch.setattr("bookforge.core.latin_pivot.talkie_lifecycle.ping_endpoint", _ping)
    talkie.set_fake(lambda *a, **k: ENGLISH_BOY)
    jev.set_fake(lambda *a, **k: {"model": "jev", "answers": {}, "usage": {"input_tokens": 1}})
    noted = []

    def hook(prompt, system):
        if "classical-style Latin" not in (prompt or ""):
            return None
        if "banned silence motif" in prompt:
            noted.append(prompt)
            assert "Tacuit" in prompt
            return LATIN_BOY
        return "Tacuit sub arbore longo hodie mane prope viam."

    fake.hooks["write"] = hook
    config.set_config({"draft": {"mode": "latin_pivot"}, "latin_pivot": {
        "fidelity": {"jev": False}, "cap_usd": 0.50,
    }})
    chapter = project.chapter("ch01")
    scene = chapter["scenes"][0]
    result = execute(
        project=project.project_dir, chapter="ch01", scene=1,
        ctx=project, chapter_obj=chapter, scene_obj=scene, step_dir=project.project_dir / "step",
        ping=_ping, no_jev=True, cap_usd=0.50,
    )
    latin = (result["out_dir"] / "latin.txt").read_text(encoding="utf-8")
    assert "tacuit" not in latin.lower()
    assert noted and all("silence or quiet" in prompt for prompt in noted)
    prompts = [prompt for _role, prompt, _sys in fake.calls]
    assert any("silence or quiet as a motif" in prompt for prompt in prompts)
    assert any(role == "plan" and "silence or quiet as a motif" in prompt and "silentium" in prompt
               for role, prompt, _sys in fake.calls)
    report = result["report"]
    assert report["aborted_reason"] is None
    assert report["spent_usd"] <= report["cap_usd"]
    assert not (result["out_dir"] / "latin_motif_hits.json").exists()


def test_regen_aborts_before_the_call_that_would_exceed_the_cap(project, fake, monkeypatch):
    _outline(project)
    config.set_config({
        "draft": {"mode": "latin_pivot"},
        "llm": {
            "roles": {"write": {"provider": "openrouter", "model": "motif-drafter"}},
            "prices": {
                "motif-drafter": {"input": 1.0, "output": 0.0},
                "openai/gpt-6-sol": {"input": 0.0, "output": 0.0},
            },
        },
        "latin_pivot": {"fidelity": {"jev": False}, "cap_usd": 100},
    })
    chapter = project.chapter("ch01")
    scene = chapter["scenes"][0]
    cap = _first_write_estimate(project, chapter, scene)
    assert cap > 0
    writes = []

    def hook(prompt, system):
        if "classical-style Latin" not in (prompt or ""):
            return None
        writes.append(prompt)
        if "banned silence motif" in prompt:
            raise AssertionError("regen call was sent after the cap")
        return "Tacuit sub arbore longo hodie mane prope viam."

    fake.hooks["write"] = hook
    with pytest.raises(BudgetExceeded):
        execute(
            project=project.project_dir, chapter="ch01", scene=1,
            ctx=project, chapter_obj=chapter, scene_obj=scene,
            step_dir=project.project_dir / "step",
            ping=_ping, cap_usd=cap, to_stage="latin",
        )
    assert len(writes) == 1
    assert "banned silence motif" not in writes[0]
    assert "Tacuit" not in writes[0]


def test_remaining_hits_after_regen_are_kept(project, fake, monkeypatch):
    _outline(project)
    monkeypatch.setattr("bookforge.core.latin_pivot.talkie_lifecycle.ping_endpoint", _ping)
    talkie.set_fake(lambda *a, **k: "Then he stayed in the long room under a tree today.")

    def hook(prompt, system):
        if "classical-style Latin" not in (prompt or ""):
            return None
        return "Tacuit sub arbore longo hodie mane prope viam."

    fake.hooks["write"] = hook
    config.set_config({"draft": {"mode": "latin_pivot"}, "latin_pivot": {
        "fidelity": {"jev": False}, "cap_usd": 5, "motif_ban": {"max_regens": 1},
    }})
    chapter = project.chapter("ch01")
    scene = chapter["scenes"][0]
    result = execute(
        project=project.project_dir, chapter="ch01", scene=1,
        ctx=project, chapter_obj=chapter, scene_obj=scene, step_dir=project.project_dir / "step",
        ping=_ping, no_jev=True, cap_usd=5,
    )
    latin = (result["out_dir"] / "latin.txt").read_text(encoding="utf-8")
    assert "Tacuit" in latin
    rows = json.loads((result["out_dir"] / "latin_motif_hits.json").read_text(encoding="utf-8"))
    assert rows and rows[0]["word"] == "Tacuit"
    assert result["report"]["latin_motif_hits"][0]["word"] == "Tacuit"
    assert fake.count("write", "banned silence motif") >= 1


def test_always_flag_bypasses_jev_and_swaps(tmp_path):
    latin = LATIN_BOY + "\n"
    path = tmp_path / "latin.txt"
    path.write_text(latin, encoding="utf-8")
    proj = _proj(tmp_path)
    english = "The tapestry sat quietly in silence today near the road. He said nothing."
    asked = []

    def fake(state, questions, model):
        asked.append(questions)
        for question in questions.values():
            instr = question.get("instructions") or ""
            if "typical of AI-generated prose" in instr:
                lowered = instr.lower()
                assert "quietly" not in lowered
                assert "silence" not in lowered
                assert "said nothing" not in lowered
        answers = {}
        for key, question in questions.items():
            if question.get("type") == "noul":
                score = 0.95 if "tapestry" in (question.get("instructions") or "") else 0.1
                answers[key] = {"type": "noul", "noul": score}
            else:
                answers[key] = {"type": "choice", "choice": "cloth", "confidence": 0.9,
                                "probabilities": {"tapestry": 0.1, "cloth": 0.9}}
        return {"model": model, "answers": answers, "usage": {"input_tokens": 12}}

    talkie.set_fake(lambda *a, **k: english)
    jev.set_fake(fake)
    config.set_config({"latin_pivot": {"fidelity": {"jev": False}, "motif_ban": {"enabled": False}}})
    result = execute(
        project=proj, chapter="ch01", latin_file=path, ping=_ping,
        synonym_source=DictSynonymSource({
            "tapestry": ["cloth"], "quietly": ["softly"], "silence": ["calm"],
        }),
    )
    tag_questions = [q for questions in asked for q in questions.values()
                     if "typical of AI-generated prose" in (q.get("instructions") or "")]
    assert tag_questions and all("tapestry" in q["instructions"] for q in tag_questions)
    assert TAG_QUESTION.split("{")[0] in tag_questions[0]["instructions"]
    flags = json.loads((result["out_dir"] / "flag_for_edit.json").read_text(encoding="utf-8"))
    by_motif = {row["motif"]: row for row in flags}
    assert set(by_motif) >= {"quietly", "silence", "said nothing"}
    for motif, expected in (("quietly", "quietly"), ("silence", "silence"), ("said nothing", "said nothing")):
        row = by_motif[motif]
        start, end = row["span"]
        assert english[start:end] == row["matched_text"] == expected
        assert row["paragraph_index"] == 0
        assert row["sentence"] in english
        assert row["ambiguous"] is False
    assert by_motif["quietly"]["sentence_index"] == 0
    assert by_motif["silence"]["sentence_index"] == 0
    assert by_motif["said nothing"]["sentence_index"] == 1
    assert by_motif["said nothing"]["sentence"] == "He said nothing."
    tags = json.loads((result["out_dir"] / "tags.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert all(c["text"].lower() != "quietly" for c in tags["candidates"])
    final = (result["out_dir"] / "final.txt").read_text(encoding="utf-8")
    assert "quietly" in final and "silence" in final and "said nothing" in final
    assert "softly" not in final.lower() and "calm" not in final.lower()
    assert "cloth" in final.lower()
    swaps = [json.loads(line) for line in (result["out_dir"] / "swaps.jsonl").read_text().splitlines()]
    touched = [item["before"].lower() for row in swaps for item in row["swaps"]]
    assert "quietly" not in touched and "silence" not in touched
    report = json.loads((result["out_dir"] / "run.json").read_text(encoding="utf-8"))
    assert report["always_flag_count"] == len(flags) == 3
    density = report["motif_density"]
    assert density["english_raw"]["hits"] == 3
    assert density["final"]["hits"] == 3
    assert density["latin"]["hits"] == 0
    assert density["english_raw"]["per_1k"] == motif_density(english, match_always_flag)["per_1k"]


def _still_run(tmp_path, english, *, fake, cap=0.50, classifier="jev", no_jev=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "latin.txt"
    path.write_text(LATIN_BOY + "\n", encoding="utf-8")
    proj = _proj(tmp_path)
    talkie.set_fake(lambda *a, **k: english)
    jev.set_fake(fake)
    config.set_config({"latin_pivot": {
        "fidelity": {"jev": False},
        "cap_usd": cap,
        "motif_ban": {"enabled": False},
        "always_flag": {"still_classifier": classifier},
    }})
    return execute(project=proj, chapter="ch01", latin_file=path, ping=_ping, cap_usd=cap, no_jev=no_jev)


def test_ambiguous_still_uses_one_fake_noul_or_flags_when_jev_is_down(tmp_path, monkeypatch):
    english = "The boy still likes the long river road today under the tree."
    asked = []

    def fake(state, questions, model):
        asked.append(questions)
        assert set(questions) == {"stillness"}
        assert questions["stillness"]["type"] == "noul"
        return {"model": model, "answers": {"stillness": {"type": "noul", "noul": 0.5}},
                "usage": {"input_tokens": 20}}

    result = _still_run(tmp_path, english, fake=fake)
    flags = json.loads((result["out_dir"] / "flag_for_edit.json").read_text(encoding="utf-8"))
    assert len(flags) == 1
    assert flags[0]["motif"] == "still" and flags[0]["ambiguous"] is False
    start, end = flags[0]["span"]
    assert english[start:end] == "still"
    assert len(asked) == 1
    assert result["report"]["calls"]["jev"] == 1
    assert result["report"]["spent_usd"] > 0

    asked.clear()
    drop = _still_run(tmp_path / "low", english, fake=lambda state, questions, model: {
        "model": model, "answers": {"stillness": {"type": "noul", "noul": 0.49}},
        "usage": {"input_tokens": 8},
    })
    assert json.loads((drop["out_dir"] / "flag_for_edit.json").read_text(encoding="utf-8")) == []

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    jev.set_fake(None)
    down = _still_run(tmp_path / "down", english, fake=None)
    flags = json.loads((down["out_dir"] / "flag_for_edit.json").read_text(encoding="utf-8"))
    assert len(flags) == 1 and flags[0]["ambiguous"] is True and flags[0]["matched_text"] == "still"

    calls = []

    def counting(state, questions, model):
        calls.append(questions)
        return {"model": model, "answers": {"stillness": {"type": "noul", "noul": 0.9}},
                "usage": {"input_tokens": 8}}

    off = _still_run(tmp_path / "off", english, fake=counting, classifier="off")
    flags = json.loads((off["out_dir"] / "flag_for_edit.json").read_text(encoding="utf-8"))
    assert calls == []
    assert flags[0]["ambiguous"] is True

    calls.clear()
    with pytest.raises(BudgetExceeded):
        _still_run(tmp_path / "cap", english, fake=counting, cap=0.0)
    assert calls == []


def test_adverb_still_is_not_flagged_on_a_run(tmp_path):
    english = "He was still waiting by the long road with the boy today."
    asked = []

    def fake(state, questions, model):
        asked.append(questions)
        return {"model": model, "answers": {}, "usage": {"input_tokens": 4}}

    result = _still_run(tmp_path, english, fake=fake)
    assert json.loads((result["out_dir"] / "flag_for_edit.json").read_text(encoding="utf-8")) == []
    assert asked == []
    assert result["report"]["always_flag_count"] == 0
    assert result["report"]["motif_density"]["final"]["hits"] == 0


def test_dry_run_reports_density_from_existing_texts_without_calls(tmp_path):
    proj = _proj(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    latin = ("silentium " * 2 + "via " * 98).strip() + "\n"
    raw = ("silence " * 8 + "road " * 562).strip() + "\n"
    final = "The road was long.\n"
    (out / "latin.txt").write_text(latin, encoding="utf-8")
    (out / "english_raw.txt").write_text(raw, encoding="utf-8")
    (out / "final.txt").write_text(final, encoding="utf-8")
    before = sorted(p.name for p in out.iterdir())
    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("client called")

    talkie.set_fake(boom)
    jev.set_fake(boom)
    llm.set_fake(boom)
    result = execute(project=proj, chapter="ch01", latin_file=out / "latin.txt", out_dir=out,
                     dry_run=True, ping=boom, runner=boom)
    assert calls["n"] == 0
    assert sorted(p.name for p in out.iterdir()) == before
    density = result["estimate"]["motif_density"]
    assert density["latin"] == {"hits": 2, "words": 100, "per_1k": 20.0}
    assert density["english_raw"] == {"hits": 8, "words": 570, "per_1k": 14.0}
    assert density["final"]["hits"] == 0 and density["final"]["words"] == 4

    runner = CliRunner()
    dry = runner.invoke(cli, ["latin-pivot", "run", "--project", str(proj), "--chapter", "ch01",
                               "--latin-file", str(out / "latin.txt"), "--out", str(out), "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "motif_density english_raw: 14.0 per_1k (8 hits / 570 words)" in dry.output
    assert "motif_density latin: 20.0 per_1k (2 hits / 100 words)" in dry.output
    assert calls["n"] == 0

    live = tmp_path / "live"
    talked = {"n": 0}

    def talk(prompt, n_predict, seed, stop, **kw):
        talked["n"] += 1
        return ENGLISH_BOY

    from bookforge.core.latin_pivot import talkie_lifecycle
    talkie.set_fake(talk)
    jev.set_fake(None)
    llm.set_fake(boom)
    talkie_lifecycle.set_ping_fake(_ping)
    sample = tmp_path / "sample.txt"
    sample.write_text(LATIN_BOY + "\n", encoding="utf-8")
    try:
        done = runner.invoke(cli, ["latin-pivot", "run", "--project", str(proj), "--chapter", "ch01",
                                    "--latin-file", str(sample), "--out", str(live), "--no-jev"])
    finally:
        talkie_lifecycle.set_ping_fake(None)
    assert done.exit_code == 0, done.output
    assert "motif_density latin:" in done.output
    assert "motif_density english_raw:" in done.output
    assert "motif_density final:" in done.output
    report = json.loads((live / "run.json").read_text(encoding="utf-8"))
    assert set(report["motif_density"]) >= {"latin", "english_raw", "final"}
    assert talked["n"] == 1
    assert calls["n"] == 0
