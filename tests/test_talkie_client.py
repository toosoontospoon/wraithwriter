"""Talkie completion client and the quote-safe sentence splitter. Fakes only."""
import json

import pytest
import requests

from bookforge.core import sentences, talkie


@pytest.fixture(autouse=True)
def _clear_fake():
    talkie.set_fake(None)
    yield
    talkie.set_fake(None)


def test_complete_fake_roundtrip(tmp_path):
    def fn(prompt, n_predict, seed, stop, ban_nl=False, **kw):
        assert prompt == "Hello"
        assert n_predict == 12
        assert seed == 3
        assert stop == ["Exercise"]
        assert ban_nl is False
        assert kw["temperature"] == 0.4
        assert kw["min_p"] == 0.1
        assert kw["repeat_penalty"] == 1.2
        return {"content": "world", "stop_type": "word",
                "timings": {"prompt_n": 4, "predicted_n": 1, "predicted_per_second": 9.0}}

    talkie.set_fake(fn)
    text, rec = talkie.complete("Hello", 12, 3, ["Exercise"], temperature=0.4, min_p=0.1,
                                 repeat_penalty=1.2, log_dir=tmp_path)
    assert text == "world"
    assert rec["prompt_n"] == 4 and rec["gen_n"] == 1 and rec["tok_s"] == 9.0
    assert rec["wall"] >= 0
    row = json.loads((tmp_path / "talkie_calls.jsonl").read_text().splitlines()[0])
    assert row["prompt_n"] == 4 and row["gen_n"] == 1 and row["tok_s"] == 9.0


def test_complete_posts_cache_and_newline_ban(monkeypatch):
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"content": "ok", "stop_type": "word",
                    "timings": {"prompt_n": 1, "predicted_n": 1, "predicted_per_second": 1}}

    def post(url, json=None, timeout=None, headers=None):
        seen["url"] = url
        seen["body"] = json
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(talkie.requests, "post", post)
    text, _rec = talkie.complete("Hi", 4, 9, ["Exercise"], ban_nl=True, timeout=7,
                                  endpoint="http://127.0.0.1:8080")
    assert text == "ok"
    assert seen["url"] == "http://127.0.0.1:8080/completion"
    assert seen["body"]["cache_prompt"] is True
    assert seen["body"]["logit_bias"][0] == ["\n", False]
    assert seen["timeout"] == 7


def test_complete_unavailable(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("nope")

    monkeypatch.setattr(talkie.requests, "post", boom)
    with pytest.raises(talkie.TalkieUnavailable):
        talkie.complete("Hello", 8, 1, ["Exercise"], timeout=1)


def test_length_control_two_steps():
    calls = []
    src = "Nia locked the gate and pocketed the iron key in her coat."
    words = len(src.split())

    def fn(prompt, n_predict, seed, stop, ban_nl=False, **kw):
        calls.append({"n": n_predict, "ban_nl": ban_nl, "seed": seed, "prompt": prompt, "stop": list(stop)})
        if len(calls) == 1:
            return {"content": "Nia shut the gate and kept the", "stop_type": "limit",
                    "timings": {"prompt_n": 10, "predicted_n": n_predict, "predicted_per_second": 5}}
        return {"content": " metal key in her coat.\nExercise", "stop_type": "word",
                "timings": {"prompt_n": 20, "predicted_n": 8, "predicted_per_second": 5}}

    talkie.set_fake(fn)
    out = talkie.generate("PROMPT", src, seed=5)
    assert calls[0]["ban_nl"] is True
    assert calls[0]["n"] == max(20, int(0.8 * words * talkie.TPW))
    assert calls[0]["seed"] == 5
    assert calls[1]["ban_nl"] is False
    assert calls[1]["prompt"] == "PROMPT" + "Nia shut the gate and kept the"
    assert calls[1]["seed"] == 5 + 7919
    assert "\n" in calls[1]["stop"]
    assert out["text"] == "Nia shut the gate and kept the metal key in her coat."
    assert "Exercise" not in out["text"]


def test_length_control_trims_when_limited():
    def fn(prompt, n_predict, seed, stop, ban_nl=False, **kw):
        if ban_nl:
            return {"content": "Nia went home today and then she", "stop_type": "limit",
                    "timings": {"prompt_n": 3, "predicted_n": 8, "predicted_per_second": 1}}
        return {"content": " saw the dog. And extra words without end", "stop_type": "limit",
                "timings": {"prompt_n": 9, "predicted_n": 8, "predicted_per_second": 1}}

    talkie.set_fake(fn)
    out = talkie.generate("P", "Nia locked the gate and pocketed the iron key.", 1)
    assert out["text"] == "Nia went home today and then she saw the dog."


def test_length_control_stop_word_is_one_step():
    calls = []

    def fn(prompt, n_predict, seed, stop, ban_nl=False, **kw):
        calls.append(ban_nl)
        return {"content": "Nia went home today. Extra words here", "stop_type": "word",
                "timings": {"prompt_n": 2, "predicted_n": 6, "predicted_per_second": 2}}

    talkie.set_fake(fn)
    out = talkie.generate("P", "Nia locked the gate and pocketed the iron key today.", 2)
    assert calls == [True]
    assert out["text"] == "Nia went home today."


def test_splitter_keeps_closing_quote():
    assert sentences.split_sentences('"I forgot it." Her aunt looked up from the letter.') == [
        '"I forgot it."',
        "Her aunt looked up from the letter.",
    ]


def test_splitter_abbreviations():
    assert sentences.split_sentences("Mr. Hale met Mrs. Poe at the gate. They went in.") == [
        "Mr. Hale met Mrs. Poe at the gate.",
        "They went in.",
    ]
    assert sentences.split_sentences('Mrs. Poe said "Wait." Mr. Hale stopped.') == [
        'Mrs. Poe said "Wait."',
        "Mr. Hale stopped.",
    ]
