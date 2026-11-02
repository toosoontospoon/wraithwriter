"""Seed spans: English bytes wrapped as ``<<SEED>>...<</SEED>>``.

The translator must copy each span unchanged. A mangled inner span whose markers
survived is spliced back. Lost markers are the caller's signal to retry.
"""
from __future__ import annotations

import re

SEED_RE = re.compile(r"<<SEED>>(.*?)<</SEED>>", re.S)
OPEN = "<<SEED>>"
CLOSE = "<</SEED>>"


class SeedLost(RuntimeError):
    """A paragraph lost its seed markers and a retry did not bring them back."""


def seed_spans(text: str) -> list[str]:
    """Full ``<<SEED>>...<</SEED>>`` spans, in order."""
    return [m.group(0) for m in SEED_RE.finditer(text or "")]


def seed_inners(text: str) -> list[str]:
    """Bytes between the markers, in order."""
    return [m.group(1) for m in SEED_RE.finditer(text or "")]


def is_seed_only(paragraph: str) -> bool:
    """True when the paragraph is one seed span and no Latin."""
    return SEED_RE.fullmatch((paragraph or "").strip()) is not None


def _ordered(spans: list[str], text: str) -> bool:
    pos = 0
    for span in spans:
        i = text.find(span, pos)
        if i < 0:
            return False
        pos = i + len(span)
    return True


def repair_seeds(latin: str, english: str) -> tuple[str, str]:
    """Return ``(english, status)`` with status ``ok``, ``repaired``, or ``lost``.

    ``repaired`` means every marker pair survived and the original span bytes
    were written back between them. ``lost`` means the markers themselves are
    gone or the counts do not match, so the caller should retry.
    """
    src = seed_spans(latin)
    if not src:
        return english, "ok"
    if _ordered(src, english or ""):
        return english, "ok"
    found = list(SEED_RE.finditer(english or ""))
    if len(found) != len(src):
        return english, "lost"
    out = english
    for src_span, match in zip(reversed(src), reversed(found)):
        out = out[:match.start()] + src_span + out[match.end():]
    return out, "repaired"


def strip_markers(text: str) -> str:
    """Drop the markers and keep the bytes they wrapped."""
    return (text or "").replace(OPEN, "").replace(CLOSE, "")


def mask_seeds(text: str) -> str:
    """Same length as ``text``, with each full seed span replaced by spaces."""
    return SEED_RE.sub(lambda m: " " * len(m.group(0)), text or "")


def map_outside_seeds(text: str, fn) -> str:
    """Apply ``fn`` to every stretch that is not inside a seed span.

    The span, markers included, is copied through byte for byte.
    """
    if not text:
        return fn(text)
    parts: list[str] = []
    last = 0
    for match in SEED_RE.finditer(text):
        parts.append(fn(text[last:match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(fn(text[last:]))
    return "".join(parts)


def shield_inners(text: str, inners: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """Replace each seed inner, in order, with an ASCII token scrub will not rewrite.

    The markers are already gone by the time the write step scrubs the scene.
    The token is put back with ``restore_shields`` after that scrub.
    """
    out = text or ""
    pos = 0
    shields: list[tuple[str, str]] = []
    for n, inner in enumerate(inners):
        if inner == "":
            continue
        i = out.find(inner, pos)
        if i < 0:
            raise SeedLost(f"seed span missing before scrub: {inner!r}")
        token = f"SEEDSHIELD{n}END"
        out = out[:i] + token + out[i + len(inner):]
        pos = i + len(token)
        shields.append((token, inner))
    return out, shields


def restore_shields(text: str, shields: list[tuple[str, str]]) -> str:
    """Put each shielded inner back. A missing token means the scrub ate the shield."""
    out = text or ""
    for token, inner in shields:
        i = out.find(token)
        if i < 0:
            raise SeedLost(f"seed shield lost during scrub: {token}")
        out = out[:i] + inner + out[i + len(token):]
    return out


def assert_seeds_preserved(source_text: str, final_text: str) -> None:
    """Each seed's inner bytes from ``source_text`` occur in ``final_text``, in order.

    ``final_text`` is the stripped manuscript: markers are already gone, the
    bytes that were inside them must still be there.
    """
    pos = 0
    final = final_text or ""
    for inner in seed_inners(source_text):
        i = final.find(inner, pos)
        if i < 0:
            raise SeedLost(f"seed span missing from final text: {inner!r}")
        pos = i + len(inner)


def visible(text: str) -> str:
    """Text with markers removed and the wrapped bytes left in place."""
    return strip_markers(text or "")
