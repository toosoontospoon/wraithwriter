"""Story-so-far pack: the fixed context every planner, writer, editor and critic call gets.

STABLE part (same bytes for a whole chapter, so provider prompt caches hit):
  the human-written bible files (style bible in full, continuity sheet, characters, plot,
  decisions log, beat sheet) plus the world sections relevant to the chapter.
RECENT part: the FULL text of the last N chapters (default 2) and short summaries of older ones,
  plus full voice sheets for this scene's speakers only (matched robustly by name).

The machine-extracted canon is NOT included as "must not contradict" anymore (it is error-prone);
only facts a human marked confirmed are. The bible and real text are the source of truth.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

from bookforge.config import load_config
from bookforge.core.rules_card import RULE_CARD

DEFAULTS = {
    "enabled": True,
    "files": ["continuity.md", "style_bible.md", "characters.md", "plot.md", "decisions-log.md"],
    "beat_sheet": "",               # e.g. book1-beats-v4.md; "" = newest book*-beats*.md in the bible dir
    "world_file": "world.md",
    "world_tokens": 4000,           # relevant world.md sections only
    "recent_chapters": 2,
    "voice_tokens": 6000,
}


def cfg() -> dict:
    return {**DEFAULTS, **(load_config().get("pack") or {})}


def _tok(text: str) -> int:
    from bookforge.core.context import count_tokens
    return count_tokens(text)


# ------------------------------------------------------------------ names
_Q = "\"'\u201c\u201d\u2018\u2019"


def norm_name(s: str) -> str:
    """Lowercase, drop quoted nicknames and quote marks: 'Tom \u201cAce\u201d Reed' -> 'tom reed'."""
    s = re.sub(rf"[{_Q}][^{_Q}]*[{_Q}]", " ", s or "")
    s = re.sub(rf"[{_Q}]", "", s)
    return " ".join(s.lower().split())


def _forms(name: str, aliases=()) -> set[str]:
    out = set()
    for f in [name, *(aliases or [])]:
        if not f:
            continue
        out.add(norm_name(f))
        for nick in re.findall(rf"[{_Q}]([^{_Q}]+)[{_Q}]", f):
            out.add(nick.lower().strip())
    return {f for f in out if f}


def match_character(chars: dict, name: str):
    """Find a character dict by full name, alias, nickname or unambiguous first/last name."""
    n = norm_name(name)
    if not n:
        return None
    for c in chars.values():
        if n in _forms(c.get("name", ""), c.get("aliases")):
            return c
    toks = n.split()
    hits = [c for c in chars.values()
            if any(t in norm_name(c.get("name", "")).split() for t in toks[:1])]
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------ files
def bible_dir(ctx) -> Path | None:
    sb = ctx.style()
    if getattr(sb, "root", None):
        return Path(sb.root)
    d = ctx.metadata.get("style_bible_dir")
    return Path(d) if d else None


def _beat_sheet(d: Path, c: dict) -> Path | None:
    if c.get("beat_sheet"):
        p = d / c["beat_sheet"]
        return p if p.exists() else None
    cands = sorted(d.glob("book*-beats*.md"), key=lambda p: [int(x) if x.isdigit() else x
                                                               for x in re.split(r"(\d+)", p.name)])
    return cands[-1] if cands else None


def _sections(md: str) -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^(#{1,3} .*)$", md)
    out, head = [], ""
    if parts and parts[0].strip():
        out.append(("", parts[0]))
    for i in range(1, len(parts), 2):
        out.append((parts[i], parts[i + 1] if i + 1 < len(parts) else ""))
    return out


def _world_relevant(text: str, chapter: dict, budget: int) -> str:
    from bookforge.core.context import truncate_tokens
    keys = set()
    blob = " ".join(str(chapter.get(k, "")) for k in ("title", "summary", "act", "ends_with")) + " " + \
        " ".join(chapter.get("key_events") or []) + " " + \
        " ".join(s.get("location", "") + " " + " ".join(s.get("characters_present") or [])
                 for s in chapter.get("scenes") or [])
    for w in re.findall(r"[A-Z][a-z]{3,}", blob):
        keys.add(w.lower())
    scored = []
    for h, body in _sections(text):
        low = (h + body).lower()
        score = sum(low.count(k) for k in keys)
        if score:
            scored.append((score, h, body))
    scored.sort(key=lambda x: -x[0])
    out, used = [], 0
    for _, h, body in scored:
        piece = f"{h}\n{body.strip()}\n"
        n = _tok(piece)
        if used + n > budget:
            continue
        out.append(piece)
        used += n
    return truncate_tokens("\n".join(out), budget)


def character_filter_enabled(force=None) -> bool:
    """Limit ``characters.md`` to the chapter cast. The prior-text mode turns this on."""
    if force is not None:
        return bool(force)
    ctx_cfg = load_config().get("context") or {}
    if str(ctx_cfg.get("characters") or "") == "chapter_cast":
        return True
    return prior_text_mode() == "summaries_plus_prev_plan"


def _entry_labels(title: str, body: str) -> list[str]:
    labels = [title] if title else []
    for line in body.splitlines():
        m = re.match(r"(?i)\s*aliases?\s*:\s*(.+)$", line)
        if m:
            labels.extend(x.strip() for x in re.split(r"[,;]", m.group(1)) if x.strip())
    return labels


def _label_hit(wanted: str, labels: list[str]) -> bool:
    w = (wanted or "").lower().strip()
    if not w:
        return False
    wfirst = w.split()[0]
    for lab in labels:
        low = lab.lower().strip()
        if not low:
            continue
        if w == low or wfirst == low or w == low.split()[0] or wfirst == low.split()[0]:
            return True
    return False


def _label_in_blob(label: str, blob: str) -> bool:
    low = label.lower().strip()
    if not low:
        return False
    if low in blob:
        return True
    first = low.split()[0]
    return re.search(rf"(?<![a-z0-9]){re.escape(first)}(?![a-z0-9])", blob) is not None


def filter_characters_markdown(ctx, chapter: dict, text: str) -> str:
    """Keep entries for this chapter's cast. Headings, aliases, and first names all count."""
    parts = re.split(r"(?m)^(#{1,3} .+)$", text)
    if len(parts) < 3:
        return text
    wanted = []
    for scene in chapter.get("scenes") or []:
        if scene.get("pov"):
            wanted.append(scene["pov"])
        wanted.extend(scene.get("characters_present") or [])
    for name in (load_config().get("context") or {}).get("always_characters") or []:
        wanted.append(name)
    plan_text, notes_text, _digest = chapter_plan_sources(ctx, chapter.get("id", ""))
    blob = (plan_text + "\n" + notes_text + "\n" + outline_plan_card(chapter)).lower()
    kept = []
    for i in range(1, len(parts), 2):
        heading = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        title = re.sub(r"^#+\s*", "", heading).strip()
        labels = _entry_labels(title, body)
        if any(_label_hit(w, labels) for w in wanted) or any(_label_in_blob(lab, blob) for lab in labels):
            kept.append((heading + body).strip())
    preamble = parts[0].strip()
    body_out = "\n\n".join(kept)
    return (preamble + "\n\n" + body_out).strip() if preamble else body_out


def stable_pack(ctx, chapter: dict, character_filter=None) -> str:
    c = cfg()
    d = bible_dir(ctx)
    if not c["enabled"] or not d:
        return ""
    blocks = []
    for name in c["files"]:
        p = d / name
        if p.exists():
            text = p.read_text().strip()
            if (name == "characters.md" and chapter.get("id")
                    and character_filter_enabled(character_filter)):
                text = filter_characters_markdown(ctx, chapter, text).strip()
            blocks.append(f"===== {name} =====\n{text}")
    bs = _beat_sheet(d, c)
    if bs:
        blocks.append(f"===== {bs.name} (beat sheet; the plan for later chapters may change) =====\n{bs.read_text().strip()}")
    if not blocks:
        return ""
    return ("STORY PACK: the series bible and continuity sheet, written by the author. This is the source of truth.\n\n"
            + "\n\n".join(blocks))


def confirmed_canon(ctx) -> str:
    """Only human-confirmed extracted facts are binding."""
    facts = [f for f in ctx.canon().facts.values()
             if f.get("status") == "established" and f.get("confirmed")]
    return ("CONFIRMED FACTS:\n" + "\n".join(f"- {f['claim']}" for f in facts)) if facts else ""


def prior_text_mode() -> str:
    """``full`` (the default) keeps recent chapter text. ``summaries_plus_prev_plan`` does not."""
    raw = (load_config().get("context") or {}).get("prior_text")
    if str(raw or "full").strip() == "summaries_plus_prev_plan":
        return "summaries_plus_prev_plan"
    return "full"


def outline_plan_card(chapter: dict) -> str:
    """Outline chapter card, scene cards, and beats. Used when no plan file exists."""
    lines = [f"Chapter {chapter.get('id')} '{chapter.get('title', '')}'",
             f"Summary: {chapter.get('summary', '')}",
             f"Ends with: {chapter.get('ends_with', '')}"]
    for s in chapter.get("scenes") or []:
        lines.append(
            f"Scene {s.get('scene_number')} ({s.get('location', '')}; POV {s.get('pov', '')}): "
            f"{s.get('summary') or s.get('action', '')}")
        for b in s.get("beats") or []:
            lines.append(f"- {b}")
        present = s.get("characters_present") or []
        if present:
            lines.append("present: " + ", ".join(present))
    return "\n".join(lines)


def chapter_plan_text(ctx, chapter: dict, with_notes: bool = False) -> str:
    """Plan file when it exists, otherwise the outline card. Notes only for the current chapter."""
    plan_text, notes_text, _digest = chapter_plan_sources(ctx, chapter.get("id"))
    body = plan_text.strip() or outline_plan_card(chapter)
    if with_notes and notes_text.strip():
        body += ("\n\nAUTHOR NOTES (these notes override the plan file when they disagree):\n"
                 + notes_text.strip())
    return body


def prior_chapter_selection(ctx, chapter: dict) -> str:
    """Summaries of every prior chapter, the previous chapter's plan, and this chapter's plan.

    No full prior-chapter prose. ``recent_chapters`` text is not included.
    """
    cid = chapter.get("id", "")
    prior = [ch for ch in ctx.load_outline() if ch.get("id", "") < cid]
    s = ctx.load_summaries()
    parts = []
    if prior:
        parts.append("EARLIER CHAPTERS (summaries):\n" + "\n".join(
            f"- {ch['id']}: {(s.get('chapters') or {}).get(ch['id'], '(no summary)')}" for ch in prior))
        parts.append("PREVIOUS CHAPTER PLAN:\n" + chapter_plan_text(ctx, prior[-1], with_notes=False))
    parts.append("CURRENT CHAPTER PLAN:\n" + chapter_plan_text(ctx, chapter, with_notes=True))
    return "\n\n".join(p for p in parts if p)


def recent_pack(ctx, chapter_id: str) -> str:
    c = cfg()
    prior = [ch["id"] for ch in ctx.load_outline() if ch["id"] < chapter_id]
    n = int(c["recent_chapters"])
    full, older = prior[-n:] if n else [], prior[:-n] if n else prior
    s = ctx.load_summaries()
    out = []
    if older:
        out.append("EARLIER CHAPTERS (summaries):\n" + "\n".join(
            f"- {pid}: {s['chapters'].get(pid, '(no summary)')}" for pid in older))
    for pid in full:
        t = ctx.best_chapter_text(pid).strip()
        if t:
            title = (ctx.chapter(pid) or {}).get("title", "")
            out.append(f"===== FULL TEXT OF {pid} '{title}' (already written; continuity and voice model) =====\n{t}")
    return "\n\n".join(out)


def speakers(ctx, chapter: dict, scene: dict | None = None) -> list[str]:
    scenes = [scene] if scene else (chapter.get("scenes") or [])
    names = []
    for s in scenes:
        names += [s.get("pov", "")] + list(s.get("characters_present") or [])
    return [n for n in dict.fromkeys(names) if n]


def voices(ctx, names: list[str]) -> str:
    sb = ctx.style()
    chars = ctx.load_characters()
    resolved = []
    for n in names:
        if sb.voice_for(n) or sb.voice_for(norm_name(n)):
            resolved.append(n if sb.voice_for(n) else norm_name(n))
            continue
        c = match_character(chars, n)
        resolved.append(c["name"] if c else n)
    out = []
    seen = set()
    for n in resolved:
        v = sb.voice_for(n) or sb.voice_for(norm_name(n))
        if v and v["_id"] not in seen:
            seen.add(v["_id"])
            out.append(sb.render_voice(v))
    if not out:
        return ""
    from bookforge.core.context import truncate_tokens
    return truncate_tokens("VOICE SHEETS for this scene's speakers (range, not templates; never reuse sample lines). "
                           "Parts about later events in the series are background only:\n" + "\n\n".join(out),
                           int(cfg()["voice_tokens"]))


def characters_block(ctx, names: list[str], notes: bool = True) -> str:
    chars = ctx.load_characters()
    rows = []
    for n in names:
        c = match_character(chars, n)
        if c:
            rows.append(f"- {c['name']} ({c.get('role', '')}, {c.get('age') or '?'}): {c.get('personality', '')}")
            cn = [n for n in (c.get("canon_notes") or []) if n][-6:] if notes else []
            if cn:
                rows.append("  extracted notes (machine-made; the story pack wins): " + "; ".join(cn))
    return ("CHARACTERS IN THIS SCENE:\n" + "\n".join(rows)) if rows else ""


def world_block(ctx, chapter: dict) -> str:
    c, d = cfg(), bible_dir(ctx)
    wf = (d / c["world_file"]) if d else None
    if not wf or not wf.exists():
        return ""
    rel = _world_relevant(wf.read_text(), chapter, int(c["world_tokens"]))
    return f"===== {c['world_file']} (sections relevant to this chapter) =====\n{rel}" if rel else ""


def chapter_pack(ctx, chapter: dict) -> str:
    """Per-chapter cached prefix: identical bytes for every call about this chapter (planner, beat
    planner, writer, critic, checkers, editor). Ends with a cache break; call-specific text follows."""
    from bookforge.core.llm import CACHE_BREAK
    names = speakers(ctx, chapter)
    recent = (prior_chapter_selection(ctx, chapter) if prior_text_mode() == "summaries_plus_prev_plan"
              else recent_pack(ctx, chapter["id"]))
    blocks = [world_block(ctx, chapter), recent,
              characters_block(ctx, names, notes=False), voices(ctx, names)]
    body = "\n\n".join(b for b in blocks if b)
    return (body + CACHE_BREAK) if body else ""


def chapter_plan_block(chapter: dict, scene: dict | None = None) -> str:
    rows = [f"THIS CHAPTER: {chapter.get('id')} '{chapter.get('title', '')}'",
            f"Summary: {chapter.get('summary', '')}",
            f"Ends with: {chapter.get('ends_with', '')}"]
    for s in chapter.get("scenes") or []:
        mark = "  <== WRITE THIS SCENE" if scene and s.get("scene_number") == scene.get("scene_number") else ""
        rows.append(f"- Scene {s.get('scene_number')} ({s.get('location', '')}, {s.get('time', '')}; POV {s.get('pov', '')}): "
                    f"{s.get('summary') or s.get('action', '')}{mark}")
    from bookforge.core import structure as _structure
    sb = _structure.block(chapter)
    if sb:
        rows.append(sb)
    return "\n".join(rows)


PERSONA = ("You are the author and editorial team of an novel described in the story pack, "
           "for its target readers. Whatever the task below (planning, writing, checking or editing), serve clear, vivid, "
           "straightforward storytelling in the narrator voice the style bible describes, and treat the story "
           "pack as the source of truth.")


def system_for(ctx, chapter: dict | None = None, character_filter=None) -> str:
    """Persona, rule card, prose rules, the human bible files, confirmed facts.

    With the character filter off this is byte-identical for every chapter. The filter,
    when ``context.characters`` is ``chapter_cast`` or ``context.prior_text`` is
    ``summaries_plus_prev_plan``, keeps only this chapter's cast from ``characters.md``.
    """
    from bookforge.core.deai import prose_rules_block
    sp = stable_pack(ctx, chapter or {}, character_filter=character_filter)
    cc = confirmed_canon(ctx)
    return "\n\n".join(x for x in (PERSONA, RULE_CARD, prose_rules_block(), sp, cc) if x)


def chapter_plan_sources(ctx, chapter_id: str) -> tuple[str, str, str]:
    """(plan text, notes text, sha256 of the file bytes). Missing files contribute nothing."""
    import hashlib
    cfg = load_config().get("plan") or {}
    template = cfg.get("plan_file") or ""
    notes = cfg.get("notes_files") or []
    if isinstance(notes, str):
        notes = [notes]
    root = Path(ctx.project_dir)

    def read(tmpl: str) -> tuple[str, bytes]:
        if not tmpl:
            return "", b""
        try:
            rel = str(tmpl).format(chapter_id=chapter_id)
        except (KeyError, IndexError, ValueError):
            return "", b""
        path = root / rel
        if not path.is_file():
            return "", b""
        data = path.read_bytes()
        return data.decode("utf-8"), data

    plan_text, plan_bytes = read(template)
    note_parts, note_blobs = [], []
    for tmpl in notes:
        text, data = read(tmpl)
        if data:
            note_parts.append(text)
            note_blobs.append(data)
    digest = hashlib.sha256()
    digest.update(plan_bytes)
    for blob in note_blobs:
        digest.update(b"\0")
        digest.update(blob)
    return plan_text, "\n\n".join(note_parts), digest.hexdigest()


def author_plan_block(ctx, chapter_id: str) -> str:
    """Plan file and notes for beat planning. Empty when the keys are unset or the files are missing."""
    plan_text, notes_text, _digest = chapter_plan_sources(ctx, chapter_id)
    parts = []
    if plan_text.strip():
        parts.append("AUTHOR PLAN FILE:\n" + plan_text.strip())
    if notes_text.strip():
        parts.append("AUTHOR NOTES (these notes override the plan file when they disagree):\n" + notes_text.strip())
    return "\n\n".join(parts)


def notes_block(ctx, chapter: dict, scene: dict | None = None) -> str:
    """Volatile extracted notes (change as extraction runs), kept OUT of the cached prefix."""
    names = speakers(ctx, chapter, scene)
    chars = ctx.load_characters()
    rows = []
    for n in names:
        c = match_character(chars, n)
        cn = [x for x in (c.get("canon_notes") or []) if x][-6:] if c else []
        if cn:
            rows.append(f"- {c['name']}: " + "; ".join(cn))
    return ("EXTRACTED NOTES (machine-made; the story pack wins):\n" + "\n".join(rows)) if rows else ""


def prompt_token_estimate(text: str) -> int:
    """Token estimate shared by the budget trim, the usage row, and pack preview.

    Uses the in-repo counter when tiktoken is already loaded. Otherwise ``len/3.5``.
    Does not import or download a tokenizer.
    """
    if not text:
        return 0
    if "tiktoken" in sys.modules:
        from bookforge.core.context import count_tokens
        return max(1, count_tokens(text))
    return max(1, math.ceil(len(text) / 3.5))


def context_budget_tokens():
    """Write-prompt budget, or None when today's untrimmed prompt should be used."""
    raw = (load_config().get("write") or {}).get("context_budget_tokens")
    if raw is None or raw == "":
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def prior_word_count() -> int:
    try:
        n = int((load_config().get("write") or {}).get("prior_words") or 1500)
    except (TypeError, ValueError):
        return 1500
    return n if n > 0 else 1500


def last_words(text: str, n: int) -> str:
    words = (text or "").split()
    if len(words) <= n:
        return (text or "").strip()
    return " ".join(words[-n:])


def prior_tail(ctx, chapter: dict, so_far: str, n: int | None = None) -> str:
    """Last ``n`` words of this chapter so far, else of the previous chapter."""
    n = prior_word_count() if n is None else n
    if so_far and so_far.strip():
        return last_words(so_far, n)
    prior = [ch["id"] for ch in ctx.load_outline() if ch["id"] < chapter.get("id", "")]
    if not prior:
        return ""
    return last_words(ctx.best_chapter_text(prior[-1]), n)


def _scene_entity_ids(ctx, scene: dict) -> list[str]:
    from bookforge.core.schema import slugify
    names = list(scene.get("characters_present") or [])
    chars = ctx.load_characters()
    ids = []
    for n in names:
        c = match_character(chars, n)
        ids.append(slugify((c or {}).get("id") or (c or {}).get("name") or n))
    return list(dict.fromkeys(i for i in ids if i))


def continuity_facts_block(ctx, scene: dict) -> str:
    """Continuity and facts for entities present in this scene."""
    ids = _scene_entity_ids(ctx, scene)
    canon = ctx.canon()
    lines = []
    cont = canon.continuity_for(ids) if ids else {}
    if cont:
        lines.append("CONTINUITY:\n" + json.dumps(cont))
    facts = canon.facts_for(ids) if ids else []
    if facts:
        lines.append("FACTS:\n" + "\n".join(f"- {f.get('claim', '')}" for f in facts))
    d = bible_dir(ctx)
    if d and (d / "continuity.md").exists() and names_present(scene):
        wanted = {n.lower() for n in names_present(scene)}
        kept = []
        for para in (d / "continuity.md").read_text().split("\n\n"):
            low = para.lower()
            if any(n in low for n in wanted):
                kept.append(para.strip())
        if kept:
            lines.append("CONTINUITY SHEET:\n" + "\n\n".join(kept))
    if not lines:
        return ""
    return "CONTINUITY AND FACTS:\n" + "\n".join(lines)


def names_present(scene: dict) -> list[str]:
    return [n for n in (scene.get("characters_present") or []) if n]


def older_chapter_block(ctx, chapter: dict) -> str:
    """Summaries of prior chapters, plus a short style-bible head. No full chapter text."""
    parts = []
    if prior_text_mode() == "summaries_plus_prev_plan":
        parts.append(prior_chapter_selection(ctx, chapter))
    else:
        prior = [ch["id"] for ch in ctx.load_outline() if ch["id"] < chapter.get("id", "")]
        s = ctx.load_summaries()
        if prior:
            parts.append("EARLIER CHAPTERS (summaries):\n" + "\n".join(
                f"- {pid}: {(s.get('chapters') or {}).get(pid, '(no summary)')}" for pid in prior))
    sb = ctx.style()
    root = getattr(sb, "root", None)
    if root and (Path(root) / "style_bible.md").exists():
        words = (Path(root) / "style_bible.md").read_text().split()
        parts.append("STYLE BIBLE (condensed):\n" + " ".join(words[:400]))
    return "\n\n".join(parts)


def _shrink_to_estimate(text: str, room: int) -> str:
    if room <= 0 or not text:
        return ""
    if prompt_token_estimate(text) <= room:
        return text
    lo, hi, best = 0, len(text), ""
    while lo <= hi:
        mid = (lo + hi) // 2
        chunk = text[:mid]
        if prompt_token_estimate(chunk) <= room:
            best = chunk
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _condense_facts(ctx, chapter, scene, facts_text: str, room: int) -> str:
    """One plan-role condensation per scene, reused by later write calls."""
    key = (chapter.get("id"), scene.get("scene_number"))
    cache = getattr(ctx, "_condensed_facts", None)
    if cache is None:
        cache = {}
        ctx._condensed_facts = cache
    if key not in cache:
        card = {k: scene.get(k) for k in ("scene_number", "pov", "location", "characters_present", "purpose")
                if scene.get(k)}
        card["beats"] = list(scene.get("beats") or [])
        from bookforge.core.llm import llm_call
        res = llm_call(
            "CONDENSE FACTS for the writer. Keep only continuity this scene still needs.\n"
            f"SCENE CARD: {json.dumps(card)}\n"
            f"SCENE: {scene.get('scene_number')}\n"
            f"FACTS:\n{facts_text[:8000]}\n"
            'Output ONLY JSON: {"beats": ["condensed continuity"]}',
            role="plan", json_mode=True, temperature=0.1, max_tokens=800)
        cache[key] = res if isinstance(res, str) else json.dumps(res)
    return _shrink_to_estimate("CONTINUITY AND FACTS (condensed):\n" + cache[key], room)


def _trim_blocks(system: str, blocks: list[tuple[str, str]], budget: int, condense) -> str:
    """Keep blocks from the top until the estimate fits. Facts are condensed instead of dropped."""
    def estimate(parts: list[str]) -> int:
        body = "\n\n".join(p for p in parts if p)
        return prompt_token_estimate(system + "\n\n" + body if body else system)

    selected: list[str] = []
    for name, text in blocks:
        if not text:
            continue
        if estimate(selected + [text]) <= budget or (name == "mandatory" and not selected):
            selected.append(text)
            continue
        if name == "facts":
            room = budget - estimate(selected)
            short = condense(text, max(0, room))
            if short and estimate(selected + [short]) <= budget:
                selected.append(short)
            continue
        break
    return "\n\n".join(selected)


def _budget_system(ctx, chapter) -> str:
    """Rules only. The story pack is assembled, in priority order, in the user prompt."""
    from bookforge.core.deai import prose_rules_block
    return "\n\n".join(x for x in (PERSONA, RULE_CARD, prose_rules_block()) if x)


def _build_write_budgeted(ctx, chapter, scene, beats, so_far, ceiling_words, part, parts, budget) -> tuple[str, str]:
    """Beats and the scene card first. Lower blocks drop until the estimate fits."""
    card = {k: scene.get(k) for k in ("scene_number", "pov", "location", "time", "characters_present",
                                       "purpose", "conflict", "sensory_anchor", "closing_image") if scene.get(k)}
    pp = f" (part {part} of {parts}; write only these beats)" if parts > 1 else ""
    mandatory = "\n\n".join([
        f"BEATS TO WRITE NOW{pp}:\n" + "\n".join(f"{i}. {b}" for i, b in enumerate(beats, 1)),
        "SCENE CARD: " + json.dumps(card),
        f"YOUR JOB: write part {part} of scene {scene.get('scene_number')} of chapter {chapter['id']}, "
        "following the RULE CARD in the system prompt.",
        "LENGTH: write each beat to completion, then stop. "
        f"Hard ceiling: {ceiling_words} words (a limit, never a goal). Output ONLY the prose.",
    ])
    voice = voices(ctx, names_present(scene))
    facts = continuity_facts_block(ctx, scene)
    if prior_text_mode() == "summaries_plus_prev_plan":
        # Current-chapter prose only. Prior chapters are summaries and a plan, not their text.
        tail = last_words(so_far, prior_word_count()) if so_far and so_far.strip() else ""
    else:
        tail = prior_tail(ctx, chapter, so_far)
    prior = ("PRIOR TEXT (the last words already written; continue from the end, do not repeat them):\n" + tail) if tail else ""
    blocks = [("mandatory", mandatory), ("voices", voice), ("facts", facts), ("prior", prior),
              ("summaries", older_chapter_block(ctx, chapter))]
    system = _budget_system(ctx, chapter)

    def condense(text, room, scene=scene):
        return _condense_facts(ctx, chapter, scene, text, room)

    return system, _trim_blocks(system, blocks, budget, condense)


def build_write(ctx, chapter: dict, scene: dict, beats: list[str], so_far: str, ceiling_words: int,
                part: int = 1, parts: int = 1) -> tuple[str, str]:
    """(system, user) for one scene write call. Cached prefix first (system, then the chapter pack);
    the chapter so far, the scene's job and the length line last.

    A set ``write.context_budget_tokens`` rebuilds that prompt in priority order and trims
    from the bottom. Unset keeps this path.
    """
    budget = context_budget_tokens()
    if prior_text_mode() == "summaries_plus_prev_plan":
        so_far = last_words(so_far, prior_word_count()) if so_far and so_far.strip() else ""
    if budget:
        return _build_write_budgeted(ctx, chapter, scene, beats, so_far, ceiling_words, part, parts, budget)
    from bookforge.core import threads
    shown = so_far.strip() or "(nothing yet: this is the chapter's opening)"
    blocks = [chapter_plan_block(chapter, scene), notes_block(ctx, chapter, scene),
              "CHAPTER SO FAR (everything already written in this chapter; continue from its last line):\n"
              + shown]
    card = {k: scene.get(k) for k in ("scene_number", "pov", "location", "time", "characters_present",
                                       "purpose", "conflict", "sensory_anchor", "closing_image") if scene.get(k)}
    blocks.append("SCENE CARD: " + json.dumps(card))
    ob = threads.chapter_block(ctx, chapter, scene)
    if ob:
        blocks.append(ob)
    if scene.get("craft_brief"):
        blocks.append(scene["craft_brief"])
    pp = f" (part {part} of {parts}; write only these beats)" if parts > 1 else ""
    blocks.append(f"YOUR JOB: write part {part} of scene {scene.get('scene_number')} of chapter {chapter['id']}, "
                  "following the RULE CARD in the system prompt.\n"
                  f"BEATS TO WRITE NOW{pp}:\n" + "\n".join(f"{i}. {b}" for i, b in enumerate(beats, 1)))
    blocks.append("LENGTH: write each beat to completion, then stop. Give every beat what it needs (clear staging, "
                  "grounding, the explanation a reader needs) and nothing more. The beat size tags are rough guides, "
                  f"not quotas. Hard ceiling: {ceiling_words} words (a limit, never a goal).\n"
                  "Before you answer, check your scene against the continuity sheet and the chapter so far "
                  "(names, places, who knows what, objects, injuries, time of day) and fix any slip. "
                  "Output ONLY the prose.")
    return system_for(ctx, chapter), chapter_pack(ctx, chapter) + "\n\n".join(b for b in blocks if b)


def write_prompt_for_scene(ctx, chapter_id: str, scene_number: int) -> tuple[str, str]:
    """The write prompt ``write_scene`` would build for this scene. No LLM call."""
    chapter = ctx.chapter(chapter_id)
    if not chapter:
        raise ValueError(f"Chapter {chapter_id} not found")
    scene = next((s for s in chapter.get("scenes") or [] if int(s.get("scene_number", 0)) == int(scene_number)), None)
    if scene is None:
        raise ValueError(f"Scene {scene_number} not found in {chapter_id}")
    sp = (ctx.progress.get("scenes") or {}).get(chapter_id, {}).get(str(scene["scene_number"]), {})
    beats = sp.get("beats") or scene.get("beats") or [scene.get("action") or "Write the scene."]
    texts = []
    for s in chapter.get("scenes") or []:
        if int(s.get("scene_number", 0)) >= int(scene_number):
            break
        path = ctx.scene_path(chapter_id, s["scene_number"], "draft")
        if path.exists():
            texts.append(path.read_text())
    return build_write(ctx, chapter, {**scene, "beats": beats}, list(beats), "\n\n".join(texts), 800)


def preview_token_counts(ctx, chapter_id: str, scene_number: int, role: str = "write") -> tuple[int, int, int]:
    """(prompt that would be sent, with the character filter, without it). No LLM call."""
    chapter = ctx.chapter(chapter_id)
    if not chapter:
        raise ValueError(f"Chapter {chapter_id} not found")
    if role == "write":
        system, user = write_prompt_for_scene(ctx, chapter_id, scene_number)
    else:
        system, user = system_for(ctx, chapter), chapter_pack(ctx, chapter)
    sent = prompt_token_estimate(system + "\n\n" + user)
    on = prompt_token_estimate(system_for(ctx, chapter, character_filter=True) + "\n\n" + user)
    off = prompt_token_estimate(system_for(ctx, chapter, character_filter=False) + "\n\n" + user)
    return sent, on, off


def fit_write_context(ctx, system: str, user: str, provider: str | None = None,
                      model: str | None = None) -> tuple[str, str]:
    """Trim a write pack that would exceed ~24k tokens on the 32k Gemma window.

    No-op for other models. Uses ``total_context_tokens`` (capped at the window's
    prompt budget) and ``block_caps`` the same way ``BookContext._assemble`` does:
    ``truncate_tokens`` per block, story pack from the head, continuation from the tail.
    """
    from bookforge.config import prompt_budget_for, resolve_role
    from bookforge.core.context import count_tokens, truncate_tokens
    # The priority trim already ran. A later tail shrink would drop the beats and the card.
    if context_budget_tokens():
        return system, user
    if not (provider and model):
        provider, model = resolve_role("write")
    budget = prompt_budget_for(provider, model, getattr(ctx, "total_context_tokens", None))
    if not budget or count_tokens(system) + count_tokens(user) <= budget:
        return system, user
    caps = getattr(ctx, "block_caps", None) or {}
    user_tokens = count_tokens(user)
    tail_floor = int(caps.get("continuity") or 2000)
    tail_cap = min(user_tokens, max(tail_floor, budget // 3))
    sys_cap = min(budget, max(int(caps.get("instructions") or 2000), budget - min(user_tokens, tail_cap)))
    system = _shrink_to(system, sys_cap, "head", count_tokens, truncate_tokens)
    remain = max(1, budget - count_tokens(system))
    user = _shrink_to(user, remain, "tail", count_tokens, truncate_tokens)
    return system, user


def _shrink_to(text: str, cap: int, keep: str, count_tokens, truncate_tokens) -> str:
    """Call truncate_tokens until the block fits. The char heuristic can undershoot a dense string."""
    for _ in range(12):
        if count_tokens(text) <= cap:
            return text
        nxt = truncate_tokens(text, max(1, cap), keep)
        if nxt == text:
            cap = max(1, int(cap * 0.8))
            nxt = truncate_tokens(text, cap, keep)
            if nxt == text:
                # Still denser than the char estimate. Cut by characters.
                chars = max(1, cap * 3)
                text = ("..." + text[-chars:]) if keep == "tail" else (text[:chars] + "...")
                continue
        text = nxt
    return text
