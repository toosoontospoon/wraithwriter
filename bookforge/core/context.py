"""BookContext: project paths, canonical state loaders, and budgeted context assembly."""
from __future__ import annotations

import datetime
import json
import math
from pathlib import Path

from bookforge.config import load_config
from bookforge.core import deai
from bookforge.core.canon import Canon
from bookforge.core.embeddings import PassageStore
from bookforge.core.io import atomic_write_json, atomic_write_text, read_json
from bookforge.core.schema import normalize_characters, normalize_outline, normalize_world


class NullEmbeddingStore:
    """Kept for compatibility (tests/CLI may pass it)."""
    def replace(self, *a, **k):
        return 0

    def remove_chapter(self, *a, **k):
        pass

    def retrieve(self, *a, **k):
        return []


try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))
except Exception:  # pragma: no cover - optional
    def count_tokens(text: str) -> int:
        return max(1, len(text) // 4)


def truncate_tokens(text: str, max_tokens: int, keep: str = "head") -> str:
    if count_tokens(text) <= max_tokens:
        return text
    chars = max_tokens * 4
    if keep == "tail":
        head, _, body = text.partition("\n")  # keep the block header line
        if body and len(head) < chars // 2:
            return head + "\n..." + body[-(chars - len(head) - 4):]
        return "..." + text[-chars:]
    return text[:chars] + "..."


class BookContext:
    def __init__(self, project_dir: Path, embedding_store=None, verbose: bool = False):
        from bookforge.core import llm
        self.project_dir = Path(project_dir)
        self.verbose = verbose
        self.steps_dir = self.project_dir / "steps"
        self.state_dir = self.project_dir / "state"
        self.manuscript_dir = self.project_dir / "manuscript"
        for d in (self.project_dir, self.steps_dir, self.state_dir, self.manuscript_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.metadata = read_json(self.project_dir / "metadata.json", {}) or {}
        self.progress = read_json(self.project_dir / "progress.json", None) or {}
        self.progress.setdefault("completed", [])
        self.progress.setdefault("chapter_status", {})
        self.progress.setdefault("hashes", {})
        self.progress.setdefault("scenes", {})
        cfg = load_config()
        if embedding_store is not None:
            self.embeddings = embedding_store
        else:
            self.embeddings = PassageStore(self.project_dir, cfg["embeddings"].get("enabled", False),
                                           cfg["embeddings"].get("model", ""))
        c = cfg["context"]
        self.total_context_tokens = c["total_context_tokens"]
        self.continuity_chars = c["continuity_chars"]
        self.block_caps = c["block_caps"]
        self.scene_max_words = c.get("scene_max_words_per_call", 2000)
        self.working_chunk_words = c.get("working_chunk_words", 500)
        llm.set_cost_log(self.project_dir / "costs.jsonl")

    # ------------------------------------------------------------------ io
    def save_json(self, name: str, data):
        atomic_write_json(self.project_dir / name, data)

    def save_progress(self):
        self.save_json("progress.json", self.progress)

    def new_step_folder(self, step_name: str) -> Path:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        folder = self.steps_dir / f"{ts}_{step_name}"
        folder.mkdir(parents=True)
        return folder

    def mark_completed(self, step: str):
        if step not in self.progress["completed"]:
            self.progress["completed"].append(step)
        self.save_progress()

    # ------------------------------------------------------------------ canonical state
    def load_outline(self) -> list[dict]:
        data = read_json(self.state_dir / "outline.json", []) or []
        return data if isinstance(data, list) else []

    def save_outline(self, outline: list):
        atomic_write_json(self.state_dir / "outline.json", normalize_outline(outline))

    def chapter(self, chapter_id: str) -> dict | None:
        return next((c for c in self.load_outline() if c["id"] == chapter_id), None)

    def load_characters(self) -> dict[str, dict]:
        """Always a dict keyed by stable id, whatever shape is on disk."""
        return normalize_characters(read_json(self.state_dir / "characters.json", {}) or {})

    def save_characters(self, chars):
        atomic_write_json(self.state_dir / "characters.json", normalize_characters(chars))

    def load_world(self) -> dict:
        return normalize_world(read_json(self.state_dir / "world.json", {}) or {})

    def save_world(self, world):
        atomic_write_json(self.state_dir / "world.json", normalize_world(world))

    def canon(self) -> Canon:
        return Canon(self.state_dir)

    # summaries: {"scenes": {ch: {n: s}}, "chapters": {ch: s}, "book": {"through": ch, "text": s}}
    def load_summaries(self) -> dict:
        s = read_json(self.state_dir / "summaries.json", None)
        if s is None:
            legacy = read_json(self.state_dir / "chapter_summaries.json", {}) or {}
            s = {"scenes": {}, "chapters": legacy, "book": {}}
        return s

    def save_summaries(self, s: dict):
        atomic_write_json(self.state_dir / "summaries.json", s)

    def load_chapter_summaries(self) -> dict:
        return self.load_summaries()["chapters"]

    def save_chapter_summary(self, chapter_id: str, summary: str):
        s = self.load_summaries()
        s["chapters"][chapter_id] = summary
        self.save_summaries(s)

    def style(self):
        """Style bible + voice sheets (cached per context)."""
        if getattr(self, "_style", None) is None:
            from bookforge.core import style_bible
            self._style = style_bible.load(self.project_dir, self.metadata.get("style_bible_dir"))
        return self._style

    def get_persona(self) -> str:
        return self.metadata.get("brainstorm", {}).get("persona", "")

    # manuscript files
    def scene_path(self, chapter_id: str, n: int, kind: str = "draft") -> Path:
        return self.manuscript_dir / chapter_id / f"scene{int(n):02d}_{kind}.txt"

    def chapter_path(self, chapter_id: str, kind: str = "draft") -> Path:
        return self.manuscript_dir / f"{chapter_id}_{kind}.txt"

    def best_chapter_text(self, chapter_id: str) -> str:
        for kind in ("final", "revised", "draft"):
            p = self.chapter_path(chapter_id, kind)
            if p.exists():
                return p.read_text()
        return ""

    def write_text(self, path: Path, text: str):
        atomic_write_text(path, text)

    def cast_names(self) -> list[str]:
        names = []
        for c in self.load_characters().values():
            names.append(c["name"].split()[0])
        return names

    # ------------------------------------------------------------------ stable prefix (cacheable)
    def stable_prefix(self) -> str:
        """Book-stable system prefix: the same prose prompt the writer caches, plus world rules.

        Wired to ``pack.system_for`` (persona, rule card, prose rules, story pack) so this is
        not a dead copy of the old cast dump. The style-bible drafter block and the saved
        world rules follow. Byte-stable for a given book. Truncated to the prefix budget.
        """
        from bookforge.core import pack
        parts = [pack.system_for(self)]
        sb = self.style().block(int(load_config().get("style", {}).get("prefix_tokens", 2500)))
        if sb:
            parts.append(sb)
        rules = [r for r in (self.load_world().get("core_rules") or []) if r]
        if rules:
            parts.append("WORLD RULES:\n" + "\n".join(f"- {r}" for r in rules))
        return truncate_tokens("\n\n".join(parts), 12000)

    # ------------------------------------------------------------------ volatile context
    def get_scene_context(self, chapter: dict, scene: dict, beats: list[str], story_so_far: str,
                          part: int = 1, parts: int = 1) -> str:
        """Budgeted per-call context. Each block has its own cap; lowest priority shrinks first."""
        caps = self.block_caps
        ch_id = chapter["id"]
        canon = self.canon()
        chars = self.load_characters()
        name_idx = canon.name_index()
        present = []
        for n in scene.get("characters_present", []):
            eid = name_idx.get(n.lower()) or name_idx.get(n.split()[0].lower()) if n else None
            if eid:
                present.append(eid)
        loc_id = name_idx.get(scene.get("location", "").lower())
        beat_text = " ".join(beats)
        mentioned = {eid for form, eid in name_idx.items() if len(form) > 3 and form in beat_text.lower()}
        ents = list(dict.fromkeys(present + ([loc_id] if loc_id else []) + sorted(mentioned)))

        blocks: list[tuple[int, str, str]] = []  # (priority, name, text); lower = more important

        instr = (f"You are writing part {part}/{parts} of scene {scene.get('scene_number')} of chapter "
                 f"{ch_id} '{chapter.get('title', '')}'.\n"
                 f"POV: {scene.get('pov', '')} | person: {scene.get('person', 'third')} | tense: {scene.get('tense', 'past')}\n"
                 f"Location: {scene.get('location', '')}\nBEATS FOR THIS PART:\n"
                 + "\n".join(f"{i + 1}. {b}" for i, b in enumerate(beats))
                 + f"\n\nEmotional arc: {scene.get('emotional_arc', '')}\nPurpose: {scene.get('purpose', '')}\n"
                 + "".join(f"{lab}: {scene[k] if isinstance(scene.get(k), str) else '; '.join(scene.get(k) or [])}\n"
                           for k, lab in (("wants", "Who wants what"), ("obstacle", "Obstacle"),
                                          ("emotional_turn", "Emotional turn"), ("sensory_anchor", "Sensory anchor (use it)"),
                                          ("closing_image", "Closing image (end the scene on it)"),
                                          ("tone_target", "Tone target"), ("voice_notes", "Voice notes"))
                           if scene.get(k))
                 + (f"Imitate style-bible example passages: {', '.join(scene['style_samples'])}\n"
                    if scene.get("style_samples") else "")
                 + f"Character moments: {scene.get('character_moments', [])}\nWorld reveals: {scene.get('world_reveals', [])}\n"
                 + ("This is the LAST part of the scene. Land its turning point.\n" if part == parts else ""))
        blocks.append((0, "instructions", truncate_tokens(instr, caps["instructions"])))
        from bookforge.core import threads as _threads
        _ob = _threads.chapter_block(self, chapter, scene)
        if _ob:
            blocks.append((0, "obligations", _ob))

        if scene.get("craft_brief"):
            blocks.append((1, "craft", scene["craft_brief"]))

        if story_so_far:
            blocks.append((1, "continuity", "TEXT SO FAR (continue directly from the end):\n\"\"\"\n"
                           + truncate_tokens(story_so_far[-self.continuity_chars:], caps["continuity"], "tail")
                           + "\n\"\"\""))

        blocks.append((2, "chapter_frame", f"CHAPTER {ch_id}: {chapter.get('title', '')} ({chapter.get('act', '')})\n"
                       f"Dramatic question: {chapter.get('dramatic_question', '')}\nEnds with: {chapter.get('ends_with', '')}\n"
                       + (f"Plan amendments: {chapter.get('amendments')}\n" if chapter.get("amendments") else "")))

        s = self.load_summaries()
        covered = s["scenes"].get(ch_id, {})
        if covered:
            blocks.append((2, "covered", "ALREADY COVERED IN THIS CHAPTER (do not repeat these events or images):\n"
                           + "\n".join(f"- scene {k}: {v}" for k, v in sorted(covered.items(), key=lambda x: int(x[0]))
                                       if int(k) < int(scene.get("scene_number", 0)))))

        facts = canon.select_for_context(ents, caps["canon"], ch_id)
        if facts:
            blocks.append((3, "canon", "ESTABLISHED CANON (must not be contradicted):\n" + "\n".join(
                f"- [{f['importance']}{', planned' if f['status'] == 'planned' else ''}] {f['claim']}" for f in facts)))
        cont = canon.continuity_for(present)
        if cont:
            blocks.append((3, "continuity_state", "WHERE THINGS STAND (continuity ledger):\n" + "\n".join(
                f"- {canon.entities.get(e, {}).get('name', e)}: at {c.get('location') or '?'}; holding {c.get('holding') or '-'}; "
                f"knows {c.get('knows')[-6:] if c.get('knows') else '-'}; flags {c.get('flags') or '-'}" for e, c in cont.items())))
        threads = [t for t in canon.open_threads() if t.get("planted") or t.get("setup_chapter") == ch_id]
        due = [t for t in threads if t.get("payoff_by") and t["payoff_by"] <= ch_id]
        if threads:
            blocks.append((4, "threads", "OPEN THREADS (setups awaiting payoff):\n" + "\n".join(
                f"- {'[DUE NOW] ' if t in due else ''}{t['description']}" for t in threads)
                + ("\nPlant in this chapter if natural: " + "; ".join(chapter.get("setups", [])) if chapter.get("setups") else "")
                + ("\nPay off in this chapter: " + "; ".join(chapter.get("payoffs", [])) if chapter.get("payoffs") else "")))

        voices = self.style().voices_block(
            list(dict.fromkeys([scene.get("pov", "")] + list(scene.get("characters_present", [])))),
            caps.get("voices", 1500))
        if voices:
            blocks.append((2, "voices", voices))

        active = [chars[e] for e in present if e in chars] or list(chars.values())[:3]
        blocks.append((5, "characters", "CHARACTERS IN SCENE:\n" + "\n".join(
            f"{c['name']} ({c['role']}, {c.get('age') or '?'}) {c.get('physical', '')}\n  Personality: {c.get('personality', '')}\n"
            f"  Wants: {c.get('motivation', '')}\n  Arc: {c.get('arc', '')}\n  Voice: {c.get('voice', '')}"
            + (f"\n  Canon notes: {c['canon_notes'][-3:]}" if c.get("canon_notes") else "") for c in active)))

        world = self.load_world()
        loc = next((l for l in world["key_locations"] if l["id"] == loc_id or
                    l["name"].lower() in scene.get("location", "").lower()), None)
        wt = (f"LOCATION {loc['name']}: {loc.get('description', '')}\n" if loc else "")
        if world.get("sensory_palette"):
            wt += "SENSORY PALETTE: " + "; ".join(f"{k}: {v}" for k, v in world["sensory_palette"].items())
        if wt:
            blocks.append((6, "world", wt))
        if scene.get("sensory_details"):
            blocks.append((6, "scene_sensory", "SENSORY NOTES: " + scene["sensory_details"]))

        prior = [c["id"] for c in self.load_outline() if c["id"] < ch_id]
        st = ""
        if s.get("book", {}).get("text"):
            st += f"BOOK SO FAR (through {s['book'].get('through')}):\n{s['book']['text']}\n\n"
        for pid in prior[-2:]:
            if pid in s["chapters"]:
                st += f"{pid}: {s['chapters'][pid]}\n"
        if st:
            blocks.append((7, "summaries", "WHAT HAPPENED BEFORE:\n" + st))

        q = f"{scene.get('location', '')} {' '.join(scene.get('characters_present', []))} {beat_text}"
        hits = self.embeddings.retrieve(q, k=3, entities=ents, before_chapter=ch_id)
        if hits:
            blocks.append((8, "passages", "VERBATIM PASSAGES FROM EARLIER CHAPTERS (match names, objects, voice):\n"
                           + "\n---\n".join(f"[{h['chapter']} s{h['scene']}] {h['text']}" for h in hits)))

        return self._assemble(blocks)

    def _assemble(self, blocks) -> str:
        caps = self.block_caps
        sized = [(p, n, truncate_tokens(t, caps.get(n, 1500), "tail" if n == "continuity" else "head"))
                 for p, n, t in blocks]
        total = sum(count_tokens(t) for _, _, t in sized)
        budget = self.total_context_tokens
        # phase 1: squeeze blocks to a floor, lowest priority first; phase 2: drop them entirely.
        # Instructions are never touched, so a huge tail can no longer evict the cast.
        for floor in (300, 0):
            for i in sorted(range(len(sized)), key=lambda i: -sized[i][0]):
                if total <= budget:
                    break
                p, n, t = sized[i]
                if p == 0 or not t:
                    continue
                cur = count_tokens(t)
                target = max(floor, cur - (total - budget))
                if target >= cur:
                    continue
                nt = truncate_tokens(t, target, "tail" if n == "continuity" else "head") if target > 50 else ""
                total -= cur - (count_tokens(nt) if nt else 0)
                sized[i] = (p, n, nt)
        order = {"instructions": 0, "craft": 0.5, "chapter_frame": 1, "voices": 1.5, "characters": 2, "world": 3, "scene_sensory": 4,
                 "summaries": 5, "passages": 6, "canon": 7, "continuity_state": 8, "threads": 9,
                 "covered": 10, "continuity": 11}
        # volatile text that the model should continue from goes last
        return "\n\n".join(t for _, n, t in sorted(sized, key=lambda x: order.get(x[1], 5)) if t)

    def plan_parts(self, scene: dict) -> list[list[str]]:
        """Split a scene's beat sheet into as few calls as the word cap allows."""
        beats = scene.get("beats") or [b for b in scene.get("action", "").replace(". ", ".\n").split("\n") if b.strip()]
        beats = beats or ["Write the scene."]
        from bookforge.core.length import beat_words
        n = max(1, math.ceil(sum(beat_words(b) for b in beats) / self.scene_max_words))
        n = min(n, len(beats))
        per = math.ceil(len(beats) / n)
        return [beats[i:i + per] for i in range(0, len(beats), per)]
