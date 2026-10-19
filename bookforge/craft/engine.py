"""Craft engine: plan -> schedule -> validate; per-scene brief; extract and observe; audit."""
from __future__ import annotations

import json

from bookforge.core.io import atomic_write_json, read_json, stable_hash
from bookforge.core.llm import llm_call
from bookforge.craft.base import LOAD_BEARING, REGISTRY, SETUP_KINDS, Events, cfg, issue, module_names, norm_text
from bookforge.craft.ledger import CraftLedger, PlanContext

# kinds whose items for one owner (ref prefix before ':') must stay in plan order inside a chapter
ORDERED_KINDS = {"arc_stage", "trust_shift", "thread_touch"}
BRIEF_HEADER = "CRAFT BRIEF (the plan for this scene; follow it silently, never name these techniques in the prose):"


def ledger(ctx) -> CraftLedger:
    return CraftLedger(ctx.state_dir)


def modules():
    return [REGISTRY[n] for n in module_names()]


# ================================================================ planning
def plan_all(ctx, step_dir=None, force=False) -> list[dict]:
    """Run every craft planner (cached per module), schedule events into scene cards, validate."""
    if not cfg()["enabled"]:
        return []
    P = PlanContext.from_ctx(ctx)
    if not P.book.chapters:
        return []
    L = ledger(ctx)
    work = ctx.state_dir / "craft" / "plan_work"
    notes, data_so_far = [], {}
    base_key = stable_hash([P.premise, P.genre, P.framework, P.brief_outline(), sorted(P.chars),
                            [(c["id"], c.get("setups"), c.get("payoffs")) for c in P.book.chapters]])
    for m in modules():
        key = stable_hash([base_key, m.name])
        rec = read_json(work / f"{m.name}.json", None)
        if rec and rec.get("key") == key and not force:
            raw = rec["raw"]
        else:
            prompt = m.plan_prompt(P)
            raw = {}
            if prompt:
                try:
                    raw = llm_call(prompt, role=m.plan_role, json_mode=True, max_tokens=6000, raw_dir=step_dir)
                except Exception as e:  # craft planning must never block the book
                    notes.append(issue("planner", "minor", f"{m.name} planner failed: {e}"))
            atomic_write_json(work / f"{m.name}.json", {"key": key, "raw": raw})
        data = m.normalize(raw, P)
        for n in m.autofix_plan(data, P):
            notes.append(issue("autofix", "minor", n))
        data_so_far[m.name] = data
    cards = data_so_far.get("structure", {}).pop("cards", {})
    L.data.update({"framework": P.framework, "modules": data_so_far, "scene_cards": cards})
    events = [e for m in modules() for e in m.planned_events(data_so_far[m.name], P)]
    notes += schedule(events, P, L)
    L.data["planned"] = events
    L.data["plan_issues"] = notes + validate_plan(L, P)
    L.data["plan_key"] = base_key
    L.save("craft plan")
    seed_canon(ctx, L)
    return L.data["plan_issues"]


def schedule(events, P, L) -> list[dict]:
    """Assign each event a scene inside its chapter, respecting the per-scene key-item cap."""
    cap = int(cfg()["max_key_items_per_scene"])
    load: dict[str, int] = {}
    last: dict = {}
    warnings = []
    scenes_of = {c["id"]: [int(s.get("scene_number", 1)) for s in (c.get("scenes") or [])] or [1] for c in P.book.chapters}
    order = sorted(range(len(events)), key=lambda i: (P.book.ci(events[i].get("ch")),
                                                      0 if events[i]["kind"] in SETUP_KINDS else 1))
    for i in order:
        e = events[i]
        e.setdefault("id", "e_" + stable_hash([e["kind"], e.get("ref"), e.get("ch"), i])[:10])
        if e.get("scene"):
            sc = e["scene"]
        else:
            opts = scenes_of.get(e.get("ch"), [1])
            chain = (e["ch"], e["kind"], str(e.get("ref", "")).split(":")[0]) if e["kind"] in ORDERED_KINDS else None
            if chain and chain in last:
                opts = [s for s in opts if s >= last[chain]] or opts[-1:]
            lb = e["kind"] in LOAD_BEARING
            if e["kind"] in SETUP_KINDS:
                sc = min(opts, key=lambda s: ((load.get(f"{e['ch']}:{s}", 0) >= cap) if lb else 0,
                                              load.get(f"{e['ch']}:{s}", 0) if lb else 0, s))
            elif e["kind"] == "beat":
                sc = opts[-1] if e["ref"] not in ("opening_image", "hook", "setup", "ordinary_world") else opts[0]
            else:
                sc = min(opts, key=lambda s: ((load.get(f"{e['ch']}:{s}", 0) >= cap) if lb else 0,
                                              load.get(f"{e['ch']}:{s}", 0) if lb else 0, -s))
            e["scene"] = sc
            if chain:
                last[chain] = sc
        if e["kind"] in LOAD_BEARING:
            k = f"{e['ch']}:{e['scene']}"
            load[k] = load.get(k, 0) + 1
            if load[k] == cap + 1:
                warnings.append(issue("scene_cap", "minor", f"Scene {k} carries more than {cap} key craft items.",
                                      "Spread items across scenes or chapters (craft.max_key_items_per_scene).",
                                      e["ch"], e["scene"]))
    return warnings


def validate_plan(L, P) -> list[dict]:
    E = Events(L.data["planned"], P.book)
    out = []
    for m in modules():
        for i in m.check(L.module(m.name), E, L, P, "plan"):
            out.append({**i, "module": m.name, "phase": "plan"})
    return out


def seed_canon(ctx, L):
    """Mirror load-bearing secrets into the canon ledger as planned facts (so deconflict sees them)."""
    canon = ctx.canon()
    added = 0
    for t in L.module("mystery").get("truths", []):
        f = canon.add_fact("secret", [], t["statement"], "planned", "critical", origin="plan")
        f["planned_in"], f["craft_ref"] = t.get("reveal_ch", ""), f"truth:{t['id']}"
        added += 1
    if added:
        canon.save("craft seed")


# ================================================================ writing
_WARNED = set()


def plan_status(ctx, chapter_id: str) -> str:
    """'off' | 'missing' | 'stale' | 'ok'. Stale = the craft plan has no cards for this chapter's
    current scene numbers (the outline was replanned after the craft plan was made)."""
    if not cfg()["enabled"]:
        return "off"
    L = ledger(ctx)
    if not L.has_plan:
        return "missing"
    ch = ctx.chapter(chapter_id) or {}
    nums = [s.get("scene_number") for s in ch.get("scenes") or []]
    if nums and not all(L.card(chapter_id, n) for n in nums):
        return "stale"
    return "ok"


def brief(ctx, chapter, scene) -> str:
    st = plan_status(ctx, chapter["id"])
    if st != "ok":
        if st in ("missing", "stale") and (st, chapter["id"]) not in _WARNED:
            _WARNED.add((st, chapter["id"]))
            print(f"[warn] craft plan {st} for {chapter['id']}: no craft brief. Run `bookforge craft plan` "
                  "or set craft.enabled: false")
        return ""
    L = ledger(ctx)
    P = PlanContext.from_ctx(ctx, L.data.get("framework"))
    ch, sn = chapter["id"], int(scene.get("scene_number", 1))
    evs = L.planned_for(ch, sn)
    lines = []
    for m in modules():
        try:
            lines += m.brief(L.module(m.name), [e for e in evs if e.get("module") == m.name], ch, sn, L, P)
        except Exception as e:  # a brief must never stop the writer
            lines.append(f"(craft brief error in {m.name}: {e})")
    return (BRIEF_HEADER + "\n" + "\n".join(f"- {x}" for x in lines)) if lines else ""


# ================================================================ extraction and per-scene checks
def _verify_quote(q, text):
    return not q or norm_text(q)[:120] in norm_text(text)


def process_scene(ctx, chapter_id, scene, text, step_dir=None) -> dict:
    L = ledger(ctx)
    if not L.has_plan or not cfg()["enabled"]:
        return {"events": 0, "issues": [], "text": text}
    P = PlanContext.from_ctx(ctx, L.data.get("framework"))
    sn = int(scene.get("scene_number", 1))
    src = {"chapter": chapter_id, "scene": sn}
    L.retract(chapter_id, sn)
    specs = [s for s in (m.extract_spec(L.module(m.name)) for m in modules()) if s]
    due = [{k: e.get(k) for k in ("kind", "ref", "module")} for e in L.planned_for(chapter_id, sn)]
    raw = llm_call(f"""You are a story analyst. Read the scene and report craft events that ACTUALLY happen in it, each with a verbatim quote (5-25 words) from the text. Report only what the text shows.

PLANNED FOR THIS SCENE (check whether each happened; also report unplanned events): {json.dumps(due)}

WHAT TO REPORT:
{chr(10).join('- ' + s for s in specs)}

SCENE TEXT ({chapter_id} scene {sn}):
\"\"\"
{text}
\"\"\"

Output ONLY JSON: {{"events": [{{"kind": "...", "ref": "...", "quote": "...", "data": {{}}}}]}}""",
                   role="craft_extract", json_mode=True, max_tokens=3000, raw_dir=step_dir)
    events = raw.get("events", []) if isinstance(raw, dict) else []
    kept, dropped = [], 0
    for e in events:
        if not isinstance(e, dict) or not e.get("kind"):
            continue
        if not _verify_quote(e.get("quote", ""), text):
            dropped += 1
            continue
        kept.append(L.add_realized({"kind": e["kind"], "ref": e.get("ref"), "ch": chapter_id, "scene": sn,
                                    "quote": e.get("quote", ""), "data": e.get("data") or {}}))
    issues = []
    for m in modules():
        for i in m.observe(L.module(m.name), kept, src, L, P):
            issues.append({**i, "module": m.name})
    text_out = apply_actions(ctx, L, issues, src, text)
    L.save(f"scene {chapter_id}:{sn}")
    rep = {"events": len(kept), "dropped_unverified": dropped, "issues": issues, "text": text_out}
    if step_dir:
        (step_dir / f"craft_scene{sn}.json").write_text(json.dumps({k: v for k, v in rep.items() if k != "text"},
                                                                    indent=2, default=str))
    return rep


def apply_actions(ctx, L, issues, src, text):
    canon = ctx.canon()
    for i in issues:
        act = i.get("action")
        if act == "revise":
            quote = next((e.get("quote", "") for e in L.data["realized"] if e.get("ref") == i.get("ref")
                          and e.get("ch") == src["chapter"] and e.get("scene") == src["scene"]), "")
            canon.add_revision_task({**src, "quote": quote}, i["fix"] or i["description"], f"craft: {i['check']}")
        elif act == "review":
            canon.enqueue_review({"fact": {"type": "plot_point", "entities": [], "claim": i["description"],
                                           "quote": "", "confidence": 0.5, "importance": "critical"},
                                  "source": src, "explanation": i["description"], "fix": i["fix"], "conflicts_with": [],
                                  "why_review": f"craft: {i['check']}", "plan_updates": [],
                                  "craft": {"module": i.get("module"), "check": i["check"], "ref": i.get("ref"),
                                            **(i.get("amend") or {})}})
        elif act == "autofix" and i.get("replace"):
            old, new = i["replace"]
            if old and old in text:
                text = text.replace(old, new)
    canon.save(f"craft {src['chapter']}:{src['scene']}")
    return text


def apply_amendment(ctx, craft_item: dict) -> bool:
    """Human chose 'canon wins' on a craft review item: move the planned event to where the prose put it."""
    L = ledger(ctx)
    move = craft_item.get("move")
    if not move:
        return False
    for e in L.data["planned"]:
        if e.get("kind") == move["kind"] and e.get("ref") == move["ref"]:
            e["ch"], e["scene"] = move["ch"], move.get("scene")
    L.data["amendments"].append({"kind": "craft_canon_wins", **craft_item})
    L.save(f"craft amendment {craft_item.get('ref')}")
    return True


# ================================================================ audit
def audit(ctx) -> list[dict]:
    L = ledger(ctx)
    if not L.has_plan or not cfg()["enabled"]:
        return []
    P = PlanContext.from_ctx(ctx, L.data.get("framework"))
    canon = ctx.canon()
    realized = list(L.data["realized"])
    for m in modules():
        if hasattr(m, "realized_from_canon"):
            realized += m.realized_from_canon(L.module(m.name), canon)
    E = Events(realized, P.book)
    out = []
    for m in modules():
        for i in m.check(L.module(m.name), E, L, P, "audit"):
            out.append({**i, "module": m.name, "phase": "audit", "craft": True})
    return out


# ================================================================ rolling refinement (DOME pattern)
REFINABLE = ("goal", "conflict", "disaster", "reaction", "dilemma", "decision", "value_shift", "tension")


def refine_next(ctx, chapter_id) -> bool:
    """After a chapter is written, refine the NEXT chapter's scene cards from what actually happened.

    Only card details move; load-bearing items (clues, reveals, arc beats, ...) stay where the plan put
    them, so validators keep holding. Cached by the realized events of this chapter.
    """
    if not cfg()["enabled"] or not cfg()["rolling_refine"]:
        return False
    L = ledger(ctx)
    if not L.has_plan:
        return False
    ids = [c["id"] for c in ctx.load_outline()]
    if chapter_id not in ids or ids.index(chapter_id) == len(ids) - 1:
        return False
    nxt = ids[ids.index(chapter_id) + 1]
    happened = [{k: e.get(k) for k in ("kind", "ref", "scene", "data")} for e in L.data["realized"] if e["ch"] == chapter_id]
    cards = {k: v for k, v in L.data["scene_cards"].items() if k.startswith(nxt + ":")}
    key = stable_hash([happened, L.data["plan_key"]])
    if L.data["refined"].get(nxt) == key:
        return False
    res = llm_call(f"""ROLLING REFINE. Chapter {chapter_id} has been written. Here is what actually happened (craft events, scene outcomes and decisions):
{json.dumps(happened)}

Refine the scene cards for the next chapter {nxt} so goals follow from the last decisions and tension continues the curve. Keep scene types unless a change is clearly needed. Do not add or move clues, reveals or arc beats.
CARDS: {json.dumps(cards)}

Output ONLY JSON: {{"cards": [{{"scene": 1, "goal": "", "conflict": "", "disaster": "", "reaction": "", "dilemma": "", "decision": "", "value_shift": {{}}, "tension": 5}}]}}""",
                   role="craft_plan", json_mode=True, max_tokens=2500)
    for c in (res.get("cards", []) if isinstance(res, dict) else []):
        if not isinstance(c, dict):
            continue
        k = f"{nxt}:{c.get('scene')}"
        if k in L.data["scene_cards"]:
            for f in REFINABLE:
                if c.get(f) not in (None, "", {}):
                    if f == "tension":
                        try:
                            L.data["scene_cards"][k][f] = max(0, min(10, int(c[f])))
                        except (TypeError, ValueError):
                            pass
                    else:
                        L.data["scene_cards"][k][f] = c[f]
    L.data["refined"][nxt] = key
    L.save(f"refine {nxt}")
    return True


def retract_chapter(ctx, chapter_id):
    L = ledger(ctx)
    if L.retract(chapter_id):
        L.save(f"retract {chapter_id}")
