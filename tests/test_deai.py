from bookforge.core import deai


def test_scrub_tokens():
    t = deai.scrub("\u201cWait\u2014\u201d she said\u2014quietly\u2026 pages 10\u201312 and \u2018ok\u2019")
    assert "\u2014" not in t and "\u201c" not in t and "\u2026" not in t
    assert t.startswith('"Wait..."') and "10-12" in t and "'ok'" in t
    assert deai.scrub(t) == t


def test_lint_flags_rules():
    r = deai.lint('It was not fear, but hunger that drove her.\n\nA tapestry of light.\n\nShe ran home.')
    assert r["counts"].get("not_x_but_y") == 1
    assert r["counts"].get("stock_word") == 1 and r["counts"].get("fragment") == 1


def test_unclear_referent():
    r = deai.lint("Finn met Elias at the dock and they talked.\n\nHe turned away.", names=["Finn", "Elias"])
    assert r["counts"].get("unclear_referent") == 1


def test_rewrite_only_flagged(fake):
    text = "The boiler hissed and Finn wiped his hands.\n\nThe sky was a tapestry of smoke over the harbor roofs."
    out, rep = deai.rewrite_flagged(text)
    assert rep["rewritten_paragraphs"] == [1]
    assert out.split("\n\n")[0] == "The boiler hissed and Finn wiped his hands."
    assert "tapestry" not in out and fake.count("deai") == 1
