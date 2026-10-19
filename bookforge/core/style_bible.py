"""Project style bible + per-character voice sheets.

Files (looked up in the project dir, else in config ``style.bible_dir``):
  style_bible.md        Markdown. Recognized sections (## headings, case-insensitive):
                        "Example passages" (highest priority; drafter imitates these),
                        "Narrator voice", "Do", "Don't", "Allowed phrases" (detector allowlist,
                        one per bullet), anything else = general guidance.
                        Example passages are split on '### ' sub-headings; each becomes a sample
                        with an id (slug of the sub-heading) that scene briefs can reference.
  voices/<id>.yaml|.yml  Voice sheet (schema below). voices/<id>.md is accepted as free text.

Voice sheet schema (all optional except name):
  name, aliases: [..], role, dialect, speech_patterns: [..], vocabulary: [..],
  never_says: [..], wants, fears, sample_lines: [..], notes
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from bookforge.config import load_config


def _count(t: str) -> int:
    from bookforge.core.context import count_tokens
    return count_tokens(t)


def _trunc(t: str, n: int) -> str:
    from bookforge.core.context import truncate_tokens
    return truncate_tokens(t, n)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _is_todo(t: str) -> bool:
    body = re.sub(r"<!--.*?-->", "", t, flags=re.S).strip()
    return not body or bool(re.fullmatch(r"(?is)\W*todo\b.*", body)) and len(body) < 400


class StyleBible:
    def __init__(self, root: Path | None):
        self.root = root
        self.sections: dict[str, str] = {}
        self.samples: list[dict] = []
        self.voices: dict[str, dict] = {}
        if not root:
            return
        p = root / "style_bible.md"
        if p.exists():
            self._parse(p.read_text())
        vd = root / "voices"
        if vd.is_dir():
            for f in sorted(vd.iterdir()):
                if f.suffix in (".yaml", ".yml"):
                    d = yaml.safe_load(f.read_text()) or {}
                    if isinstance(d, dict):
                        d.setdefault("name", f.stem)
                        d["_id"] = f.stem
                        self.voices[f.stem] = d
                elif f.suffix == ".md":
                    self.voices[f.stem] = {"name": f.stem, "_id": f.stem, "_text": f.read_text()}

    def _parse(self, md: str):
        md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
        parts = re.split(r"(?m)^##\s+(?!#)(.+)$", md)
        for i in range(1, len(parts), 2):
            self.sections[parts[i].strip().lower()] = parts[i + 1].strip()
        ex = next((v for k, v in self.sections.items() if k.startswith("example passage")), "")
        for j, chunk in enumerate(re.split(r"(?m)^###\s+", ex)):
            if not chunk.strip():
                continue
            title, _, body = chunk.partition("\n")
            if j == 0 and not ex.lstrip().startswith("###"):
                title, body = "sample", chunk
            body = body.strip()
            if body and not _is_todo(body):
                self.samples.append({"id": _slug(title) or f"s{j}", "title": title.strip(), "text": body})

    # ---------------------------------------------------------------- accessors
    @property
    def empty(self) -> bool:
        return not (self.sections or self.voices)

    def allowlist(self) -> list[str]:
        sec = next((v for k, v in self.sections.items() if k.startswith("allowed")), "")
        return [re.sub(r"^[-*]\s*", "", l).strip().strip('"') for l in sec.splitlines()
                if l.strip().startswith(("-", "*"))]

    def guidance(self) -> str:
        skip = ("example passage", "allowed")
        return "\n\n".join(f"{k.upper()}:\n{v}" for k, v in self.sections.items()
                           if not k.startswith(skip) and v and not _is_todo(v))

    def sample_ids(self) -> list[str]:
        return [s["id"] for s in self.samples]

    def block(self, max_tokens: int, prefer: list[str] | None = None, for_drafter: bool = True) -> str:
        """Example passages FIRST (preferred ids first), then guidance, within max_tokens."""
        if self.empty or max_tokens <= 0:
            return ""
        prefer = prefer or []
        samples = sorted(self.samples, key=lambda s: (s["id"] not in prefer, prefer.index(s["id"])
                                                      if s["id"] in prefer else 0))
        out, used = [], 0
        head = ("STYLE BIBLE. EXAMPLE PASSAGES (imitate their voice, rhythm and diction; "
                "do not copy their content. Be at least as clear as they are):" if for_drafter else
                "STYLE BIBLE. EXAMPLE PASSAGES (the target voice; plan scenes this voice can carry):")
        if samples:
            out.append(head)
            used = _count(head)
            sample_budget = int(max_tokens * 0.6)
            for s in samples:
                t = f"[{s['id']}] {s['title']}\n{s['text']}"
                n = _count(t)
                if used + n > sample_budget:
                    if used < sample_budget - 80:
                        out.append(_trunc(t, sample_budget - used))
                        used = sample_budget
                    break
                out.append(t)
                used += n
        g = self.guidance()
        if g:
            out.append(_trunc("STYLE GUIDANCE:\n" + g, max(0, max_tokens - used)))
        return _trunc("\n\n".join(x for x in out if x), max_tokens)

    def voice_for(self, name: str) -> dict | None:
        n = (name or "").lower().strip()
        if not n:
            return None
        for v in self.voices.values():
            forms = [v.get("name", ""), v["_id"], *(v.get("aliases") or [])]
            forms = [f.lower() for f in forms if f]
            toks = set(re.findall(r"[a-z']+", n))
            if n in forms or any(" " not in f and f in toks for f in forms):
                return v
        return None

    @staticmethod
    def render_voice(v: dict, compact: bool = False) -> str:
        if "_text" in v:
            return f"{v['name']}:\n{v['_text'].strip()}"
        L = [f"{v.get('name')}" + (f" ({v['role']})" if v.get("role") else "")]
        for k, lab in (("conversational_wants", "In conversation wants"), ("avoids", "Steers away from"),
                       ("hides", "Hides (doesn't say outright, but the reader should be able to tell)"), ("dialect", "Dialect"),
                       ("speech_patterns", "Habits (use sparingly, never every line)"), ("vocabulary", "Uses words like"),
                       ("never_says", "NEVER says"), ("wants", "Wants"), ("fears", "Fears"),
                       ("sample_lines", "Range of voice (NOT templates; never reuse or paraphrase these lines)"),
                       ("notes", "Notes")):
            val = v.get(k)
            if not val or (isinstance(val, str) and _is_todo(val)):
                continue
            if isinstance(val, list):
                val = [x for x in val if x and not _is_todo(str(x))]
                if not val:
                    continue
                if compact and k == "sample_lines":
                    val = val[:2]
                val = " | ".join(str(x) for x in val)
            L.append(f"  {lab}: {val}")
        return "\n".join(L)

    def voices_block(self, names: list[str], max_tokens: int, compact: bool = False) -> str:
        seen, out = set(), []
        for n in names:
            v = self.voice_for(n)
            if v and v["_id"] not in seen:
                seen.add(v["_id"])
                out.append(self.render_voice(v, compact))
        if not out:
            return ""
        return _trunc("VOICE SHEETS (what each speaker wants, avoids and hides drives the talk; the sample lines show range, not templates. Never reuse them; vary exchange shapes):\n"
                      + "\n".join(out), max_tokens)

    def all_voices_block(self, max_tokens: int) -> str:
        return self.voices_block([v["name"] for v in self.voices.values()], max_tokens, compact=True)

    def never_says(self) -> dict[str, list[str]]:
        return {v.get("name", k): [w for w in (v.get("never_says") or []) if w and not _is_todo(str(w))]
                for k, v in self.voices.items()}


def load(project_dir: Path | None, extra_dir: str | None = None) -> StyleBible:
    cfg = load_config().get("style", {}) or {}
    cand = []
    if project_dir:
        cand.append(Path(project_dir))
    if extra_dir:
        cand.append(Path(extra_dir).expanduser())
    if cfg.get("bible_dir"):
        cand.append(Path(cfg["bible_dir"]).expanduser())
    for c in cand:
        if (c / "style_bible.md").exists() or (c / "voices").is_dir():
            return StyleBible(c)
    return StyleBible(None)
