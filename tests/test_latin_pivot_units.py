"""Prompt, seeds, fidelity heuristic, synonyms, and picker margin. Offline."""
import json

import pytest

from bookforge.config import DEFAULT_CONFIG
from bookforge.core import talkie
from bookforge.core.latin_pivot.fidelity import FIDELITY_QUESTION, heuristic
from bookforge.core.latin_pivot.picker import PICK_QUESTION, decide
from bookforge.core.latin_pivot.seeds import (
    SeedLost,
    assert_seeds_preserved,
    is_seed_only,
    repair_seeds,
    strip_markers,
)
from bookforge.core.latin_pivot.synonyms import (
    DictSynonymSource,
    SynonymError,
    WordNetSynonymSource,
    inflect_like,
    suggest,
)
from bookforge.core.latin_pivot.tagger import TAG_QUESTION, find_candidates
from bookforge.core.latin_pivot.translate import (
    REPEAT_PENALTY,
    STOPS,
    TOP_K,
    build_prompt,
    draft_instruction,
    load_fewshot,
    n_predict_for,
    package_fewshot_path,
)

pytestmark = pytest.mark.usefixtures("block_network")


def test_defaults_stay_standard_and_nltk_is_optional():
    assert DEFAULT_CONFIG["draft"]["mode"] == "standard"
    lp = DEFAULT_CONFIG["latin_pivot"]
    assert lp["cap_usd"] == 0.50
    assert lp["fidelity"]["jev"] is True
    assert lp["fidelity"]["jev_threshold"] == 0.6
    assert lp["fidelity"]["retries"] == 1
    assert lp["fidelity"]["ratio_min"] == 0.9
    assert lp["fidelity"]["ratio_max"] == 2.2
    assert lp["tag"]["threshold"] == 0.6
    assert lp["tag"]["max_per_paragraph"] == 12
    assert lp["tag"]["skip_dialogue"] is False
    assert lp["synonyms"]["max"] == 8
    assert lp["pick"]["margin"] == 0.1
    talkie_cfg = lp["talkie"]
    assert talkie_cfg["endpoint"] == "http://127.0.0.1:8080"
    assert talkie_cfg["manage"] is False
    assert talkie_cfg["stop_cmd"] == "docker stop talkie"
    assert talkie_cfg["start_cmd"] == ""
    assert talkie_cfg["stop_after"] is None
    from pathlib import Path
    py = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert 'latin = ["nltk"]' in py
    assert "nltk" not in py.split("[project.optional-dependencies]")[0]


def test_prompt_shape_header_pairs_stops_and_sampling(tmp_path):
    data = load_fewshot()
    target = "Milites in castra ierunt."
    prompt = build_prompt(target, fewshot=data)
    assert prompt.startswith("LATIN PROSE FOR TRANSLATION\n")
    assert "English words between <<SEED>> and <</SEED>> are copied unchanged." in prompt
    pairs = [pair["latin"] for pair in data["pairs"]]
    assert "Puer in agro ambulabat cum canem parvum vidit." in pairs[0]
    assert '<<SEED>>"Not a word," he told the boy.<</SEED>>' in pairs[3]
    positions = [prompt.index(pair) for pair in pairs]
    assert positions == sorted(positions)
    assert prompt.index(pairs[3]) < prompt.index("Latin:\n" + target)
    assert prompt.endswith("English:\n")
    assert "classical-style Latin" in draft_instruction(["Keep this line."])
    assert '<<SEED>>Keep this line.<</SEED>>' in draft_instruction(["Keep this line."])
    seen = {}

    def fake(prompt, n_predict, seed, stop, ban_nl=False, **kw):
        seen["stop"] = stop
        seen.update(kw)
        seen["n"] = n_predict
        return "A boy walked in the field and saw a small dog near the road today."

    talkie.set_fake(fake)
    words = len(target.split())
    expected_n = int(max(80, min(600, 2.2 * words + 40)))
    talkie.complete(prompt, n_predict_for(target), 3, STOPS, temperature=0, top_k=TOP_K,
                    repeat_penalty=REPEAT_PENALTY, cache_prompt=True)
    assert seen["stop"] == ["\n\n", "\nLatin:", "Latin:"]
    assert seen["temperature"] == 0
    assert seen["top_k"] == 1
    assert seen["repeat_penalty"] == 1.05
    assert seen["cache_prompt"] is True
    assert seen["n"] == expected_n == n_predict_for(target)
    # Existing callers still send top_k 0.
    talkie.complete("Hi", 4, 1, ["Exercise"])
    assert seen["top_k"] == 0
    disk = json.loads(package_fewshot_path().read_text(encoding="utf-8"))
    assert disk["pairs"][3]["latin"] == pairs[3]
    assert disk["pairs"][3]["english"].startswith("The sailor came back to the harbour.")


def test_seed_repair_and_assert():
    latin = 'Nauta rediit. <<SEED>>"Not a word," he told the boy.<</SEED>> Deinde tacuit.'
    mangled = 'The sailor returned. <<SEED>>"Nope," he said.<</SEED>> Then he was quiet.'
    fixed, status = repair_seeds(latin, mangled)
    assert status == "repaired"
    assert '<<SEED>>"Not a word," he told the boy.<</SEED>>' in fixed
    assert repair_seeds(latin, fixed)[1] == "ok"
    lost, status = repair_seeds(latin, "The sailor returned and said something else today.")
    assert status == "lost"
    assert is_seed_only('  <<SEED>>"Not a word," he told the boy.<</SEED>>  ')
    assert not is_seed_only(latin)
    final = strip_markers(fixed)
    assert "<<SEED>>" not in final
    assert_seeds_preserved(latin, final)
    with pytest.raises(SeedLost):
        assert_seeds_preserved(latin, "The sailor returned.")


def test_fidelity_heuristic_quote_name_and_ratio():
    latin = 'Soror epistulam e Roma accepit et "vale" dixit ille puer.'
    good = 'My sister received a letter from Roma and said "vale" to that boy today.'
    assert heuristic(latin, good)["ok"]
    dropped = heuristic(latin, "My sister received a letter from Roma and the boy heard it.")
    assert not dropped["quotes_ok"] and not dropped["ok"]
    missing = heuristic(latin, 'My sister received a letter and said "vale" to that boy today.')
    assert "Roma" in missing["missing_names"] and not missing["ok"]
    short = heuristic(latin, "No.")
    assert not short["ratio_ok"] and not short["ok"]
    assert FIDELITY_QUESTION.startswith("Does the English keep every event")


def test_synonym_inflection_caps_and_filters():
    assert inflect_like("walked", "stroll") == "strolled"
    assert inflect_like("Walking", "stroll") == "Strolling"
    assert inflect_like("WALKS", "stroll") == "STROLLS"
    assert inflect_like("quickly", "swift") == "swiftly"
    source = DictSynonymSource({
        "delved": ["stroll", "delve", "one two three four", "stroll"],
        "tapestry": ["cloth"],
    })
    tells = {"delve", "tapestry"}
    got = suggest("delved", source, tells, limit=8)
    assert got == ["strolled"]
    assert suggest("Tapestry", source, tells, limit=8) == ["Cloth"]
    assert len(suggest("delved", DictSynonymSource({"delved": [f"w{i}" for i in range(20)]}), set(), limit=8)) == 8


def test_wordnet_missing_is_a_clear_error(monkeypatch):
    import builtins
    real = builtins.__import__

    def boom(name, *args, **kwargs):
        if name == "nltk" or name.startswith("nltk."):
            raise ImportError("no nltk")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", boom)
    with pytest.raises(SynonymError, match="nltk"):
        WordNetSynonymSource().lemmas("walk")


def test_picker_keeps_original_when_margin_is_not_beaten():
    kept = decide("tapestry", ["cloth"], {"tapestry": 0.55, "cloth": 0.60}, "cloth", 0.1)
    assert kept["swapped"] is False and kept["after"] == "tapestry" and kept["reason"] == "kept_margin"
    swapped = decide("tapestry", ["cloth"], {"tapestry": 0.20, "cloth": 0.80}, "cloth", 0.1)
    assert swapped["swapped"] is True and swapped["after"] == "cloth"
    same = decide("tapestry", ["cloth"], {"tapestry": 0.9, "cloth": 0.1}, "tapestry", 0.1)
    assert same["reason"] == "kept_original" and same["after"] == "tapestry"
    assert PICK_QUESTION.startswith("Which option reads most natural")


def test_tag_candidates_cap_dialogue_and_question():
    text = 'The tapestry sat quietly. He would delve later. "delve" she said.'
    hits = find_candidates(text, ["tapestry", "delve"], skip_dialogue=False, limit=12)
    assert [h["text"].lower() for h in hits] == ["tapestry", "delve", "delve"]
    capped = find_candidates(text, ["tapestry", "delve"], limit=1)
    assert len(capped) == 1 and capped[0]["text"].lower() == "tapestry"
    skipped = find_candidates(text, ["tapestry", "delve"], skip_dialogue=True, limit=12)
    assert all(h["start"] < text.index('"') for h in skipped)
    from bookforge.core.latin_pivot.tagger import tag_questions
    question = tag_questions([{"text": "delve"}])["c0"]["instructions"]
    assert question == TAG_QUESTION.format(term="delve")
