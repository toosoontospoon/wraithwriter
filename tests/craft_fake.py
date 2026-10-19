"""Scripted craft planner + extractor for offline end-to-end tests.

The extractor 'realizes' exactly the planned events of each scene (quoting the scene's
first words), plus scene outcome and the card's tension, so a faithful book passes the audit.
Tests mutate ``drop``/``extra`` to simulate a writer that misses or breaks the plan.
"""
import json
import re

PLANS = {
    "story structure editor": {"cards": [
        {"chapter": "ch01", "scene": 2, "try_fail": {"goal": "main", "attempt": 1, "outcome": "no_and", "cause": "flaw"}},
        {"chapter": "ch02", "scene": 2, "try_fail": {"goal": "main", "attempt": 2, "outcome": "no", "cause": "opponent"}},
        {"chapter": "ch03", "scene": 2, "try_fail": {"goal": "main", "attempt": 3, "outcome": "yes"}}]},
    "promises this book makes": {"promises": [
        {"id": "p_genre", "kind": "genre", "statement": "a steampunk sky adventure", "payoff_ch": "ch03"}]},
    "Design character arcs": {"arcs": [
        {"character": "finn_cooper", "arc_type": "positive", "lie": "I must earn my place by never failing",
         "want": "a berth", "need": "to accept help", "truth": "crews carry each other", "flaw": "pride"}]},
    "foreshadowing this book needs": {"items": []},
}
P1_PLANS = {
    "mystery plotter": {"applies": True, "main_truth": "t1", "culprit": "elias_thorne",
                        "truths": [{"id": "t1", "statement": "Elias sabotaged the engine", "reveal_ch": "ch03",
                                    "revealed_to": ["finn_cooper"]}],
                        "clues": [{"id": "c1", "points_to": "t1", "description": "grease on Elias's cuff", "plant_ch": "ch01",
                                   "disguise": "listed with other stains"}],
                        "red_herrings": [{"id": "h1", "false_claim": "Zara broke the valve", "suspect": "zara_blackwind",
                                          "plant_ch": "ch01", "cleared_ch": "ch02", "clearing_method": "her alibi"}],
                        "suspects": [{"entity": "elias_thorne", "first_appear_ch": "ch01"}],
                        "knowledge": [{"who": "zara_blackwind", "truth": "t1", "learns_ch": "ch02"}]},
    "Design the plot threads": {"threads": [
        {"id": "a", "label": "A", "mice_type": "E", "description": "save the ship", "open_ch": "ch01", "close_ch": "ch03",
         "chapters": ["ch01", "ch02", "ch03"], "max_gap": 1},
        {"id": "b", "label": "B", "mice_type": "C", "description": "Finn learns to accept help", "open_ch": "ch01",
         "close_ch": "ch03", "parent": "a", "max_gap": 2,
         "convergences": [{"id": "cv1", "ch": "ch03", "with": "a", "supplies": "Zara's trust"}]}]},
    "Design the magic system": {"applies": True, "hardness": 0.9, "rules": [
        {"id": "r1", "kind": "power", "text": "aether song lifts metal", "shown_ch": "ch01"},
        {"id": "r2", "kind": "cost", "text": "singing burns the singer's voice", "applies_to": ["r1"], "shown_ch": "ch01"}],
        "solutions": [{"id": "s1", "ch": "ch03", "description": "Finn sings the ship up", "rules": ["r1"], "cost": "r2"}]},
}


class CraftScript:
    def __init__(self, ctx_getter):
        self.ctx = ctx_getter
        self.plans = json.loads(json.dumps(PLANS))
        self.drop = set()          # (kind, ref) never realized
        self.extra = {}            # "ch:scene" -> list of extra events
        self.outcome = {}          # "ch:scene" -> result override

    def plan(self, prompt, system):
        for marker, resp in self.plans.items():
            if marker in prompt:
                return resp
        return {}

    def extract(self, prompt, system):
        from bookforge.craft import engine
        m = re.search(r"SCENE TEXT \((ch\d\d) scene (\d+)\)", prompt)
        ch, sc = m.group(1), int(m.group(2))
        text = prompt.split('"""\n', 1)[1].split('\n"""', 1)[0]
        quote = " ".join(text.split()[:8])
        due = json.loads(prompt.split("PLANNED FOR THIS SCENE (check whether each happened; also report unplanned events): ", 1)[1]
                         .split("\n", 1)[0])
        card = engine.ledger(self.ctx()).card(ch, sc)
        L = engine.ledger(self.ctx())
        pdata = {(e["kind"], e.get("ref")): e.get("data") or {} for e in L.planned_for(ch, sc)}
        evs = [{"kind": d["kind"], "ref": d["ref"], "quote": quote, "data": pdata.get((d["kind"], d["ref"]), {})}
               for d in due if (d["kind"], d["ref"]) not in self.drop and d["kind"] != "prophecy_quote"]
        for d in due:
            if d["kind"] == "prophecy_quote":
                pr = next((p for p in L.module("prophecy").get("prophecies", []) if p["id"] == d["ref"]), None)
                if pr and pr["text"] in text:
                    evs.append({"kind": "prophecy_quote", "ref": d["ref"], "quote": pr["text"]})
        key = f"{ch}:{sc}"
        res = self.outcome.get(key) or (card.get("try_fail") or {}).get("outcome") or (
            "no_and" if card.get("scene_type") == "scene" else "no")
        evs.append({"kind": "scene_outcome", "data": {"result": res, "decision": "go on" if card.get("scene_type") == "sequel" else "",
                                                      "cause": "flaw"}})
        evs.append({"kind": "tension", "data": {"value": card.get("tension", 5)}})
        evs += self.extra.get(key, [])
        return {"events": evs}


def install(fake, project):
    cs = CraftScript(lambda: project.__class__(project.project_dir))
    fake.hooks["craft_plan"] = cs.plan
    fake.hooks["craft_extract"] = cs.extract
    return cs

P2_PLANS = {
    "relationship arcs": {"relationships": [{"id": "finn_zara", "a": "finn_cooper", "b": "zara_blackwind", "start_trust": 0,
                                             "stages": [{"name": "friction", "chapter": "ch01", "trust_delta": -1},
                                                        {"name": "alliance", "chapter": "ch02", "trust_delta": 2},
                                                        {"name": "bond", "chapter": "ch03", "trust_delta": 2}]}]},
    "hollow-iceberg": {"elements": [{"id": "w1", "element": "aether engines", "first_hint_ch": "ch01", "first_explain_ch": "ch02"}]},
    "prophecy, oracle": {"applies": True, "prophecies": [{"id": "pr1", "text": "When iron sings, the sky shall fall.",
                                                         "true_meaning": "Finn's song drops the ship to safety",
                                                         "false_readings": ["the ship will crash"], "first_quote_ch": "ch01",
                                                         "false_reading_ch": "ch02", "fulfillment_ch": "ch03"}]},
    "mentor figure": {"applies": True, "mentors": [{"id": "m1", "mentor": "zara_blackwind", "exit_ch": "ch02",
                                                    "lessons": [{"id": "l1", "text": "listen to the engine", "given_ch": "ch01",
                                                                 "applied_ch": "ch03"}]}]},
    "tropes a reader": {"tropes": [{"id": "tr1", "trope": "Chosen One", "treatment": "subvert", "setup_ch": "ch01",
                                    "turn_ch": "ch03", "note": "the song was Zara's all along"}]},
}
