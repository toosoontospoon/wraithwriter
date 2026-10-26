#!/usr/bin/env python3
"""Mechanical token scrub for external drafts.

LLMs miss Unicode tells (esp. em dashes). Run this AFTER the prose pass.
Does not invent wording: only replaces/strips known mechanical tokens.
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


@dataclass
class Report:
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, n: int = 1) -> None:
        if n:
            self.counts[key] = self.counts.get(key, 0) + n


def scrub(text: str) -> tuple[str, Report]:
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


def main() -> int:
    p = argparse.ArgumentParser(description="Scrub AI Unicode tokens from a draft.")
    p.add_argument("path", nargs="?", help="Input file (default: stdin)")
    p.add_argument("-o", "--output", help="Write to file (default: stdout)")
    p.add_argument("-i", "--inplace", action="store_true", help="Overwrite input file")
    p.add_argument("--report", action="store_true", help="Print change counts to stderr")
    args = p.parse_args()

    if args.path:
        raw = open(args.path, encoding="utf-8").read()
    else:
        raw = sys.stdin.read()

    cleaned, report = scrub(raw)

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
