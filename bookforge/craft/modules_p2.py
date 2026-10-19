"""P2 craft modules: relationship arcs, worldbuilding reveal pacing, prophecy, mentor, trope choices."""
from __future__ import annotations

import json

from bookforge.core.schema import slugify
from bookforge.craft.base import CraftModule, cfg, issue, norm_text, register
from bookforge.craft.modules_p0 import _sid, _valid_ch


def _items(raw, key):
    return [x for x in ((raw or {}).get(key, []) if isinstance(raw, dict) else []) if isinstance(x, dict)]


# ================================================================ relationships
@register
class Relationships(CraftModule):
    name = "relationships"
    tier = "P2"

    def plan_prompt(self, P):
        return f"""Design 1-4 key relationship arcs (pairs of characters). Each has a starting trust (-5..+5) and ordered stages (e.g. strangers, friction, alliance, rupture, repair, bond) pinned to chapters, each with a trust change of at most 2 so intimacy is earned.

CAST: {json.dumps({k: v.get('name') for k, v in P.chars.items()})}
OUTLINE: {json.dumps(P.brief_outline())}

Output ONLY JSON: {{"relationships": [{{"id": "finn_zara", "a": "finn", "b": "zara", "start_trust": 0, "stages": [{{"name": "friction", "chapter": "ch02", "trust_delta": -1}}]}}]}}"""

    def normalize(self, raw, P):
        out = []
        for i, r in enumerate(_items(raw, "relationships")):
            stages = [{"name": s.get("name", f"stage{j + 1}"), "chapter": _valid_ch(P, s.get("chapter"), P.book.clamp(j)),
                       "trust_delta": int(s.get("trust_delta") or 0)} for j, s in enumerate(r.get("stages", [])) if isinstance(s, dict)]
            stages.sort(key=lambda s: P.book.ci(s["chapter"]))
            out.append({"id": _sid("rel", i, r.get("id")), "a": slugify(r.get("a", "")), "b": slugify(r.get("b", "")),
                        "start_trust": int(r.get("start_trust") or 0), "stages": stages})
        return {"relationships": out}

    def planned_events(self, data, P):
        return [{"kind": "trust_shift", "ref": r["id"], "ch": s["chapter"], "module": self.name,
                 "data": {"delta": s["trust_delta"], "stage": s["name"]}} for r in data["relationships"] for s in r["stages"]]

    def brief(self, data, card_events, ch, scene, L, P):
        by = {r["id"]: r for r in data.get("relationships", [])}
        out = []
        for e in card_events:
            r = by.get(e.get("ref"))
            if e["kind"] == "trust_shift" and r:
                before = r["start_trust"] + sum(s["trust_delta"] for s in r["stages"] if P.book.ci(s["chapter"]) < P.book.ci(ch))
                d = (e.get("data") or {})
                names = "/".join(P.chars.get(x, {}).get("name", x) for x in (r["a"], r["b"]))
                out.append(f"RELATIONSHIP {names}: stage '{d.get('stage')}'. Trust {before:+d} moves {d.get('delta', 0):+d} "
                           "through one specific act or line, not a summary.")
        return out

    def extract_spec(self, data):
        if not data.get("relationships"):
            return ""
        return ('Report trust_shift (ref relationship id, data.delta -3..+3) when trust between the pair visibly changes: '
                + json.dumps({r["id"]: [r["a"], r["b"]] for r in data["relationships"]}))

    def observe(self, data, scene_events, src, L, P):
        return [issue("relationship_jump", "major", f"Trust in {e['ref']} jumps {e['data']['delta']:+d} in one scene.",
                      "Earn it over several scenes; soften this shift.", src["chapter"], src["scene"], e["ref"], action="revise")
                for e in scene_events if e["kind"] == "trust_shift" and abs(int((e.get("data") or {}).get("delta") or 0)) >= 3]

    def check(self, data, E, L, P, mode):
        out = []
        for r in data.get("relationships", []):
            if mode == "plan":
                out += [issue("relationship_jump", "minor", f"{r['id']}: stage '{s['name']}' moves trust {s['trust_delta']:+d}.",
                              "Split it into smaller steps.", s["chapter"], ref=r["id"]) for s in r["stages"] if abs(s["trust_delta"]) >= 3]
                continue
            planned = r["start_trust"]
            for s in r["stages"]:
                planned += s["trust_delta"]
                got = r["start_trust"] + sum(int((e.get("data") or {}).get("delta") or 0) for e in E.of("trust_shift", r["id"])
                                             if P.book.ci(e["ch"]) <= P.book.ci(s["chapter"]))
                if abs(got - planned) > 1:
                    out.append(issue("relationship_trajectory", "major", f"{r['id']}: trust {got:+d} by {s['chapter']}, planned "
                                     f"{planned:+d} ('{s['name']}').", "Add or soften the moments that move trust.", s["chapter"], ref=r["id"]))
                    break
        return out


# ================================================================ worldbuilding reveal pacing
@register
class WorldReveal(CraftModule):
    name = "world_reveal"
    tier = "P2"

    def plan_prompt(self, P):
        return f"""Schedule worldbuilding reveals with the hollow-iceberg approach (Sanderson): each element the story needs is first mentioned plainly (the reader is told what it is in a sentence), and its deeper workings are explained later, clearly, when the plot needs them. No scene explains more than {cfg()['max_new_concepts_per_scene']} new concepts.

WORLD: {json.dumps({k: P.world.get(k) for k in ('core_rules', 'unique_elements', 'lore') if P.world.get(k)})}
OUTLINE: {json.dumps(P.brief_outline())}

Output ONLY JSON: {{"elements": [{{"id": "w1", "element": "...", "first_hint_ch": "ch01", "first_explain_ch": "ch04", "depth": "hint|light|full"}}]}}"""

    def normalize(self, raw, P):
        out = []
        for i, w in enumerate(_items(raw, "elements")):
            if not w.get("element"):
                continue
            h = _valid_ch(P, w.get("first_hint_ch"), P.book.clamp(0))
            x = _valid_ch(P, w.get("first_explain_ch"), "") if w.get("depth", "light") != "hint" else ""
            out.append({"id": _sid("w", i, w.get("id")), "element": w["element"], "first_hint_ch": h, "first_explain_ch": x,
                        "depth": w.get("depth", "light")})
        return {"elements": out}

    def autofix_plan(self, data, P):
        notes = []
        for w in data["elements"]:
            if w["first_explain_ch"] and P.book.ci(w["first_hint_ch"]) > P.book.ci(w["first_explain_ch"]):
                w["first_hint_ch"] = P.book.clamp(P.book.ci(w["first_explain_ch"]) - 1)
                notes.append(f"world_reveal: '{w['element']}' now hinted in {w['first_hint_ch']} before it is explained")
        return notes

    def planned_events(self, data, P):
        ev = []
        for w in data["elements"]:
            ev.append({"kind": "hint", "ref": w["id"], "ch": w["first_hint_ch"], "module": self.name})
            if w["first_explain_ch"]:
                ev.append({"kind": "explain", "ref": w["id"], "ch": w["first_explain_ch"], "module": self.name})
        return ev

    def brief(self, data, card_events, ch, scene, L, P):
        by = {w["id"]: w for w in data.get("elements", [])}
        out = []
        for e in card_events:
            w = by.get(e.get("ref"))
            if not w:
                continue
            if e["kind"] == "hint":
                out.append(f"WORLD MENTION: {w['element']}. Mention it plainly and say in a sentence what it is; its deeper workings come later.")
            elif e["kind"] == "explain":
                out.append(f"WORLD EXPLAIN: {w['element']}. Explain it plainly and clearly, tied to what is happening. No lecture.")
        return out

    def extract_spec(self, data):
        if not data.get("elements"):
            return ""
        return ("Report hint (element referenced without explanation) and explain (element explained) events, ref = element id, "
                "and explain with ref 'new' for any other invented concept explained here: "
                + json.dumps({w["id"]: w["element"] for w in data["elements"]}))

    def observe(self, data, scene_events, src, L, P):
        n = sum(1 for e in scene_events if e["kind"] == "explain")
        k = cfg()["max_new_concepts_per_scene"]
        if n > k:
            return [issue("info_dump", "major", f"Scene explains {n} world concepts (max {k}).",
                          "Spread the explanations across scenes; keep each plain and brief.", src["chapter"], src["scene"], action="revise")]
        return []

    def check(self, data, E, L, P, mode):
        out, k = [], cfg()["max_new_concepts_per_scene"]
        for w in data.get("elements", []):
            x, h = E.first("explain", w["id"]), E.first("hint", w["id"])
            if x and (h is None or P.book.pos(h) > P.book.pos(x)):
                out.append(issue("iceberg", "major", f"'{w['element']}' is explained before it is ever hinted.",
                                 "Hint at it earlier so the explanation lands on curiosity.", x["ch"], ref=w["id"]))
        per = {}
        for e in E.of("explain"):
            per[(e["ch"], e.get("scene"))] = per.get((e["ch"], e.get("scene")), 0) + 1
        for (ch, sc), n in per.items():
            if n > k:
                out.append(issue("info_dump", "major", f"{ch}:{sc} explains {n} concepts (max {k}).", "Spread the reveals.", ch, sc))
        return out


# ================================================================ prophecy
@register
class Prophecy(CraftModule):
    name = "prophecy"
    tier = "P2"

    def plan_prompt(self, P):
        return f"""If this story uses a prophecy, oracle or binding foretelling, design it; otherwise output {{"applies": false}}.
Freeze its exact wording. Give its true meaning, at least one plausible false reading the characters believe (and the chapter they act on it), and how it is fulfilled (literal, twisted, or self-fulfilling).

PREMISE: {P.premise}
OUTLINE: {json.dumps(P.brief_outline())}

Output ONLY JSON: {{"applies": true, "prophecies": [{{"id": "pr1", "text": "exact words", "true_meaning": "...", "false_readings": ["..."], "first_quote_ch": "ch01", "false_reading_ch": "ch03", "fulfillment_ch": "ch10", "mode": "twisted"}}]}}"""

    def normalize(self, raw, P):
        if isinstance(raw, dict) and raw.get("applies") is False:
            return {}
        out = []
        for i, p in enumerate(_items(raw, "prophecies")):
            if not p.get("text"):
                continue
            out.append({"id": _sid("pr", i, p.get("id")), "text": p["text"].strip(), "true_meaning": p.get("true_meaning", ""),
                        "false_readings": p.get("false_readings", []), "first_quote_ch": _valid_ch(P, p.get("first_quote_ch"), P.book.clamp(0)),
                        "false_reading_ch": _valid_ch(P, p.get("false_reading_ch"), P.book.clamp(P.book.n // 3)),
                        "fulfillment_ch": _valid_ch(P, p.get("fulfillment_ch"), P.book.clamp(P.book.n - 1)),
                        "mode": p.get("mode", "twisted")})
        return {"prophecies": out} if out else {}

    def planned_events(self, data, P):
        ev = []
        for p in data.get("prophecies", []):
            ev.append({"kind": "prophecy_quote", "ref": p["id"], "ch": p["first_quote_ch"], "module": self.name})
            if p["false_readings"]:
                ev.append({"kind": "false_reading", "ref": p["id"], "ch": p["false_reading_ch"], "module": self.name})
            ev.append({"kind": "fulfillment", "ref": p["id"], "ch": p["fulfillment_ch"], "module": self.name})
        return ev

    def brief(self, data, card_events, ch, scene, L, P):
        by = {p["id"]: p for p in data.get("prophecies", [])}
        out = []
        for e in card_events:
            p = by.get(e.get("ref"))
            if not p:
                continue
            if e["kind"] == "prophecy_quote":
                out.append(f'QUOTE THE PROPHECY word for word: "{p["text"]}"')
            elif e["kind"] == "false_reading":
                out.append(f"Characters act on a false reading of the prophecy: {p['false_readings'][0]}. Make it plausible.")
            elif e["kind"] == "fulfillment":
                out.append(f"FULFILL THE PROPHECY ({p['mode']}): {p['true_meaning']}. The false reading collapses.")
        for p in data.get("prophecies", []):
            if P.book.ci(ch) >= P.book.ci(p["first_quote_ch"]) and not any(e.get("ref") == p["id"] and e["kind"] == "prophecy_quote" for e in card_events):
                out.append(f'If anyone quotes the prophecy, use these exact words: "{p["text"]}"')
        return out

    def extract_spec(self, data):
        if not data.get("prophecies"):
            return ""
        return ("Prophecy: whenever it is quoted, report prophecy_quote (ref id) with quote = the quoted words EXACTLY as they appear "
                "in the scene; also false_reading and fulfillment events. Prophecies: " + json.dumps({p["id"]: p["text"] for p in data["prophecies"]}))

    def observe(self, data, scene_events, src, L, P):
        by = {p["id"]: p for p in data.get("prophecies", [])}
        out = []
        for e in scene_events:
            p = by.get(e.get("ref"))
            if e["kind"] == "prophecy_quote" and p and e.get("quote") and norm_text(e["quote"]).strip(' ."') != norm_text(p["text"]).strip(' ."'):
                out.append({**issue("prophecy_wording", "minor", f"Prophecy misquoted; restored canonical wording ({p['id']}).",
                                    "", src["chapter"], src["scene"], p["id"], action="autofix"), "replace": (e["quote"], p["text"])})
                e["data"] = {**(e.get("data") or {}), "fixed": True, "original": e["quote"]}
                e["quote"] = p["text"]
        return out

    def check(self, data, E, L, P, mode):
        out = []
        for p in data.get("prophecies", []):
            for q in E.of("prophecy_quote", p["id"]):
                if q.get("quote") and norm_text(q["quote"]).strip(' ."') != norm_text(p["text"]).strip(' ."'):
                    out.append(issue("prophecy_wording", "critical", f"Prophecy wording drifts in {q['ch']}: '{q['quote']}'.",
                                     f'Use: "{p["text"]}"', q["ch"], q.get("scene"), p["id"]))
            f = E.first("fulfillment", p["id"])
            if f is None:
                out.append(issue("prophecy", "major", f"Prophecy {p['id']} never fulfilled.", "", p["fulfillment_ch"], ref=p["id"]))
            elif p["false_readings"]:
                fr = E.first("false_reading", p["id"])
                if fr is None or not E.before(fr, f):
                    out.append(issue("prophecy", "major", f"Prophecy {p['id']}: no false reading seeded before fulfillment.",
                                     "Let characters misread it earlier.", ref=p["id"]))
        return out


# ================================================================ mentor
@register
class Mentor(CraftModule):
    name = "mentor"
    tier = "P2"

    def plan_prompt(self, P):
        return f"""If the story has a mentor figure, design the mentorship; otherwise output {{"applies": false}}.
The mentor gives lessons with limits, often exits (leaves, fails, dies) so the hero must apply the lessons alone, and never solves the hero's central problem.

CAST: {json.dumps({k: v.get('name') for k, v in P.chars.items()})}
OUTLINE: {json.dumps(P.brief_outline())}

Output ONLY JSON: {{"applies": true, "mentors": [{{"id": "m1", "mentor": "character_id", "limits": "...", "exit_ch": "ch07", "lessons": [{{"id": "l1", "text": "...", "given_ch": "ch02", "applied_ch": "ch10"}}]}}]}}"""

    def normalize(self, raw, P):
        if isinstance(raw, dict) and raw.get("applies") is False:
            return {}
        out = []
        for i, m in enumerate(_items(raw, "mentors")):
            lessons = [{"id": _sid(f"m{i + 1}l", j, l.get("id")), "text": l.get("text", ""),
                        "given_ch": _valid_ch(P, l.get("given_ch"), P.book.clamp(1)),
                        "applied_ch": _valid_ch(P, l.get("applied_ch"), P.book.clamp(P.book.n - 1))}
                       for j, l in enumerate(m.get("lessons", [])) if isinstance(l, dict)]
            out.append({"id": _sid("m", i, m.get("id")), "mentor": slugify(m.get("mentor", "")), "limits": m.get("limits", ""),
                        "exit_ch": _valid_ch(P, m.get("exit_ch"), ""), "lessons": lessons})
        return {"mentors": out} if out else {}

    def planned_events(self, data, P):
        ev = []
        for m in data.get("mentors", []):
            for l in m["lessons"]:
                ev.append({"kind": "lesson_given", "ref": l["id"], "ch": l["given_ch"], "module": self.name})
                ev.append({"kind": "lesson_applied", "ref": l["id"], "ch": l["applied_ch"], "module": self.name})
            if m["exit_ch"]:
                ev.append({"kind": "mentor_exit", "ref": m["id"], "ch": m["exit_ch"], "module": self.name})
        return ev

    def brief(self, data, card_events, ch, scene, L, P):
        out = []
        for m in data.get("mentors", []):
            name = P.chars.get(m["mentor"], {}).get("name", m["mentor"])
            ls = {l["id"]: l for l in m["lessons"]}
            for e in card_events:
                if e["kind"] == "lesson_given" and e["ref"] in ls:
                    out.append(f"MENTOR LESSON ({name}): {ls[e['ref']]['text']}. Show the mentor's limits ({m['limits'] or 'they cannot do it for the hero'}).")
                elif e["kind"] == "lesson_applied" and e["ref"] in ls:
                    out.append(f"THE HERO APPLIES THE LESSON alone: {ls[e['ref']]['text']}.")
                elif e["kind"] == "mentor_exit" and e["ref"] == m["id"]:
                    out.append(f"MENTOR EXIT: {name} leaves the hero to stand alone.")
            if any(e["kind"].startswith("lesson") for e in card_events):
                out.append(f"{name} must not solve the hero's central problem.")
        return out

    def extract_spec(self, data):
        if not data.get("mentors"):
            return ""
        return ("Mentor: report lesson_given / lesson_applied (ref lesson id), mentor_exit (ref mentor id), and mentor_solves if the "
                "mentor resolves the hero's central problem: " + json.dumps({m["id"]: {"mentor": m["mentor"], "lessons":
                                                                                       {l["id"]: l["text"] for l in m["lessons"]}}
                                                                                for m in data["mentors"]}))

    def observe(self, data, scene_events, src, L, P):
        return [issue("mentor_solves", "major", "The mentor solves the hero's problem.", "Give the decisive act to the hero.",
                      src["chapter"], src["scene"], action="revise") for e in scene_events if e["kind"] == "mentor_solves"]

    def check(self, data, E, L, P, mode):
        out = []
        for m in data.get("mentors", []):
            ex = E.first("mentor_exit", m["id"])
            for l in m["lessons"]:
                g, a = E.first("lesson_given", l["id"]), E.first("lesson_applied", l["id"])
                if a is None or g is None or not E.before(g, a):
                    out.append(issue("mentor_lesson", "major", f"Lesson '{l['text']}' is not given and later applied.",
                                     f"Give it by {l['given_ch']}, apply it in {l['applied_ch']}.", ref=l["id"]))
                elif ex and P.book.pos(a) < P.book.pos(ex):
                    out.append(issue("mentor_lesson", "minor", f"Lesson '{l['text']}' applied while the mentor is still present.",
                                     "Let the hero apply it alone after the mentor exits.", a["ch"], ref=l["id"]))
        out += [issue("mentor_solves", "major", f"Mentor solves the hero's problem in {e['ch']}.", "", e["ch"]) for e in E.of("mentor_solves")]
        return out


# ================================================================ tropes
@register
class Tropes(CraftModule):
    name = "tropes"
    tier = "P2"

    def plan_prompt(self, P):
        return f"""List the 4-8 tropes a reader of this genre will expect from this premise (use TV Tropes names). For each choose a treatment: straight, subvert (set up the expectation, then deny it), invert (flip it), or lampshade. Plan at least {cfg()['subversions_per_act']} deliberate subversion or inversion per act, each with a fair setup chapter before its turn chapter. Avoid the most predictable choices.

PREMISE: {P.premise}  GENRE: {P.genre}
OUTLINE: {json.dumps(P.brief_outline())}

Output ONLY JSON: {{"tropes": [{{"id": "tr1", "trope": "Mentor Occupational Hazard", "treatment": "subvert", "setup_ch": "ch02", "turn_ch": "ch06", "note": "how the turn plays"}}]}}"""

    def normalize(self, raw, P):
        out = []
        for i, t in enumerate(_items(raw, "tropes")):
            if not t.get("trope"):
                continue
            tr = t.get("treatment") if t.get("treatment") in ("straight", "subvert", "invert", "lampshade") else "straight"
            out.append({"id": _sid("tr", i, t.get("id")), "trope": t["trope"], "treatment": tr,
                        "setup_ch": _valid_ch(P, t.get("setup_ch"), P.book.clamp(0)),
                        "turn_ch": _valid_ch(P, t.get("turn_ch"), "") if tr != "straight" else "", "note": t.get("note", "")})
        return {"tropes": out}

    def planned_events(self, data, P):
        ev = []
        for t in data["tropes"]:
            ev.append({"kind": "trope_setup", "ref": t["id"], "ch": t["setup_ch"], "module": self.name})
            if t["turn_ch"]:
                ev.append({"kind": "trope_turn", "ref": t["id"], "ch": t["turn_ch"], "module": self.name})
        return ev

    def brief(self, data, card_events, ch, scene, L, P):
        by = {t["id"]: t for t in data.get("tropes", [])}
        out = []
        for e in card_events:
            t = by.get(e.get("ref"))
            if not t:
                continue
            if e["kind"] == "trope_setup":
                out.append(f"TROPE SETUP ({t['trope']}): play the familiar expectation straight here"
                           + (f"; it will be {t['treatment']}ed later, so plant what makes the turn fair." if t["treatment"] != "straight" else "."))
            elif e["kind"] == "trope_turn":
                out.append(f"TROPE TURN ({t['treatment']} {t['trope']}): {t['note']}. It must follow from the earlier setup.")
        return out

    def extract_spec(self, data):
        if not data.get("tropes"):
            return ""
        return "Report trope_setup / trope_turn (ref trope id) when they happen: " + json.dumps({t["id"]: t["trope"] for t in data["tropes"]})

    def check(self, data, E, L, P, mode):
        out = []
        turned_acts = set()
        for t in data.get("tropes", []):
            if t["treatment"] in ("subvert", "invert"):
                s, u = E.first("trope_setup", t["id"]), E.first("trope_turn", t["id"])
                if u is None:
                    out.append(issue("trope_subversion", "major", f"Planned {t['treatment']} of '{t['trope']}' never turns.",
                                     f"Deliver the turn in {t['turn_ch']}.", ref=t["id"]))
                elif s is None or not E.before(s, u):
                    out.append(issue("trope_subversion", "major", f"Unfair {t['treatment']} of '{t['trope']}': no setup before the turn.",
                                     "Plant the expectation first.", u["ch"], ref=t["id"]))
                else:
                    turned_acts.add(P.book.act_of(u["ch"]))
        if data.get("tropes"):
            if all(t["treatment"] == "straight" for t in data["tropes"]):
                out.append(issue("trope_homogeneity", "minor", "Every trope is played straight; the book will feel predictable.",
                                 "Subvert or invert at least one per act."))
            elif mode == "plan" and cfg()["subversions_per_act"]:
                miss = [a for a in (1, 2, 3) if a not in turned_acts]
                if miss:
                    out.append(issue("trope_homogeneity", "minor", f"No subversion or inversion in act(s) {miss}.", ""))
        return out
