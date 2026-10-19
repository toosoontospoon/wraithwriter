"""Versioned canon ledger: entities, facts with provenance and status, continuity
state, Chekhov threads, plan amendments, revision tasks and the human review queue.

Stored as ``state/canon.json``; every save snapshots the previous version into
``state/canon_history/v<N>.json`` so any change can be audited or rolled back.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Iterable

from bookforge.core.io import atomic_write_json, read_json, stable_hash
from bookforge.core.schema import slugify

STATUSES = ("planned", "established", "contradicted", "retired")
IMPORTANCE_RANK = {"critical": 0, "major": 1, "local": 2}


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _norm_claim(c: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", c.lower()).strip()


def fact_id(claim: str, entities: Iterable[str]) -> str:
    return "f_" + stable_hash(_norm_claim(claim) + "|" + ",".join(sorted(entities)))[:10]


def _src_key(src: dict) -> str:
    return f"{src.get('chapter', '')}:{src.get('scene', '')}"


class Canon:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "canon.json"
        self.hist = self.state_dir / "canon_history"
        d = read_json(self.path, None) or {}
        self.data = {
            "version": d.get("version", 0),
            "entities": d.get("entities", {}),
            "facts": d.get("facts", {}),
            "continuity": d.get("continuity", {}),
            "threads": d.get("threads", {}),
            "amendments": d.get("amendments", []),
            "revision_tasks": d.get("revision_tasks", []),
            "review_queue": d.get("review_queue", []),
        }

    # -------------------------------------------------------------- persistence
    def save(self, reason: str = "") -> int:
        if self.path.exists():
            self.hist.mkdir(parents=True, exist_ok=True)
            prev = read_json(self.path, {})
            atomic_write_json(self.hist / f"v{prev.get('version', 0):04d}.json", prev)
        self.data["version"] += 1
        self.data["last_change"] = {"at": _now(), "reason": reason}
        atomic_write_json(self.path, self.data)
        return self.data["version"]

    # -------------------------------------------------------------- entities
    @property
    def entities(self) -> dict:
        return self.data["entities"]

    def add_entity(self, eid: str, name: str, etype: str = "character", status: str = "planned",
                   aliases: list[str] | None = None, description: str = "", source: dict | None = None,
                   importance: str = "major") -> dict:
        eid = slugify(eid)
        e = self.entities.get(eid)
        if e is None:
            e = {"id": eid, "name": name, "type": etype, "aliases": [], "status": status,
                 "description": description, "importance": importance, "sources": []}
            self.entities[eid] = e
        for a in aliases or []:
            if a and a not in e["aliases"] and a != e["name"]:
                e["aliases"].append(a)
        if status == "established" and e["status"] == "planned":
            e["status"] = "established"
        if source and source not in e["sources"]:
            e["sources"].append(source)
        return e

    def name_index(self) -> dict[str, str]:
        """Lowercase surface form (full name, each name part, alias) -> entity id."""
        idx = {}
        for eid, e in self.entities.items():
            forms = [e["name"], *e.get("aliases", [])]
            for f in forms:
                if not f:
                    continue
                idx[f.lower()] = eid
                for part in re.findall(r"[A-Za-z][A-Za-z'\-]+", f):
                    if len(part) > 2 and part[0].isupper():
                        idx.setdefault(part.lower(), eid)
        return idx

    # -------------------------------------------------------------- facts
    @property
    def facts(self) -> dict:
        return self.data["facts"]

    def add_fact(self, ftype: str, entities: list[str], claim: str, status: str, importance: str,
                 confidence: float = 1.0, source: dict | None = None, origin: str = "prose") -> dict:
        ents = sorted({slugify(e) for e in entities if e})
        fid = fact_id(claim, ents)
        f = self.facts.get(fid)
        if f is None:
            f = {"id": fid, "type": ftype, "entities": ents, "claim": claim, "status": status,
                 "importance": importance, "confidence": confidence, "origin": origin,
                 "provenance": [], "supersedes": [], "superseded_by": None,
                 "version_added": self.data["version"] + 1, "history": []}
            self.facts[fid] = f
        else:
            if IMPORTANCE_RANK[importance] < IMPORTANCE_RANK[f["importance"]]:
                f["importance"] = importance
            f["confidence"] = max(f["confidence"], confidence)
            if f["status"] in ("planned", "retired") and status == "established":
                self.set_status(fid, "established", "re-established in prose")
        if source:
            if source not in f["provenance"]:
                f["provenance"].append(source)
        return f

    def set_status(self, fid: str, status: str, note: str = "") -> None:
        assert status in STATUSES, status
        f = self.facts[fid]
        f["history"].append({"at": _now(), "from": f["status"], "to": status, "note": note})
        f["status"] = status

    def supersede(self, old_id: str, new_id: str, note: str = "") -> None:
        self.facts[old_id]["superseded_by"] = new_id
        if old_id not in self.facts[new_id]["supersedes"]:
            self.facts[new_id]["supersedes"].append(old_id)
        self.set_status(old_id, "retired", f"superseded by {new_id}. {note}".strip())

    def active_facts(self) -> list[dict]:
        return [f for f in self.facts.values() if f["status"] in ("planned", "established")]

    def facts_for(self, entities: Iterable[str], statuses=("planned", "established")) -> list[dict]:
        es = set(entities)
        return [f for f in self.facts.values() if f["status"] in statuses and es & set(f["entities"])]

    def retract_source(self, chapter: str, scene=None) -> int:
        """Remove provenance from a chapter (or scene) so re-extraction is idempotent.

        Prose facts left with no provenance are deleted; planned facts revert to planned.
        Continuity snapshots and threads opened there are rolled back too.
        """
        def hit(src):
            return src.get("chapter") == chapter and (scene is None or src.get("scene") == scene)
        n = 0
        for fid in list(self.facts):
            f = self.facts[fid]
            before = len(f["provenance"])
            f["provenance"] = [s for s in f["provenance"] if not hit(s)]
            if len(f["provenance"]) != before:
                n += 1
                if not f["provenance"]:
                    if f["origin"] == "plan":
                        if f["status"] == "established":
                            f["status"] = "planned"
                    elif not f["supersedes"]:
                        del self.facts[fid]
        for t in list(self.data["threads"].values()):
            if t.get("setup_source") and hit(t["setup_source"]) and t.get("origin") == "prose":
                del self.data["threads"][t["id"]]
            elif t.get("payoff_source") and hit(t["payoff_source"]):
                t["status"], t["payoff_source"] = "open", None
        for eid, c in self.data["continuity"].items():
            c["history"] = [h for h in c.get("history", []) if not hit(h.get("source", {}))]
            self._recompute_continuity(eid)
        self.data["revision_tasks"] = [t for t in self.data["revision_tasks"]
                                       if not (t["status"] == "open" and hit(t["source"]))]
        return n

    # -------------------------------------------------------------- continuity
    def update_continuity(self, entity: str, source: dict, location=None, gained=(), lost=(),
                          learned=(), flags=()) -> None:
        c = self.data["continuity"].setdefault(slugify(entity), {"history": []})
        c["history"].append({"source": source, "location": location, "gained": list(gained),
                             "lost": list(lost), "learned": list(learned), "flags": list(flags)})
        self._recompute_continuity(slugify(entity))

    def _recompute_continuity(self, eid: str) -> None:
        c = self.data["continuity"][eid]
        loc, holding, knows, flags = None, [], [], []
        for h in c.get("history", []):
            if h.get("location"):
                loc = h["location"]
            for g in h.get("gained", []):
                if g not in holding:
                    holding.append(g)
            for lo in h.get("lost", []):
                if lo in holding:
                    holding.remove(lo)
            for k in h.get("learned", []):
                if k not in knows:
                    knows.append(k)
            for fl in h.get("flags", []):
                if fl.startswith("-") and fl[1:] in flags:
                    flags.remove(fl[1:])
                elif fl not in flags and not fl.startswith("-"):
                    flags.append(fl)
        c.update({"location": loc, "holding": holding, "knows": knows, "flags": flags,
                  "as_of": _src_key(c["history"][-1]["source"]) if c.get("history") else None})

    def continuity_for(self, entities: Iterable[str]) -> dict:
        return {e: {k: v for k, v in self.data["continuity"][e].items() if k != "history"}
                for e in entities if e in self.data["continuity"]}

    # -------------------------------------------------------------- threads (Chekhov ledger)
    def open_thread(self, description: str, source: dict | None, importance: str = "major",
                    payoff_by: str | None = None, origin: str = "prose", tid: str | None = None) -> dict:
        tid = tid or ("t_" + stable_hash(_norm_claim(description))[:8])
        t = self.data["threads"].get(tid)
        if t is None:
            t = {"id": tid, "description": description, "importance": importance, "status": "open",
                 "origin": origin, "setup_source": source, "payoff_source": None,
                 "payoff_by": payoff_by, "planted": source is not None}
            self.data["threads"][tid] = t
        elif source and not t.get("planted"):
            t["setup_source"], t["planted"] = source, True
        return t

    def resolve_thread(self, tid_or_desc: str, source: dict) -> bool:
        t = self.data["threads"].get(tid_or_desc)
        if t is None:
            key = _norm_claim(tid_or_desc)
            for cand in self.data["threads"].values():
                if cand["status"] == "open" and (key in _norm_claim(cand["description"])
                                                 or _norm_claim(cand["description"]) in key):
                    t = cand
                    break
        if t is None:
            return False
        t["status"], t["payoff_source"] = "paid_off", source
        return True

    def open_threads(self) -> list[dict]:
        return [t for t in self.data["threads"].values() if t["status"] == "open"]

    # -------------------------------------------------------------- work queues
    def add_revision_task(self, source: dict, fix: str, reason: str, fact: dict | None = None) -> dict:
        task = {"id": "r_" + stable_hash([source, fix])[:8], "source": source, "fix": fix,
                "reason": reason, "fact": fact, "status": "open", "created": _now()}
        if not any(t["id"] == task["id"] for t in self.data["revision_tasks"]):
            self.data["revision_tasks"].append(task)
        return task

    def open_revision_tasks(self, chapter: str) -> list[dict]:
        return [t for t in self.data["revision_tasks"]
                if t["status"] == "open" and t["source"].get("chapter") == chapter]

    def add_amendment(self, amendment: dict) -> dict:
        amendment = {"id": "a_" + stable_hash(amendment)[:8], "at": _now(), **amendment}
        self.data["amendments"].append(amendment)
        return amendment

    def enqueue_review(self, item: dict) -> dict:
        item = {"id": "q_" + stable_hash(item)[:8], "status": "pending", "created": _now(), **item}
        if not any(q["id"] == item["id"] for q in self.data["review_queue"]):
            self.data["review_queue"].append(item)
        return item

    def pending_reviews(self) -> list[dict]:
        return [q for q in self.data["review_queue"] if q["status"] == "pending"]

    # -------------------------------------------------------------- feed-forward selection
    def select_for_context(self, entities: Iterable[str], token_budget: int, chapter: str = "") -> list[dict]:
        """Relevant facts first (by entity overlap), then all critical facts, within budget."""
        ents = set(entities)
        active = self.active_facts()
        relevant = sorted((f for f in active if ents & set(f["entities"])),
                          key=lambda f: (IMPORTANCE_RANK[f["importance"]], -len(ents & set(f["entities"]))))
        critical = [f for f in active if f["importance"] == "critical" and f not in relevant]
        out, used = [], 0
        for f in relevant[:200] + critical:
            cost = len(f["claim"]) // 4 + 8
            if used + cost > token_budget:
                continue
            out.append(f)
            used += cost
        return out
