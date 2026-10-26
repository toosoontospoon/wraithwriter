"""Jev tell tagger: splitter, dialogue skip, hit rows, thresholds, page gates, cache."""
import json

import pytest

from bookforge.core import jev, jev_tells


@pytest.fixture(autouse=True)
def _no_jev(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    jev.set_fake(None)
    yield
    jev.set_fake(None)


def _fake(answers: dict, calls: list):
    def fake(state, qs, model):
        calls.append({"state": state, "qs": qs, "model": model})
        return {"model": model, "answers": {k: {"type": "noul", "noul": float(answers.get(k, 0.0))} for k in qs},
                "usage": {"input_tokens": 12}}
    jev.set_fake(fake)
    return calls


def test_split_keeps_quotes_and_breaks_on_blank_lines():
    text = 'She said "Stop right there." Then she left the yard.\n\nHe sat down.'
    spans = jev_tells.split_sentences(text)
    parts = [text[a:b] for a, b in spans]
    assert parts == ['She said "Stop right there."', "Then she left the yard.", "He sat down."]
    assert all("\n" not in p for p in parts)
    # A sentence must not swallow the paragraph break.
    joined = "Alpha ends here.\n\nBeta starts now."
    bits = [joined[a:b] for a, b in jev_tells.split_sentences(joined)]
    assert bits == ["Alpha ends here.", "Beta starts now."]


def test_dialogue_only_sentences_are_not_asked():
    text = 'She said "Stop right there." Then she left the yard.\n\n"Go home now."'
    calls = _fake({}, [])
    out = jev_tells.tag_text(text, cfg={"page_words": 350})
    sents = {s["id"]: s for s in out["sentences"]}
    assert sents["s03"]["dialogue_only"] and sents["s03"]["text"] == '"Go home now."'
    assert not sents["s01"]["dialogue_only"] and not sents["s02"]["dialogue_only"]
    sent_call = next(c for c in calls if any("__s" in k for k in c["qs"]))
    assert not any(k.endswith("__s03") for k in sent_call["qs"])
    assert any(k.endswith("__s01") for k in sent_call["qs"])
    mixed = sent_call["qs"]["ai_vocab__s01"]["instructions"]
    assert "ignore words inside quotation marks" in mixed.lower()
    plain = sent_call["qs"]["ai_vocab__s02"]["instructions"]
    assert "quotation marks" not in plain.lower()
    # The dialogue line is still in the numbered document, as context.
    assert "s03|" in sent_call["state"]["document"]


def test_answers_become_hits_with_severity_and_thresholds():
    text = "The market was a tapestry of color and noise. She counted the loaves."
    calls = _fake({
        "ai_vocab__s01": 0.95,
        "emotion_explained__s01": 0.8,
        "stock_phrase__s01": 0.6,
        "gesture_beat__s02": 0.74,
    }, [])
    out = jev_tells.tag_text(text, cfg={"thresholds": {"stock_phrase": 0.7, "gesture_beat": 0.5}})
    by = {}
    for h in out["hits"]:
        by.setdefault(h["category"], []).append(h)
    assert by["ai_vocab"][0]["severity"] == 3
    assert by["emotion_explained"][0]["severity"] == 2
    assert "stock_phrase" not in by
    assert by["gesture_beat"][0]["severity"] == 1
    hit = by["ai_vocab"][0]
    assert hit["rule"] == "jev_ai_vocab" and hit["source"] == "jev" and hit["confirm"] is False
    assert hit["ids"] == ["s01"] and hit["p"] == 0.95
    assert hit["span"] == "The market was a tapestry of color and noise."
    assert hit["span"] in text
    assert hit["paragraph"] and "fix" in hit and "scene" in hit and "para" in hit
    assert out["counts"]["ai_vocab"] == 1
    assert out["jev"] == "ok"
    assert calls[0] and calls[1]
    assert any(k.startswith("pg__") for k in calls[1]["qs"])
    assert "tidy_ending_page" in out["page_scores"][0]


def test_page_gates_tidy_ending_and_instead():
    text = " ".join([
        "One apple sat on the step.",
        "Two birds crossed the yard.",
        "Three clouds moved west.",
        "Four dogs slept outside.",
        "Five lamps burned low.",
        "Instead, she folded the cloth.",
    ])
    # s01..s06. Last three narration sentences are s04, s05, s06.
    answers = {
        "tidy_ending__s01": 0.99,
        "tidy_ending__s06": 0.91,
        "pg__tidy_ending_page": 0.2,
        "pg__instead_contrast_page": 0.88,
    }
    _fake(answers, [])
    out = jev_tells.tag_text(text)
    tidy = [h for h in out["hits"] if h["category"] == "tidy_ending"]
    assert [h["ids"] for h in tidy] == [["s06"]]
    instead = [h for h in out["hits"] if h["category"] == "not_x_but_y"]
    assert len(instead) == 1 and instead[0]["ids"] == ["s06"]
    assert instead[0]["p"] == 0.88 and instead[0]["span"].startswith("Instead,")

    # No sentence hit in the closing zone: the page score promotes the last narration sentence.
    promoted = {k: 0.0 for k in answers}
    promoted["pg__tidy_ending_page"] = 0.86
    promoted["tidy_ending__s01"] = 0.97  # mid-page, must be dropped, not enough to block promotion
    promoted["pg__instead_contrast_page"] = 0.1
    _fake(promoted, [])
    out = jev_tells.tag_text(text + " extra", cfg={"page_words": 350})
    # different text so the cache from the previous call cannot apply (no log_dir anyway)
    tidy = [h for h in out["hits"] if h["category"] == "tidy_ending"]
    assert len(tidy) == 1 and tidy[0]["ids"] == ["s06"] and tidy[0]["p"] == 0.86
    assert not any(h["category"] == "not_x_but_y" for h in out["hits"])


def test_span_must_be_verbatim():
    text = "She set the cup down."
    kept = jev_tells.keep_verbatim([
        {"span": "She set the cup down.", "category": "gesture_beat"},
        {"span": "She set the cup down slowly.", "category": "gesture_beat"},
        {"span": "", "category": "gesture_beat"},
    ], text)
    assert [h["span"] for h in kept] == ["She set the cup down."]


def test_jev_unavailable_returns_code_only(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    jev.set_fake(None)
    out = jev_tells.tag_text("She walked to the pier and counted the boats.")
    assert out["jev"] == "skipped: TYPESAFE_API_KEY not set"
    assert out["hits"] == []
    assert out["sentences"]


def test_cache_hit_makes_no_further_calls(tmp_path):
    calls = _fake({}, [])
    text = "She walked to the pier and counted the boats."
    first = jev_tells.tag_text(text, log_dir=tmp_path)
    n = len(calls)
    assert n >= 1 and first["jev"] == "ok"
    second = jev_tells.tag_text(text, log_dir=tmp_path)
    assert len(calls) == n
    assert second["sentences"] == first["sentences"]
    assert (tmp_path / "jev_tells_cache.json").exists()


def test_pages_split_on_paragraphs_and_scene_breaks():
    paras = [f"Sentence number {i} stands on its own in the yard today." for i in range(6)]
    text = "\n\n".join(paras[:3] + ["* * *"] + paras[3:])
    sents = jev_tells.annotate(text)
    pages = jev_tells._pages_of(sents, text, page_words=20)
    assert len(pages) >= 2
    assert pages[0][-1]["scene"] == 1
    assert pages[-1][0]["scene"] == 2
    flat = [s["id"] for page in pages for s in page]
    assert flat == [s["id"] for s in sents]


def test_scene_break_is_its_own_paragraph_and_not_a_sentence():
    text = "She walked home.\n\n* * *\n\nHe locked the door."
    out_spans = [text[a:b] for a, b in jev_tells.split_sentences(text)]
    assert out_spans == ["She walked home.", "He locked the door."]
    sents = jev_tells.annotate(text)
    assert [s["scene"] for s in sents] == [1, 2]
    assert [s["para"] for s in sents] == [0, 2]
