"""Persistent craft ledger: state/craft/ledger.json (versioned like canon)."""
from __future__ import annotations

import copy
import datetime
from dataclasses import dataclass, field
from pathlib import Path

from bookforge.core.io import atomic_write_json, read_json, stable_hash
from bookforge.craft.base import Book

EMPTY = {"version": 0, "framework": "", "modules": {}, "planned": [], "realized": [], "scene_cards": {},
         "plan_issues": [], "amendments": [], "refined": {}, "plan_key": ""}


@dataclass
class PlanContext:
    book: Book
    premise: str = ""
    genre: str = ""
    chars: dict = field(default_factory=dict)
    world: dict = field(default_factory=dict)
    framework: str = "save_the_cat"

    @classmethod
    def from_ctx(cls, ctx, framework=None):
        from bookforge.craft.base import cfg
        bs = ctx.metadata.get("brainstorm", {}) or {}
        return cls(book=Book(ctx.load_outline()), premise=ctx.metadata.get("prompt", ""),
                   genre=bs.get("genre", "") if isinstance(bs, dict) else "",
                   chars=ctx.load_characters(), world=ctx.load_world(),
                   framework=framework or ctx.metadata.get("craft_framework") or cfg()["framework"])

    def brief_outline(self):
        return [{"id": c["id"], "act": c.get("act", ""), "title": c.get("title", ""),
                 "summary": (c.get("summary") or "")[:160], "scenes": len(c.get("scenes") or [])}
                for c in self.book.chapters]


class CraftLedger:
    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir) / "craft"
        self.path = self.dir / "ledger.json"
        self.data = {**copy.deepcopy(EMPTY), **read_json(self.path, {})}
        for k, v in EMPTY.items():
            self.data.setdefault(k, type(v)() if isinstance(v, (dict, list)) else v)

    # ------------------------------------------------------------------ basics
    def save(self, reason=""):
        self.data["version"] += 1
        self.data["saved"] = datetime.datetime.now().isoformat(timespec="seconds")
        self.data["reason"] = reason
        atomic_write_json(self.path, self.data)
        atomic_write_json(self.dir / "history" / f"v{self.data['version']:05d}.json", self.data)
        return self.data["version"]

    @property
    def has_plan(self) -> bool:
        return bool(self.data["modules"])

    def module(self, name) -> dict:
        return self.data["modules"].get(name) or {}

    def card(self, ch, scene) -> dict:
        return self.data["scene_cards"].get(f"{ch}:{scene}", {})

    def planned_for(self, ch, scene):
        return [e for e in self.data["planned"] if e.get("ch") == ch and e.get("scene") == scene]

    # ------------------------------------------------------------------ realized events
    def add_realized(self, ev: dict):
        ev = {**ev}
        ev["id"] = "x_" + stable_hash([ev.get("kind"), ev.get("ref"), ev.get("ch"), ev.get("scene"),
                                       ev.get("quote", ""), ev.get("data")])[:10]
        if not any(e["id"] == ev["id"] for e in self.data["realized"]):
            self.data["realized"].append(ev)
        return ev

    def retract(self, ch, scene=None) -> int:
        before = len(self.data["realized"])
        self.data["realized"] = [e for e in self.data["realized"]
                                 if not (e.get("ch") == ch and (scene is None or e.get("scene") == scene))]
        if scene is None:
            self.data["refined"].pop(ch, None)
        return before - len(self.data["realized"])

    def realized_before(self, book: Book, pos) -> list[dict]:
        return [e for e in self.data["realized"] if book.pos(e) < pos]
