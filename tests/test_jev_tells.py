"""Jev tell tagger: splitter, dialogue skip, hit rows, thresholds, page gates, cache."""
import pytest

from bookforge import config
from bookforge.core import editor, jev, jev_tells, screen


_REAL_TAX = jev_tells.load_taxonomy


def _flat_taxonomy():
    """The shipped taxonomy with every threshold at 0.5, so tests do not track calibration."""
    tax = _REAL_TAX()
    for group in ("categories", "code_categories", "page_questions"):
        for row in tax[group]:
            row["threshold"] = 0.5
    return tax


@pytest.fixture(autouse=True)
def _no_jev(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(jev_tells, "load_taxonomy", _flat_taxonomy)
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
    page_call = next(c for c in calls if any(k.startswith("pg__") for k in c["qs"]))
    assert page_call["qs"]
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


def test_reask_band_averages_only_the_borderline_items():
    text = "The market was a tapestry of noise."
    calls = _fake({
        "ai_vocab__s01": 0.5,
        "para__ai_vocab__s01": 0.9,
        "stock_phrase__s01": 0.95,
        "filter_hedge__s01": 0.2,
    }, [])
    out = jev_tells.tag_text(text)
    reask_calls = [c for c in calls if any(k.startswith("para__") or k.startswith("again__") for k in c["qs"])]
    assert len(reask_calls) == 1
    assert set(reask_calls[0]["qs"]) == {"para__ai_vocab__s01"}
    hit = next(h for h in out["hits"] if h["category"] == "ai_vocab")
    assert hit["p"] == 0.7
    assert out["reasked"] == [{"category": "ai_vocab", "id": "s01", "ps": [0.5, 0.9]}]
    assert not any(h["category"] == "filter_hedge" for h in out["hits"])

    calls.clear()
    jev.set_fake(None)
    quiet = _fake({}, [])
    jev_tells.tag_text("A gull sat on the post.")
    assert len(quiet) == 2  # sentence batch + page batch, nothing in the band
    assert not any(any(k.startswith("para__") for k in c["qs"]) for c in quiet)

    calls2 = _fake({"ai_vocab__s01": 0.5, "para__ai_vocab__s01": 0.5, "again__ai_vocab__s01": 0.8}, [])
    out = jev_tells.tag_text("The market was a tapestry of noise.", cfg={"reask": 2})
    kinds = [next(iter(c["qs"])).split("__", 1)[0] for c in calls2]
    assert kinds == ["ai_vocab", "para", "again", "pg"]
    assert out["reasked"][0]["ps"] == [0.5, 0.5, 0.8]
    assert next(h["p"] for h in out["hits"] if h["category"] == "ai_vocab") == 0.6


def test_repeated_action_positives_and_negatives():
    palms = ("Marta wiped her palms on her trousers.\n\n"
             "The wind came in off the water.\n\n"
             "She wiped her palms on her trousers again.")
    scraper = ("She held the scraper against the hull.\n\n"
               "A gull cried once.\n\n"
               "She put the scraper against the hull.")
    tubs = ("He punched down the first tub.\n\n"
            "The room stayed warm.\n\n"
            "He punched down the second tub.")
    jev.set_fake(None)
    for text in (palms, scraper, tubs):
        hits = [h for h in jev_tells.tag_text(text)["hits"] if h["category"] == "repeated_action"]
        assert len(hits) == 1, text
        assert hits[0]["ids"][0] == "s01" and hits[0]["ids"][-1] == "s03"
        assert hits[0]["severity"] == 1 and hits[0]["source"] == "code"
        assert hits[0]["span"] in text

    triple = ("She wiped her palms on her trousers.\n\n"
              "She wiped her palms on her trousers again.\n\n"
              "She wiped her palms on her trousers once more.")
    third = [h for h in jev_tells.tag_text(triple)["hits"] if h["category"] == "repeated_action"]
    assert any(h["severity"] == 2 for h in third)

    door = "She opened the door.\n\nShe closed the door."
    names = "Marta crossed the yard.\n\nMarta crossed the lane."
    dialogue = ('"She wiped her palms on her trousers," Ned said.\n\n'
                '"She wiped her palms on her trousers again," Ned said.')
    far = "\n\n".join([
        "She wiped her palms on her trousers.",
        "The wind shifted.",
        "A bell rang.",
        "Smoke drifted east.",
        "Children left the lane.",
        "Water climbed the step.",
        "Noon passed quietly.",
        "The cat slept.",
        "Dust settled on the sill.",
        "She wiped her palms on her trousers again.",
    ])
    for text, kw in ((door, {}), (names, {"names": ["Marta"]}), (dialogue, {}), (far, {})):
        hits = [h for h in jev_tells.tag_text(text, cfg=kw)["hits"] if h["category"] == "repeated_action"]
        assert hits == [], text


def test_uniform_rhythm_length_and_opening_word():
    jev.set_fake(None)
    length = "She opened the door. She looked inside. She saw the table. She sat down."
    hits = [h for h in jev_tells.tag_text(length)["hits"] if h["category"] == "uniform_rhythm"]
    assert any(len(h["ids"]) >= 4 and "similar length" in h["reason"] for h in hits)
    assert any("open with 'she'" in h["reason"] for h in hits)
    assert all(h["source"] == "code" and h["span"] in length for h in hits)

    opening = "He tied his apron. He lit the burners. He checked the proof box."
    opens = [h for h in jev_tells.tag_text(opening)["hits"] if h["category"] == "uniform_rhythm"]
    assert len(opens) == 1 and opens[0]["severity"] == 2 and opens[0]["ids"] == ["s01", "s02", "s03"]

    varied = "The long afternoon dragged over the empty yard behind the bakery. He sat. A gull cried once."
    assert not any(h["category"] == "uniform_rhythm" for h in jev_tells.tag_text(varied)["hits"])

    quoted = ('"She left the house."\n\n"She locked the door."\n\n"She crossed the yard."\n\n"She fed the cat."')
    assert not any(h["category"] == "uniform_rhythm" for h in jev_tells.tag_text(quoted)["hits"])


def _score_fake(category: str, p: float = 0.95):
    def fake(state, qs, model):
        ans = {}
        for k, spec in qs.items():
            if spec.get("type") == "choice":
                ans[k] = {"type": "choice", "choice": "none", "probabilities": {"none": 1}, "confidence": 1}
            else:
                noul = p if "document" in state and k.startswith(f"{category}__") else 0.0
                ans[k] = {"type": "noul", "noul": noul}
        return {"model": model, "answers": ans, "usage": {"input_tokens": 5}}
    jev.set_fake(fake)


def test_findings_and_screen_carry_jev_tell_rows(project):
    text = "Nia counted the dry nets on the wall."
    ch = {"id": "ch01", "title": "Nets", "scenes": [{"scene_number": 1, "pov": "Nia"}]}
    project.save_outline([ch])
    project.save_characters([{"name": "Nia Cole"}])
    project.save_world({})
    chapter = project.chapter("ch01")
    _score_fake("gesture_beat", 0.95)
    lines = editor.findings(project, "ch01", {}, chapter, text)
    assert any("RULE jev_gesture_beat" in ln and "dry nets" in ln for ln in lines)
    pk = screen.run(project, chapter, text, {})
    flags = [f for fl in pk["flags"].values() for f in fl if f.get("check") == "tell_gesture_beat"]
    assert len(flags) == 1
    assert flags[0]["source"] == "jev" and flags[0]["evidence"] == text
    assert flags[0]["section"] == pk["sections"][0]["sid"]
    assert flags[0]["p"] == 0.95 and flags[0]["severity"] == 3
    assert text in pk["sections"][0]["text"]


def test_disabled_jev_tells_add_nothing(project):
    config.set_config({"llm": {"json_retries": 1}, "deai": {"jev_tells": {"enabled": False}},
                       "screen": {"enabled": True, "section_words": 80}})
    text = "She opened the door. She looked inside. She saw the table. She sat down."
    ch = {"id": "ch01", "title": "T", "scenes": [{"scene_number": 1, "pov": "Nia"}]}
    project.save_outline([ch])
    project.save_characters([])
    project.save_world({})
    chapter = project.chapter("ch01")
    seen = []

    def fake(state, qs, model):
        seen.append(state)
        ans = {k: {"type": "noul", "noul": 0.0} for k, spec in qs.items() if spec.get("type") != "choice"}
        for k, spec in qs.items():
            if spec.get("type") == "choice":
                ans[k] = {"type": "choice", "choice": "none", "probabilities": {"none": 1}, "confidence": 1}
        return {"model": model, "answers": ans, "usage": {"input_tokens": 1}}

    jev.set_fake(fake)
    lines = editor.findings(project, "ch01", {}, chapter, text)
    assert not any("jev_" in ln for ln in lines)
    assert seen == []
    pk = screen.run(project, chapter, text, {})
    assert not any(str(f.get("check", "")).startswith("tell_") for fl in pk["flags"].values() for f in fl)
    assert all("document" not in state for state in seen)


def test_scene_break_is_its_own_paragraph_and_not_a_sentence():
    text = "She walked home.\n\n* * *\n\nHe locked the door."
    out_spans = [text[a:b] for a, b in jev_tells.split_sentences(text)]
    assert out_spans == ["She walked home.", "He locked the door."]
    sents = jev_tells.annotate(text)
    assert [s["scene"] for s in sents] == [1, 2]
    assert [s["para"] for s in sents] == [0, 2]
