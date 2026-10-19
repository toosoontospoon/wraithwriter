"""Tabular views shared by the CLI and the web UI."""


def ledger_rows(L, which):
    if which == "cards":
        return [{"scene": k, **{f: v for f, v in c.items() if v not in ("", None, {}, [])}}
                for k, c in L.data["scene_cards"].items()]
    if which == "events":
        real = {(e["kind"], e.get("ref")): f"{e['ch']}:{e.get('scene')}" for e in L.data["realized"]}
        return [{"kind": e["kind"], "ref": e.get("ref"), "planned": f"{e['ch']}:{e.get('scene')}",
                 "realized": real.get((e["kind"], e.get("ref")), "-")} for e in L.data["planned"]]
    data = L.module(which)
    rows = []
    for key, val in data.items():
        if isinstance(val, list):
            rows += [{"_list": key, **v} if isinstance(v, dict) else {"_list": key, "value": v} for v in val]
        else:
            rows.append({"_field": key, "value": val})
    return rows


def timeline(L, chapter_ids):
    """Rows of (kind, ref) x chapter: 'P' planned only, 'R' realized only, 'X' both."""
    cells = {}
    for src, mark in ((L.data["planned"], "P"), (L.data["realized"], "R")):
        for e in src:
            if e["kind"] in ("tension", "scene_outcome"):
                continue
            row = cells.setdefault((e["kind"], str(e.get("ref"))), {})
            cur = row.get(e["ch"], "")
            row[e["ch"]] = "X" if cur and cur != mark else mark
    rows = [{"item": f"{k}:{r}", **{c: v.get(c, "") for c in chapter_ids}} for (k, r), v in sorted(cells.items())]
    return rows


def tension_series(L, scene_keys):
    """Planned vs realized tension per scene, in reading order."""
    real = {f"{e['ch']}:{e.get('scene')}": (e.get("data") or {}).get("value") for e in L.data["realized"] if e["kind"] == "tension"}
    return [{"scene": k, "planned": L.data["scene_cards"].get(k, {}).get("tension"), "realized": real.get(k)} for k in scene_keys]
