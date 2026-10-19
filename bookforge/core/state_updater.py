"""Compatibility layer. Character/world state now lives in the canon ledger
(continuity + facts); these helpers keep characters.json/world.json in sync."""
from bookforge.core.schema import normalize_characters, normalize_world


def sync_character_state(ctx):
    """Copy continuity-ledger state into characters.json current_state."""
    chars = ctx.load_characters()
    cont = ctx.canon().continuity_for(chars.keys())
    for cid, c in cont.items():
        cs = chars[cid].setdefault("current_state", {})
        if c.get("location"):
            cs["location"] = c["location"]
        cs["holding"] = c.get("holding", [])
        cs["knowledge"] = list(dict.fromkeys(cs.get("knowledge", []) + c.get("knows", [])))
        cs["flags"] = c.get("flags", [])
    ctx.save_characters(chars)


def update_states_from_chapter(ctx, chapter_id, revised_text=None):
    sync_character_state(ctx)


def merge_character_deltas(ctx, deltas):
    chars = ctx.load_characters()
    for cid, delta in (deltas or {}).items():
        if cid not in chars:
            continue
        cs = chars[cid].setdefault("current_state", {})
        for key, val in (delta.get("current_state") or {}).items():
            if isinstance(val, list):
                cur = cs.setdefault(key, [])
                cur.extend(v for v in val if v not in cur)
            elif val:
                cs[key] = val
        chars[cid].setdefault("relationships", {}).update(
            {str(k): str(v) for k, v in (delta.get("relationships") or {}).items()})
    ctx.save_characters(normalize_characters(chars))


def merge_world_deltas(ctx, deltas):
    world = ctx.load_world()
    deltas = deltas or {}
    names = {l["name"].lower() for l in world["key_locations"]}
    for loc in deltas.get("new_locations", []) or []:
        loc = loc if isinstance(loc, dict) else {"name": str(loc)}
        if loc.get("name", "").lower() not in names:
            world["key_locations"].append(loc)
            names.add(loc.get("name", "").lower())
    for key, src in (("lore", "lore_updates"), ("core_rules", "rule_changes")):
        vals = deltas.get(src, [])
        vals = [vals] if isinstance(vals, (str, dict)) else (vals or [])
        for v in vals:
            v = v if isinstance(v, str) else "; ".join(f"{a}: {b}" for a, b in v.items()) if isinstance(v, dict) else str(v)
            if v not in world[key]:
                world[key].append(v)
    ctx.save_world(normalize_world(world))
