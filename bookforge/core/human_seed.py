"""Human seed: the verbatim opening of a scene.

A seed file is plain UTF-8 markdown. An optional YAML front matter block is the
only thing removed. The body is not normalized: no quote, dash, whitespace, or
unicode changes, and no scrub, clean_prose, or enforce_first_line.

The locked body is that text minus exactly one trailing end-of-line when the
file has one. No other character is dropped. If the body's last non-space
character is an unmatched opening quote (``"`` or U+201C), the continuation is
appended with nothing between them. Otherwise it is joined with the paragraph
separator ``\\n\\n``.
"""
from __future__ import annotations

import sys
from pathlib import Path

class HumanSeedError(RuntimeError):
    """Drafting refused because the chapter has no usable human opening."""


def strip_front_matter(text: str) -> str:
    """Remove one leading ``---`` front-matter block, if it is closed.

    The block is a first line that is exactly ``---``, YAML lines, and a later
    line that is exactly ``---``. The newline that ends the closing ``---``
    line is removed with it. Nothing else is changed. An unclosed block, or a
    file that does not start with ``---``, is returned unchanged.
    """
    if not text.startswith("---"):
        return text
    nl = "\r\n" if text.startswith("---\r\n") else "\n" if text.startswith("---\n") else ""
    if not nl:
        return text
    rest = text[len("---" + nl):]
    lines = rest.splitlines(keepends=True)
    for i, line in enumerate(lines):
        bare = line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line
        if bare == "---":
            return "".join(lines[i + 1:])
    return text


def seed_body(text: str) -> str:
    """Front matter removed, then exactly one trailing end-of-line if the file has one.

    A final ``\\r\\n`` counts as one end-of-line. A second trailing newline stays.
    Spaces before that end-of-line stay, including trailing spaces on earlier lines.
    """
    body = strip_front_matter(text)
    if body.endswith("\r\n"):
        return body[:-2]
    if body.endswith("\n") or body.endswith("\r"):
        return body[:-1]
    return body


def ends_inside_open_quote(body: str) -> bool:
    """True when the last non-space character is an unmatched ``"`` or U+201C.

    Spaces and tabs before the end do not count. Straight quotes toggle. U+201C
    opens and U+201D closes. A quote that is still open at that character means
    the continuation continues inside the spoken line.
    """
    i = len(body) - 1
    while i >= 0 and body[i] in " \t":
        i -= 1
    if i < 0 or body[i] not in ('"', "\u201c"):
        return False
    straight = False
    curly = 0
    for ch in body:
        if ch == "\u201c":
            curly += 1
        elif ch == "\u201d":
            curly = max(0, curly - 1)
        elif ch == '"':
            straight = not straight
    if body[i] == "\u201c":
        return curly > 0
    return straight


def drop_reopened_quote(continuation: str) -> str:
    """The seed already opened the spoken line. A model that opens it again (``"Go on``)
    would leave ``""Go on``. Drop leading whitespace and that one repeated opening quote.
    Only the continuation changes; the seed is never touched."""
    rest = continuation.lstrip(" \t\r\n")
    if rest[:1] in ('"', "\u201c"):
        return rest[1:]
    return continuation


def join_opening(opening: str, continuation: str) -> str:
    """Append a continuation after a seed body.

    Inside an unmatched opening quote, nothing is inserted. Otherwise the
    paragraph separator used between prose chunks (``\\n\\n``) is inserted.
    """
    if not opening:
        return continuation
    if ends_inside_open_quote(opening):
        return opening + drop_reopened_quote(continuation)
    if not continuation:
        return opening
    return opening + "\n\n" + continuation


def continuation_note(opening: str) -> str:
    """Prompt text that quotes the seed and, when needed, the open spoken line."""
    if ends_inside_open_quote(opening):
        return ("ALREADY WRITTEN (verbatim; continue directly after this; "
                "do not repeat or rewrite it). You are continuing inside that spoken line; "
                "the next character follows the opening quote with no inserted whitespace. "
                "Do not write another opening quote mark; start with the first spoken word:\n"
                + opening)
    return ("ALREADY WRITTEN (verbatim; continue directly after this; "
            "do not repeat or rewrite it):\n" + opening)


def chapter_seed_path(ctx, chapter_id: str) -> Path:
    return ctx.manuscript_dir / chapter_id / "human_seed.md"


def scene_seed_candidates(ctx, chapter_id: str, scene_number: int) -> list[Path]:
    """``scene01`` then ``scene1``. Both folder names are accepted."""
    n = int(scene_number)
    root = ctx.manuscript_dir / chapter_id
    names = [f"scene{n:02d}"]
    plain = f"scene{n}"
    if plain not in names:
        names.append(plain)
    return [root / name / "human_seed.md" for name in names]


def scene_seed_path(ctx, chapter_id: str, scene_number: int) -> Path | None:
    for p in scene_seed_candidates(ctx, chapter_id, scene_number):
        if p.is_file():
            return p
    return None


def _read(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def seed_for_scene(ctx, chapter: dict, scene: dict) -> str:
    """Opening prefix for this scene.

    A per-scene file replaces the chapter seed for that scene. The chapter
    seed applies only to the chapter's first scene. Missing files yield ``""``.
    """
    scenes = chapter.get("scenes") or []
    first = scenes[0]["scene_number"] if scenes else None
    override = _read(scene_seed_path(ctx, chapter["id"], scene.get("scene_number")))
    if override is not None:
        return seed_body(override)
    if scene.get("scene_number") == first:
        chapter_text = _read(chapter_seed_path(ctx, chapter["id"]))
        if chapter_text is not None:
            return seed_body(chapter_text)
    return ""


def _format_note(chapter_path: Path) -> str:
    scene_hint = chapter_path.parent / "scene1" / "human_seed.md"
    scene_hint_pad = chapter_path.parent / "scene01" / "human_seed.md"
    return (
        f"Expected plain UTF-8 markdown at {chapter_path} "
        f"(optional YAML front matter: a line ---, YAML keys such as chapter, scene, "
        f"beat_id, author, date, and a closing line ---; only that block is removed). "
        f"A per-scene override replaces it at {scene_hint} or {scene_hint_pad}. "
        f"The body is kept verbatim."
    )


def read_pasted_seed(stream) -> str:
    """Read a pasted seed. Stop on a line that is only ``.`` or on EOF.

    Lines keep the newline the stream returned. The terminator line is not
    included. Nothing else is edited.
    """
    chunks = []
    while True:
        line = stream.readline()
        if line == "":
            break
        bare = line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line
        if bare == ".":
            break
        chunks.append(line)
    return "".join(chunks)


def ensure_chapter_seed(ctx, chapter, stdin=None) -> None:
    """Refuse to draft when the first scene has no non-whitespace seed.

    A TTY may paste a seed (end with a line ``.`` or EOF). It is saved to the
    chapter seed path and drafting continues. A non-TTY raises HumanSeedError
    and does not prompt. No-op when ``write.require_human_seed`` is false.
    """
    from bookforge.config import load_config
    if not (load_config().get("write") or {}).get("require_human_seed", True):
        return
    scenes = chapter.get("scenes") or []
    if not scenes:
        return
    first = scenes[0]
    body = seed_for_scene(ctx, chapter, first)
    if body.strip():
        return
    path = chapter_seed_path(ctx, chapter["id"])
    stream = stdin if stdin is not None else sys.stdin
    if getattr(stream, "isatty", lambda: False)():
        print(
            f"No human seed for {chapter['id']}. {_format_note(path)}\n"
            "Paste the opening now. End with a line containing only . or with EOF:"
        )
        pasted = read_pasted_seed(stream)
        if not seed_body(pasted).strip():
            raise HumanSeedError(
                f"Refusing to draft {chapter['id']}: pasted seed is empty. {_format_note(path)}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pasted, encoding="utf-8")
        return
    raise HumanSeedError(
        f"Refusing to draft {chapter['id']}: no human seed. {_format_note(path)}"
    )


def raw_family(model: str) -> str:
    """``gemma`` when the write-role model id contains gemma, else a short slug."""
    m = (model or "").lower()
    if "gemma" in m:
        return "gemma"
    slug = m.split("/")[-1] if m else "model"
    out = []
    for ch in slug:
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    family = "".join(out).strip("-")[:24]
    return family or "model"


def next_raw_path(directory: Path, chapter_id: str, family: str) -> Path:
    """``<chapter_id>-vN-<family>-raw.txt``. N is the smallest free version starting at 1."""
    directory = Path(directory)
    n = 1
    while True:
        path = directory / f"{chapter_id}-v{n}-{family}-raw.txt"
        if not path.exists():
            return path
        n += 1
