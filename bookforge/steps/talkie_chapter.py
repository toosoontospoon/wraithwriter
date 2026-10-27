"""Opt-in rolling reword. Writes manuscript/<id>_talkie.txt and leaves the draft and revision alone.

A no-op when talkie.enabled is false, unless the caller passes force (the CLI does).
"""
from pathlib import Path

from bookforge.config import load_config
from bookforge.core import progress
from bookforge.core.io import atomic_write_text
from bookforge.core.talkie_roll import run_chapter, split_paragraphs


def _source(ctx, chapter_id: str) -> Path:
    revised = ctx.chapter_path(chapter_id, "revised")
    if revised.exists():
        return revised
    draft = ctx.chapter_path(chapter_id, "draft")
    if draft.exists():
        return draft
    raise FileNotFoundError(f"Draft or revise {chapter_id} first")


def _persons(ctx) -> str:
    names = [n for n in ctx.cast_names() if n]
    return ", ".join(names) if names else "the people in this chapter"


def _opening(ctx, chapter_id: str) -> str:
    ids = [c["id"] for c in ctx.load_outline()]
    if chapter_id not in ids:
        return ""
    i = ids.index(chapter_id)
    if i == 0:
        return ""
    prev = ctx.best_chapter_text(ids[i - 1])
    paras = split_paragraphs(prev)
    return paras[-1] if paras else ""


def run_text(text: str, log_dir, *, persons: str = "the people in this chapter",
             opening: str = "", cfg: dict | None = None):
    """Reword ``text`` into ``log_dir``. Returns ``(final_paragraphs, summary)``."""
    if cfg is None:
        cfg = load_config().get("talkie") or {}
    return run_chapter(text, cfg, persons, opening, log_dir)


def write_out(dest: Path, finals: list[str]) -> None:
    body = "\n\n".join(finals).strip() + "\n"
    atomic_write_text(Path(dest), body)


def log_dir_for(dest: Path) -> Path:
    dest = Path(dest)
    return dest.parent / f"{dest.stem}_talkie"


def run(ctx, chapter_id: str, force: bool = False):
    cfg = load_config().get("talkie") or {}
    inputs = progress.input_hash(ctx, "talkie", chapter_id)
    if not cfg.get("enabled") and not force:
        progress.record(ctx, "talkie", chapter_id, inputs=inputs)
        print(f"   talkie skipped (disabled) for {chapter_id}")
        return {"skipped": True}
    src = _source(ctx, chapter_id)
    step_dir = ctx.new_step_folder(f"talkie_{chapter_id}")
    finals, summary = run_text(
        src.read_text(), step_dir, persons=_persons(ctx), opening=_opening(ctx, chapter_id), cfg=cfg)
    write_out(ctx.chapter_path(chapter_id, "talkie"), finals)
    write_out(step_dir / "talkie.txt", finals)
    ctx.save_json(f"state/talkie_{chapter_id}.json", summary)
    progress.record(ctx, "talkie", chapter_id, inputs=inputs)
    print(f"   talkie {chapter_id}: {summary['paragraphs']} paragraphs, "
          f"{summary['passes']} passed, ${summary['jev_sol_usd']:.4f}")
    return summary
