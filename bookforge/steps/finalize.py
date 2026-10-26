"""Assemble the manuscript from final (de-AI'd) chapters, generate title and blurb."""
import json

from pydantic import Field

from bookforge.core import progress
from bookforge.core.io import atomic_write_text, read_json
from bookforge.core.deai import scrub
from bookforge.core.llm import llm_call
from bookforge.core.schema import _Loose


class TitleBlurb(_Loose):
    title: str = "Untitled"
    blurb: str = ""


def run(ctx, force: bool = False):
    inputs = progress.input_hash(ctx, "finalize")
    outline = ctx.load_outline()
    if not outline:
        raise ValueError("No outline")
    missing = [c["id"] for c in outline if not ctx.best_chapter_text(c["id"])
               or progress.STATUS_RANK.get(ctx.progress["chapter_status"].get(c["id"]), 0) < 3]
    if missing and not force:
        print(f"Chapters not finished: {', '.join(missing)}")
        return None
    audit = read_json(ctx.state_dir / "audit.json", None)
    if not force and (audit is None or not progress.is_current(ctx, "audit")):
        print("Run the audit step first (bookforge run --step audit), or pass --force.")
        return None
    if not force and not audit.get("passed"):
        print(f"Audit has critical issues ({audit['counts']}). Fix them or pass --force.")
        return None
    step_dir = ctx.new_step_folder("finalize")
    summaries = ctx.load_summaries()
    tb = llm_call(f"""Propose a commercial title and a 150-word back-cover blurb (plain sentences, no dashes).
PREMISE: {ctx.metadata.get('prompt')}
BOOK: {summaries.get('book', {}).get('text') or json.dumps(summaries['chapters'])[:6000]}
Output ONLY JSON: {{"title": "...", "blurb": "..."}}""", role="summary", schema=TitleBlurb, max_tokens=800)
    title, blurb = scrub(tb.title), scrub(tb.blurb)
    parts = [title.upper(), "=" * len(title), ""]
    stats, total = [], 0
    for i, ch in enumerate(outline, 1):
        text = ctx.best_chapter_text(ch["id"]).strip()
        w = len(text.split())
        total += w
        stats.append({"id": ch["id"], "title": ch["title"], "words": w})
        parts += ["", f"CHAPTER {i}", ch["title"].upper(), "", text, ""]
    final = "\n".join(parts)
    atomic_write_text(ctx.project_dir / "final_manuscript.txt", final)
    (step_dir / "final_manuscript.txt").write_text(final)
    (step_dir / "stats.json").write_text(json.dumps({"title": title, "blurb": blurb, "chapters": stats,
                                                     "total_words": total, "chapter_count": len(stats)}, indent=2))
    ctx.metadata["title"], ctx.metadata["blurb"] = title, blurb
    ctx.save_json("metadata.json", ctx.metadata)
    progress.record(ctx, "finalize", inputs=inputs)
    print(f"Final manuscript: {ctx.project_dir / 'final_manuscript.txt'} ({len(stats)} chapters, {total} words)")
    return final
