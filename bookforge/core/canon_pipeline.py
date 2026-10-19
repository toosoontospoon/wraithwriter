"""Canon pipeline: extract -> integrate -> deconflict -> resolve, run after every scene.

Order per scene:
  1. cheap code checks (unknown names, POV person drift)
  2. LLM extraction into ExtractionResult (schema validated, quotes verified)
  3. deconfliction against plan + canon (LLM judge, skipped for exact repeats)
  4. resolution policy: consistent / new_compatible -> canon;
     conflict -> revision task (plan wins) | plan amendment (canon wins) | human review queue
  5. continuity + Chekhov thread updates
"""
from __future__ import annotations

import json
import re
from collections import Counter

from bookforge.config import load_config
from bookforge.core.canon import Canon, fact_id
from bookforge.core.llm import LLMError, llm_call
from bookforge.core.schema import DeconflictResult, ExtractionResult, slugify

COMMON_CAPS = set("""
I A An The And But Or If When Then Than That This These Those There Here He She It They We You His Her
Its Their Our Your Him Them Me My Mine What Who Whom Whose Which Where Why How Yes No Not Now Just
Still Even Only Once After Before Above Below Somewhere Nothing Everything Something Someone Everyone
Anyone Nobody Every Each Some Any All Both Few Many Most Other Another Such For From With Without Into
Onto Upon Over Under Through Across Around Between Behind Beyond Inside Outside Near Far Again Maybe
Perhaps Okay Oh Ah Hey Well Mister Sir Madam Lady Lord Captain Master Miss Mrs Mr Ms Dr Doctor Father
Mother Dad Mom Mum Papa Mama Uncle Aunt God Gods Chapter Scene Monday Tuesday Wednesday Thursday Friday
Saturday Sunday January February March April May June July August September October November December
Three Two One Four Five Six Seven Eight Nine Ten First Second Third Last Next Kid Boy Girl Man Woman
Don Can Could Would Should Will Shall Might Must Let Get Got Go Come Look Listen Stop Wait Please Thank
Thanks Good Bad Dear Rule Rules Storm Pressure Steady Run Hold Move Down Up Out Off On In At By To Of
""".split())

PERSON_RX = {"first": re.compile(r"\b(I|me|my|mine|myself)\b"),
             "third": re.compile(r"\b(he|she|him|her|his|hers|they|them)\b", re.I)}
_DIALOGUE = re.compile(r'"[^"\n]*"|\u201c[^\u201d]*\u201d')


# ---------------------------------------------------------------- seeding from the plan
def seed_from_plan(ctx, canon: Canon | None = None) -> Canon:
    """Load the outline, cast and world into canon as *planned* entities, facts and threads."""
    canon = canon or ctx.canon()
    chars, world, outline = ctx.load_characters(), ctx.load_world(), ctx.load_outline()
    for cid, c in chars.items():
        canon.add_entity(cid, c["name"], "character", "planned", c.get("aliases", []),
                         c.get("role", ""), importance="critical" if c.get("role") in (
                             "protagonist", "antagonist") else "major")
        key = c.get("role") in ("protagonist", "antagonist", "love interest", "deuteragonist")
        if c.get("motivation"):
            canon.add_fact("motive", [cid], f"{c['name']} wants: {c['motivation']}", "planned",
                           "critical" if key else "major", origin="plan")
        if c.get("secret"):
            canon.add_fact("secret", [cid], f"{c['name']} secret: {c['secret']}", "planned", "critical",
                           origin="plan")
        for other, dyn in (c.get("relationships") or {}).items():
            if other.startswith("_"):
                continue
            canon.add_fact("relationship", [cid, slugify(other)], f"{c['name']} and {other}: {dyn}",
                           "planned", "major", origin="plan")
        if c.get("age"):
            canon.add_fact("character_trait", [cid], f"{c['name']} is {c['age']} years old", "planned",
                           "major", origin="plan")
    for loc in world.get("key_locations", []):
        canon.add_entity(loc["id"], loc["name"], "location", "planned", description=loc.get("description", ""))
    for r in world.get("core_rules", []):
        canon.add_fact("world_rule", [], r, "planned", "critical", origin="plan")
    for ch in outline:
        for ev in ch.get("key_events", []):
            f = canon.add_fact("plot_point", [], ev, "planned", "major", origin="plan")
            f["planned_in"] = ch["id"]
        if ch.get("ends_with"):
            f = canon.add_fact("plot_point", [], f"{ch['id']} ends with: {ch['ends_with']}", "planned",
                               "critical", origin="plan")
            f["planned_in"] = ch["id"]
        for s in ch.get("setups", []):
            t = canon.open_thread(s, None, "major", origin="plan")
            t["setup_chapter"] = ch["id"]
        for p in ch.get("payoffs", []):
            for t in canon.data["threads"].values():
                if _similar(t["description"], p):
                    t["payoff_by"] = t.get("payoff_by") or ch["id"]
    canon.save("seed from plan")
    return canon


def _similar(a: str, b: str) -> bool:
    wa = set(re.findall(r"[a-z]{4,}", a.lower()))
    wb = set(re.findall(r"[a-z]{4,}", b.lower()))
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.6


# ---------------------------------------------------------------- 1. cheap code checks
def code_checks(text: str, scene: dict, canon: Canon, extra_known: set[str] | None = None) -> dict:
    known = set(canon.name_index()) | {k.lower() for k in (extra_known or set())}
    caps = Counter()
    sample = {}
    for m in re.finditer(r"(?<![.!?\"']\s)(?<!^)\b([A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})?)\b", text, re.M):
        name = m.group(1)
        first = name.split()[0]
        if first in COMMON_CAPS:
            continue
        if name.lower() in known or first.lower() in known:
            continue
        caps[name] += 1
        sample.setdefault(name, text[max(0, m.start() - 60): m.end() + 60].replace("\n", " "))
    # names seen at sentence starts only still count if repeated
    for m in re.finditer(r"\b([A-Z][a-z]{2,})\b", text):
        n = m.group(1)
        if n in COMMON_CAPS or n.lower() in known or n in caps:
            continue
        if len(re.findall(r"\b" + n + r"\b", text)) >= 3:
            caps[n] = len(re.findall(r"\b" + n + r"\b", text))
            sample.setdefault(n, text[max(0, m.start() - 60): m.end() + 60].replace("\n", " "))
    unknown = [{"name": n, "count": c, "sample": sample.get(n, "")} for n, c in caps.most_common()]
    narration = _DIALOGUE.sub(" ", text)
    first = len(PERSON_RX["first"].findall(narration))
    third = len(PERSON_RX["third"].findall(narration))
    want = scene.get("person", "third")
    pov_issue = None
    total = first + third
    if total >= 10:
        if want == "third" and first / total > 0.35:
            pov_issue = f"Narration is in first person ({first} first-person pronouns) but scene is planned third person."
        elif want == "first" and first / total < 0.1:
            pov_issue = "Narration is in third person but scene is planned first person."
    return {"unknown_names": unknown, "pov_issue": pov_issue,
            "pronouns": {"first": first, "third": third}}


# ---------------------------------------------------------------- 2. extraction
def _entity_table(canon: Canon) -> str:
    return "\n".join(f"- {e['id']}: {e['name']} ({e['type']})"
                     + (f" aka {', '.join(e['aliases'])}" if e.get("aliases") else "")
                     for e in canon.entities.values())


def extract(text: str, scene: dict, src: dict, canon: Canon, checks: dict, raw_dir=None) -> ExtractionResult:
    unknown = ", ".join(u["name"] for u in checks["unknown_names"]) or "(none)"
    threads = "\n".join(f"- {t['id']}: {t['description']}" for t in canon.open_threads()) or "(none)"
    prompt = f"""You are a continuity clerk. Extract every story fact from this scene that a later chapter could reference or contradict.

KNOWN ENTITIES (use these exact ids):
{_entity_table(canon)}

NAMES IN THE TEXT THAT ARE NOT IN THE CAST: {unknown}
For each, either link it to a known id in "aliases" (if it is the same person/place under another name) or list it in "new_entities" with a new snake_case id.

OPEN THREADS (setups awaiting payoff):
{threads}

SCENE PLAN: {json.dumps({k: scene.get(k) for k in ('pov', 'location', 'characters_present', 'action')})}

SCENE TEXT ({src['chapter']} scene {src.get('scene')}):
\"\"\"
{text}
\"\"\"

Rules:
- One atomic claim per fact. "quote" must be copied verbatim from the text (5-25 words).
- type: plot_point | motive | secret | relationship | object | location | world_rule | timeline | scene_detail | character_trait | state_change | setup | payoff
- importance: critical (would break the plot if contradicted later), major (matters across chapters), local (color).
- confidence: 0-1, how clearly the text states it.
- setups are planted details that demand a later payoff (Chekhov's gun). payoffs resolve an open thread; put its id in threads_resolved.
- state_changes: where each present entity ends the scene, objects gained/lost, what they learned, flags (injured, captured; prefix "-" to clear).

Output ONLY JSON: {{"aliases": [{{"name": "...", "entity": "id"}}], "facts": [{{"type": "...", "entities": ["id"], "claim": "...", "quote": "...", "confidence": 0.9, "importance": "major"}}], "new_entities": [{{"id": "...", "name": "...", "type": "character|location|object|organization|concept", "description": "...", "importance": "local"}}], "state_changes": [{{"entity": "id", "location": "...", "gained": [], "lost": [], "learned": [], "flags": []}}], "threads_opened": [{{"description": "...", "importance": "major", "payoff_by": null}}], "threads_resolved": ["thread id"]}}"""
    res = llm_call(prompt, role="extract", schema=ExtractionResult, max_tokens=12000, temperature=0.1,
                   raw_dir=raw_dir)
    flat = re.sub(r"\s+", " ", text)
    for f in res.facts:
        q = re.sub(r"\s+", " ", f.quote).strip().strip('"')
        i = flat.find(q) if q else -1
        if i < 0:
            f.confidence = round(f.confidence * 0.6, 3)
            f.span = None
        else:
            f.span = [i, i + len(q)]
    return res


# ---------------------------------------------------------------- 3. deconfliction
def _plan_view(ctx, chapter_id: str) -> list[dict]:
    out = []
    for ch in ctx.load_outline():
        if ch["id"] >= chapter_id:
            out.append({"id": ch["id"], "title": ch["title"], "summary": ch.get("summary", ""),
                        "key_events": ch.get("key_events", []), "ends_with": ch.get("ends_with", "")})
    return out[:6]


def deconflict(ctx, canon: Canon, facts: list, src: dict, raw_dir=None) -> DeconflictResult:
    from bookforge.core.schema import Verdict
    verdicts, pending = [], []
    for i, f in enumerate(facts):
        fid = fact_id(f.claim, f.entities)
        if fid in canon.facts and canon.facts[fid]["status"] in ("planned", "established"):
            verdicts.append(Verdict(fact_index=i, verdict="consistent", conflicts_with=[fid], confidence=1.0))
        else:
            pending.append(i)
    if not pending:
        return DeconflictResult(verdicts=verdicts)
    ents = {e for i in pending for e in facts[i].entities}
    cands = canon.facts_for(ents, statuses=("planned", "established"))
    cands += [f for f in canon.active_facts() if f["importance"] == "critical" and f not in cands]
    cand_txt = "\n".join(f"- {f['id']} [{f['status']}, {f['origin']}, {f['importance']}"
                         + (f", planned_in {f['planned_in']}" if f.get("planned_in") else "")
                         + f"] {f['claim']}" for f in cands[:120]) or "(none)"
    new_txt = "\n".join(f"{i}. [{facts[i].type}, {facts[i].importance}] {facts[i].claim}  (quote: \"{facts[i].quote}\")"
                        for i in pending)
    chars = {cid: {k: c.get(k) for k in ("name", "role", "motivation", "secret", "age", "arc")}
             for cid, c in ctx.load_characters().items()}
    prompt = f"""You are the continuity judge for a novel in progress. Compare NEW FACTS just written in {src['chapter']} scene {src.get('scene')} against the PLAN and existing CANON.

CHARACTER PLAN:
{json.dumps(chars, indent=1)}

OUTLINE (current and upcoming chapters):
{json.dumps(_plan_view(ctx, src['chapter']), indent=1)}

CANON AND PLANNED FACTS:
{cand_txt}

NEW FACTS:
{new_txt}

For each new fact give a verdict:
- consistent: already implied by the plan or canon (list the matching fact ids in conflicts_with).
- new_compatible: new information that contradicts nothing. If it is a setup that the plan must pay off, or it should be foreshadowed or echoed later, add plan_updates for FUTURE chapters only.
- conflict: contradicts the plan or canon. Put the contradicted fact ids (or "plan:chNN") in conflicts_with. Write "fix": the exact change to the scene text that would restore the plan. Set new_is_better=true only if the new detail is clearly a better story choice than the plan; then add plan_updates that rewrite future outline bullets to fit it (action add_beat | replace_text | add_setup | add_payoff, with old/new text).
confidence = how sure you are of the verdict (0-1).

Output ONLY JSON: {{"verdicts": [{{"fact_index": 0, "verdict": "consistent|new_compatible|conflict", "conflicts_with": [], "explanation": "...", "fix": "", "new_is_better": false, "confidence": 0.9, "plan_updates": [{{"chapter": "ch05", "action": "add_beat", "old": "", "new": "..."}}]}}]}}"""
    try:
        res = llm_call(prompt, role="deconflict", schema=DeconflictResult, max_tokens=12000,
                       temperature=0.1, raw_dir=raw_dir)
        got = {v.fact_index for v in res.verdicts}
        verdicts += [v for v in res.verdicts if v.fact_index in pending]
        missing = [i for i in pending if i not in got]
    except LLMError:
        missing = pending
    for i in missing:  # judge failed: never silently canonize
        verdicts.append(Verdict(fact_index=i, verdict="conflict", confidence=0.0,
                                explanation="deconflict judge returned no verdict"))
    return DeconflictResult(verdicts=verdicts)


# ---------------------------------------------------------------- 4. resolution
def apply_plan_updates(ctx, updates, from_chapter: str) -> list[dict]:
    """Apply plan updates to chapters AFTER from_chapter; returns the applied updates."""
    outline = ctx.load_outline()
    applied = []
    for u in updates:
        ch = next((c for c in outline if c["id"] == u.chapter), None)
        if ch is None or ch["id"] <= from_chapter:
            continue
        if u.action == "add_beat" and u.new:
            ch["key_events"].append(u.new)
        elif u.action == "add_setup" and u.new:
            ch["setups"].append(u.new)
        elif u.action == "add_payoff" and u.new:
            ch["payoffs"].append(u.new)
        elif u.action == "replace_text" and u.old:
            blob = json.dumps(ch)
            if u.old not in blob:
                continue
            ch.clear()
            ch.update(json.loads(blob.replace(json.dumps(u.old)[1:-1], json.dumps(u.new)[1:-1])))
        else:
            continue
        ch.setdefault("amendments", []).append(f"{u.action}: {u.old + ' -> ' if u.old else ''}{u.new}")
        applied.append(u.model_dump())
    if applied:
        ctx.save_outline(outline)
    return applied


def resolve(ctx, canon: Canon, res: ExtractionResult, dec: DeconflictResult, src: dict) -> dict:
    cfg = load_config()["canon"]
    report = {"consistent": 0, "new": 0, "revision_tasks": [], "amendments": [], "review": []}
    for v in dec.verdicts:
        f = res.facts[v.fact_index]
        fsrc = {**src, "quote": f.quote, "span": getattr(f, "span", None)}
        conf = min(v.confidence, f.confidence)
        targets = [canon.facts[t] for t in v.conflicts_with if t in canon.facts]
        if v.verdict == "consistent":
            canon.add_fact(f.type, f.entities, f.claim, "established", f.importance, f.confidence, fsrc)
            for t in targets:
                if t["status"] == "planned":
                    canon.set_status(t["id"], "established", f"realized in {src['chapter']}")
                if fsrc not in t["provenance"]:
                    t["provenance"].append(fsrc)
            report["consistent"] += 1
            continue
        if v.verdict == "new_compatible":
            nf = canon.add_fact(f.type, f.entities, f.claim, "established", f.importance, f.confidence, fsrc)
            if f.type == "setup":
                canon.open_thread(f.claim, fsrc, f.importance)
            if v.plan_updates:
                applied = apply_plan_updates(ctx, v.plan_updates, src["chapter"])
                if applied:
                    report["amendments"].append(canon.add_amendment(
                        {"kind": "propagate", "fact": nf["id"], "updates": applied,
                         "reason": v.explanation, "source": fsrc}))
            report["new"] += 1
            continue
        # ---- conflict
        against_prose = any(t["status"] == "established" and t["origin"] != "plan" and
                            any(p.get("chapter") != src["chapter"] for p in t["provenance"]) for t in targets)
        item = {"fact": f.model_dump(), "source": fsrc, "conflicts_with": v.conflicts_with,
                "explanation": v.explanation, "fix": v.fix, "new_is_better": v.new_is_better,
                "confidence": conf, "plan_updates": [u.model_dump() for u in v.plan_updates]}
        canon_wins = cfg["conflict_policy"] == "canon_wins" or (cfg["allow_canon_wins"] and v.new_is_better)
        if conf < cfg["review_confidence"] or (f.importance == "critical" and conf < cfg["critical_auto_confidence"]) \
                or (against_prose and canon_wins):
            item["why_review"] = ("low confidence" if conf < cfg["review_confidence"] else
                                  "would rewrite text already established in earlier chapters"
                                  if against_prose and canon_wins else "high-stakes conflict")
            canon.add_fact(f.type, f.entities, f.claim, "contradicted", f.importance, f.confidence, fsrc)
            report["review"].append(canon.enqueue_review(item))
        elif canon_wins:
            report["amendments"].append(plan_amendment(ctx, canon, f, fsrc, targets, v))
        else:
            canon.add_fact(f.type, f.entities, f.claim, "contradicted", f.importance, f.confidence, fsrc)
            fix = v.fix or f"Rewrite so it agrees with: {'; '.join(t['claim'] for t in targets) or v.explanation}"
            report["revision_tasks"].append(canon.add_revision_task(fsrc, fix, v.explanation, f.model_dump()))
    return report


def plan_amendment(ctx, canon: Canon, f, fsrc: dict, targets: list[dict], v) -> dict:
    """Canon wins: promote the new fact, retire what it contradicts, rewrite future plan."""
    nf = canon.add_fact(f.type, f.entities, f.claim, "established", f.importance, f.confidence, fsrc)
    for t in targets:
        canon.supersede(t["id"], nf["id"], v.explanation)
    applied = apply_plan_updates(ctx, v.plan_updates, fsrc["chapter"])
    chars = ctx.load_characters()
    touched = False
    for e in f.entities:
        if e in chars:
            chars[e].setdefault("canon_notes", []).append(f"{fsrc['chapter']}: {f.claim}")
            touched = True
    if touched:
        ctx.save_characters(chars)
    return canon.add_amendment({"kind": "canon_wins", "fact": nf["id"], "supersedes": [t["id"] for t in targets],
                                "updates": applied, "reason": v.explanation, "source": fsrc})


# ---------------------------------------------------------------- 5. full scene pass
def process_scene(ctx, chapter_id: str, scene: dict, text: str, step_dir=None, chunk=None) -> dict:
    canon = ctx.canon()
    src = {"chapter": chapter_id, "scene": scene.get("scene_number"), "chunk": chunk}
    canon.retract_source(chapter_id, scene.get("scene_number"))  # idempotent re-runs
    checks = code_checks(text, scene, canon)
    res = extract(text, scene, src, canon, checks, raw_dir=step_dir)
    for a in res.aliases:
        if a.entity in canon.entities:
            canon.add_entity(a.entity, canon.entities[a.entity]["name"], aliases=[a.name])
    for e in res.new_entities:
        canon.add_entity(e.id, e.name, e.type, "established", description=e.description,
                         source=src, importance=e.importance)
    dec = deconflict(ctx, canon, res.facts, src, raw_dir=step_dir)
    report = resolve(ctx, canon, res, dec, src)
    # unknown names the extractor could not place, repeated enough to matter
    idx = canon.name_index()
    for u in checks["unknown_names"]:
        if u["name"].lower() not in idx and u["name"].split()[0].lower() not in idx and u["count"] >= 3:
            report["revision_tasks"].append(canon.add_revision_task(
                {**src, "quote": u["sample"]},
                f"'{u['name']}' is not in the cast or canon. Replace with the planned character or remove.",
                "unknown name (code check)"))
    if checks["pov_issue"]:
        report["revision_tasks"].append(canon.add_revision_task(
            {**src, "quote": ""}, checks["pov_issue"] + " Rewrite the narration in the planned person.",
            "POV drift (code check)"))
    for sc in res.state_changes:
        canon.update_continuity(sc.entity, src, sc.location, sc.gained, sc.lost, sc.learned, sc.flags)
    for t in res.threads_opened:
        canon.open_thread(t.description, src, t.importance, t.payoff_by)
    for tid in res.threads_resolved:
        canon.resolve_thread(tid, src)
    for f in res.facts:
        if f.type == "payoff":
            canon.resolve_thread(f.claim, src)
    canon.save(f"scene {chapter_id}:{scene.get('scene_number')}")
    report.update({"checks": checks, "facts_extracted": len(res.facts),
                   "revision_tasks": [t["id"] for t in report["revision_tasks"]],
                   "review": [q["id"] for q in report["review"]],
                   "amendments": [a["id"] for a in report["amendments"]]})
    if step_dir:
        (step_dir / f"canon_scene{scene.get('scene_number')}.json").write_text(json.dumps(
            {"extraction": res.model_dump(), "verdicts": dec.model_dump(), "report": report}, indent=2, default=str))
    return report


# ---------------------------------------------------------------- human review
def resolve_review(ctx, item_id, decision, fix=None):
    cn = ctx.canon()
    q = next((x for x in cn.data["review_queue"] if x["id"] == item_id), None)
    if q is None:
        raise KeyError(f"No review item {item_id}")
    from bookforge.core.schema import ExtractedFact, PlanUpdate, Verdict
    f = ExtractedFact.model_validate(q["fact"])
    if decision == "plan":
        cn.add_revision_task(q["source"], fix or q.get("fix") or q["explanation"], "human review: plan wins", q["fact"])
    elif decision == "canon" and q.get("craft"):
        from bookforge.craft import engine as _craft
        _craft.apply_amendment(ctx, q["craft"])
    elif decision == "canon":
        targets = [cn.facts[t] for t in q["conflicts_with"] if t in cn.facts]
        v = Verdict(fact_index=0, verdict="conflict", explanation=q["explanation"], new_is_better=True,
                    plan_updates=[PlanUpdate.model_validate(u) for u in q.get("plan_updates", [])])
        plan_amendment(ctx, cn, f, q["source"], targets, v)
    else:
        fid = fact_id(f.claim, f.entities)
        if fid in cn.facts:
            cn.set_status(fid, "established", "human review: accepted")
    q["status"] = f"resolved:{decision}"
    cn.save(f"review {item_id} -> {decision}")
