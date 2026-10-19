"""Offline fake LLM. Dispatches on role; tests override behaviour with hooks."""
import json
import re

CAST = [
    {"id": "finn_cooper", "name": "Finn Cooper", "role": "protagonist", "age": 16, "motivation": "earn a berth as engineer",
     "voice": "quick, eager", "relationships": {"zara_blackwind": "mentor"}},
    {"id": "zara_blackwind", "name": "Zara Blackwind", "role": "supporting", "age": 30, "motivation": "keep the engines alive",
     "voice": "dry, clipped", "relationships": "Teaches Finn"},
    {"id": "elias_thorne", "name": "Elias Thorne", "role": "antagonist", "age": 50, "motivation": "sell the ship",
     "secret": "he owes the guild", "voice": "smooth"},
]
WORLD = {"genre_tone": "steampunk adventure",
         "core_rules": ["Aether engines need fresh water every day"],
         "key_locations": {"Harbor": "a sooty skyport", "Engine Room": "hot and loud"},  # dict on purpose (old bug)
         "supernatural_rules": "No magic.",
         "sensory_palette": "brass and steam",
         "lore": "Old ships were sung aloft."}
CHAPTERS = [
    {"id": "whispers-from-the-tide", "act": "Act 1", "title": "Aboard", "summary": "Finn joins the crew.",
     "dramatic_question": "Will Finn be hired?", "target_words": 600, "key_events": ["Finn is hired"],
     "ends_with": "the ship lifts off", "setups": ["the brass key under the floorboard"], "payoffs": []},
    {"id": "fractured_prophecies", "act": "Act 2", "title": "Storm", "summary": "A storm hits.",
     "dramatic_question": "Will the ship survive?", "target_words": 600, "key_events": ["the storm strikes"],
     "ends_with": "the engine fails", "setups": [], "payoffs": []},
    {"id": "ch01", "act": "Act 3", "title": "Home", "summary": "Finn returns.", "dramatic_question": "Will Finn stay?",
     "target_words": 600, "key_events": ["Finn opens the lock"], "ends_with": "Finn goes home",
     "setups": [], "payoffs": ["the brass key under the floorboard"]},
]

ID_RX = re.compile(r'"id": "(ch\d\d)"')


class FakeLLM:
    def __init__(self):
        self.calls = []          # (role, prompt)
        self.hooks = {}          # role -> fn(prompt, system) -> value or None (fall through)
        self.fail = {}           # (role, key) -> remaining failures

    def __call__(self, role, system, prompt, json_mode):
        self.calls.append((role, prompt, system))
        if role in self.hooks:
            out = self.hooks[role](prompt, system)
            if out is not None:
                return out
        return getattr(self, "r_" + role.replace("-", "_"), self.r_default)(prompt, system, json_mode)

    def count(self, role, contains=""):
        return sum(1 for r, p, _ in self.calls if r == role and contains in p)

    # ------------------------------------------------------------ defaults
    def r_default(self, prompt, system, json_mode):
        return {} if json_mode else "ok"

    def r_web_search(self, prompt, system, json_mode):
        return {"text": "research", "sources": []}

    def r_brainstorm(self, prompt, system, json_mode):
        if json_mode:
            return {"genre": "steampunk", "themes": ["coming of age"], "why_they_work": "fun"}
        return "A warm, plain-spoken adventure voice in close third person, past tense."

    def r_outline(self, prompt, system, json_mode):
        if "story architect" in prompt:
            return {"chapters": CHAPTERS}
        if "character psychologist" in prompt:
            return {"characters": CAST}
        if "scene architect" in prompt:
            cid = ID_RX.search(prompt).group(1)
            return {"id": "WRONG-ID", "scenes": [
                {"scene_number": 1, "pov": "Finn Cooper", "location": "Harbor",
                 "characters_present": ["Finn Cooper", "Zara Blackwind"], "action": "Finn works.",
                 "beats": ["Finn Cooper checks the boiler", "Zara Blackwind gives an order"], "target_words": 300},
                {"scene_number": 2, "pov": "Finn Cooper", "location": "Engine Room",
                 "characters_present": ["Finn Cooper", "Elias Thorne"], "action": "Finn argues.",
                 "beats": ["Elias Thorne makes a demand", "Finn Cooper refuses"], "target_words": 300}]}
        return {}

    def r_worldbuild(self, prompt, system, json_mode):
        return WORLD

    def r_character_design(self, prompt, system, json_mode):
        return {"characters": {"finn_cooper": {"name": "Finn Cooper", "physical": "freckled",
                                               "current_state": {"location": "Harbor"}}}}

    def r_world_design(self, prompt, system, json_mode):
        return {"core_rules": ["Aether engines need fresh water every day", "Airships cannot fly above the clouds"]}

    def r_critique(self, prompt, system, json_mode):
        if "developmental editor" in prompt:
            return {"overall_score": 8, "ready_to_write": True}
        return {k: 7 for k in ("style_score", "engagement_score", "pacing_score", "arc_integration_score",
                                "mode_balance_score", "world_integration_score", "narration_balance_score",
                                "consistency_score")} | {"weak_spots": [], "suggested_fixes": ["tighten"],
                                                         "strongest_moments": []}

    def r_plan(self, prompt, system, json_mode):
        card = json.loads(prompt.split("SCENE CARD: ", 1)[1].split("\n", 1)[0])
        return {"beats": card["beats"]}

    def r_write(self, prompt, system, json_mode):
        m = re.search(r"of scene (\d+) of chapter (ch\d\d)", prompt)
        beats = re.findall(r"^\d+\. (.+)$", re.split(r"BEATS TO WRITE NOW[^\n]*:", prompt, maxsplit=1)[1].split("\n\n")[0], re.M)
        return "\n\n".join(f"{b}. The wind came hard off the river and the deck creaked under every step."
                           for b in beats)

    def r_summary(self, prompt, system, json_mode):
        if json_mode:
            return {"title": "The Brass Key", "blurb": "A boy and a ship."}
        return "Things happened in order."

    def r_extract(self, prompt, system, json_mode):
        m = re.search(r"SCENE TEXT \((ch\d\d) scene (\d+)\)", prompt)
        ch, sc = m.group(1), int(m.group(2))
        facts = [{"type": "scene_detail", "entities": ["finn_cooper"], "claim": f"Finn feels the deck creak in {ch} scene {sc}",
                  "quote": "the deck creaked under every step", "confidence": 0.9, "importance": "local"}]
        res = {"facts": facts, "state_changes": [{"entity": "finn_cooper", "location": f"deck {ch}"}]}
        if ch == "ch01" and sc == 1:
            res["threads_opened"] = [{"description": "the brass key under the floorboard", "importance": "major"}]
        if ch == "ch03" and sc == 1:
            res["threads_resolved"] = ["the brass key under the floorboard"]
        return res

    def r_deconflict(self, prompt, system, json_mode):
        n = len(re.findall(r"^\d+\. \[", prompt.split("NEW FACTS:", 1)[1], re.M))
        return {"verdicts": [{"fact_index": i, "verdict": "new_compatible", "confidence": 0.9} for i in range(n)]}

    def r_revision(self, prompt, system, json_mode):
        if "PARAGRAPH TO REVISE:" in prompt:
            para = prompt.split("PARAGRAPH TO REVISE:\n", 1)[1].split("\n\nNEXT PARAGRAPH", 1)[0]
            return para.replace("ship's cook", "ship's mechanic")
        return prompt.split('"""\n', 1)[1].split('\n"""', 1)[0]

    def r_edit(self, prompt, system, json_mode):
        return prompt.split("CHAPTER DRAFT:\n", 1)[1].split("\n\nYOUR JOB", 1)[0].replace("ship's cook", "ship's mechanic")

    def r_deai(self, prompt, system, json_mode):
        para = prompt.split("PARAGRAPH TO FIX:\n", 1)[1].split("\n\nPROBLEMS FOUND", 1)[0]
        para = re.sub(r"\btapestry\b", "pattern", para)
        return re.sub(r"was not (\w+), but (\w+)", r"was \2", para)

    def r_audit(self, prompt, system, json_mode):
        return {"issues": [], "passed": True}
