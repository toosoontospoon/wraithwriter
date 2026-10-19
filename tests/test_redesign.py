"""2026-10-12 context redesign: story pack, rule card, one call per scene, editor pass, final gate."""
import inspect
import json

from bookforge import config as C
from bookforge.core import pack
from bookforge.steps import deai_chapter, revise_chapter


def test_name_matching_handles_curly_quoted_nicknames():
    chars = {"tom": {"name": "Tom \u201cAce\u201d Reed", "role": "friend"},
             "ada": {"name": "Ada Lane", "role": "hero"}}
    for n in ("Tom \"Ace\" Reed", "Tom Reed", "Tom", "Ace", "tom \u201cace\u201d reed"):
        assert pack.match_character(chars, n)["name"].startswith("Tom"), n
    assert pack.match_character(chars, "Ada")["name"] == "Ada Lane"
    assert pack.match_character(chars, "Dale") is None


def test_roles_and_tiers():
    cfg = C.DEFAULT_CONFIG if hasattr(C, "DEFAULT_CONFIG") else C.load_config()
    llm = cfg["llm"]
    assert llm["models"]["drafter"]["extra_body"]["reasoning"]["effort"] == "medium"
    assert llm["models"]["editor"]["extra_body"]["reasoning"]["effort"] == "high"
    assert llm["roles"]["edit"]["tier"] == "editor"
    for r in ("extract", "deconflict", "summary"):
        assert llm["roles"][r]["tier"] == "planner"


def test_stable_pack_reads_bible_and_continuity(tmp_path):
    (tmp_path / "continuity.md").write_text("# Continuity\nMillbrook is the county seat.")
    (tmp_path / "style_bible.md").write_text("# Style\nFolksy narrator.")
    (tmp_path / "book1-beats-v2.md").write_text("old")
    (tmp_path / "book1-beats-v10.md").write_text("newest")

    class SB:
        root = tmp_path

    class Ctx:
        metadata = {}
        def style(self):
            return SB()
    out = pack.stable_pack(Ctx(), {"title": "x", "scenes": []})
    assert "Millbrook is the county seat." in out and "Folksy narrator." in out
    assert "newest" in out and "old\n" not in out
    assert out.index("continuity.md") < out.index("style_bible.md")


def test_pipeline_wiring():
    rsrc = inspect.getsource(revise_chapter)  # edit_chapter is called from _edit (screen fallback)
    assert "editor.edit_chapter" in rsrc and "revoice" not in rsrc
    dsrc = inspect.getsource(deai_chapter)
    assert "rewrite_flagged" not in dsrc and "rolled_back" in dsrc and "check_obligations" in dsrc
    from bookforge.steps import write_chapter
    assert "pack.build_write" in inspect.getsource(write_chapter.write_scene)


def test_extracted_canon_demoted():
    src = inspect.getsource(pack)
    assert "confirmed" in inspect.getsource(pack.confirmed_canon)
    from bookforge.steps import critique_chapter
    assert "may be wrong" in inspect.getsource(critique_chapter.critique_text)
