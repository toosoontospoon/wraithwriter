from bookforge.core import protect
from bookforge.core import editor

CH = {"id": "ch04", "summary": "x", "scenes": [{"scene_number": 1, "beats": [
    'Marlow talks to the air. KEEP: "Partners. Yes. Till the job is done, was it?" / "But you\'ll be near him."',
    'Watcher. SUBTEXT: \u201cIt was your voice that woke it\u201d',
    'An ordinary beat with "a quote that is not protected".']}],
    "protected_lines": ["Both of you."], "protect_exact_lines": True}


def test_extracts_marked_lines_only():
    p = protect.protected(CH)
    assert "Partners. Yes. Till the job is done, was it?" in p
    assert "But you'll be near him." in p
    assert "It was your voice that woke it" in p
    assert "Both of you." in p
    assert "a quote that is not protected" not in p


def test_critic_findings_on_protected_lines_dropped():
    spots = [{"location": "Partners. Yes. Till the job is done, was it?", "problem": "too revealing", "severity": "critical"},
             {"location": "the sun had reached the treetops", "problem": "timing", "severity": "major"}]
    kept, dropped = protect.filter_spots(spots, protect.protected(CH))
    assert [w["location"] for w in kept] == ["the sun had reached the treetops"]
    assert len(dropped) == 1


def test_editor_findings_skip_protected(monkeypatch):
    class C:
        def open_revision_tasks(self, _): return []
    class Ctx:
        def canon(self): return C()
        def chapter(self, _): return CH
    crit = {"weak_spots": [{"location": "But you'll be near him", "problem": "explains binding"}],
            "rule_checks": [{"rule": "clarity", "pass": False, "quote": "It was your voice that woke it", "note": "n"},
                            {"rule": "tempo", "pass": False, "quote": "something else", "note": "n"}]}
    fx = editor.findings(Ctx(), "ch04", crit, CH)
    assert len(fx) == 1 and "tempo" in fx[0]


def test_restore_reinserts_cut_line():
    before = "A para.\n\n\"Partners. Yes. Till the job is done, was it?\" he said.\n\nEnd para."
    after = "A para, tightened.\n\n\"No. Wait,\" he said.\n\nEnd para."
    out, back = protect.restore(before, after, protect.protected(CH))
    assert "Till the job is done" in out and back
    assert out.startswith("A para, tightened.") and out.endswith("End para.")


def test_restore_reinserts_deleted_paragraph():
    before = "One.\n\n\"Hold on,\" he said. \"Both of you.\"\n\nThree."
    after = "One.\n\nThree."
    out, back = protect.restore(before, after, protect.protected(CH))
    assert out.split("\n\n") == ["One.", "\"Hold on,\" he said. \"Both of you.\"", "Three."]


def test_restore_noop_when_kept_or_absent():
    t = "Nothing protected here."
    assert protect.restore(t, t + " x", protect.protected(CH)) == (t + " x", [])


def test_blocks_render():
    assert "PROTECTED LINES" in protect.block(["a line"])
    assert protect.block([]) == ""


def test_exact_lines_off_by_default(monkeypatch):
    monkeypatch.delenv("BOOKFORGE_PROTECT_EXACT_LINES", raising=False)
    ch = {k: v for k, v in CH.items() if k != "protect_exact_lines"}
    assert protect.protected(ch) == []
    assert protect.restore("a", "b", protect.protected(ch)) == ("b", [])
    monkeypatch.setenv("BOOKFORGE_PROTECT_EXACT_LINES", "1")
    assert "Both of you." in protect.protected(ch)


def test_fact_event_hints_always_work(monkeypatch):
    monkeypatch.delenv("BOOKFORGE_PROTECT_EXACT_LINES", raising=False)
    ch = {"id": "ch05", "summary": "x", "scenes": [{"scene_number": 1, "beats": [
        "Dinner. FACT: Marlow sets a place for Tom. EVENT: Ada checks the latch before grace."]}],
        "protected_facts": ["Nell is buried beside Lady Joan"]}
    f = protect.facts(ch)
    assert "Marlow sets a place for Tom" in f and "Ada checks the latch before grace" in f
    assert "Nell is buried beside Lady Joan" in f
    b = protect.block(protect.protected(ch), f)
    assert "PROTECTED FACTS/EVENTS" in b and "Wording is free" in b and "PROTECTED LINES" not in b
