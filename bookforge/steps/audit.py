"""Whole-book consistency audit against canon, run before finalize.

Deterministic checks: unpaid setups, planned critical facts never realized, open
revision tasks, pending human reviews, uncast names. Then one long-context LLM read
over the canon digest plus chapter summaries (and full text when it fits).
"""
import json

from bookforge.config import load_config
from bookforge.core import canon_pipeline, progress
from bookforge.core.context import count_tokens
from bookforge.core.io import atomic_write_json
from bookforge.core.llm import llm_call
from bookforge.core.schema import AuditResult


def deterministic(ctx) -> list[dict]:
    canon = ctx.canon()
    issues = []
    for t in canon.open_threads():
        if t.get("planted"):
            issues.append({"severity": "major" if t["importance"] != "critical" else "critical",
                           "chapter": (t.get("setup_source") or {}).get("chapter", ""),
                           "description": f"Setup never paid off: {t['description']}",
                           "fix": f"Add a payoff by {t.get('payoff_by') or 'the climax'} or cut the setup."})
        elif t.get("origin") == "plan":
            issues.append({"severity": "minor", "chapter": t.get("setup_chapter", ""),
                           "description": f"Planned setup never planted: {t['description']}", "fix": ""})
    for f in canon.facts.values():
        if f["status"] == "planned" and f["importance"] == "critical" and f.get("origin") == "plan" \
                and f["type"] in ("plot_point",):
            issues.append({"severity": "major", "chapter": f.get("planned_in", ""),
                           "description": f"Planned critical beat not found in prose: {f['claim']}", "fix": ""})
    for t in canon.data["revision_tasks"]:
        if t["status"] == "open":
            issues.append({"severity": "major", "chapter": t["source"].get("chapter", ""),
                           "description": f"Open continuity fix: {t['fix']}", "fix": t["fix"]})
    for q in canon.pending_reviews():
        issues.append({"severity": "critical", "chapter": q["source"].get("chapter", ""),
                       "description": f"Pending human review: {q['fact']['claim']} ({q.get('why_review')})",
                       "fix": "bookforge review resolve"})
    return issues


def run(ctx):
    inputs = progress.input_hash(ctx, "audit")
    step_dir = ctx.new_step_folder("audit")
    outline = ctx.load_outline()
    canon = ctx.canon()
    issues = deterministic(ctx)
    from bookforge.craft import engine as craft
    craft_issues = craft.audit(ctx)
    issues += craft_issues
    texts = {c["id"]: ctx.best_chapter_text(c["id"]) for c in outline}
    for cid, t in texts.items():
        chk = canon_pipeline.code_checks(t, {"person": (ctx.chapter(cid) or {"scenes": [{}]})["scenes"][0].get("person", "third")
                                             if (ctx.chapter(cid) or {}).get("scenes") else "third"}, canon)
        for u in chk["unknown_names"]:
            if u["count"] >= 3:
                issues.append({"severity": "minor", "chapter": cid,
                               "description": f"Name not in canon: {u['name']} x{u['count']}", "fix": ""})
    digest = "\n".join(f"- [{f['type']}, {f['importance']}, {f['status']}] {f['claim']}"
                       for f in canon.facts.values() if f["status"] in ("established", "planned")
                       and f["importance"] != "local")
    summaries = ctx.load_summaries()["chapters"]
    body = "\n\n".join(f"=== {cid} ===\n{t}" for cid, t in texts.items())
    limit = load_config()["context"].get("audit_full_text_tokens", 150000)
    material = body if count_tokens(body) < limit else "\n".join(f"{k}: {v}" for k, v in summaries.items())
    res = llm_call(f"""You are a continuity auditor reading a whole novel. Find contradictions in names, ages, timeline, locations, object possession, who knows what, world rules, and character motivation; dropped threads; and repeated scenes or images.

CANON DIGEST:
{digest}

BOOK ({'full text' if material is body else 'chapter summaries'}):
{material}

Output ONLY JSON: {{"issues": [{{"severity": "critical|major|minor", "chapter": "chNN", "description": "...", "fix": "..."}}], "passed": true}}""",
                   role="audit", schema=AuditResult, max_tokens=6000, raw_dir=step_dir)
    all_issues = issues + [i.model_dump() for i in res.issues]
    passed = not any(i["severity"] == "critical" for i in all_issues)
    report = {"passed": passed, "issues": all_issues, "llm_passed": res.passed,
              "craft": {"issues": len(craft_issues),
                        "by_check": {c: sum(1 for i in craft_issues if i["check"] == c) for c in {i["check"] for i in craft_issues}}},
              "counts": {s: sum(1 for i in all_issues if i["severity"] == s) for s in ("critical", "major", "minor")}}
    atomic_write_json(ctx.state_dir / "audit.json", report)
    (step_dir / "audit.json").write_text(json.dumps(report, indent=2))
    progress.record(ctx, "audit", inputs=inputs)
    print(f"Audit {'PASSED' if passed else 'FAILED'}: {report['counts']} -> {ctx.state_dir / 'audit.json'}")
    return report
