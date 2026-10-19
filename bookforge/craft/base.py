"""Shared craft types: events, issues, positions, the module registry."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bookforge.config import load_config

# Kinds that carry plot weight. The per-scene cap counts only these.
LOAD_BEARING = {"plant", "payoff", "clue", "reveal", "herring", "herring_cleared", "arc_stage", "convergence",
                "magic_use", "trope_turn", "fulfillment", "lesson_applied", "explain", "promise_payoff",
                "thread_resolved", "trust_shift"}
# Setup-like kinds go to early scenes of a chapter, payoff-like kinds to late scenes.
SETUP_KINDS = {"plant", "clue", "herring", "hint", "rule_shown", "lesson_given", "trope_setup", "promise_made",
               "false_reading", "appear", "thread_open"}
REGISTRY: dict[str, "CraftModule"] = {}

SEVERITIES = ("critical", "major", "minor")


def cfg() -> dict:
    return {**DEFAULTS, **(load_config().get("craft") or {})}


DEFAULTS = {
    "enabled": True,
    "framework": "save_the_cat",          # save_the_cat | three_act | seven_point | story_grid | hero_journey
    "scene_sequel": True,                  # Swain scene/sequel shapes on every scene card
    "max_key_items_per_scene": 3,          # load-bearing craft items per scene (avoid mechanical prose)
    "min_setup_gap_chapters": 2,           # important setups land >= this many chapters before payoff
    "beat_tolerance_pct": 5,               # framework beats realized within +/- this % of book
    "tension_min_correlation": 0.3,
    "rolling_refine": True,                # DOME-style: refine next chapter's cards after each chapter
    "law1_min_gap_chapters": 1,            # magic rules shown >= N chapters before solving a conflict
    "no_new_magic_after_pct": 60,
    "max_new_concepts_per_scene": 3,       # iceberg pacing
    "subversions_per_act": 1,
    "rerank": False,                       # optional: 2 candidate endings for high-tension scenes (costs more)
    "rerank_min_tension": 7,
    "modules": None,                       # None = all registered; or a list of module names
}


def module_names():
    want = cfg().get("modules")
    return [n for n in REGISTRY if want is None or n in want]


def register(cls):
    REGISTRY[cls.name] = cls()
    return cls


def issue(check, severity, description, fix="", chapter="", scene=None, ref="", action=None):
    return {"check": check, "severity": severity, "chapter": chapter, "scene": scene,
            "description": description, "fix": fix, "ref": ref, "action": action}


def norm_text(s: str) -> str:
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


@dataclass
class Book:
    """Chapter order and positions. A position is (chapter_index, scene_number)."""
    chapters: list[dict]
    index: dict = field(default_factory=dict)

    def __post_init__(self):
        self.index = {c["id"]: i for i, c in enumerate(self.chapters)}

    @property
    def n(self):
        return len(self.chapters)

    def ch_at(self, pct: float) -> str:
        if not self.chapters:
            return ""
        i = min(self.n - 1, max(0, round(pct / 100 * (self.n - 1))))
        return self.chapters[i]["id"]

    def ci(self, ch) -> int:
        if isinstance(ch, int):
            return ch
        return self.index.get(ch, 10 ** 6)

    def pct(self, ch) -> float:
        return 100.0 * self.ci(ch) / max(1, self.n - 1)

    def pos(self, ev) -> tuple:
        return (self.ci(ev.get("ch")), ev.get("scene") or 0)

    def act_of(self, ch) -> int:
        """1, 2 or 3 by quarter split (25/50/25)."""
        p = self.pct(ch)
        return 1 if p < 25 else (2 if p < 75 else 3)

    def scenes(self):
        for c in self.chapters:
            for s in c.get("scenes") or [{"scene_number": 1}]:
                yield c["id"], int(s.get("scene_number", 1))

    def clamp(self, i: int) -> str:
        return self.chapters[min(self.n - 1, max(0, i))]["id"] if self.chapters else ""


class Events:
    """Query helper over a list of events (planned or realized)."""

    def __init__(self, events: list[dict], book: Book):
        self.ev = sorted(events, key=book.pos)
        self.book = book

    def of(self, kind, ref=None):
        return [e for e in self.ev if e["kind"] == kind and (ref is None or e.get("ref") == ref)]

    def first(self, kind, ref=None):
        x = self.of(kind, ref)
        return x[0] if x else None

    def before(self, a, b) -> bool:
        return self.book.pos(a) < self.book.pos(b)


class CraftModule:
    name = ""
    tier = "P0"
    plan_role = "craft_plan"

    # ---- planning
    def plan_prompt(self, P) -> str | None:
        return None

    def normalize(self, raw, P) -> dict:
        return raw if isinstance(raw, dict) else {}

    def planned_events(self, data, P) -> list[dict]:
        return []

    def autofix_plan(self, data, P) -> list[str]:
        return []

    # ---- writing
    def brief(self, data, card_events, ch, scene, L, P) -> list[str]:
        return []

    def extract_spec(self, data) -> str:
        """Extra instructions for the craft extractor (which ids exist, which kinds to report)."""
        return ""

    # ---- verifying
    def observe(self, data, scene_events, src, L, P) -> list[dict]:
        return []

    def check(self, data, events: Events, L, P, mode: str) -> list[dict]:
        return []
