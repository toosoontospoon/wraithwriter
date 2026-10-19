"""Post-draft screen + revision packet (saves revision cost).

Per chapter, after a draft and its critique:
  1. free code checks (core/screen_checks.py) against the chapter facts file (core/facts.py);
  2. one Jev call per section (pinned jev-1.13.0) with only the questions that worked, at tuned
     thresholds, plus code-extracted/Jev-judged count rules ("X answers only once");
  3. a compact REVISION PACKET: only flagged sections, the conflicting bible lines and the beat map.
     The expensive editor reads the packet instead of the whole chapter + story pack
     (core/editor.py: edit_packet), and only flagged sections are rewritten and spliced back.
Graceful fallback: no TYPESAFE_API_KEY or a Jev error -> Jev is skipped, code checks still run, and
the packet is marked jev="skipped: <reason>".
"""
from __future__ import annotations

import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bookforge.config import load_config
from bookforge.core import facts as facts_mod
from bookforge.core import jev, screen_checks

TEMPLATE = Path(__file__).resolve().parent.parent / "jev_templates" / "chapter_section_screen.json"
DEFAULTS = {"enabled": False, "jev": True, "model": "jev-1.13.0", "section_words": 600, "workers": 4,
            "cap_usd": 1.0, "skip_edit_if_clean": True, "fallback_full_edit": True,
            "packet_system_files": ["style_bible.md"], "context_paras": 1}
# regex AI-tell checks are candidates only: with Jev they are kept only where Jev agrees
SOFT = {"rule_of_three": "ai_rule_of_three", "mirrored": "ai_mirrored_lines", "epigram_closer": "ai_tidy_closer"}
_PH = re.compile(r"\{\{(\w+)\}\}")


def cfg() -> dict:
    return {**DEFAULTS, **(load_config().get("screen") or {})}


# ------------------------------------------------------------------ sections
def split_sections(text: str, target_words: int = 600) -> list[dict]:
    """Scenes (* * *) then paragraph groups of about target_words. Rebuild with join_sections."""
    from bookforge.steps._common import split_scenes
    out = []
    for si, sc in enumerate(split_scenes(text), 1):
        paras = [p.strip() for p in re.split(r"\n\s*\n", sc) if p.strip()]
        groups, cur, n = [], [], 0
        for p in paras:
            w = len(p.split())
            if cur and n + w > target_words:
                groups.append(cur)
                cur, n = [], 0
            cur.append(p)
            n += w
        if cur:
            if groups and n < target_words // 4:
                groups[-1].extend(cur)
            else:
                groups.append(cur)
        for g in groups:
            out.append({"sid": f"S{len(out) + 1}", "scene": si, "text": "\n\n".join(g)})
    return out


def join_sections(sections: list[dict]) -> str:
    from bookforge.steps._common import SCENE_BREAK
    scenes: dict[int, list[str]] = {}
    for s in sections:
        scenes.setdefault(s["scene"], []).append(s["text"].strip())
    return SCENE_BREAK.join("\n\n".join(v) for _, v in sorted(scenes.items()))


def plan_scene_of(facts: dict, scene: int) -> str:
    keys = sorted((facts.get("plan_scenes") or {}), key=lambda k: int(k) if str(k).isdigit() else 0)
    m = (facts.get("section_scene_to_plan_scene") or {}).get(str(scene))
    if m is not None:
        return str(m)
    if not keys:
        return ""
    return str(scene) if str(scene) in keys else keys[-1]


# ------------------------------------------------------------------ Jev
def _fill(obj, vars):
    if isinstance(obj, str):
        m = _PH.fullmatch(obj.strip())
        if m:
            return vars[m.group(1)]
        return _PH.sub(lambda m: str(vars[m.group(1)]), obj)
    if isinstance(obj, dict):
        return {k: _fill(v, vars) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, vars) for v in obj]
    return obj


def _section_vars(facts, s):
    ps = copy.deepcopy((facts.get("plan_scenes") or {}).get(plan_scene_of(facts, s["scene"])) or {"beats": {}})
    ps["rules"] = list(facts.get("default_rules", [])) + list(ps.get("rules", []))
    crit = dict(ps.get("beats") or {})
    crit["none"] = "The section shows none of these planned beats."
    return {"section_id": s["sid"], "section_text": s["text"], "scene_plan": ps, "bible": facts.get("bible", {}),
            "known_characters": facts.get("known_characters", {}), "beat_criteria": crit}


def jev_sections(sections, facts, c, log_dir):
    t = json.loads(TEMPLATE.read_text())

    def one(s):
        v = _section_vars(facts, s)
        res = jev.ask(_fill(t["state"], v), _fill(t["questions"], v), model=c["model"], tag=f"screen:{s['sid']}",
                      log_dir=log_dir, cap_usd=c["cap_usd"])
        return s["sid"], res

    with ThreadPoolExecutor(max(1, int(c["workers"]))) as ex:
        return dict(ex.map(one, sections)), t


def jev_count_rules(sections, facts, c, log_dir):
    out = []
    for rule in facts.get("count_rules") or []:
        scenes = {s["scene"] for s in sections if plan_scene_of(facts, s["scene"]) == str(rule.get("plan_scene"))}
        items = screen_checks.character_lines(sections, rule.get("speaker", ""), scenes)
        r = {"id": rule["id"], "max": rule.get("max", 1), "rule": rule.get("rule") or rule.get("question"), "items": items}
        if items:
            state = {"items": [{"section": i["section"], "context_before": i["context"], "line": i["line"]} for i in items]}
            qs = {f"item_{k}": {"type": "noul", "instructions": rule["question"].replace("`item.", f"`items[{k}].").replace("`item`", f"`items[{k}]`"),
                                "criteria": {"true": rule.get("true", "Yes."), "false": rule.get("false", "No.")}} for k in range(len(items))}
            res = jev.ask(state, qs, model=c["model"], tag=f"screen:count:{rule['id']}", log_dir=log_dir, cap_usd=c["cap_usd"])
            for k, it in enumerate(items):
                it["p"] = round(jev.noul(res, f"item_{k}") or 0.0, 3)
            r["count"] = sum(it["p"] > 0.5 for it in items)
            r["over"] = r["count"] > r["max"]
        out.append(r)
    return out


# ------------------------------------------------------------------ critique / canon mapping
def _locate(sections, quote: str):
    from bookforge.steps.revise_chapter import _find_para
    if not quote or len(quote.strip()) < 8:
        return None
    i = _find_para([s["text"] for s in sections], quote)
    return sections[i]["sid"] if i is not None else None


def critique_flags(ctx, chapter, sections, crit):
    from bookforge.core import editor
    located, chapter_level = [], []
    fx = editor.findings(ctx, chapter["id"], crit or {}, chapter) if crit is not None else []
    for f in fx:
        m = re.search(r'AT "([^"]{8,})"', f)
        sid = _locate(sections, m.group(1)) if m else None
        (located.append({"section": sid, "source": "critique", "check": "editor_finding", "detail": f}) if sid
         else chapter_level.append(f))
    for t in ctx.canon().open_revision_tasks(chapter["id"]):
        sid = _locate(sections, (t.get("source") or {}).get("quote") or t.get("quote", ""))
        if sid:
            located.append({"section": sid, "source": "canon", "check": "continuity_task", "detail": t.get("fix", "")})
            if t.get("fix"):
                chapter_level = [x for x in chapter_level if t["fix"] not in x]
    return located, chapter_level


# ------------------------------------------------------------------ main
def run(ctx, chapter: dict, text: str, crit: dict | None = None, step_dir: Path | None = None,
        use_jev: bool | None = None) -> dict:
    c = cfg()
    facts = facts_mod.load_or_generate(ctx, chapter)
    sections = split_sections(text, int(c["section_words"]))
    flags: dict[str, list] = {s["sid"]: [] for s in sections}

    for f in screen_checks.run(sections, facts):                       # 1. code
        flags[f["section"]].append({**f, "source": "code"})
    new_people = screen_checks.new_people_per_scene(sections, facts)
    chapter_level = []
    for row in new_people:
        if row["over"]:
            for sid in row["sections"]:
                flags[sid].append({"source": "code", "check": "too_many_new_people", "section": sid,
                                   "detail": f"scene {row['scene']} introduces {row['new_named']} (limit {row['limit']})",
                                   "bible": f"No scene introduces more than {row['limit']} new people."})

    jev_status, answers, beat_map, counts = "ok", {}, {}, []
    want = c["jev"] if use_jev is None else use_jev
    log_dir = ctx.state_dir
    if not want:
        jev_status = "skipped: disabled"
    elif not jev.available():
        jev_status = f"skipped: {jev.KEY_ENV} not set"
    else:
        try:                                                           # 2. Jev
            answers, t = jev_sections(sections, facts, c, log_dir)
            counts = jev_count_rules(sections, facts, c, log_dir)
            th = t["thresholds"]
            soft_hit = {(sid, SOFT[f["check"]]) for sid, fl in flags.items() for f in fl
                        if f["source"] == "code" and f["check"] in SOFT}
            for sid, res in answers.items():
                for q, rule in th.items():
                    p = jev.noul(res, q) if isinstance(rule, dict) else None
                    if p is None:
                        continue
                    bar = rule.get("flag_with_code_at", rule.get("flag_at")) if (sid, q) in soft_hit else rule.get("flag_at")
                    hit = p < rule["flag_below"] if "flag_below" in rule else p >= bar
                    if hit:
                        hint = t["bible_hint"].get(q, "")
                        bible = (facts.get("bible") or {}).get(hint, hint) if hint in ("pov",) else hint
                        if hint == "timetable":
                            bible = "\n".join((facts.get("bible") or {}).get("timetable", []))
                        flags[sid].append({"source": "jev", "check": q, "p": round(p, 3), "section": sid,
                                           "detail": t["questions"][q]["instructions"].replace("`section_text`", "this section"),
                                           "bible": bible})
            for r in counts:
                if r.get("over"):
                    for it in r["items"]:
                        if it.get("p", 0) > 0.5:
                            flags[it["section"]].append({"source": "code+jev", "check": f"count:{r['id']}", "p": it["p"],
                                                         "section": it["section"], "evidence": it["line"][:200],
                                                         "detail": f"{r['count']} matching lines in the scene, plan allows {r['max']}",
                                                         "bible": r.get("rule", "")})
            # soft code checks (regex AI tells over-flag) need Jev's confirmation; their evidence rides along
            for sid, fl in flags.items():
                keep = []
                for f in fl:
                    q = SOFT.get(f["check"]) if f["source"] == "code" else None
                    if q is None:
                        keep.append(f)
                        continue
                    j = next((x for x in fl if x["source"] == "jev" and x["check"] == q), None)
                    if j is not None and "evidence" not in j:
                        j["evidence"] = f.get("evidence", "")
                flags[sid] = keep
        except jev.JevUnavailable as e:
            jev_status, answers, counts = f"skipped: {e}", {}, []
            for fl in flags.values():
                for f in fl:
                    if f["source"] == "code" and f["check"] in SOFT:
                        f["unconfirmed"] = True

    for s in sections:                                                 # beat map
        ps = (facts.get("plan_scenes") or {}).get(plan_scene_of(facts, s["scene"])) or {}
        if s["sid"] in answers:
            b, conf = jev.choice(answers[s["sid"]], "plan_beat")
        else:
            b, conf = None, 0.0
        beat_map[s["sid"]] = {"scene": s["scene"], "plan_scene": plan_scene_of(facts, s["scene"]),
                              "beat": b, "beat_text": (ps.get("beats") or {}).get(b, "") if b else "",
                              "confidence": round(conf, 2), "opens": " ".join(s["text"].split()[:14])}

    located, cl = critique_flags(ctx, chapter, sections, crit)          # editor findings
    for f in located:
        flags[f["section"]].append(f)
    chapter_level += cl

    flagged = [s for s in sections if flags[s["sid"]]]
    packet = {"chapter": chapter["id"], "jev": jev_status, "model": c["model"], "sections_total": len(sections),
              "flagged": [s["sid"] for s in flagged], "flags": {k: v for k, v in flags.items() if v},
              "chapter_findings": chapter_level, "beat_map": beat_map, "new_people": new_people, "count_rules": counts,
              "jev_probs": {sid: {q: a.get("noul") for q, a in (res.get("answers") or {}).items() if a.get("type") == "noul"}
                            for sid, res in answers.items()},
              "sections": sections, "facts_file": str(facts_mod.path(ctx, chapter["id"]))}
    packet["prompt"] = packet_prompt(packet, c)
    if step_dir:
        Path(step_dir).mkdir(parents=True, exist_ok=True)
        (Path(step_dir) / "revision_packet.json").write_text(json.dumps({k: v for k, v in packet.items() if k != "prompt"}, indent=1, ensure_ascii=False))
        (Path(step_dir) / "revision_packet.md").write_text(packet["prompt"])
    return packet


def packet_prompt(packet: dict, c: dict | None = None) -> str:
    c = c or cfg()
    secs = {s["sid"]: s for s in packet["sections"]}
    order = [s["sid"] for s in packet["sections"]]
    k = int(c.get("context_paras", 1))
    rows = [f"REVISION PACKET for {packet['chapter']}: {len(packet['flagged'])} of {packet['sections_total']} sections flagged "
            f"(screen: code checks + Jev {packet['jev']}). Only these sections are sent; everything else is final.", "",
            "BEAT MAP (every section; where the plan's beats fall):"]
    for sid in order:
        b = packet["beat_map"][sid]
        mark = "  <== FLAGGED" if sid in packet["flagged"] else ""
        rows.append(f"- {sid} (scene {b['scene']}): {b['beat'] or '?'} {('- ' + b['beat_text'][:140]) if b['beat_text'] else ''} | opens: \"{b['opens']}...\"{mark}")
    if packet["chapter_findings"]:
        rows += ["", "CHAPTER-LEVEL FINDINGS (fix inside the flagged sections if you can; otherwise list under \"unfixed\"):"]
        rows += [f"- {f}" for f in packet["chapter_findings"]]
    for sid in packet["flagged"]:
        i = order.index(sid)
        rows += ["", f"===== {sid} (scene {secs[sid]['scene']}) ====="]
        bible_lines = []
        rows.append("PROBLEMS:")
        for f in packet["flags"][sid]:
            p = f" p={f['p']}" if "p" in f else ""
            ev = f" EVIDENCE: \"{f['evidence']}\"" if f.get("evidence") else ""
            rows.append(f"- [{f['source']}:{f['check']}{p}] {f.get('detail', '')}{ev}")
            if f.get("bible") and f["bible"] not in bible_lines:
                bible_lines.append(f["bible"])
        if bible_lines:
            rows.append("BIBLE / PLAN LINES THAT GOVERN THIS:")
            rows += [f"  {b}" for b in bible_lines]
        if i > 0:
            prev = secs[order[i - 1]]["text"].split("\n\n")[-k:]
            rows.append("CONTEXT BEFORE (do not edit):\n" + "\n\n".join(prev))
        rows.append(f"TEXT OF {sid} (edit this):\n{secs[sid]['text']}")
        if i + 1 < len(order):
            nxt = secs[order[i + 1]]["text"].split("\n\n")[:k]
            rows.append("CONTEXT AFTER (do not edit):\n" + "\n\n".join(nxt))
    rows += ["", """YOUR JOB: you are the book's line and continuity editor. For each flagged section, EDIT it to fix every listed problem. A flag may be a false positive: if the governing bible line shows the text is already right, leave that part alone. Leave every sentence that already works exactly as it is. Keep events, speakers and paragraph breaks; keep it flowing into the context after. Do not touch unflagged sections.
Output ONLY JSON: {"sections": {"<section id>": "<full edited text of that section>"}, "unfixed": ["<finding you could not fix and why>"]}"""]
    return "\n".join(rows)


def lean_system(ctx, chapter) -> str:
    """Persona + rule card + the configured style files (not the whole story pack)."""
    from bookforge.core import pack
    from bookforge.core.rules_card import RULE_CARD
    parts = [pack.PERSONA, RULE_CARD]
    d = pack.bible_dir(ctx)
    for name in cfg().get("packet_system_files") or []:
        p = Path(d) / name if d else None
        if p and p.exists():
            parts.append(f"===== {name} =====\n{p.read_text().strip()}")
    return "\n\n".join(parts)


def splice(packet: dict, edits: dict, lo: float = 0.5, hi: float = 1.6) -> tuple[str, dict]:
    """Replace flagged sections with edited text (length-guarded); returns (text, report)."""
    from bookforge.core.deai import scrub
    from bookforge.steps._common import clean_prose
    secs = [dict(s) for s in packet["sections"]]
    rep = {"applied": [], "rejected": {}, "ignored": []}
    by = {s["sid"]: s for s in secs}
    for sid, new in (edits or {}).items():
        if sid not in packet["flagged"] or sid not in by:
            rep["ignored"].append(sid)
            continue
        new = scrub(clean_prose(str(new or ""))).strip()
        n0, n1 = len(by[sid]["text"].split()), len(new.split())
        if not new or not (lo * n0 <= n1 <= hi * n0) or "* * *" in new:
            rep["rejected"][sid] = f"length {n1} vs {n0} or scene break"
            continue
        by[sid]["text"] = new
        rep["applied"].append(sid)
    return join_sections(secs), rep


def estimate(ctx, chapter, text, crit, packet) -> dict:
    """Token estimate: full editor call (story pack + whole chapter) vs the packet call."""
    from bookforge.core import editor
    from bookforge.core.context import count_tokens
    sys_full, prompt_full = editor.build(ctx, chapter, text, crit or {})
    full_in = count_tokens(sys_full) + count_tokens(prompt_full)
    pk_in = count_tokens(lean_system(ctx, chapter)) + count_tokens(packet["prompt"])
    words = len(text.split())
    flagged_words = sum(len(s["text"].split()) for s in packet["sections"] if s["sid"] in packet["flagged"])
    full_out, pk_out = int(words * 1.35), int(flagged_words * 1.35) + 50
    return {"full_input_tokens": full_in, "packet_input_tokens": pk_in, "full_output_tokens_est": full_out,
            "packet_output_tokens_est": pk_out, "input_saving_pct": round(100 * (1 - pk_in / full_in), 1) if full_in else 0,
            "output_saving_pct": round(100 * (1 - pk_out / full_out), 1) if full_out else 0,
            "full_system_tokens": count_tokens(sys_full), "packet_system_tokens": count_tokens(lean_system(ctx, chapter)),
            "chapter_words": words, "flagged_words": flagged_words}
