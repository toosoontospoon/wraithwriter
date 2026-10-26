"""Vendored token scrubber: fiction dashes, prose byte-identical to upstream."""
import importlib.util
import json
import random
import re
import subprocess
import sys
from pathlib import Path

from bookforge.core import deai

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "bookforge" / "style" / "vendor" / "token_scrub" / "scrub.py"
UPSTREAM = ROOT / "tests" / "fixtures" / "deai" / "scrub_upstream.py"
TELLS = list(
    "\u2014\u2013\u2012\u2015\u2212\u2026\u201c\u201d\u2018\u2019\u201e\u201a\u00ab\u00bb"
    "\u00a0\u202f\u200b\u200c\u200d\ufeff"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fiction_cases():
    """(label, raw, fiction output). Prose is compared to upstream, not to these strings."""
    return [
        ("spaced dash", "He paused \u2014 then ran.", "He paused, then ran."),
        ("tight dash", "word\u2014word", "word, word"),
        ("dash before close quote", '"Wait\u2014" she said.', '"Wait..." she said.'),
        ("leading dash in quote", '"\u2014No," he said.', '"No," he said.'),
        ("dash before open quote", 'She said \u2014 "hello"', 'She said, "hello"'),
        ("dash before open quote tight", "She whispered\u2014'Go.'", "She whispered, 'Go.'"),
        ("dash after close quote", '"Wait." \u2014 she said.', '"Wait.", she said.'),
        ("dash after close quote tight", 'He shouted "No"\u2014then left.', 'He shouted "No", then left.'),
        ("apostrophe inside quotes", "She said 'don't'\u2014then left.", "She said 'don't', then left."),
        ("possessive before dash", "the boys'\u2014then the girls left.", "the boys', then the girls left."),
        ("interrupted contraction", "'don't\u2014'", "'don't...'"),
        ("possessive inside dialogue",
         "She said 'the boys' boat.'\u2014then left.",
         "She said 'the boys' boat.', then left."),
        ("plural possessive inside dialogue",
         "She said 'the boys' boats.'\u2014then left.",
         "She said 'the boys' boats.', then left."),
        ("second quotation after closer",
         "She said 'hello' \u2014 'go'.",
         "She said 'hello', 'go'."),
        ("closer before a later quotation",
         "She said 'hello' and waved. He answered 'yes.'\u2014then left.",
         "She said 'hello' and waved. He answered 'yes.', then left."),
        ("dash-interrupted possessive",
         "She said 'the boys' room\u2014' and left.",
         "She said 'the boys' room...' and left."),
        ("curly dash-interrupted possessive",
         "She said \u2018the boys\u2019 room\u2014\u2019 and left.",
         "She said 'the boys' room...' and left."),
        ("curly possessive inside dialogue",
         "She said \u2018the boys\u2019 boat.\u2019\u2014then left.",
         "She said 'the boys' boat.', then left."),
        ("curly interrupted contraction",
         "\u2018don\u2019t\u2014\u2019",
         "'don't...'"),
        ("name apostrophe", "O'Brien\u2014ran", "O'Brien, ran"),
        ("line-end dash", "He stopped\u2014\nNext.", "He stopped.\nNext."),
        ("double hyphen", "A--B", "A, B"),
        ("number range", "pages 10\u201312", "pages 10-12"),
        ("ellipsis", "wait\u2026", "wait..."),
        ("all quote kinds",
         "\u201cHi\u201d \u2018x\u2019 \u00aby\u00bb \u201ez\u201c \u201aW\u2018",
         "\"Hi\" 'x' \"y\" \"z\" 'W'"),
        ("nbsp", "a\u00a0b", "a b"),
        ("nnbsp", "a\u202fb", "a b"),
        ("zwsp", "a\u200bb", "ab"),
        ("zwnj", "a\u200cb", "ab"),
        ("zwj", "a\u200db", "ab"),
        ("bom", "\ufeffHi", "Hi"),
        ("minus between words", "a\u2212b", "a-b"),
        ("minus range", "10\u221212", "10-12"),
        ("figure dash", "a\u2012b", "a-b"),
        ("horizontal bar", "a\u2015b", "a-b"),
        ("spaced minus", "a \u2212 b", "a, b"),
        ("mixed dialogue",
         "\u201cWait\u2014\u201d she said\u2014quietly\u2026 pages 10\u201312 and \u2018ok\u2019",
         '"Wait..." she said, quietly... pages 10-12 and \'ok\''),
    ]


def _bad_periods(text: str) -> int:
    """A period, a space, then a lowercase word. Ellipses ('... pages') do not count."""
    return len(re.findall(r"(?<!\.)\.[ \t]+[a-z]", text))


def test_fiction_case_table_and_idempotence():
    vendor = _load(VENDOR, "vendor_scrub_cases")
    for label, raw, expected in _fiction_cases():
        out, _report = vendor.scrub(raw, mode="fiction")
        assert out == expected, label
        assert vendor.scrub(out, mode="fiction")[0] == out, label
        assert not any(ch in out for ch in TELLS), label
        assert '". ' not in out, label
        assert _bad_periods(out) <= _bad_periods(raw), label


def test_prose_mode_matches_upstream_table_and_random_strings():
    vendor = _load(VENDOR, "vendor_scrub_prose")
    upstream = _load(UPSTREAM, "upstream_scrub_prose")
    samples = [raw for _label, raw, _exp in _fiction_cases()]
    rng = random.Random(20261026)
    alphabet = list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 \n.,;:!?-'\"") + TELLS
    for _ in range(40):
        samples.append("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80))))
    for raw in samples:
        assert vendor.scrub(raw, mode="prose")[0] == upstream.scrub(raw)[0]
        assert vendor.scrub(raw)[0] == upstream.scrub(raw)[0]


def test_fiction_mode_drops_tell_codepoints_on_random_strings():
    vendor = _load(VENDOR, "vendor_scrub_prop")
    rng = random.Random(7)
    alphabet = list("abcdefghijklmnopqrstuvwxyz \n.,;:!?-'\"") + TELLS
    for _ in range(30):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 100)))
        out = vendor.scrub(raw, mode="fiction")[0]
        assert vendor.scrub(out, mode="fiction")[0] == out
        assert not any(ch in out for ch in TELLS)
        assert out.count('". ') <= raw.count('". ')
        assert _bad_periods(out) <= _bad_periods(raw)


def test_deai_scrub_is_fiction_mode():
    raw = "\u201cWait\u2014\u201d she said\u2014quietly\u2026 pages 10\u201312 and \u2018ok\u2019"
    assert deai.scrub(raw).startswith('"Wait..."')
    assert "10-12" in deai.scrub(raw) and "'ok'" in deai.scrub(raw)
    assert deai.scrub(deai.scrub(raw)) == deai.scrub(raw)


def test_scrub_report_and_count_token_tells():
    text, counts = deai.scrub_report("He paused \u2014 then ran.")
    assert text == "He paused, then ran."
    assert counts.get("em_dash")
    assert deai.count_token_tells("a\u2014b\u00a0c")["em_dash"] == 1
    assert deai.count_token_tells("a\u2014b\u00a0c")["nbsp"] == 1
    assert deai.count_token_tells("plain words") == {}


def test_scrub_obj_keeps_json_valid_and_does_not_need_raw_text():
    obj = {"title": "Wait\u2014 now", "n": 2, "ok": True, "scenes": [{"beats": ["pages 10\u201312"]}]}
    out = deai.scrub_obj(obj)
    assert out["title"] == "Wait, now"
    assert out["n"] == 2 and out["ok"] is True
    assert out["scenes"][0]["beats"] == ["pages 10-12"]
    assert list(out) == list(obj)
    again = json.loads(json.dumps(out))
    assert again == out
    raw = json.dumps({"note": "10\u201312"})
    cleaned = deai.scrub_obj(json.loads(raw))
    assert json.loads(json.dumps(cleaned))["note"] == "10-12"


def test_vendored_manifest_hashes_and_sync_check():
    manifest = json.loads((ROOT / "bookforge/style/vendor/VENDORED.json").read_text())
    assert manifest["sources"]["scrub"]["sha256"] == (
        "1ef87b93fc769c0e00b83a7663bd296e1a0400ac2cb9f1d6b58f2e6e64b28400")
    assert manifest["sources"]["skill"]["sha256"]
    assert manifest["date"] and "fiction" in manifest["note"].lower()
    import hashlib
    for rel, meta in manifest["files"].items():
        digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        assert digest == meta["sha256"], rel
    proc = subprocess.run([sys.executable, str(ROOT / "tools/sync_style_rules.py"), "--check"],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert not list((ROOT / "bookforge").rglob("SKILL.md"))
    header = (ROOT / "bookforge/style/generated/deai_skill_banlist.txt").read_text().splitlines()[0]
    assert header == "GENERATED by tools/sync_style_rules.py, do not edit"
