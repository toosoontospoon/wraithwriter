"""Canonical schemas. Every LLM JSON output is validated against one of these."""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "x"


def chapter_id(n: int) -> str:
    return f"ch{int(n):02d}"


def _as_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(_as_str(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_as_str(x)}" for k, x in v.items())
    return str(v)


def _as_str_list(v: Any) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [f"{k}: {_as_str(x)}" for k, x in v.items()]
    return [_as_str(x) for x in v]


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ---------------------------------------------------------------- characters
class CurrentState(_Loose):
    location: str = ""
    holding: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    emotions: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, d):
        if not isinstance(d, dict):
            return {}
        d = dict(d)
        for k in ("holding", "knowledge", "emotions", "flags"):
            if k in d:
                d[k] = _as_str_list(d[k])
        if "location" in d:
            d["location"] = _as_str(d["location"])
        return d


class Character(_Loose):
    id: str = ""
    name: str
    aliases: list[str] = Field(default_factory=list)
    role: str = "supporting"
    age: Optional[str] = None
    gender: str = ""
    physical: str = ""
    personality: str = ""
    backstory: str = ""
    motivation: str = ""
    secret: str = ""
    arc: str = ""
    voice: str = ""
    relationships: dict[str, str] = Field(default_factory=dict)
    current_state: CurrentState = Field(default_factory=CurrentState)
    chapters_present: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, d):
        if not isinstance(d, dict):
            raise ValueError("character must be an object")
        d = dict(d)
        demo = d.pop("demographics", None) or {}
        if isinstance(demo, dict):
            d.setdefault("age", demo.get("age"))
            d.setdefault("gender", demo.get("gender", ""))
            if demo.get("occupation"):
                d.setdefault("occupation", demo["occupation"])
        if "goals_motivations" in d and not d.get("motivation"):
            d["motivation"] = d.pop("goals_motivations")
        if "voice_sample" in d and not d.get("voice"):
            d["voice"] = d.pop("voice_sample")
        if isinstance(d.get("physical"), dict):
            d["physical"] = _as_str(d["physical"])
        for k in ("personality", "backstory", "motivation", "arc", "voice", "secret", "gender"):
            if k in d:
                d[k] = _as_str(d[k])
        if d.get("age") is not None:
            d["age"] = str(d["age"])
        rel = d.get("relationships")
        if isinstance(rel, str):
            d["relationships"] = {"_notes": rel} if rel else {}
        elif isinstance(rel, list):
            d["relationships"] = {f"_{i}": _as_str(x) for i, x in enumerate(rel)}
        elif isinstance(rel, dict):
            d["relationships"] = {str(k): _as_str(v) for k, v in rel.items()}
        if "aliases" in d:
            d["aliases"] = _as_str_list(d["aliases"])
        if "chapters_present" in d:
            d["chapters_present"] = _as_str_list(d["chapters_present"])
        return d


def normalize_characters(data: Any) -> dict[str, dict]:
    """Accept list, dict-by-id, or {"characters": ...}; return dict keyed by stable id."""
    if isinstance(data, dict) and "characters" in data and len(data) <= 2:
        data = data["characters"]
    items: list[tuple[str | None, dict]] = []
    if isinstance(data, list):
        items = [(None, c) for c in data if isinstance(c, dict)]
    elif isinstance(data, dict):
        if "name" in data and isinstance(data.get("name"), str):
            items = [(None, data)]
        else:
            items = [(k, v) for k, v in data.items() if isinstance(v, dict)]
    out: dict[str, dict] = {}
    for key, raw in items:
        raw = dict(raw)
        raw.setdefault("name", raw.get("id") or key or "Unknown")
        c = Character.model_validate(raw)
        cid = slugify(c.id or key or c.name)
        c.id = cid
        while cid in out:
            cid += "_2"
            c.id = cid
        out[cid] = c.model_dump()
    return out


# ---------------------------------------------------------------- world
class Location(_Loose):
    id: str = ""
    name: str
    description: str = ""
    significance: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, d):
        if isinstance(d, str):
            return {"name": d}
        d = dict(d)
        if "sensory_description" in d and not d.get("description"):
            d["description"] = d.pop("sensory_description")
        if "importance" in d and not d.get("significance"):
            d["significance"] = d.pop("importance")
        for k in ("description", "significance"):
            if k in d:
                d[k] = _as_str(d[k])
        return d


class World(_Loose):
    genre_tone: str = ""
    core_rules: list[str] = Field(default_factory=list)
    key_locations: list[Location] = Field(default_factory=list)
    history_timeline: list[str] = Field(default_factory=list)
    culture_society: dict[str, str] = Field(default_factory=dict)
    economy_resources: str = ""
    sensory_palette: dict[str, str] = Field(default_factory=dict)
    unique_elements: list[str] = Field(default_factory=list)
    consistency_flags: list[str] = Field(default_factory=list)
    lore: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, d):
        if not isinstance(d, dict):
            return {}
        d = dict(d)
        if "world" in d and isinstance(d["world"], dict) and len(d) == 1:
            d = dict(d["world"])
        kl = d.get("key_locations")
        if isinstance(kl, dict):
            d["key_locations"] = [{"name": k, "description": _as_str(v)} for k, v in kl.items()]
        if "supernatural_rules" in d:
            d["core_rules"] = _as_str_list(d.get("core_rules")) + _as_str_list(d.pop("supernatural_rules"))
        for k in ("core_rules", "history_timeline", "unique_elements", "consistency_flags", "lore"):
            if k in d:
                d[k] = _as_str_list(d[k])
        for k in ("culture_society", "sensory_palette"):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = {"notes": v}
            elif isinstance(v, dict):
                d[k] = {str(a): _as_str(b) for a, b in v.items()}
        for k in ("genre_tone", "economy_resources"):
            if k in d:
                d[k] = _as_str(d[k])
        return d

    @model_validator(mode="after")
    def _ids(self):
        for loc in self.key_locations:
            loc.id = loc.id or slugify(loc.name)
        return self


def normalize_world(data: Any) -> dict:
    return World.model_validate(data or {}).model_dump()


# ---------------------------------------------------------------- outline
class Scene(_Loose):
    scene_number: int = 1
    pov: str = ""
    person: Literal["first", "second", "third"] = "third"
    tense: Literal["past", "present"] = "past"
    location: str = ""
    characters_present: list[str] = Field(default_factory=list)
    action: str = ""
    beats: list[str] = Field(default_factory=list)
    emotional_arc: str = ""
    sensory_details: str = ""
    purpose: str = ""
    target_words: int = 0            # REPORT-ONLY estimate (sum of beat sizes); never a writing target
    prose_modes: list[str] = Field(default_factory=list)
    character_moments: list[str] = Field(default_factory=list)
    world_reveals: list[str] = Field(default_factory=list)
    # rich scene brief (planner -> drafter)
    wants: list[str] = Field(default_factory=list)          # "Name: wants X"
    obstacle: str = ""
    emotional_turn: str = ""                                 # "start value -> end value"
    sensory_anchor: str = ""
    closing_image: str = ""
    voice_notes: list[str] = Field(default_factory=list)    # per present character
    tone_target: str = ""
    style_samples: list[str] = Field(default_factory=list)  # style-bible example ids to imitate
    thread_obligations: list[dict] = Field(default_factory=list)  # inherited from the chapter (threads.py)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, d):
        d = dict(d)
        for k in ("characters_present", "beats", "prose_modes", "character_moments", "world_reveals",
                  "wants", "voice_notes", "style_samples"):
            if k in d:
                d[k] = _as_str_list(d[k])
        for k in ("action", "emotional_arc", "sensory_details", "purpose", "pov", "location",
                  "obstacle", "emotional_turn", "sensory_anchor", "closing_image", "tone_target"):
            if k in d:
                d[k] = _as_str(d[k])
        if str(d.get("person", "third")).lower() not in ("first", "second", "third"):
            d["person"] = "third"
        if str(d.get("tense", "past")).lower() not in ("past", "present"):
            d["tense"] = "past"
        try:
            d["target_words"] = int(d.get("target_words") or 0)
        except (TypeError, ValueError):
            d["target_words"] = 0
        return d


class Chapter(_Loose):
    id: str
    slug: str = ""
    act: str = ""
    title: str = ""
    summary: str = ""
    dramatic_question: str = ""
    target_words: int = 0            # REPORT-ONLY estimate (sum of scenes); never a writing target
    key_events: list[str] = Field(default_factory=list)
    ends_with: str = ""
    setups: list[str] = Field(default_factory=list)
    payoffs: list[str] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    amendments: list[str] = Field(default_factory=list)
    # thread ledger (core/threads.py)
    thread_obligations: list[dict] = Field(default_factory=list)  # {thread, action, how, scene}
    why_matters: str = ""
    opening_hook: str = ""
    grounding: str = ""
    opening_intent: str = ""         # what the opening should do/convey (guidance, not wording)
    first_line: str = ""             # legacy; read as opening_intent
    plan_source: str = ""            # "planned" | "realized" (resynced from the text after revision)
    structure: dict = Field(default_factory=dict)  # story-structure block (core/structure.py)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, d):
        d = dict(d)
        for k in ("key_events", "setups", "payoffs", "amendments"):
            if k in d:
                d[k] = _as_str_list(d[k])
        try:
            d["target_words"] = int(d.get("target_words") or 0)
        except (TypeError, ValueError):
            d["target_words"] = 0
        d["id"] = str(d.get("id") or "")
        for k in ("slug", "act", "title", "summary", "dramatic_question", "ends_with"):
            if k in d:
                d[k] = _as_str(d[k])
        if isinstance(d.get("act"), str) and d["act"].isdigit():
            d["act"] = f"Act {d['act']}"
        if d.get("structure"):
            from bookforge.core.structure import Structure
            d["structure"] = Structure.model_validate(d["structure"]).model_dump(exclude_defaults=True)
        return d


def normalize_outline(chapters: list) -> list[dict]:
    """Force ids to ch01..chNN in order; keep the LLM's id as `slug`."""
    out = []
    for i, ch in enumerate(chapters, 1):
        if not isinstance(ch, dict):
            continue
        ch = dict(ch)
        orig = str(ch.get("id") or ch.get("title") or "")
        ch["id"] = chapter_id(len(out) + 1)
        ch.setdefault("slug", slugify(orig) if orig and orig != ch["id"] else "")
        c = Chapter.model_validate(ch)
        for j, s in enumerate(c.scenes, 1):
            s.scene_number = j
        out.append(c.model_dump())
    return out


# ---------------------------------------------------------------- canon
FactType = Literal["plot_point", "motive", "secret", "relationship", "object", "location",
                   "world_rule", "timeline", "scene_detail", "character_trait", "state_change",
                   "setup", "payoff"]
Importance = Literal["critical", "major", "local"]


class ExtractedFact(_Loose):
    type: FactType = "scene_detail"
    entities: list[str] = Field(default_factory=list)
    claim: str
    quote: str = ""
    confidence: float = 0.7
    importance: Importance = "local"

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.5

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v):
        v = slugify(v or "scene_detail")
        allowed = FactType.__args__  # type: ignore[attr-defined]
        return v if v in allowed else "scene_detail"

    @field_validator("importance", mode="before")
    @classmethod
    def _imp(cls, v):
        v = str(v or "local").lower()
        return v if v in ("critical", "major", "local") else "local"

    @field_validator("entities", mode="before")
    @classmethod
    def _ents(cls, v):
        return [slugify(x) for x in _as_str_list(v)]


class NewEntity(_Loose):
    id: str
    name: str
    type: Literal["character", "location", "object", "organization", "concept"] = "character"
    description: str = ""
    importance: Importance = "local"

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, v):
        return slugify(v)

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v):
        v = str(v or "character").lower()
        return v if v in ("character", "location", "object", "organization", "concept") else "concept"

    @field_validator("importance", mode="before")
    @classmethod
    def _imp(cls, v):
        v = str(v or "local").lower()
        return v if v in ("critical", "major", "local") else "local"


class StateChange(_Loose):
    entity: str
    location: Optional[str] = None
    gained: list[str] = Field(default_factory=list)
    lost: list[str] = Field(default_factory=list)
    learned: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)

    @field_validator("entity", mode="before")
    @classmethod
    def _e(cls, v):
        return slugify(v)


class ThreadOpen(_Loose):
    id: str = ""
    description: str
    importance: Importance = "major"
    payoff_by: Optional[str] = None


class AliasLink(_Loose):
    name: str
    entity: str

    @field_validator("entity", mode="before")
    @classmethod
    def _e(cls, v):
        return slugify(v)


class ExtractionResult(_Loose):
    aliases: list[AliasLink] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)
    new_entities: list[NewEntity] = Field(default_factory=list)
    state_changes: list[StateChange] = Field(default_factory=list)
    threads_opened: list[ThreadOpen] = Field(default_factory=list)
    threads_resolved: list[str] = Field(default_factory=list)


class PlanUpdate(_Loose):
    chapter: str
    action: Literal["add_beat", "replace_text", "add_setup", "add_payoff"] = "add_beat"
    old: str = ""
    new: str = ""


class Verdict(_Loose):
    fact_index: int
    verdict: Literal["consistent", "new_compatible", "conflict"]
    conflicts_with: list[str] = Field(default_factory=list)
    explanation: str = ""
    fix: str = ""
    new_is_better: bool = False
    confidence: float = 0.7
    plan_updates: list[PlanUpdate] = Field(default_factory=list)


class DeconflictResult(_Loose):
    verdicts: list[Verdict] = Field(default_factory=list)


class Critique(_Loose):
    clarity_score: float = 0
    style_score: float = 0
    engagement_score: float = 0
    pacing_score: float = 0
    arc_integration_score: float = 0
    mode_balance_score: float = 0
    world_integration_score: float = 0
    narration_balance_score: float = 0
    consistency_score: float = 0
    economy_score: float = 0         # padding/filler check (10 = no filler)
    consistency_issues: list[str] = Field(default_factory=list)
    weak_spots: list[dict] = Field(default_factory=list)
    suggested_fixes: list[str] = Field(default_factory=list)
    strongest_moments: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, d):
        d = dict(d)
        for k, v in list(d.items()):
            if k.endswith("_score"):
                try:
                    d[k] = float(v)
                except (TypeError, ValueError):
                    d[k] = 0
        for k in ("consistency_issues", "suggested_fixes", "strongest_moments"):
            if k in d:
                d[k] = _as_str_list(d[k])
        ws = d.get("weak_spots", [])
        d["weak_spots"] = [w if isinstance(w, dict) else {"problem": str(w)} for w in (ws or [])]
        return d

    def total(self) -> float:
        return sum(v for k, v in self.model_dump().items() if k.endswith("_score"))


class AuditIssue(_Loose):
    severity: Literal["critical", "major", "minor"] = "major"
    chapter: str = ""
    description: str
    fix: str = ""


class AuditResult(_Loose):
    issues: list[AuditIssue] = Field(default_factory=list)
    passed: bool = True
