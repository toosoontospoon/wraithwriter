"""Enrich the outline's cast and world (merge, never replace) and seed the canon ledger."""
import json

from bookforge.core import progress
from bookforge.core.canon_pipeline import seed_from_plan
from bookforge.core.llm import llm_call
from bookforge.core.schema import normalize_characters, normalize_world


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if k == "id":
            continue
        if k not in out or out[k] in ("", None, [], {}):
            out[k] = v
        elif isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = {**v, **out[k]}
        elif isinstance(out[k], list) and isinstance(v, list):
            out[k] = out[k] + [x for x in v if x not in out[k]]
    return out


def run(ctx):
    inputs = progress.input_hash(ctx, "initialize_cast_and_world")
    step_dir = ctx.new_step_folder("initialize_cast_and_world")
    outline, chars, world = ctx.load_outline(), ctx.load_characters(), ctx.load_world()
    bs = ctx.metadata.get("brainstorm", {})
    context = (f"PREMISE: {ctx.metadata['prompt']}\nGENRE: {bs.get('genre', '')}\n"
               f"OUTLINE: {json.dumps([{k: c[k] for k in ('id', 'title', 'summary', 'setups', 'payoffs')} for c in outline])}")
    (step_dir / "input_snapshot.json").write_text(json.dumps({"outline": outline, "characters": chars}, indent=2))

    print("Deepening character profiles...")
    raw = llm_call(f"""You are a character designer. Deepen these EXISTING characters. Keep every id and name exactly. Add missing detail: physical, backstory, secret, arc, voice (with two sample lines), relationships keyed by other ids, and current_state at the start of the book.

{context}

CAST:
{json.dumps(chars, indent=1)}

Output ONLY JSON: {{"characters": {{"<existing id>": {{"name": "...", "secret": "...", "voice": "...", "current_state": {{"location": "...", "holding": [], "knowledge": [], "emotions": [], "flags": []}}}}}}}}""",
                   role="character_design", json_mode=True, max_tokens=12000, raw_dir=step_dir)
    (step_dir / "characters_raw.json").write_text(json.dumps(raw, indent=2))
    extra = normalize_characters(raw)
    for cid, c in extra.items():
        chars[cid] = _merge(chars[cid], c) if cid in chars else c
    ctx.save_characters(chars)

    print("Deepening world bible...")
    raww = llm_call(f"""You are a worldbuilder. Extend this EXISTING world bible. Keep all existing entries; add missing rules, history and locations the outline needs.

{context}

WORLD:
{json.dumps(world, indent=1)}

Output ONLY the full world JSON with the same keys.""", role="world_design", json_mode=True, max_tokens=10000,
                    raw_dir=step_dir)
    (step_dir / "world_raw.json").write_text(json.dumps(raww, indent=2))
    world = _merge(world, normalize_world(raww))
    ctx.save_world(world)

    canon = seed_from_plan(ctx)
    (step_dir / "characters.json").write_text(json.dumps(ctx.load_characters(), indent=2))
    (step_dir / "world.json").write_text(json.dumps(ctx.load_world(), indent=2))
    progress.record(ctx, "initialize_cast_and_world", inputs=inputs)
    print(f"Cast ({len(chars)}) and world ready; canon seeded with {len(canon.facts)} planned facts -> {step_dir}")
