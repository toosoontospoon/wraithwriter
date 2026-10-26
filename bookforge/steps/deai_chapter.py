"""Final step: deterministic detector autofix (no LLM rewrite), then the threads + rules gate
on the FINAL text with rollback.

The gate counts duties met (thread obligations + craft rules) on the final text and compares it with
the draft's count from the critique. If the edited text meets fewer duties than the draft did, the
draft (autofixed) becomes final and the rollback is recorded. The ledger is updated from the kept text.
"""
import json

from bookforge.core import deai, detector, progress
from bookforge.core.io import read_json
from bookforge.steps._common import join_scenes, split_scenes


def _autofix(text: str) -> tuple[str, list]:
    fixed, notes = detector.autofix(deai.scrub(text))
    return fixed, notes


def _token_note(before: dict, counts: dict, after: dict) -> str:
    return ("\n\n"
            f"token_tells_before: {sum(before.values())} {before}\n"
            f"scrub_counts: {counts}\n"
            f"token_tells_after: {sum(after.values())} {after}\n")


def _final_token_gate(step_dir, text: str) -> tuple[str, str]:
    """Count token tells, scrub once more, and fail closed when any remain."""
    from bookforge.config import load_config
    before = deai.count_token_tells(text)
    cleaned, counts = deai.scrub_report(text)
    after = deai.count_token_tells(cleaned)
    note = _token_note(before, counts, after)
    if after and (load_config().get("deai") or {}).get("fail_on_tokens", True):
        (step_dir / "detector_report.txt").write_text("token gate failed." + note)
        raise RuntimeError(f"deai token gate: token tells remain after scrub: {after}")
    return cleaned, note


def _duties(ctx, chapter, text, step_dir):
    from bookforge.core import rules_check, threads
    ob = threads.check_obligations(ctx, chapter, text, raw_dir=step_dir) if chapter.get("thread_obligations") else []
    ru = rules_check.check(ctx, chapter, text, raw_dir=step_dir)
    return len(ob) - len(threads.failed(ob)) + len(ru) - len(rules_check.failed(ru)), ob, ru


def run(ctx, chapter_id: str):
    inputs = progress.input_hash(ctx, "deai", chapter_id)
    src = ctx.chapter_path(chapter_id, "revised")
    if not src.exists():
        raise FileNotFoundError(f"Revise {chapter_id} first")
    step_dir = ctx.new_step_folder(f"deai_{chapter_id}")
    chapter = ctx.chapter(chapter_id) or {"scenes": []}
    from bookforge.core import rules_check, threads
    from bookforge.core.rules_check import enforce_first_line
    final, notes = _autofix(src.read_text())
    final = deai.scrub(enforce_first_line(chapter, final))
    gate = {"autofix": notes}
    crit = read_json(ctx.state_dir / "critiques" / f"{chapter_id}.json", {}) or {}
    d_final, ob, ru = _duties(ctx, chapter, final, step_dir)
    tc0, rc0 = crit.get("thread_checks", []), crit.get("rule_checks", [])
    d_draft = len(tc0) - len(threads.failed(tc0)) + len(rc0) - len(rules_check.failed(rc0))
    gate.update({"duties_final": d_final, "duties_draft": d_draft, "rolled_back": False})
    draft_p = ctx.chapter_path(chapter_id, "draft")
    if crit and d_final < d_draft and draft_p.exists():
        final, n2 = _autofix(draft_p.read_text())
        final = deai.scrub(enforce_first_line(chapter, final))
        ob, ru = tc0, rc0
        gate["rolled_back"] = True
        gate["autofix"] = n2
    if chapter.get("thread_obligations"):
        threads.update_ledger(ctx, chapter, ob)
    gate["threads_met"] = f"{len(ob) - len(threads.failed(ob))}/{len(ob)}"
    gate["rules_pass"] = f"{len(ru) - len(rules_check.failed(ru))}/{len(ru)}"
    gate["rules_failed"] = [{k: r.get(k) for k in ("rule", "quote", "note")} for r in rules_check.failed(ru)]
    gate["threads_failed"] = [{k: r.get(k) for k in ("thread", "note")} for r in threads.failed(ob)]
    final, token_note = _final_token_gate(step_dir, final)
    scenes = split_scenes(final)
    for sc, t in zip(chapter.get("scenes") or [], scenes):
        ctx.write_text(ctx.scene_path(chapter_id, sc["scene_number"], "final"), t)
    final = join_scenes(scenes)
    ctx.write_text(ctx.chapter_path(chapter_id, "final"), final)
    (step_dir / "final.txt").write_text(final)
    (step_dir / "final_gate.json").write_text(json.dumps(gate, indent=2))
    (step_dir / "thread_checks.json").write_text(json.dumps(ob, indent=2))
    (step_dir / "rule_checks.json").write_text(json.dumps(ru, indent=2))
    progress.record(ctx, "deai", chapter_id, inputs=inputs)
    sb = ctx.style()
    det = detector.detect(final, sb.allowlist(), ctx.cast_names())
    (step_dir / "detector_report.txt").write_text(detector.format_report(det, max_hits=200) + token_note)
    print(f"{chapter_id} final gate: duties draft {d_draft} -> final {d_final}"
          f"{' ROLLED BACK to draft' if gate['rolled_back'] else ''}; threads {gate['threads_met']}, "
          f"rules {gate['rules_pass']}; detector {det['score']}/10")
