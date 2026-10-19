"""P1 craft modules: mystery + who-knows-what, subplots (A/B/C, MICE, braiding, convergence), magic (Sanderson)."""
from __future__ import annotations

import json

from bookforge.core.schema import slugify
from bookforge.craft.base import CraftModule, cfg, issue, register
from bookforge.craft.modules_p0 import _sid, _valid_ch


def _pov_id(P, ch, sn):
    c = next((c for c in P.book.chapters if c["id"] == ch), {})
    s = next((s for s in c.get("scenes") or [] if int(s.get("scene_number", 0)) == sn), {})
    pov = (s.get("pov") or "").lower()
    for k, v in P.chars.items():
        if pov and (pov == (v.get("name") or "").lower() or pov.split()[0] == (v.get("name") or "x").lower().split()[0]):
            return k
    return slugify(pov) if pov else ""


# ================================================================ mystery
@register
class Mystery(CraftModule):
    """Fair-play mystery: truths, clue chain, red herrings, suspects, per-character knowledge."""
    name = "mystery"
    tier = "P1"

    def plan_prompt(self, P):
        return f"""You are a mystery plotter who plays fair (Knox). If this premise has no real mystery or secret to uncover, output {{"applies": false}}.
Otherwise design: the hidden truths (with the chapter where the reader learns each), a solution chain of 3-7 clues each planted at least {cfg()['min_setup_gap_chapters']} chapters before the main reveal and shown plainly but disguised, 1-3 red herrings each with a clearing chapter, suspects (the culprit appears in the first quarter), and who learns which truth when.

PREMISE: {P.premise}
CAST: {json.dumps({k: v.get('name') for k, v in P.chars.items()})}
OUTLINE: {json.dumps(P.brief_outline())}

Output ONLY JSON: {{"applies": true, "main_truth": "t1", "culprit": "character_id", "truths": [{{"id": "t1", "statement": "...", "reveal_ch": "ch09", "revealed_to": ["character_id"]}}], "clues": [{{"id": "c1", "points_to": "t1", "description": "...", "plant_ch": "ch02", "disguise": "how it is hidden in plain sight"}}], "red_herrings": [{{"id": "h1", "false_claim": "...", "suspect": "character_id", "plant_ch": "ch03", "cleared_ch": "ch07", "clearing_method": "..."}}], "suspects": [{{"entity": "character_id", "motive": "", "means": "", "opportunity": "", "first_appear_ch": "ch01"}}], "solution_chain": ["c1"], "knowledge": [{{"who": "character_id", "truth": "t1", "learns_ch": "ch06"}}], "dramatic_irony": []}}"""

    def normalize(self, raw, P):
        raw = raw if isinstance(raw, dict) else {}
        if not raw.get("applies", True) or not raw.get("truths"):
            return {}
        last = P.book.clamp(P.book.n - 1)
        truths = [{"id": _sid("t", i, t.get("id")), "statement": t.get("statement", ""),
                   "reveal_ch": _valid_ch(P, t.get("reveal_ch"), last),
                   "revealed_to": [slugify(x) for x in t.get("revealed_to", [])]}
                  for i, t in enumerate(raw["truths"]) if isinstance(t, dict)]
        tids = {t["id"] for t in truths}
        main = raw.get("main_truth") if raw.get("main_truth") in tids else truths[0]["id"]
        clues = [{"id": _sid("c", i, c.get("id")), "points_to": c.get("points_to") if c.get("points_to") in tids else main,
                  "description": c.get("description", ""), "plant_ch": _valid_ch(P, c.get("plant_ch"), P.book.clamp(0)),
                  "disguise": c.get("disguise", "")} for i, c in enumerate(raw.get("clues", [])) if isinstance(c, dict)]
        herrings = [{"id": _sid("h", i, h.get("id")), "false_claim": h.get("false_claim", ""),
                     "suspect": slugify(h.get("suspect", "")), "plant_ch": _valid_ch(P, h.get("plant_ch"), P.book.clamp(1)),
                     "cleared_ch": _valid_ch(P, h.get("cleared_ch"), ""), "clearing_method": h.get("clearing_method", "")}
                    for i, h in enumerate(raw.get("red_herrings", [])) if isinstance(h, dict)]
        suspects = [{"entity": slugify(s.get("entity", "")), **{k: s.get(k, "") for k in ("motive", "means", "opportunity")},
                     "first_appear_ch": _valid_ch(P, s.get("first_appear_ch"), P.book.clamp(0))}
                    for s in raw.get("suspects", []) if isinstance(s, dict) and s.get("entity")]
        cids = {c["id"] for c in clues}
        chain = [c for c in raw.get("solution_chain", []) if c in cids] or [c["id"] for c in clues if c["points_to"] == main]
        know = {}
        for t in truths:
            know.setdefault(t["id"], {})["reader"] = t["reveal_ch"]
            for w in t["revealed_to"]:
                know[t["id"]][w] = t["reveal_ch"]
        for k in raw.get("knowledge", []):
            if isinstance(k, dict) and k.get("truth") in tids and k.get("learns_ch") in P.book.index:
                know[k["truth"]][slugify(k.get("who", "reader")) if k.get("who") != "reader" else "reader"] = k["learns_ch"]
        culprit = slugify(raw.get("culprit", "")) if raw.get("culprit") else ""
        if culprit and not any(s["entity"] == culprit for s in suspects):
            suspects.append({"entity": culprit, "motive": "", "means": "", "opportunity": "", "first_appear_ch": P.book.clamp(0)})
        return {"main_truth": main, "culprit": culprit, "truths": truths, "clues": clues, "red_herrings": herrings,
                "suspects": suspects, "solution_chain": chain, "knowledge": know,
                "dramatic_irony": [t for t in raw.get("dramatic_irony", []) if t in tids]}

    def _truth(self, data, tid):
        return next((t for t in data.get("truths", []) if t["id"] == tid), None)

    def autofix_plan(self, data, P):
        if not data:
            return []
        notes, gap = [], cfg()["min_setup_gap_chapters"]
        main = self._truth(data, data["main_truth"])
        mr = P.book.ci(main["reveal_ch"])
        for c in data["clues"]:
            t = self._truth(data, c["points_to"])
            limit = P.book.ci(t["reveal_ch"]) - gap
            if P.book.ci(c["plant_ch"]) > limit >= 0:
                c["plant_ch"] = P.book.clamp(limit)
                notes.append(f"mystery: moved clue {c['id']} to {c['plant_ch']} (fair play gap {gap})")
        for h in data["red_herrings"]:
            if not h["cleared_ch"] or P.book.ci(h["cleared_ch"]) > mr:
                h["cleared_ch"] = main["reveal_ch"]
                notes.append(f"mystery: red herring {h['id']} now cleared by {h['cleared_ch']}")
        q = P.book.clamp(int(0.25 * (P.book.n - 1)))
        for s in data["suspects"]:
            if s["entity"] == data["culprit"] and P.book.ci(s["first_appear_ch"]) > P.book.ci(q):
                s["first_appear_ch"] = q
                notes.append(f"mystery: culprit now appears by {q} (Knox rule 1)")
        return notes

    def planned_events(self, data, P):
        if not data:
            return []
        ev = [{"kind": "clue", "ref": c["id"], "ch": c["plant_ch"], "module": self.name} for c in data["clues"]]
        ev += [{"kind": "reveal", "ref": t["id"], "ch": t["reveal_ch"], "module": self.name} for t in data["truths"]]
        for h in data["red_herrings"]:
            ev.append({"kind": "herring", "ref": h["id"], "ch": h["plant_ch"], "module": self.name})
            if h["cleared_ch"]:
                ev.append({"kind": "herring_cleared", "ref": h["id"], "ch": h["cleared_ch"], "module": self.name})
        ev += [{"kind": "appear", "ref": s["entity"], "ch": s["first_appear_ch"], "module": self.name} for s in data["suspects"]]
        for tid, who in data["knowledge"].items():
            for w, ch in who.items():
                if w != "reader":
                    ev.append({"kind": "knows", "ref": tid, "ch": ch, "module": self.name, "data": {"who": w, "mode": "learns"}})
        return ev

    def _knows_by(self, data, who, ci, P):
        return {tid for tid, m in data.get("knowledge", {}).items() if who in m and P.book.ci(m[who]) <= ci}

    def brief(self, data, card_events, ch, scene, L, P):
        if not data:
            return []
        out, ci = [], P.book.ci(ch)
        clues = {c["id"]: c for c in data["clues"]}
        hs = {h["id"]: h for h in data["red_herrings"]}
        for e in card_events:
            if e["kind"] == "clue" and e["ref"] in clues:
                c = clues[e["ref"]]
                out.append(f"PLANT CLUE: {c['description']}. Disguise: {c['disguise'] or 'place it among ordinary details'}. "
                           "Describe it plainly so the reader can picture it (fair play); just don't point out its significance.")
            elif e["kind"] == "reveal":
                t = self._truth(data, e["ref"])
                chain = [clues[c]["description"] for c in data["solution_chain"] if c in clues] if t["id"] == data["main_truth"] else \
                    [c["description"] for c in data["clues"] if c["points_to"] == t["id"]]
                out.append(f"REVEAL: {t['statement']}" + (f" (to {', '.join(t['revealed_to'])})" if t["revealed_to"] else "")
                           + (f". Earn it from the planted clues: {chain}" if chain else "") + ". Reveal it once; do not re-explain.")
            elif e["kind"] == "herring" and e["ref"] in hs:
                out.append(f"RED HERRING: make '{hs[e['ref']]['false_claim']}' plausible without lying in narration.")
            elif e["kind"] == "herring_cleared" and e["ref"] in hs:
                out.append(f"CLEAR RED HERRING: '{hs[e['ref']]['false_claim']}' is disproved by {hs[e['ref']]['clearing_method'] or 'a concrete fact'}.")
            elif e["kind"] == "appear":
                out.append(f"INTRODUCE {P.chars.get(e['ref'], {}).get('name', e['ref'])} naturally, as one of several people.")
        here = {e["ref"] for e in card_events if e["kind"] == "reveal"}
        hidden = [t["statement"] for t in data["truths"] if P.book.ci(t["reveal_ch"]) > ci and t["id"] not in here
                  and t["id"] not in data.get("dramatic_irony", [])]
        if hidden:
            out.append("MUST NOT REVEAL OR HINT YET: " + " | ".join(hidden))
        pov = _pov_id(P, ch, scene)
        if pov:
            name = P.chars.get(pov, {}).get("name", pov)
            unknown = [t["statement"] for t in data["truths"]
                       if t["id"] not in self._knows_by(data, pov, ci, P) and t["id"] not in here]
            if unknown:
                out.append(f"{name} DOES NOT KNOW (cannot act on or think about as fact): " + " | ".join(unknown))
        return out

    def extract_spec(self, data):
        if not data:
            return ""
        return ("Mystery: report clue (ref clue id; data.overt true if narration spotlights it), reveal (ref truth id; "
                "data.to list incl. 'reader'), herring, herring_cleared (ref herring id), appear (ref suspect id, first on-page "
                "appearance), knows (ref truth id; data.who = character id; data.mode = learns|acts_on). Ids: "
                + json.dumps({"truths": {t["id"]: t["statement"] for t in data["truths"]},
                              "clues": {c["id"]: c["description"] for c in data["clues"]},
                              "herrings": {h["id"]: h["false_claim"] for h in data["red_herrings"]},
                              "suspects": [s["entity"] for s in data["suspects"]]}))

    def _needed(self, data, tid):
        if tid == data["main_truth"]:
            return list(data["solution_chain"])
        return [c["id"] for c in data["clues"] if c["points_to"] == tid]

    def observe(self, data, scene_events, src, L, P):
        if not data:
            return []
        out = []
        pos = (P.book.ci(src["chapter"]), src["scene"])
        prior = L.realized_before(P.book, pos)
        planted = {e["ref"] for e in prior if e["kind"] == "clue"} | {e["ref"] for e in scene_events if e["kind"] == "clue"}
        for e in scene_events:
            if e["kind"] == "clue" and (e.get("data") or {}).get("overt"):
                out.append(issue("spotlight", "major", f"Clue {e['ref']} is spotlighted.", "Bury it among ordinary details.",
                                 src["chapter"], src["scene"], e["ref"], action="revise"))
            if e["kind"] == "reveal":
                t = self._truth(data, e.get("ref"))
                if not t:
                    continue
                missing = [c for c in self._needed(data, t["id"]) if c not in planted]
                if missing:
                    out.append(issue("reveal_before_setup", "critical", f"Truth {t['id']} revealed before clues {missing} were planted.",
                                     f"Cut or soften the reveal: {t['statement']}", src["chapter"], src["scene"], t["id"], action="revise"))
                elif P.book.ci(t["reveal_ch"]) > pos[0] and t["id"] not in data.get("dramatic_irony", []):
                    out.append({**issue("early_leak", "critical", f"Truth {t['id']} leaks in {src['chapter']}, planned for {t['reveal_ch']}.",
                                        "Plan wins: remove the leak. Canon wins: move the reveal here.", src["chapter"],
                                        src["scene"], t["id"], action="review"),
                                "amend": {"move": {"kind": "reveal", "ref": t["id"], "ch": src["chapter"], "scene": src["scene"]}}})
            if e["kind"] == "knows" and (e.get("data") or {}).get("mode") == "acts_on":
                who, tid = (e.get("data") or {}).get("who"), e.get("ref")
                learned = any(x["kind"] == "knows" and x.get("ref") == tid and (x.get("data") or {}).get("who") == who
                              and (x.get("data") or {}).get("mode") == "learns" for x in prior + scene_events)
                if not learned and tid not in self._knows_by(data, who, pos[0], P):
                    out.append(issue("knowledge_leak", "critical", f"{who} acts on truth {tid} before learning it.",
                                     f"{who} cannot know this yet; rewrite the moment.", src["chapter"], src["scene"], tid,
                                     action="revise"))
        return out

    def check(self, data, E, L, P, mode):
        if not data:
            return []
        out, gap = [], cfg()["min_setup_gap_chapters"]
        main = self._truth(data, data["main_truth"])
        mrev = E.first("reveal", main["id"])
        if mrev is None:
            out.append(issue("fair_play", "major", f"Main truth never revealed: {main['statement']}", "", main["reveal_ch"]))
        else:
            for cid in data["solution_chain"]:
                c = E.first("clue", cid)
                if c is None or not E.before(c, mrev):
                    out.append(issue("fair_play", "critical", f"Clue {cid} is not shown before the solution.",
                                     "Plant it on-page before the reveal.", mrev["ch"], ref=cid))
                elif P.book.ci(mrev["ch"]) - P.book.ci(c["ch"]) < gap:
                    out.append(issue("fair_play", "major", f"Clue {cid} lands {P.book.ci(mrev['ch']) - P.book.ci(c['ch'])} chapter(s) before the reveal.",
                                     f"Plant it at least {gap} chapters earlier.", c["ch"], ref=cid))
        if data.get("culprit"):
            a = E.first("appear", data["culprit"])
            if a is None or P.book.pct(a["ch"]) > 25:
                out.append(issue("fair_play", "critical", f"Culprit {data['culprit']} first appears "
                                 f"{'never' if a is None else 'in ' + a['ch']}; must be on-page in the first quarter.",
                                 "Introduce them early among other suspects.", ref=data["culprit"]))
        for h in data["red_herrings"]:
            if E.first("herring", h["id"]) is None:
                continue
            cl = E.first("herring_cleared", h["id"])
            if cl is None or (mrev and P.book.pos(cl) > P.book.pos(mrev)):
                out.append(issue("red_herring", "major", f"Red herring never cleared (before the reveal): {h['false_claim']}",
                                 f"Clear it by {h['cleared_ch'] or main['reveal_ch']}.", ref=h["id"]))
        for t in data["truths"]:
            r = E.first("reveal", t["id"])
            if r and P.book.ci(r["ch"]) < P.book.ci(t["reveal_ch"]) and t["id"] not in data.get("dramatic_irony", []):
                out.append(issue("knowledge_leak", "critical", f"Reader learns {t['id']} in {r['ch']}, planned {t['reveal_ch']}.",
                                 "Remove the leak or amend the plan.", r["ch"], ref=t["id"]))
        for e in E.of("knows"):
            d = e.get("data") or {}
            if d.get("mode") != "acts_on":
                continue
            learns = [x for x in E.of("knows", e["ref"]) if (x.get("data") or {}).get("who") == d.get("who")
                      and (x.get("data") or {}).get("mode") == "learns" and P.book.pos(x) <= P.book.pos(e)]
            if not learns and e["ref"] not in self._knows_by(data, d.get("who"), P.book.ci(e["ch"]), P):
                out.append(issue("knowledge_leak", "critical", f"{d.get('who')} acts on {e['ref']} in {e['ch']} before learning it.",
                                 "", e["ch"], e.get("scene"), e["ref"]))
        return out


# ================================================================ subplots
@register
class Subplots(CraftModule):
    """A/B/C threads with MICE types, nesting, braiding gaps and convergence points."""
    name = "subplots"
    tier = "P1"
    GAP = {"A": 1, "B": 2, "C": 3}

    def plan_prompt(self, P):
        return f"""Design the plot threads: the A plot (external), a B plot (usually relationship/theme) and 0-2 C threads.
For each give a MICE type (Milieu, Inquiry, Character, Event), open and close chapters (threads close in reverse order of opening, like nested parentheses), the chapters where it appears (braiding), a max gap in chapters before it must reappear, and convergence points (midpoint, all is lost, climax) where it collides with another thread and what it supplies.

PREMISE: {P.premise}
OUTLINE: {json.dumps(P.brief_outline())}

Output ONLY JSON: {{"threads": [{{"id": "a", "label": "A", "mice_type": "E", "description": "...", "open_ch": "ch01", "close_ch": "ch10", "parent": null, "chapters": ["ch01"], "max_gap": 1, "convergences": [{{"id": "cv1", "ch": "ch05", "with": "b", "supplies": "..."}}]}}]}}"""

    def normalize(self, raw, P):
        out = []
        for i, t in enumerate((raw or {}).get("threads", []) if isinstance(raw, dict) else []):
            if not isinstance(t, dict):
                continue
            label = (t.get("label") or "C").upper()[:1]
            o = _valid_ch(P, t.get("open_ch"), P.book.clamp(0))
            c = _valid_ch(P, t.get("close_ch"), P.book.clamp(P.book.n - 1))
            gap = int(t.get("max_gap") or self.GAP.get(label, 3))
            chs = sorted({x for x in t.get("chapters", []) if x in P.book.index} | {o, c}, key=P.book.ci)
            if len(chs) <= 2:  # default braid: every `gap` chapters
                chs = sorted({P.book.clamp(k) for k in range(P.book.ci(o), P.book.ci(c) + 1, max(1, gap))} | {o, c}, key=P.book.ci)
            convs = [{"id": cv.get("id") or f"{_sid('t', i, t.get('id'))}_cv{j + 1}", "ch": _valid_ch(P, cv.get("ch"), c),
                      "with": cv.get("with", ""), "supplies": cv.get("supplies", "")}
                     for j, cv in enumerate(t.get("convergences", [])) if isinstance(cv, dict)]
            out.append({"id": _sid("t", i, t.get("id")), "label": label, "mice_type": (t.get("mice_type") or "E")[:1].upper(),
                        "description": t.get("description", ""), "open_ch": o, "close_ch": c, "parent": t.get("parent"),
                        "chapters": chs, "max_gap": gap, "convergences": convs})
        return {"threads": out}

    def planned_events(self, data, P):
        ev = []
        for t in data["threads"]:
            ev.append({"kind": "thread_open", "ref": t["id"], "ch": t["open_ch"], "module": self.name})
            ev += [{"kind": "thread_touch", "ref": t["id"], "ch": c, "module": self.name} for c in t["chapters"]
                   if c not in (t["open_ch"], t["close_ch"])]
            ev.append({"kind": "thread_resolved", "ref": t["id"], "ch": t["close_ch"], "module": self.name})
            ev += [{"kind": "convergence", "ref": cv["id"], "ch": cv["ch"], "module": self.name, "data": {"thread": t["id"]}}
                   for cv in t["convergences"]]
        return ev

    MICE = {"M": "place (enter/exit)", "I": "question (ask/answer)", "C": "identity (shift/new self)", "E": "status quo (break/restore)"}

    def brief(self, data, card_events, ch, scene, L, P):
        by = {t["id"]: t for t in data.get("threads", [])}
        cv = {c["id"]: (t, c) for t in data.get("threads", []) for c in t["convergences"]}
        out = []
        for e in card_events:
            t = by.get(e.get("ref"))
            if e["kind"] == "thread_open" and t:
                out.append(f"OPEN {t['label']} THREAD ({self.MICE.get(t['mice_type'], '')}): {t['description']}.")
            elif e["kind"] == "thread_touch" and t:
                out.append(f"TOUCH {t['label']} THREAD: {t['description']}. Move it one concrete step; recall where it stood.")
            elif e["kind"] == "thread_resolved" and t:
                out.append(f"RESOLVE {t['label']} THREAD: {t['description']}. Close it the way it opened ({self.MICE.get(t['mice_type'], '')}).")
            elif e["kind"] == "convergence" and e["ref"] in cv:
                tt, c = cv[e["ref"]]
                out.append(f"CONVERGENCE: thread {tt['label']} collides with {c['with']}; it supplies: {c['supplies']}.")
        return out

    def extract_spec(self, data):
        if not data.get("threads"):
            return ""
        return ("Report thread_open / thread_touch / thread_resolved (ref thread id) and convergence (ref convergence id) for: "
                + json.dumps({t["id"]: {"desc": t["description"], "convergences": [c["id"] for c in t["convergences"]]}
                              for t in data["threads"]}))

    def check(self, data, E, L, P, mode):
        out = []
        closes = {}
        for t in data.get("threads", []):
            seen = sorted({P.book.ci(e["ch"]) for k in ("thread_open", "thread_touch", "thread_resolved") for e in E.of(k, t["id"])})
            res = E.first("thread_resolved", t["id"])
            if res is None:
                out.append(issue("subplot_unresolved", "major", f"Thread {t['label']} never resolved: {t['description']}",
                                 f"Resolve it by {t['close_ch']}.", t["close_ch"], ref=t["id"]))
            else:
                closes[t["id"]] = P.book.pos(res)
            lo = P.book.ci(t["open_ch"])
            hi = P.book.ci(res["ch"]) if res else P.book.ci(t["close_ch"])
            pts = [lo] + [s for s in seen if lo < s < hi] + [hi]
            for a, b in zip(pts, pts[1:]):
                if b - a > t["max_gap"]:
                    out.append(issue("subplot_gap", "major", f"Thread {t['label']} vanishes from {P.book.clamp(a)} to {P.book.clamp(b)} "
                                     f"({b - a} chapters; max {t['max_gap']}).", "Touch it in between.", P.book.clamp(a + 1), ref=t["id"]))
                    break
            for cv in t["convergences"]:
                if E.first("convergence", cv["id"]) is None:
                    out.append(issue("convergence", "major", f"Convergence {cv['id']} ({t['label']} x {cv['with']}) never happens.",
                                     f"Make the threads collide in {cv['ch']}.", cv["ch"], ref=cv["id"]))
        for t in data.get("threads", []):
            p = t.get("parent")
            if p in closes and t["id"] in closes and closes[t["id"]] > closes[p]:
                out.append(issue("mice_nesting", "minor", f"Inner thread {t['id']} closes after its outer thread {p}.",
                                 "Close inner threads first (MICE nesting).", ref=t["id"]))
        return out


# ================================================================ magic
@register
class Magic(CraftModule):
    """Sanderson's laws: understanding before solutions, limits over powers, expand before adding."""
    name = "magic"
    tier = "P1"

    def plan_prompt(self, P):
        return f"""Design the magic system (or advanced technology that plays the same role). If the story has none, output {{"applies": false}}.
Follow Sanderson's laws: (1) magic may solve a conflict only to the degree the reader understands it, so every rule used in a solution is shown on-page earlier; (2) limitations, weaknesses and costs matter more than powers, and every power has one; (3) expand existing rules before adding new ones, so no new power appears after {cfg()['no_new_magic_after_pct']}% of the book.
Hardness 0 (soft, mysterious, never solves problems) to 1 (hard, rule-bound).

PREMISE: {P.premise}
WORLD RULES: {json.dumps(P.world.get('core_rules', []))}
OUTLINE: {json.dumps(P.brief_outline())}

Output ONLY JSON: {{"applies": true, "hardness": 0.8, "rules": [{{"id": "r1", "kind": "power|limitation|weakness|cost", "text": "...", "applies_to": ["r1"], "shown_ch": "ch02"}}], "solutions": [{{"id": "s1", "ch": "ch10", "description": "how magic resolves a conflict", "rules": ["r1"], "cost": "r3"}}]}}"""

    def normalize(self, raw, P):
        raw = raw if isinstance(raw, dict) else {}
        if not raw.get("applies", True) or not raw.get("rules"):
            return {}
        rules = [{"id": _sid("r", i, r.get("id")), "kind": r.get("kind", "power") if r.get("kind") in ("power", "limitation", "weakness", "cost") else "power",
                  "text": r.get("text", ""), "applies_to": r.get("applies_to", []), "shown_ch": _valid_ch(P, r.get("shown_ch"), P.book.clamp(0))}
                 for i, r in enumerate(raw["rules"]) if isinstance(r, dict)]
        rid = {r["id"] for r in rules}
        sols = [{"id": _sid("s", i, s.get("id")), "ch": _valid_ch(P, s.get("ch"), P.book.clamp(P.book.n - 1)),
                 "description": s.get("description", ""), "rules": [x for x in s.get("rules", []) if x in rid],
                 "cost": s.get("cost") if s.get("cost") in rid else ""}
                for i, s in enumerate(raw.get("solutions", [])) if isinstance(s, dict)]
        try:
            hard = max(0.0, min(1.0, float(raw.get("hardness", 0.7))))
        except (TypeError, ValueError):
            hard = 0.7
        return {"hardness": hard, "rules": rules, "solutions": sols}

    def _rule(self, data, rid):
        return next((r for r in data.get("rules", []) if r["id"] == rid), None)

    def autofix_plan(self, data, P):
        if not data:
            return []
        notes, gap = [], cfg()["law1_min_gap_chapters"]
        for s in data["solutions"]:
            for rid in s["rules"] + ([s["cost"]] if s["cost"] else []):
                r = self._rule(data, rid)
                lim = P.book.ci(s["ch"]) - gap
                if r and P.book.ci(r["shown_ch"]) > lim >= 0:
                    r["shown_ch"] = P.book.clamp(lim)
                    notes.append(f"magic: rule {rid} now shown by {r['shown_ch']} (Law 1)")
        return notes

    def planned_events(self, data, P):
        if not data:
            return []
        ev = [{"kind": "rule_shown", "ref": r["id"], "ch": r["shown_ch"], "module": self.name} for r in data["rules"]]
        ev += [{"kind": "magic_use", "ref": s["id"], "ch": s["ch"], "module": self.name,
                "data": {"rules": s["rules"], "at_conflict": True, "cost_paid": bool(s["cost"])}} for s in data["solutions"]]
        return ev

    def _shown_before(self, data, L, P, ci):
        planned = {r["id"] for r in data.get("rules", []) if P.book.ci(r["shown_ch"]) < ci}
        real = {e["ref"] for e in L.data["realized"] if e["kind"] == "rule_shown" and P.book.ci(e["ch"]) < ci}
        return planned | real

    def brief(self, data, card_events, ch, scene, L, P):
        if not data:
            return []
        out, ci = [], P.book.ci(ch)
        for e in card_events:
            if e["kind"] == "rule_shown":
                r = self._rule(data, e["ref"])
                out.append(f"SHOW MAGIC RULE in action, with consequences not lecture: {r['text']} ({r['kind']}).")
            elif e["kind"] == "magic_use":
                s = next(x for x in data["solutions"] if x["id"] == e["ref"])
                rules = [self._rule(data, x)["text"] for x in s["rules"]]
                cost = self._rule(data, s["cost"])["text"] if s["cost"] else ""
                out.append(f"MAGIC SOLUTION: {s['description']}. Use ONLY these established rules: {rules}."
                           + (f" The cost must bite: {cost}." if cost else ""))
        known = [self._rule(data, r)["text"] for r in sorted(self._shown_before(data, L, P, ci)) if self._rule(data, r)]
        if known:
            out.append(f"MAGIC THE READER KNOWS: {known}. Do not invent new powers or ignore costs."
                       + (" Magic here is soft: it must not solve the protagonist's problems." if data["hardness"] < 0.5 else ""))
        return out

    def extract_spec(self, data):
        if not data:
            return ""
        return ("Magic: report rule_shown (ref rule id) when a rule is demonstrated, and magic_use (ref solution id or any) with data "
                "{rules: [rule ids used], at_conflict: true if it resolves a conflict, cost_paid: bool, new_power: true if it shows "
                "an ability not in this list}. Rules: " + json.dumps({r["id"]: r["text"] for r in data["rules"]}))

    def observe(self, data, scene_events, src, L, P):
        if not data:
            return []
        out, ci, gap = [], P.book.ci(src["chapter"]), cfg()["law1_min_gap_chapters"]
        shown = {e["ref"] for e in L.data["realized"] if e["kind"] == "rule_shown" and P.book.ci(e["ch"]) <= ci - gap}
        for e in scene_events:
            if e["kind"] != "magic_use":
                continue
            d = e.get("data") or {}
            if d.get("new_power") and P.book.pct(src["chapter"]) > cfg()["no_new_magic_after_pct"]:
                out.append(issue("magic_law3", "major", "A new magical ability appears late in the book.",
                                 "Use an existing rule in a new way instead (expand, don't add).", src["chapter"], src["scene"],
                                 e.get("ref"), action="revise"))
            if d.get("at_conflict"):
                if data["hardness"] < 0.5:
                    out.append(issue("magic_law1", "major", "Soft magic solves a conflict.", "Let the characters solve it.",
                                     src["chapter"], src["scene"], e.get("ref"), action="revise"))
                unknown = [r for r in d.get("rules", []) if r not in shown]
                if unknown or d.get("new_power"):
                    out.append(issue("magic_law1", "critical", f"Deus ex magica: conflict solved with rules not shown earlier {unknown or '(new power)'}.",
                                     "Show the rule earlier (plan amendment) or solve it another way.", src["chapter"], src["scene"],
                                     e.get("ref"), action="review"))
        return out

    def check(self, data, E, L, P, mode):
        if not data:
            return []
        out, gap = [], cfg()["law1_min_gap_chapters"]
        for u in E.of("magic_use"):
            d = u.get("data") or {}
            if not d.get("at_conflict"):
                continue
            for rid in d.get("rules", []):
                s = E.first("rule_shown", rid)
                if s is None or P.book.ci(u["ch"]) - P.book.ci(s["ch"]) < gap:
                    out.append(issue("magic_law1", "critical", f"Rule {rid} solves a conflict in {u['ch']} without being shown "
                                     f"{gap}+ chapter(s) earlier.", "Demonstrate it earlier.", u["ch"], ref=rid))
            if d.get("new_power"):
                out.append(issue("magic_law1", "critical", f"New power used to solve a conflict in {u['ch']}.", "", u["ch"]))
        powers = [r for r in data["rules"] if r["kind"] == "power"]
        limits = [r for r in data["rules"] if r["kind"] != "power"]
        for p in powers:
            if not any(p["id"] in (l.get("applies_to") or []) for l in limits) and not (limits and len(powers) == 1):
                out.append(issue("magic_law2", "major", f"Power {p['id']} has no limitation, weakness or cost.",
                                 "Give it a limit that bites.", ref=p["id"]))
        if mode == "audit" and limits and E.of("magic_use") and not any((u.get("data") or {}).get("cost_paid") for u in E.of("magic_use")):
            out.append(issue("magic_law2", "major", "Magic is used but its costs never bite.", "Make a cost hurt at least once."))
        cut = cfg()["no_new_magic_after_pct"]
        for p in powers:
            s = E.first("rule_shown", p["id"])
            if s and P.book.pct(s["ch"]) > cut:
                out.append(issue("magic_law3", "major", f"Power {p['id']} first appears at {P.book.pct(s['ch']):.0f}% (after {cut}%).",
                                 "Introduce it earlier or expand an existing rule.", s["ch"], ref=p["id"]))
        return out
