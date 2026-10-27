"""Step registry, input-hash invalidation, run-all/range/restart."""
from __future__ import annotations

import datetime
import importlib
import shutil

from bookforge.core.io import read_json, stable_hash

STEP_ORDER = ["brainstorm", "outline", "initialize_cast_and_world", "write", "critique", "revise",
              "talkie", "deai", "audit", "finalize"]
CHAPTER_STEPS = ["write", "critique", "revise", "talkie", "deai"]
STATUS_FOR = {"write": "drafted", "critique": "critiqued", "revise": "revised", "talkie": "talkie",
              "deai": "deai_done"}
STATUS_RANK = {"": 0, None: 0, "drafted": 1, "critiqued": 2, "revised": 3, "talkie": 4, "deai_done": 5}
MODULES = {"brainstorm": "brainstorm", "outline": "outline", "initialize_cast_and_world": "initialize_cast_and_world",
           "write": "write_chapter", "critique": "critique_chapter", "revise": "revise_chapter",
           "talkie": "talkie_chapter", "deai": "deai_chapter", "audit": "audit", "finalize": "finalize"}
ALIASES = {"init": "initialize_cast_and_world", "initialize": "initialize_cast_and_world",
           "draft": "write", "polish": "audit", "global_polish": "audit", "de-ai": "deai", "deai_chapter": "deai",
           "talkie_chapter": "talkie"}


def canonical_step(name: str) -> str:
    name = ALIASES.get(name, name)
    if name not in STEP_ORDER:
        raise ValueError(f"Unknown step '{name}'. Steps: {', '.join(STEP_ORDER)}")
    return name


def _file_hash(p) -> str:
    return stable_hash(p.read_text()) if p.exists() else "missing"


def _run_id(ctx, key):
    return ctx.progress["hashes"].get(key, {}).get("run_id", "never") if isinstance(
        ctx.progress["hashes"].get(key), dict) else "never"


def input_hash(ctx, step: str, ch: str | None = None) -> str:
    md = ctx.metadata
    if step == "brainstorm":
        return stable_hash([md.get("prompt"), md.get("target_words")])
    if step == "outline":
        return stable_hash([md.get("prompt"), md.get("target_words"), md.get("brainstorm"), _run_id(ctx, "brainstorm")])
    if step == "initialize_cast_and_world":
        return stable_hash(_run_id(ctx, "outline"))
    if step == "write":
        chap = ctx.chapter(ch) or {}
        return stable_hash([_run_id(ctx, "initialize_cast_and_world"), chap])
    if step == "critique":
        return _file_hash(ctx.chapter_path(ch, "draft"))
    if step == "revise":
        return stable_hash([_file_hash(ctx.chapter_path(ch, "draft")),
                            _file_hash(ctx.state_dir / "critiques" / f"{ch}.json")])
    if step == "talkie":
        from bookforge.config import load_config
        revised = ctx.chapter_path(ch, "revised")
        src = revised if revised.exists() else ctx.chapter_path(ch, "draft")
        enabled = bool((load_config().get("talkie") or {}).get("enabled"))
        return stable_hash([_file_hash(src), enabled])
    if step == "deai":
        talkie_p = ctx.chapter_path(ch, "talkie")
        src = talkie_p if talkie_p.exists() else ctx.chapter_path(ch, "revised")
        return _file_hash(src)
    if step in ("audit", "finalize"):
        texts = [stable_hash(ctx.best_chapter_text(c["id"])) for c in ctx.load_outline()]
        return stable_hash(texts + ([_run_id(ctx, "audit")] if step == "finalize" else []))
    raise ValueError(step)


def _key(step, ch=None):
    return f"{step}:{ch}" if ch else step


def is_current(ctx, step, ch=None) -> bool:
    rec = ctx.progress["hashes"].get(_key(step, ch))
    return isinstance(rec, dict) and rec.get("inputs") == input_hash(ctx, step, ch)


def record(ctx, step, ch=None, inputs: str | None = None):
    """Called by a step on success. ``inputs`` should be the hash taken BEFORE the step ran."""
    ctx.progress["hashes"][_key(step, ch)] = {
        "inputs": inputs if inputs is not None else input_hash(ctx, step, ch),
        "run_id": datetime.datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "at": datetime.datetime.now().isoformat(timespec="seconds")}
    if ch:
        ctx.progress["chapter_status"][ch] = STATUS_FOR[step]
    elif step not in ctx.progress["completed"]:
        ctx.progress["completed"].append(step)
    ctx.save_progress()


def run_step(ctx, step: str, ch: str | None = None, **kw):
    step = canonical_step(step)
    from bookforge.core import llm as _llm
    _llm.set_usage_context(chapter=ch, step=step)
    mod = importlib.import_module(f"bookforge.steps.{MODULES[step]}")
    if step in CHAPTER_STEPS:
        if not ch:
            raise ValueError(f"step '{step}' needs --chapter")
        return mod.run(ctx, ch, **kw)
    return mod.run(ctx, **kw)


def _needs(ctx, step, ch=None, force=False):
    return force or not is_current(ctx, step, ch)


def run_all_with_progress(ctx, until: str = "finalize", force: bool = False, chapters: list[str] | None = None):
    """Run everything that is missing or stale, chapter by chapter, in order."""
    until_i = STEP_ORDER.index(canonical_step(until))
    for step in ["brainstorm", "outline", "initialize_cast_and_world"]:
        if STEP_ORDER.index(step) <= until_i and _needs(ctx, step, force=force):
            run_step(ctx, step)
    for chap in ctx.load_outline():
        ch = chap["id"]
        if chapters and ch not in chapters:
            continue
        for step in CHAPTER_STEPS:
            if STEP_ORDER.index(step) <= until_i and _needs(ctx, step, ch, force):
                run_step(ctx, step, ch)
    for step in ["audit", "finalize"]:
        if STEP_ORDER.index(step) <= until_i and _needs(ctx, step, force=force):
            run_step(ctx, step)


def get_steps_in_range(from_step, to_step):
    a, b = STEP_ORDER.index(canonical_step(from_step)), STEP_ORDER.index(canonical_step(to_step))
    return STEP_ORDER[a:b + 1] if a <= b else []


def run_step_range(ctx, from_step, to_step, chapter=None):
    steps = get_steps_in_range(from_step, to_step)
    if not steps:
        print(f"Invalid range: {from_step} to {to_step}")
        return
    print("Running steps: " + " -> ".join(steps))
    globals_ = [s for s in steps if s not in CHAPTER_STEPS]
    for s in [s for s in globals_ if STEP_ORDER.index(s) < STEP_ORDER.index("write")]:
        run_step(ctx, s)
    chap_steps = [s for s in steps if s in CHAPTER_STEPS]
    if chap_steps:
        for ch in ([chapter] if chapter else [c["id"] for c in ctx.load_outline()]):
            for s in chap_steps:
                run_step(ctx, s, ch)
    for s in [s for s in globals_ if STEP_ORDER.index(s) > STEP_ORDER.index("deai")]:
        run_step(ctx, s)


def reset_progress_after_step(ctx, step_name, chapter: str | None = None):
    """Invalidate ``step_name`` and everything downstream so they rerun.

    Chapter artifacts that become stale are archived to ``archive/<ts>/`` (never deleted)
    and their canon provenance, passages and summaries are retracted.
    """
    step = canonical_step(step_name)
    idx = STEP_ORDER.index(step)
    later = set(STEP_ORDER[idx:])
    chapters = [chapter] if chapter else [c["id"] for c in ctx.load_outline()]
    for k in list(ctx.progress["hashes"]):
        s, _, ch = k.partition(":")
        if s in later and (not chapter or not ch or ch == chapter or s not in CHAPTER_STEPS):
            del ctx.progress["hashes"][k]
    ctx.progress["completed"] = [c for c in ctx.progress["completed"] if c not in later]
    floor = {"write": 0, "critique": 1, "revise": 2, "talkie": 3, "deai": 4}.get(
        step, 0 if idx < STEP_ORDER.index("audit") else None)
    if floor is not None:
        for ch in chapters:
            if STATUS_RANK.get(ctx.progress["chapter_status"].get(ch, ""), 0) > floor:
                ctx.progress["chapter_status"][ch] = ["", "drafted", "critiqued", "revised", "talkie"][floor]
    if idx <= STEP_ORDER.index("write"):
        arch = ctx.project_dir / "archive" / datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        canon = ctx.canon()
        summaries = ctx.load_summaries()
        for ch in chapters:
            for p in list(ctx.manuscript_dir.glob(f"{ch}_*.txt")) + ([ctx.manuscript_dir / ch] if (ctx.manuscript_dir / ch).exists() else []):
                arch.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(arch / p.name))
            canon.retract_source(ch)
            from bookforge.craft import engine as _craft
            _craft.retract_chapter(ctx, ch)
            ctx.embeddings.remove_chapter(ch)
            summaries["scenes"].pop(ch, None)
            summaries["chapters"].pop(ch, None)
            ctx.progress["scenes"].pop(ch, None)
        if summaries.get("book", {}).get("through", "") >= min(chapters or ["~"]):
            summaries["book"] = {}
        ctx.save_summaries(summaries)
        canon.save(f"restart from {step}" + (f" {chapter}" if chapter else ""))
    ctx.save_progress()
    print(f"Progress reset from {step}" + (f" for {chapter}" if chapter else ""))
