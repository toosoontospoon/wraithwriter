#!/usr/bin/env python3
"""Mechanical token scrub for external drafts.

LLMs miss Unicode tells (esp. em dashes). Run this AFTER the prose pass.
Does not invent wording: only replaces/strips known mechanical tokens.

``mode="prose"`` (the default, and the CLI default) is the upstream scrubber:
its output is byte-identical to the canonical token-scrub script.

``mode="fiction"`` is a local patch for narrative prose. Em/en dashes (and the
other dash-like codepoints) follow fiction punctuation instead of becoming
". ", then the remaining upstream token classes run. Fiction mode never
applies the upstream dash-to-period substitution, which breaks dialogue
("Wait. " she said.) and leaves a lowercase word after a period.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field


EM = "\u2014"  # —
EN = "\u2013"  # –
FIG_DASH = "\u2012"
HORIZ_BAR = "\u2015"
MINUS = "\u2212"
DASHES = (EM, EN, FIG_DASH, HORIZ_BAR, MINUS)
# Codepoints fiction mode must not leave behind. Prose mode removes the same
# set except U+201E / U+201A, which the upstream scrubber does not touch.
TELLS = {
    "em_dash": EM,
    "en_dash": EN,
    "figure_dash": FIG_DASH,
    "horizontal_bar": HORIZ_BAR,
    "minus_sign": MINUS,
    "ellipsis": "\u2026",
    "smart_quote_201c": "\u201c",
    "smart_quote_201d": "\u201d",
    "smart_quote_2018": "\u2018",
    "smart_quote_2019": "\u2019",
    "smart_quote_201e": "\u201e",
    "smart_quote_201a": "\u201a",
    "smart_quote_00ab": "\u00ab",
    "smart_quote_00bb": "\u00bb",
    "nbsp": "\u00a0",
    "nnbsp": "\u202f",
    "zwsp": "\u200b",
    "zwnj": "\u200c",
    "zwj": "\u200d",
    "bom": "\ufeff",
}
# Every dash-like codepoint fiction mode rewrites with the fiction rules,
# so none of them fall through to the upstream ". " substitution.
_FICTION_DASH = "".join(DASHES)
_QUOTES = "\"'\u201c\u201d\u2018\u2019\u201e\u201a\u00ab\u00bb"


@dataclass
class Report:
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, n: int = 1) -> None:
        if n:
            self.counts[key] = self.counts.get(key, 0) + n


def _scrub_prose(text: str) -> tuple[str, Report]:
    """Upstream scrub, unchanged. Do not edit this body; prose output must
    stay byte-identical to the canonical script."""
    r = Report()
    out = text

    # 1) Tight word—word / 2020–2021 → ASCII hyphen (keep compound/range readable)
    for ch in DASHES:
        pat = re.compile(rf"(?<=\w){re.escape(ch)}(?=\w)")
        n = len(pat.findall(out))
        if n:
            out = pat.sub("-", out)
            r.add("dash_between_words", n)

    # 2) Remaining dashes (usually spaced AI em-dash habit) → ". "
    for name, ch in (
        ("em_dash", EM),
        ("en_dash", EN),
        ("figure_dash", FIG_DASH),
        ("horizontal_bar", HORIZ_BAR),
        ("minus_sign", MINUS),
    ):
        if ch not in out:
            continue
        pat = re.compile(rf"\s*{re.escape(ch)}\s*")
        n = len(pat.findall(out))
        if n:
            out = pat.sub(". ", out)
            r.add(name, n)

    # Ellipsis character → three dots
    n = out.count("\u2026")
    if n:
        out = out.replace("\u2026", "...")
        r.add("ellipsis", n)

    # Smart quotes → straight
    smart = {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u00ab": '"',
        "\u00bb": '"',
    }
    for ch, repl in smart.items():
        n = out.count(ch)
        if n:
            out = out.replace(ch, repl)
            r.add(f"smart_quote_{ord(ch):04x}", n)

    # NBSP / zero-width junk
    for name, ch, repl in (
        ("nbsp", "\u00a0", " "),
        ("nnbsp", "\u202f", " "),
        ("zwsp", "\u200b", ""),
        ("zwnj", "\u200c", ""),
        ("zwj", "\u200d", ""),
        ("bom", "\ufeff", ""),
    ):
        n = out.count(ch)
        if n:
            out = out.replace(ch, repl)
            r.add(name, n)

    # Collapse accidental ". ." from dash→period (keep "...")
    out2 = re.sub(r"(?<!\.)\.\s+\.(?!\.)", ".", out)
    if out2 != out:
        r.add("double_period_collapse", 1)
        out = out2

    # Whitespace tidy
    out2 = re.sub(r"[ \t]+\n", "\n", out)
    out2 = re.sub(r"[ \t]{2,}", " ", out2)
    out2 = re.sub(r" +([,.;:?!)])", r"\1", out2)
    if out2 != out:
        r.add("whitespace_tidy", 1)
        out = out2

    return out, r


def _fiction_dashes(text: str, r: Report) -> str:
    """Fiction dash policy, applied before any other token class.

    - number ranges (10–12) become 10-12
    - a dash before a closing quote becomes ...
    - a dash right after an opening quote is removed
    - a dash at end of line becomes .
    - "--" becomes ", "
    - any other em/en/figure/horizontal/minus dash, tight or spaced, becomes ", "
    """
    out = text
    range_pat = re.compile(rf"(\d)\s*[{_FICTION_DASH}]\s*(\d)")
    n = len(range_pat.findall(out))
    if n:
        out = range_pat.sub(r"\1-\2", out)
        r.add("number_range", n)

    before_q = re.compile(rf"\s*[{_FICTION_DASH}]+\s*(?=[{_QUOTES}])")
    n = len(before_q.findall(out))
    if n:
        out = before_q.sub("...", out)
        r.add("dash_before_close_quote", n)

    after_q = re.compile(rf"(?<=[{_QUOTES}])\s*[{_FICTION_DASH}]+\s*")
    n = len(after_q.findall(out))
    if n:
        out = after_q.sub("", out)
        r.add("dash_after_open_quote", n)

    line_end = re.compile(rf"\s*[{_FICTION_DASH}]+\s*$", re.M)
    n = len(line_end.findall(out))
    if n:
        out = line_end.sub(".", out)
        r.add("dash_line_end", n)

    hy = re.compile(r"\s*--+\s*")
    n = len(hy.findall(out))
    if n:
        out = hy.sub(", ", out)
        r.add("double_hyphen", n)

    for name, ch in (
        ("em_dash", EM),
        ("en_dash", EN),
        ("figure_dash", FIG_DASH),
        ("horizontal_bar", HORIZ_BAR),
        ("minus_sign", MINUS),
    ):
        if ch not in out:
            continue
        pat = re.compile(rf"\s*{re.escape(ch)}+\s*")
        n = len(pat.findall(out))
        if n:
            out = pat.sub(", ", out)
            r.add(name, n)

    # "Hello—," would otherwise become "Hello, ,". Repeat so ", ," collapses
    # in this pass and a second scrub does not change the text.
    collapsed = False
    while True:
        out2 = re.sub(r",\s*([.,;:!?])", r"\1", out)
        if out2 == out:
            break
        collapsed = True
        out = out2
    if collapsed:
        r.add("comma_punct_collapse", 1)
    return out


def _other_tokens(out: str, r: Report, *, low9: bool) -> str:
    """Ellipsis, smart quotes, invisible spaces. ``low9`` adds „ ‚ (fiction only)."""
    n = out.count("\u2026")
    if n:
        out = out.replace("\u2026", "...")
        r.add("ellipsis", n)

    smart = {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u00ab": '"',
        "\u00bb": '"',
    }
    if low9:
        smart["\u201e"] = '"'
        smart["\u201a"] = "'"
    for ch, repl in smart.items():
        n = out.count(ch)
        if n:
            out = out.replace(ch, repl)
            r.add(f"smart_quote_{ord(ch):04x}", n)

    for name, ch, repl in (
        ("nbsp", "\u00a0", " "),
        ("nnbsp", "\u202f", " "),
        ("zwsp", "\u200b", ""),
        ("zwnj", "\u200c", ""),
        ("zwj", "\u200d", ""),
        ("bom", "\ufeff", ""),
    ):
        n = out.count(ch)
        if n:
            out = out.replace(ch, repl)
            r.add(name, n)

    out2 = re.sub(r"(?<!\.)\.\s+\.(?!\.)", ".", out)
    if out2 != out:
        r.add("double_period_collapse", 1)
        out = out2

    out2 = re.sub(r"[ \t]+\n", "\n", out)
    out2 = re.sub(r"[ \t]{2,}", " ", out2)
    out2 = re.sub(r" +([,.;:?!)])", r"\1", out2)
    if out2 != out:
        r.add("whitespace_tidy", 1)
        out = out2
    return out


def _stabilize_fiction(out: str) -> str:
    """Repeat the punctuation tidy until the text stops changing.

    Dash replacement and the ellipsis substitution can leave ", ..." or ",,".
    One pass of the comma rule does not always see what the later substitutions
    just created, so a second scrub would still change the text.
    """
    for _ in range(8):
        nxt = re.sub(r",\s*([.,;:!?])", r"\1", out)
        nxt = re.sub(r"(?<!\.)\.\s+\.(?!\.)", ".", nxt)
        nxt = re.sub(r"[ \t]+\n", "\n", nxt)
        nxt = re.sub(r"[ \t]{2,}", " ", nxt)
        nxt = re.sub(r" +([,.;:?!)])", r"\1", nxt)
        if nxt == out:
            return out
        out = nxt
    return out


def _scrub_fiction(text: str) -> tuple[str, Report]:
    r = Report()
    out = _fiction_dashes(text, r)
    out = _other_tokens(out, r, low9=True)
    stable = _stabilize_fiction(out)
    if stable != out:
        r.add("whitespace_tidy", 1)
        out = stable
    return out, r


def count_tells(text: str) -> dict[str, int]:
    """How many of each tell codepoint are still in ``text``."""
    return {name: n for name, ch in TELLS.items() if (n := text.count(ch))}


def scrub(text: str, mode: str = "prose") -> tuple[str, Report]:
    """Scrub mechanical tokens. ``mode`` is ``prose`` (default) or ``fiction``."""
    if mode == "fiction":
        return _scrub_fiction(text)
    if mode != "prose":
        raise ValueError(f"unknown scrub mode: {mode!r}")
    return _scrub_prose(text)


def main() -> int:
    p = argparse.ArgumentParser(description="Scrub AI Unicode tokens from a draft.")
    p.add_argument("path", nargs="?", help="Input file (default: stdin)")
    p.add_argument("-o", "--output", help="Write to file (default: stdout)")
    p.add_argument("-i", "--inplace", action="store_true", help="Overwrite input file")
    p.add_argument("--report", action="store_true", help="Print change counts to stderr")
    p.add_argument("--mode", choices=("prose", "fiction"), default="prose",
                   help="prose: upstream rules (default). fiction: dialogue-safe dashes, then the other token classes.")
    args = p.parse_args()

    if args.path:
        raw = open(args.path, encoding="utf-8").read()
    else:
        raw = sys.stdin.read()

    cleaned, report = scrub(raw, mode=args.mode)

    if args.report and report.counts:
        for k, v in sorted(report.counts.items()):
            print(f"{k}: {v}", file=sys.stderr)
    elif args.report:
        print("clean: nothing to scrub", file=sys.stderr)

    if args.inplace:
        if not args.path:
            print("error: --inplace needs a path", file=sys.stderr)
            return 2
        open(args.path, "w", encoding="utf-8").write(cleaned)
    elif args.output:
        open(args.output, "w", encoding="utf-8").write(cleaned)
    else:
        sys.stdout.write(cleaned)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
