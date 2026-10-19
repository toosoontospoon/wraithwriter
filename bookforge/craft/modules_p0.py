"""P0 craft modules: structure (framework, scene/sequel, tension, try/fail), promises, arcs, foreshadowing."""
from __future__ import annotations

import json
import math

from bookforge.core.schema import slugify
from bookforge.craft.base import CraftModule, Events, cfg, issue, norm_text, register
from bookforge.craft.frameworks import ARC_STAGES, NEGATIVE_ARC_STAGES, beats, tension_target

FAIL = {"no", "no_and", "yes_but"}


def _valid_ch(P, ch, default):
    return ch if ch in P.book.index else default


def _sid(prefix, i, raw=None):
    return slugify(str(raw)) if raw else f"{prefix}{i + 1}"


def pearson(a, b):
    n = len(a)
    if n < 3:
        return 1.0
    ma, mb = sum(a) / n, sum(b) / n
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if va == 0 or vb == 0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb)


def flat_runs(vals, run=3):
    """Indices where `run` consecutive values are identical."""
    return [i for i in range(len(vals) - run + 1) if len(set(vals[i:i + run])) == 1]


# ================================================================ structure
@register
class Structure(CraftModule):
    """Framework beats, Swain scene/sequel cards, tension curve, try/fail cycles."""
    name = "structure"

    def plan_prompt(self, P):
        b = beats(P.framework)
        return f"""You are a story structure editor. Framework: {P.framework}. Beats with target positions (% of book): {json.dumps(b)}.
Map each beat to a chapter, then design a card for every scene using Dwight Swain's scene/sequel:
a SCENE has goal, conflict, disaster (it ends in "no, and" or "yes, but" unless it is the finale); a SEQUEL has reaction, dilemma, decision, and the decision sets the next scene's goal.
Give each scene a value shift (value, from, to) and a target tension 0-10 that rises in a sawtooth: spikes, partial releases, the climax the highest point in the final quarter.
Mark the protagonist's attempts at the main goal with try_fail (attempt number and outcome); at least two fail before the one that succeeds, and failures come from a flaw, missing skill or a smarter opponent.

PREMISE: {P.premise}
OUTLINE: {json.dumps(P.brief_outline())}

Output ONLY JSON: {{"beats": [{{"name": "midpoint", "chapter": "ch05", "description": "..."}}], "cards": [{{"chapter": "ch01", "scene": 1, "scene_type": "scene|sequel", "goal": "", "conflict": "", "disaster": "", "reaction": "", "dilemma": "", "decision": "", "value_shift": {{"value": "safety", "from": "secure", "to": "threatened"}}, "tension": 5, "try_fail": {{"goal": "main", "attempt": 1, "outcome": "no_and", "cause": "flaw"}}}}]}}"""

    def normalize(self, raw, P):
        raw = raw if isinstance(raw, dict) else {}
        given = {slugify(b.get("name", "")): b for b in raw.get("beats", []) if isinstance(b, dict)}
        out_beats = []
        for name, pct in beats(P.framework):
            g = given.get(name, {})
            out_beats.append({"name": name, "target_pct": pct, "chapter": _valid_ch(P, g.get("chapter"), P.book.ch_at(pct)),
                              "description": g.get("description", "")})
        scenes = list(P.book.scenes())
        cards = {}
        n = max(1, len(scenes) - 1)
        for i, (ch, sn) in enumerate(scenes):
            last_in_ch = sn == max(s for c, s in scenes if c == ch)
            # chapter-final scenes are SCENEs (end on a disaster hook); alternate backwards from there
            k = max(s for c, s in scenes if c == ch) - sn
            stype = "scene" if (k % 2 == 0 or i == 0) else "sequel"
            t = tension_target(i / n) + (0.8 if stype == "scene" else -0.8)
            cards[f"{ch}:{sn}"] = {"scene_type": stype, "tension": max(0, min(10, round(t))),
                                   "goal": "", "conflict": "", "disaster": "", "reaction": "", "dilemma": "",
                                   "decision": "", "value_shift": {}, "try_fail": None, "chapter_end": last_in_ch}
        for c in raw.get("cards", []):
            if not isinstance(c, dict):
                continue
            key = f"{c.get('chapter')}:{int(c.get('scene') or 0)}"
            if key in cards:
                upd = {k: v for k, v in c.items() if k not in ("chapter", "scene") and v not in (None, "", {}, [])}
                if "tension" in upd:
                    try:
                        upd["tension"] = max(0, min(10, int(upd["tension"])))
                    except (TypeError, ValueError):
                        upd.pop("tension")
                if upd.get("scene_type") not in ("scene", "sequel"):
                    upd.pop("scene_type", None)
                cards[key].update(upd)
        self._unflatten(cards)
        return {"framework": P.framework, "beats": out_beats, "cards": cards}

    @staticmethod
    def _unflatten(cards):
        keys = list(cards)
        vals = [cards[k]["tension"] for k in keys]
        for i in flat_runs(vals):
            k = keys[i + 2]
            cards[k]["tension"] = max(0, min(10, cards[k]["tension"] + (1 if cards[k]["tension"] < 9 else -1)))
            vals[i + 2] = cards[k]["tension"]

    def planned_events(self, data, P):
        return [{"kind": "beat", "ref": b["name"], "ch": b["chapter"], "module": self.name} for b in data["beats"]]

    def brief(self, data, card_events, ch, scene, L, P):
        card = L.card(ch, scene)
        out = []
        if card and cfg()["scene_sequel"]:
            if card["scene_type"] == "scene":
                out.append(f"SHAPE: SCENE. Goal: {card.get('goal') or 'the POV character wants something concrete now'}. "
                           f"Obstacle: {card.get('conflict') or 'active opposition'}. "
                           f"End in disaster: {card.get('disaster') or 'a setback that makes things worse (no, and) or a costly win (yes, but)'}.")
            else:
                out.append(f"SHAPE: SEQUEL. React to {card.get('reaction') or 'the last setback'}; weigh the dilemma "
                           f"({card.get('dilemma') or 'no good options'}); end on a clear decision: "
                           f"{card.get('decision') or 'a choice that sets the next goal'}.")
            prev = self._prev_card(L, P, ch, scene)
            out.append(f"TENSION: target {card['tension']}/10" + (f" (previous scene {prev['tension']}/10)" if prev else "") + ".")
            vs = card.get("value_shift") or {}
            if vs.get("value"):
                out.append(f"VALUE SHIFT: {vs['value']} goes from {vs.get('from', '?')} to {vs.get('to', '?')}.")
            tf = card.get("try_fail")
            if isinstance(tf, dict) and tf.get("outcome"):
                out.append(f"TRY/FAIL: attempt {tf.get('attempt', '?')} at the main goal; outcome {tf['outcome']}"
                           + (f", caused by {tf['cause']}" if tf.get("cause") else "") + ". Make the attempt intelligent.")
        for e in card_events:
            if e["kind"] == "beat":
                b = next((x for x in data["beats"] if x["name"] == e["ref"]), {})
                out.append(f"STRUCTURE BEAT: {e['ref'].replace('_', ' ')}. {b.get('description', '')}".strip())
        return out

    @staticmethod
    def _prev_card(L, P, ch, scene):
        keys = [f"{c}:{s}" for c, s in P.book.scenes()]
        k = f"{ch}:{scene}"
        if k in keys and keys.index(k) > 0:
            return L.data["scene_cards"].get(keys[keys.index(k) - 1])
        return None

    def extract_spec(self, data):
        return ('Always report one {"kind": "scene_outcome", "data": {"result": "yes|no|yes_but|no_and", "decision": "...", '
                '"cause": "flaw|skill|opponent|random|none"}} and one {"kind": "tension", "data": {"value": 0-10}}. '
                f'Report {{"kind": "beat", "ref": <name>}} if the scene clearly delivers a structure beat from: '
                f'{[b["name"] for b in data.get("beats", [])]}.')

    def observe(self, data, scene_events, src, L, P):
        card = L.card(src["chapter"], src["scene"])
        out = []
        oc = next((e for e in scene_events if e["kind"] == "scene_outcome"), None)
        if not card or not oc or not cfg()["scene_sequel"]:
            return out
        d = oc.get("data") or {}
        planned_win = (card.get("try_fail") or {}).get("outcome") == "yes"
        if card["scene_type"] == "scene" and d.get("result") == "yes" and not planned_win \
                and P.book.pct(src["chapter"]) < 85:
            out.append(issue("scene_shape", "major", "Scene planned to end in a setback ends in a clean success.",
                             f"Rewrite the ending so it lands as: {card.get('disaster') or 'no, and / yes, but'}.",
                             src["chapter"], src["scene"], action="revise"))
        if card["scene_type"] == "sequel" and not (d.get("decision") or "").strip():
            out.append(issue("scene_shape", "major", "Sequel ends without a decision.",
                             f"End on the decision: {card.get('decision') or 'a concrete choice that sets the next goal'}.",
                             src["chapter"], src["scene"], action="revise"))
        return out

    def check(self, data, E: Events, L, P, mode):
        out = []
        tol = cfg()["beat_tolerance_pct"]
        cards = L.data["scene_cards"]
        keys = [f"{c}:{s}" for c, s in P.book.scenes() if f"{c}:{s}" in cards]
        # ---- beats
        for b in data.get("beats", []):
            ev = E.first("beat", b["name"])
            if ev is None:
                if mode == "audit":
                    sev = "major" if b["name"] in ("midpoint", "all_is_lost", "climax", "finale", "catalyst") else "minor"
                    out.append(issue("framework_beat", sev, f"Beat '{b['name']}' not found in the prose.",
                                     f"Deliver it near {b['target_pct']}% ({b['chapter']}).", b["chapter"], ref=b["name"]))
                continue
            # chapter granularity: allow one chapter of slack on short books
            slack = max(tol, 100 / max(1, P.book.n - 1))
            if abs(P.book.pct(ev["ch"]) - b["target_pct"]) > slack:
                out.append(issue("framework_beat", "minor",
                                 f"Beat '{b['name']}' lands at {P.book.pct(ev['ch']):.0f}% (target {b['target_pct']}%).",
                                 "Move the beat or rebalance chapter lengths.", ev["ch"], ref=b["name"]))
        # ---- tension
        if mode == "plan":
            vals = [cards[k]["tension"] for k in keys]
        else:
            tmap = {f"{e['ch']}:{e.get('scene')}": (e.get("data") or {}).get("value") for e in E.of("tension")}
            pairs = [(cards[k]["tension"], tmap[k]) for k in keys if isinstance(tmap.get(k), (int, float))]
            vals = [r for _, r in pairs]
            if len(pairs) >= 5:
                r = pearson([p for p, _ in pairs], vals)
                if r < cfg()["tension_min_correlation"]:
                    out.append(issue("tension_curve", "major", f"Realized tension tracks the plan poorly (r={r:.2f}).",
                                     "Raise stakes in planned spikes; release after disasters."))
        if len(vals) >= 5:
            for i in flat_runs(vals):
                k = keys[i] if mode == "plan" else None
                out.append(issue("tension_curve", "major", f"Three scenes in a row at tension {vals[i]} (from scene #{i + 1}).",
                                 "Vary intensity: spike or release.", (k or ":").split(":")[0]))
                break
            peak = max(range(len(vals)), key=lambda i: (vals[i], i))
            if peak < 0.75 * (len(vals) - 1):
                out.append(issue("tension_curve", "major", f"Tension peaks at scene #{peak + 1} of {len(vals)}, before the final quarter.",
                                 "Make the climax the most intense sequence."))
        # ---- try/fail and scene/sequel (audit)
        attempts = [(k, cards[k]["try_fail"]) for k in keys if isinstance(cards[k].get("try_fail"), dict)
                    and (cards[k]["try_fail"].get("goal") or "main") == "main"]
        if attempts:
            if mode == "plan":
                outs = [tf.get("outcome") for _, tf in attempts]
            else:
                om = {f"{e['ch']}:{e.get('scene')}": e.get("data") or {} for e in E.of("scene_outcome")}
                outs = [om.get(k, {}).get("result") for k, _ in attempts if k in om]
                for k, _ in attempts:
                    if om.get(k, {}).get("cause") == "random":
                        out.append(issue("try_fail", "minor", "A main-goal failure is caused by chance.",
                                         "Tie the failure to a flaw, missing skill or the opponent.", k.split(":")[0]))
            first_win = next((i for i, o in enumerate(outs) if o == "yes"), len(outs))
            fails = sum(1 for o in outs[:first_win] if o in FAIL)
            if outs and fails < 2:
                out.append(issue("try_fail", "major", f"Only {fails} failed attempt(s) at the main goal before success.",
                                 "Add at least two caused failures before the win."))
        if mode == "audit" and cfg()["scene_sequel"]:
            om = {f"{e['ch']}:{e.get('scene')}": e.get("data") or {} for e in E.of("scene_outcome")}
            seq_no_dec = [k for k in keys if k in om and cards[k]["scene_type"] == "sequel" and not om[k].get("decision")]
            if seq_no_dec:
                out.append(issue("scene_sequel", "major", f"Sequels without a decision: {seq_no_dec}.", "End each sequel on a choice."))
        return out


# ================================================================ promises
@register
class Promises(CraftModule):
    """Sanderson: promise, progress, payoff."""
    name = "promises"

    def plan_prompt(self, P):
        return f"""List the 4-8 promises this book makes to its reader (Sanderson's promise, progress, payoff): genre, tone, the main plot question, the protagonist's arc, any mystery, any magic.
Genre and tone promises are made in the first chapter. Each promise has a payoff chapter and mode (fulfill, expand, or substitute).

PREMISE: {P.premise}  GENRE: {P.genre}
OUTLINE: {json.dumps(P.brief_outline())}

Output ONLY JSON: {{"promises": [{{"id": "p1", "kind": "genre|tone|plot|arc|mystery|magic", "statement": "...", "made_ch": "ch01", "payoff_ch": "chNN", "payoff_mode": "fulfill"}}]}}"""

    def normalize(self, raw, P):
        items = []
        first, last = P.book.clamp(0), P.book.clamp(P.book.n - 1)
        for i, p in enumerate((raw or {}).get("promises", []) if isinstance(raw, dict) else []):
            if not isinstance(p, dict) or not p.get("statement"):
                continue
            kind = p.get("kind", "plot")
            made = first if kind in ("genre", "tone") else _valid_ch(P, p.get("made_ch"), first)
            items.append({"id": _sid("p", i, p.get("id")), "kind": kind, "statement": p["statement"], "made_ch": made,
                          "payoff_ch": _valid_ch(P, p.get("payoff_ch"), last), "payoff_mode": p.get("payoff_mode", "fulfill")})
        return {"promises": items}

    def planned_events(self, data, P):
        ev = []
        for p in data["promises"]:
            a, b = P.book.ci(p["made_ch"]), P.book.ci(p["payoff_ch"])
            ev.append({"kind": "promise_made", "ref": p["id"], "ch": p["made_ch"], "module": self.name})
            for act in sorted({P.book.act_of(P.book.clamp(i)) for i in range(a, b + 1)}):
                mids = [i for i in range(a + 1, b) if P.book.act_of(P.book.clamp(i)) == act]
                if mids:
                    ev.append({"kind": "promise_progress", "ref": p["id"], "ch": P.book.clamp(mids[len(mids) // 2]),
                               "module": self.name})
            ev.append({"kind": "promise_payoff", "ref": p["id"], "ch": p["payoff_ch"], "module": self.name})
        return ev

    def brief(self, data, card_events, ch, scene, L, P):
        by = {p["id"]: p for p in data.get("promises", [])}
        verbs = {"promise_made": "MAKE THIS PROMISE unmistakable", "promise_progress": "SHOW VISIBLE PROGRESS on the promise",
                 "promise_payoff": "PAY OFF the promise"}
        return [f"{verbs[e['kind']]}: {by[e['ref']]['statement']}"
                + (f" (mode: {by[e['ref']]['payoff_mode']})" if e["kind"] == "promise_payoff" else "")
                for e in card_events if e["kind"] in verbs and e["ref"] in by]

    def extract_spec(self, data):
        if not data.get("promises"):
            return ""
        return ('Report promise_made / promise_progress / promise_payoff events (ref = promise id) for: '
                + json.dumps({p["id"]: p["statement"] for p in data["promises"]}))

    def check(self, data, E, L, P, mode):
        out = []
        for p in data.get("promises", []):
            made, pay = E.first("promise_made", p["id"]), E.first("promise_payoff", p["id"])
            if made is None:
                out.append(issue("promise", "major", f"Promise never made: {p['statement']}", f"Establish it in {p['made_ch']}.",
                                 p["made_ch"], ref=p["id"]))
                continue
            if p["kind"] in ("genre", "tone") and P.book.ci(made["ch"]) > 0:
                out.append(issue("promise", "major", f"{p['kind']} promise first made in {made['ch']}, not the opening.",
                                 "Signal it in chapter one.", made["ch"], ref=p["id"]))
            if pay is None or not E.before(made, pay):
                out.append(issue("promise", "major", f"Promise not paid off: {p['statement']}",
                                 f"Pay it off by {p['payoff_ch']}.", p["payoff_ch"], ref=p["id"]))
                continue
            acts = sorted({P.book.act_of(c["id"]) for c in P.book.chapters[P.book.ci(made["ch"]) + 1:P.book.ci(pay["ch"])]})
            got = {P.book.act_of(e["ch"]) for e in E.of("promise_progress", p["id"])}
            for a in acts:
                if a not in got:
                    out.append(issue("promise", "major", f"No progress on '{p['statement']}' in act {a}.",
                                     "Add a visible step toward the promise.", ref=p["id"]))
        return out


# ================================================================ arcs
@register
class Arcs(CraftModule):
    """K.M. Weiland lie/want/need/truth arcs with ordered stage beats."""
    name = "arcs"

    def plan_prompt(self, P):
        cast = [{"id": k, "name": c.get("name"), "role": c.get("role"), "motivation": c.get("motivation", ""),
                 "arc": c.get("arc", "")} for k, c in P.chars.items()]
        return f"""Design character arcs (K.M. Weiland) for each POV or main character: the Lie they believe, the Want it drives, the Need, the Truth, the wound, the flaw, and arc type (positive, flat, negative, corruption).
Pin stages to chapters in order: {[s for s, _ in ARC_STAGES]} (negative arcs end with lie_embraced instead of truth_embraced). The tested_choice is a costly choice at the climax that proves the change.

CAST: {json.dumps(cast)}
OUTLINE: {json.dumps(P.brief_outline())}

Output ONLY JSON: {{"arcs": [{{"character": "id", "arc_type": "positive", "lie": "", "want": "", "need": "", "truth": "", "wound": "", "flaw": "", "climax_choice": "", "stages": [{{"name": "first_doubt", "chapter": "ch03"}}]}}]}}"""

    def normalize(self, raw, P):
        arcs = []
        for a in (raw or {}).get("arcs", []) if isinstance(raw, dict) else []:
            if not isinstance(a, dict) or not a.get("character"):
                continue
            cid = slugify(a["character"])
            neg = a.get("arc_type") in ("negative", "corruption")
            given = {slugify(s.get("name", "")): s.get("chapter") for s in a.get("stages", []) if isinstance(s, dict)}
            stages = [{"name": n, "chapter": _valid_ch(P, given.get(n), P.book.ch_at(pct))}
                      for n, pct in (NEGATIVE_ARC_STAGES if neg else ARC_STAGES)]
            arcs.append({**{k: a.get(k, "") for k in ("arc_type", "lie", "want", "need", "truth", "wound", "flaw",
                                                      "climax_choice")}, "character": cid, "stages": stages})
        return {"arcs": arcs}

    def autofix_plan(self, data, P):
        notes = []
        for a in data["arcs"]:
            chs = [s["chapter"] for s in a["stages"]]
            srt = sorted(chs, key=P.book.ci)
            if chs != srt:
                for s, c in zip(a["stages"], srt):
                    s["chapter"] = c
                notes.append(f"arcs: reordered {a['character']} stage chapters to follow stage order")
        return notes

    def planned_events(self, data, P):
        return [{"kind": "arc_stage", "ref": f"{a['character']}:{s['name']}", "ch": s["chapter"], "module": self.name}
                for a in data["arcs"] for s in a["stages"]]

    GUIDE = {"lie_established": "Show the Lie driving a choice in ordinary life.",
             "first_doubt": "Challenge the Lie for the first time; show doubt through a choice, not a realization speech.",
             "midpoint_glimpse": "Let the character glimpse the Truth and act on it partially.",
             "relapse": "The character falls back on the Lie under pressure, at a cost.",
             "dark_night": "The Lie's cost becomes undeniable; let the character sit in it.",
             "truth_embraced": "The character acts on the Truth for the first time.",
             "lie_embraced": "The character chooses the Lie over the Truth, and it costs them.",
             "tested_choice": "Force a costly choice that proves the change; no speeches about growth."}

    def brief(self, data, card_events, ch, scene, L, P):
        by = {a["character"]: a for a in data.get("arcs", [])}
        out = []
        for e in card_events:
            if e["kind"] != "arc_stage":
                continue
            cid, stage = e["ref"].split(":", 1)
            a = by.get(cid)
            if a:
                name = P.chars.get(cid, {}).get("name", cid)
                out.append(f"ARC BEAT ({name}, {stage.replace('_', ' ')}): Lie '{a['lie']}'; Truth '{a['truth']}'. {self.GUIDE.get(stage, '')}"
                           + (f" Climax choice: {a['climax_choice']}." if stage == "tested_choice" and a.get("climax_choice") else ""))
        return out

    def extract_spec(self, data):
        if not data.get("arcs"):
            return ""
        return ('Report {"kind": "arc_stage", "ref": "<character>:<stage>", "quote": ...} when behavior shows an arc stage. Arcs: '
                + json.dumps({a["character"]: {"lie": a["lie"], "truth": a["truth"], "stages": [s["name"] for s in a["stages"]]}
                              for a in data["arcs"]}))

    def _order(self, data, cid):
        a = next((x for x in data.get("arcs", []) if x["character"] == cid), None)
        return [s["name"] for s in a["stages"]] if a else []

    def observe(self, data, scene_events, src, L, P):
        out = []
        prior = {e["ref"] for e in L.realized_before(P.book, (P.book.ci(src["chapter"]), src["scene"])) if e["kind"] == "arc_stage"}
        prior |= {e.get("ref") for e in scene_events if e["kind"] == "arc_stage"}
        for e in scene_events:
            if e["kind"] != "arc_stage" or ":" not in e.get("ref", ""):
                continue
            cid, stage = e["ref"].split(":", 1)
            order = self._order(data, cid)
            if stage in order and order.index(stage) > 1:
                prev = order[order.index(stage) - 1]
                if f"{cid}:{prev}" not in prior:
                    out.append(issue("arc_order", "major", f"{cid} reaches '{stage}' before '{prev}'.",
                                     f"Soften this beat or plant '{prev}' earlier.", src["chapter"], src["scene"],
                                     e["ref"], action="revise"))
        return out

    def check(self, data, E, L, P, mode):
        out = []
        for a in data.get("arcs", []):
            seen = []
            for s in a["stages"]:
                ev = E.first("arc_stage", f"{a['character']}:{s['name']}")
                if ev is None:
                    sev = "major" if s["name"] in ("tested_choice", "truth_embraced", "lie_embraced", "dark_night") else "minor"
                    out.append(issue("arc_stage", sev, f"{a['character']}: stage '{s['name']}' missing.",
                                     f"Show it near {s['chapter']}.", s["chapter"], ref=f"{a['character']}:{s['name']}"))
                    continue
                if seen and P.book.pos(ev) < P.book.pos(seen[-1][1]):
                    out.append(issue("arc_order", "major", f"{a['character']}: '{s['name']}' happens before '{seen[-1][0]}'.",
                                     "Restore stage order.", ev["ch"], ref=f"{a['character']}:{s['name']}"))
                seen.append((s["name"], ev))
                if s["name"] == "tested_choice" and P.book.pct(ev["ch"]) < 70:
                    out.append(issue("arc_stage", "major", f"{a['character']}: tested choice at {ev['ch']} is too early for a climax.",
                                     "Move the proving choice into the climax.", ev["ch"]))
        return out


# ================================================================ foreshadowing
@register
class Foreshadow(CraftModule):
    """Chekhov's gun with payoff windows (foreshadow, trigger, payoff)."""
    name = "foreshadow"

    def plan_prompt(self, P):
        return f"""List the foreshadowing this book needs (Chekhov's guns): every element planted early that pays off later, especially anything the climax depends on.
Each has a plant chapter, a payoff chapter at least {cfg()['min_setup_gap_chapters']} chapters later for important items, optional trigger chapter, and subtlety (overt or disguised).

PREMISE: {P.premise}
OUTLINE (with chapter setups/payoffs): {json.dumps([{**c, "setups": x.get("setups", []), "payoffs": x.get("payoffs", [])} for c, x in zip(P.brief_outline(), P.book.chapters)])}

Output ONLY JSON: {{"items": [{{"id": "f1", "element": "...", "plant_ch": "ch02", "trigger_ch": "", "payoff_ch": "ch09", "subtlety": "disguised", "importance": "critical|major|minor"}}]}}"""

    def normalize(self, raw, P):
        items, seen = [], set()
        for i, f in enumerate((raw or {}).get("items", []) if isinstance(raw, dict) else []):
            if isinstance(f, dict) and f.get("element") and f.get("plant_ch") in P.book.index:
                items.append(self._item(P, _sid("f", i, f.get("id")), f["element"], f["plant_ch"], f.get("payoff_ch"),
                                        f.get("subtlety", "disguised"), f.get("importance", "major"), f.get("trigger_ch")))
                seen.add(norm_text(f["element"]))
        # derive from the outline's own setups/payoffs (what the canon Chekhov ledger already tracks)
        for c in P.book.chapters:
            for s in c.get("setups") or []:
                if norm_text(s) in seen:
                    continue
                pay = next((d["id"] for d in P.book.chapters if any(norm_text(s) in norm_text(p) or norm_text(p) in norm_text(s)
                                                                    for p in d.get("payoffs") or [])), None)
                items.append(self._item(P, "o_" + slugify(s)[:30], s, c["id"], pay, "disguised", "major", None))
                seen.add(norm_text(s))
        return {"items": items}

    def _item(self, P, fid, element, plant, payoff, subtlety, importance, trigger):
        gap = cfg()["min_setup_gap_chapters"]
        a = P.book.ci(plant)
        b = P.book.ci(payoff) if payoff in P.book.index else min(P.book.n - 1, a + max(gap, P.book.n // 2))
        lo, hi = max(a + (gap if importance != "minor" else 1), b - 1), min(P.book.n - 1, max(b + 1, a + gap))
        return {"id": fid, "element": element, "plant_ch": plant, "trigger_ch": trigger if trigger in P.book.index else "",
                "payoff_ch": P.book.clamp(b), "window": [P.book.clamp(min(lo, hi)), P.book.clamp(hi)],
                "subtlety": subtlety if subtlety in ("overt", "disguised") else "disguised", "importance": importance}

    def autofix_plan(self, data, P):
        notes, gap = [], cfg()["min_setup_gap_chapters"]
        for f in data["items"]:
            a, b = P.book.ci(f["plant_ch"]), P.book.ci(f["payoff_ch"])
            if f["importance"] != "minor" and b - a < gap and b - gap >= 0:
                f["plant_ch"] = P.book.clamp(b - gap)
                f["window"] = self._item(P, f["id"], f["element"], f["plant_ch"], f["payoff_ch"], f["subtlety"],
                                         f["importance"], None)["window"]
                notes.append(f"foreshadow: moved plant of '{f['element']}' to {f['plant_ch']} (min gap {gap})")
        return notes

    def planned_events(self, data, P):
        ev = []
        for f in data["items"]:
            ev.append({"kind": "plant", "ref": f["id"], "ch": f["plant_ch"], "module": self.name,
                       "data": {"subtlety": f["subtlety"]}})
            if f.get("trigger_ch"):
                ev.append({"kind": "trigger", "ref": f["id"], "ch": f["trigger_ch"], "module": self.name})
            ev.append({"kind": "payoff", "ref": f["id"], "ch": f["payoff_ch"], "module": self.name})
        return ev

    def brief(self, data, card_events, ch, scene, L, P):
        by = {f["id"]: f for f in data.get("items", [])}
        out = []
        for e in card_events:
            f = by.get(e.get("ref"))
            if not f:
                continue
            if e["kind"] == "plant":
                out.append(f"PLANT: {f['element']}." + (" Disguise it: mention it in passing among ordinary details. "
                                                          "No narrator comment, no 'would remember', no lingering."
                                                          if f["subtlety"] == "disguised" else " Make it noticeable."))
            elif e["kind"] == "trigger":
                out.append(f"TRIGGER: bring back {f['element']} so its payoff becomes possible.")
            elif e["kind"] == "payoff":
                out.append(f"PAY OFF: {f['element']} (planted in {f['plant_ch']}). Make it matter decisively.")
        return out

    def extract_spec(self, data):
        if not data.get("items"):
            return ""
        return ('Report plant / trigger / payoff events (ref = item id; for plant add data.overt = true if the narration '
                'spotlights it) for: ' + json.dumps({f["id"]: f["element"] for f in data["items"]}))

    def observe(self, data, scene_events, src, L, P):
        by = {f["id"]: f for f in data.get("items", [])}
        prior = {e["ref"] for e in L.realized_before(P.book, (P.book.ci(src["chapter"]), src["scene"])) if e["kind"] == "plant"}
        here = {e["ref"] for e in scene_events if e["kind"] == "plant"}
        out = []
        for e in scene_events:
            f = by.get(e.get("ref"))
            if not f:
                continue
            if e["kind"] == "plant" and (e.get("data") or {}).get("overt") and f["subtlety"] == "disguised":
                out.append(issue("spotlight", "major", f"Disguised setup is spotlighted: {f['element']}.",
                                 "Bury it in a list of ordinary details; cut any narrator comment on it.",
                                 src["chapter"], src["scene"], f["id"], action="revise"))
            if e["kind"] == "payoff" and f["id"] not in prior and f["id"] not in here:
                out.append(issue("payoff_without_setup", "critical", f"Payoff with no earlier setup: {f['element']}.",
                                 f"Plant it in {f['plant_ch']} first, or cut this payoff.",
                                 src["chapter"], src["scene"], f["id"], action="review"))
        return out

    def realized_from_canon(self, data, canon):
        ev = []
        for t in canon.data.get("threads", {}).values():
            for f in data.get("items", []):
                a, b = norm_text(t["description"]), norm_text(f["element"])
                if a and (a in b or b in a):
                    if t.get("setup_source") and t.get("planted"):
                        s = t["setup_source"]
                        ev.append({"kind": "plant", "ref": f["id"], "ch": s.get("chapter"), "scene": s.get("scene"), "via": "canon"})
                    if t.get("payoff_source"):
                        s = t["payoff_source"]
                        ev.append({"kind": "payoff", "ref": f["id"], "ch": s.get("chapter"), "scene": s.get("scene"), "via": "canon"})
        return ev

    def check(self, data, E, L, P, mode):
        out, gap = [], cfg()["min_setup_gap_chapters"]
        for f in data.get("items", []):
            pl, pay = E.first("plant", f["id"]), E.first("payoff", f["id"])
            if pay and (pl is None or not E.before(pl, pay)):
                out.append(issue("payoff_without_setup", "critical", f"'{f['element']}' pays off before (or without) its setup.",
                                 f"Plant it by {f['plant_ch']}.", pay["ch"], ref=f["id"]))
                continue
            if pl is None:
                out.append(issue("foreshadow", "major", f"Setup never planted: {f['element']}.", f"Plant in {f['plant_ch']}.",
                                 f["plant_ch"], ref=f["id"]))
                continue
            if pay is None:
                out.append(issue("foreshadow", "major", f"Setup never paid off: {f['element']}.",
                                 f"Pay off within {f['window'][0]}..{f['window'][1]} or cut the setup.", f["payoff_ch"], ref=f["id"]))
                continue
            lo, hi = P.book.ci(f["window"][0]), P.book.ci(f["window"][1])
            if not lo <= P.book.ci(pay["ch"]) <= hi:
                out.append(issue("payoff_window", "major", f"Payoff of '{f['element']}' in {pay['ch']} is outside its window "
                                 f"{f['window'][0]}..{f['window'][1]}.", "Move the payoff or widen the window by plan amendment.",
                                 pay["ch"], ref=f["id"]))
            if f["importance"] != "minor" and P.book.ci(pay["ch"]) - P.book.ci(pl["ch"]) < gap:
                out.append(issue("payoff_window", "major", f"'{f['element']}' pays off {P.book.ci(pay['ch']) - P.book.ci(pl['ch'])} "
                                 f"chapter(s) after its plant; needs {gap}.", "Plant earlier so the payoff feels earned.",
                                 pay["ch"], ref=f["id"]))
        return out
