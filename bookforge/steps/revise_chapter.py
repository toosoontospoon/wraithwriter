"""Revise = one editing pass (core/editor.py), then canon
re-extraction from the edited text. The threads + rules gate (with rollback) runs last, on the
final text, in the deai step.

With `screen.enabled` the pass is packet-mode: code checks + Jev flag sections first and the editor
reads only those (core/screen.py), instead of the whole chapter plus the full story pack."""
import json
import re

from bookforge.config import load_config
from bookforge.craft import engine as craft
from bookforge.core import canon_pipeline, progress
from bookforge.core import deai
from bookforge.core.deai import scrub, split_paragraphs
from bookforge.core.io import read_json
from bookforge.core.llm import llm_call
from bookforge.core.state_updater import sync_character_state
from bookforge.steps._common import clean_prose, join_scenes, split_scenes
from bookforge.steps.critique_chapter import critique_text
from bookforge.steps.write_chapter import update_chapter_summaries


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip().strip('"').lower()


def _find_para(paras, quote):
    q = _norm(quote)
    if len(q) < 8:
        return None
    for i, p in enumerate(paras):
        if q in _norm(p):
            return i
    # fall back to the longest run of words
    words = q.split()
    for n in (8, 5):
        for j in range(0, max(1, len(words) - n + 1)):
            frag = " ".join(words[j:j + n])
            for i, p in enumerate(paras):
                if frag and frag in _norm(p):
                    return i
    return None


def targeted_fix(ctx, text: str, fixes: list[dict], system: str) -> tuple[str, list[str]]:
    """fixes: [{"quote", "fix", "id"?}]. Rewrites only the paragraphs containing the quotes."""
    paras = split_paragraphs(text)
    done = []
    for fx in fixes:
        i = _find_para(paras, fx.get("quote", ""))
        if i is None:
            continue
        prompt = (f"PREVIOUS PARAGRAPH (context):\n{paras[i - 1] if i else ''}\n\n"
                  f"PARAGRAPH TO REVISE:\n{paras[i]}\n\nNEXT PARAGRAPH (context):\n{paras[i + 1] if i + 1 < len(paras) else ''}\n\n"
                  f"REQUIRED CHANGE: {fx['fix']}\n\nRewrite ONLY the paragraph to revise so the required change is made "
                  "and it still flows into the next paragraph. Keep it clear for a reader around 16. Keep everything else. Output only the paragraph.")
        new = scrub(clean_prose(llm_call(prompt, system=system, role="revision", max_tokens=1500, temperature=0.5)))
        if new:
            paras[i] = new
            done.append(fx.get("id") or fx["fix"][:40])
    return "\n\n".join(paras), done


def _edit(ctx, chapter: dict, text: str, crit: dict, step_dir, cfg) -> tuple[str, dict]:
    """Editor pass. With screen.enabled the post-draft screen (code checks + Jev, core/screen.py)
    builds a revision packet and the expensive editor reads only the flagged sections; a clean
    chapter skips the editor call. Packet failures fall back to the full-chapter editor."""
    from bookforge.core import editor, screen
    sc = screen.cfg()
    if not sc.get("enabled"):
        return editor.edit_chapter(ctx, chapter, text, crit, step_dir)
    packet = screen.run(ctx, chapter, text, crit, step_dir)
    est = screen.estimate(ctx, chapter, text, crit, packet)
    (step_dir / "screen_estimate.json").write_text(json.dumps(est, indent=2))
    print(f"   screen: {len(packet['flagged'])}/{packet['sections_total']} sections flagged "
          f"{packet['flagged']}, jev {packet['jev']}; editor input ~{est['packet_input_tokens']} tok "
          f"vs ~{est['full_input_tokens']} full ({est['input_saving_pct']}% saved)")
    if not packet["flagged"] and not packet["chapter_findings"] and sc.get("skip_edit_if_clean", True):
        return text, {"mode": "packet", "accepted": False, "reason": "screen: nothing flagged, editor skipped",
                      "words_in": len(text.split()), "words_out": len(text.split()), "findings": 0, "jev": packet["jev"]}
    if not packet["flagged"]:   # only unlocated chapter-level findings: the packet has no text to edit
        return editor.edit_chapter(ctx, chapter, text, crit, step_dir)
    try:
        return editor.edit_packet(ctx, chapter, text, packet, step_dir)
    except Exception as e:  # bad JSON / provider error after retries
        print(f"   [warn] packet editor failed ({str(e)[:120]})")
        if sc.get("fallback_full_edit", True):
            return editor.edit_chapter(ctx, chapter, text, crit, step_dir)
        return text, {"mode": "packet", "accepted": False, "reason": f"packet editor failed: {e}",
                      "words_in": len(text.split()), "words_out": len(text.split()), "findings": 0}


def run(ctx, chapter_id: str):
    inputs = progress.input_hash(ctx, "revise", chapter_id)
    chapter = ctx.chapter(chapter_id)
    crit = read_json(ctx.state_dir / "critiques" / f"{chapter_id}.json", None)
    if not chapter or crit is None or not ctx.chapter_path(chapter_id, "draft").exists():
        raise FileNotFoundError(f"Need draft and critique for {chapter_id}")
    cfg = load_config()
    step_dir = ctx.new_step_folder(f"revise_{chapter_id}")
    (step_dir / "input_snapshot.json").write_text(json.dumps({"chapter": chapter, "critique": crit}, indent=2))
    canon = ctx.canon()
    scenes = chapter["scenes"]
    draft_scenes = []
    for sc in scenes:
        p = ctx.scene_path(chapter_id, sc["scene_number"], "draft")
        draft_scenes.append(p.read_text() if p.exists() else "")
    if not any(draft_scenes):  # legacy single-file draft
        draft_scenes = split_scenes(ctx.chapter_path(chapter_id, "draft").read_text())
        scenes = scenes[:len(draft_scenes)] or [{"scene_number": i + 1} for i in range(len(draft_scenes))]
    applied = [t["id"] for t in canon.open_revision_tasks(chapter_id)]
    from bookforge.core.rules_check import enforce_first_line as _efl
    edited, erep = _edit(ctx, chapter, join_scenes(draft_scenes), crit, step_dir, cfg)
    (step_dir / "editor_report.json").write_text(json.dumps(erep, indent=2))
    kept = "revised" if erep.get("accepted") else "draft"
    revised = split_scenes(edited)
    if len(revised) != len(scenes):  # editor merged/split scenes: keep text, map what we can
        print(f"   [warn] editor returned {len(revised)} scenes for {len(scenes)}; scene files approximate")
    rev_text = deai.scrub(_efl(chapter, join_scenes(revised)))
    revised = split_scenes(rev_text)
    for sc, t in zip(scenes, revised):
        ctx.write_text(ctx.scene_path(chapter_id, sc.get("scene_number"), "revised"), t)
    ctx.write_text(ctx.chapter_path(chapter_id, "revised"), rev_text)
    (step_dir / "revised.txt").write_text(rev_text)
    print(f"   editor pass: {'accepted' if erep.get('accepted') else 'rejected (' + erep.get('reason', '') + ')'}, "
          f"{erep['words_in']} -> {erep['words_out']} words, {erep['findings']} findings")
    from bookforge.core import plan_sync
    draft_text = join_scenes(draft_scenes)
    if cfg.get("plan_sync", {}).get("enabled", True) and plan_sync.changed_a_lot(draft_text, rev_text):
        plan_sync.resync(ctx, chapter_id, rev_text, step_dir, reextract=False)  # re-extract runs below
        chapter = ctx.chapter(chapter_id)
        scenes = chapter["scenes"]
        print("   scene plan resynced to the revised text")

    canon = ctx.canon()
    for t in canon.data["revision_tasks"]:
        if t["id"] in applied and kept == "revised":
            t["status"] = "applied"
    canon.save(f"revise {chapter_id}")
    if cfg["canon"].get("reextract_after_revise", True):
        s = ctx.load_summaries()
        for sc, t in zip(scenes, revised):
            canon_pipeline.process_scene(ctx, chapter_id, sc, t, step_dir)
            craft.process_scene(ctx, chapter_id, sc, t, step_dir)
            ctx.embeddings.replace(chapter_id, sc.get("scene_number"), t, list(ctx.canon().entities))
        ctx.save_summaries(s)
    update_chapter_summaries(ctx, chapter_id)
    sync_character_state(ctx)
    progress.record(ctx, "revise", chapter_id, inputs=inputs)
    print(f"{chapter_id} revised ({kept} kept): {len(rev_text.split())} words, "
          f"{len(ctx.canon().open_revision_tasks(chapter_id))} continuity tasks still open")
