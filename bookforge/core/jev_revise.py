"""Opt-in per-category reviser.

A full LLM rewrites one tell category at a time. Jev only scores fidelity and cuts.
Untouched sentences stay byte-identical. The guards below always run; config can make
a threshold stricter and cannot turn a guard off. If Jev is unavailable the text is
left unchanged.
"""
from __future__ import annotations

import re

from bookforge.core import jev, jev_tells

_QUOTE = re.compile(r'["\u201c][^"\u201d]*["\u201d]')
_FID_FLOOR = 0.6
_CUT_CAP = 0.10

SYS = ("You are a line editor for fiction. Fix one named flaw at a time. Change only the lines you are given. "
       "Keep every plot fact, name, object, action, and line of dialogue. Do not add events, metaphors, or "
       "explanations of feelings. A thought must not become spoken dialogue. "
       'Reply with JSON only: {"replacements": [{"id": "s01", "text": "..."}]}.')


def revise_settings(cfg: dict | None = None) -> dict:
    from bookforge.config import load_config
    base = dict((load_config().get("deai") or {}).get("jev_revise") or {})
    defaults = {
        "enabled": False,
        "model": "mistralai/mistral-small-3.2-24b-instruct",
        "provider": "openrouter",
        "max_rounds": 2,
        "fidelity_threshold": _FID_FLOOR,
        "max_cut_ratio": _CUT_CAP,
        "allow_cuts": True,
    }
    defaults.update({k: v for k, v in base.items() if v is not None})
    if cfg:
        defaults.update(cfg)
    # Guards cannot be relaxed past the floor. A higher fidelity bar or a smaller cut cap is allowed.
    defaults["fidelity_threshold"] = max(_FID_FLOOR, float(defaults.get("fidelity_threshold") or _FID_FLOOR))
    defaults["max_cut_ratio"] = min(_CUT_CAP, float(defaults.get("max_cut_ratio") if defaults.get("max_cut_ratio") is not None else _CUT_CAP))
    defaults["max_rounds"] = max(1, int(defaults.get("max_rounds") or 1))
    defaults["enabled"] = bool(defaults.get("enabled"))
    defaults["allow_cuts"] = bool(defaults.get("allow_cuts"))
    return defaults


def _blank(status: str, enabled: bool = True) -> dict:
    return {"enabled": enabled, "rounds": 0, "kept": [], "rejected": [], "reverted": [],
            "words_cut": 0, "jev": status}


def _severity(tagged: dict) -> int:
    return sum(int(h.get("severity") or 0) for h in tagged.get("hits") or [])


def _quotes(text: str) -> list[str]:
    return _QUOTE.findall(text or "")


def _page_budget(text: str, page_words: int) -> tuple[dict, list[int]]:
    sents = jev_tells.annotate(text)
    pages = jev_tells._pages_of(sents, text, page_words) or ([sents] if sents else [])
    index, counts = {}, []
    for i, page in enumerate(pages):
        counts.append(sum(len(s["text"].split()) for s in page))
        for s in page:
            index[s["id"]] = i
    return index, counts


def _ask_scores(state: dict, questions: dict, log_dir, cap: float, tag: str) -> dict:
    if not questions:
        return {}
    res = jev.ask(state, questions, tag=tag, log_dir=log_dir, cap_usd=cap)
    out = {}
    for qid in questions:
        p = jev.noul(res, qid)
        out[qid] = None if p is None else float(p)
    return out


def _fidelity(sents: list[dict], originals: dict, cand: dict, log_dir, cap: float) -> dict:
    """P(the rewrite adds nothing and does not turn a thought into speech). High is a pass."""
    keys = list(cand)
    state = {"passage": jev_tells.doc_state(sents),
             "edits": [{"id": k, "original": originals[k], "revised": cand[k]} for k in keys]}
    qs = {}
    for n, k in enumerate(keys):
        qs[f"f{n}"] = {"type": "noul", "instructions": (
            f"Compare edits[{n}].revised with edits[{n}].original. Does the revised line add no new fact, "
            "action, object or event, and is a thought not turned into speech?"),
            "criteria": {"true": "It adds no new fact, action, object or event, and a thought is not turned into speech.",
                         "false": "It adds a new fact, action, object or event, or a thought is turned into speech."}}
    scores = _ask_scores(state, qs, log_dir, cap, "jev_revise:fidelity")
    return {k: scores.get(f"f{n}") for n, k in enumerate(keys)}


def _cuts(sents: list[dict], cuts: list[str], log_dir, cap: float) -> dict:
    """P(removing the line loses a fact another line depends on). High is a fail."""
    state = {"passage": jev_tells.doc_state(sents)}
    qs = {}
    for n, k in enumerate(cuts):
        qs[f"c{n}"] = {"type": "noul", "instructions": (
            f"Would removing line {k} lose a fact another line depends on?"),
            "criteria": {"true": "Removing it would lose a fact another line depends on.",
                         "false": "No other line depends on a fact only this line states."}}
    scores = _ask_scores(state, qs, log_dir, cap, "jev_revise:cut")
    return {k: scores.get(f"c{n}") for n, k in enumerate(cuts)}


def _splice(text: str, reps: dict[str, str]) -> str:
    spans = jev_tells.split_sentences(text)
    out, last = [], 0
    strip_next = False
    for i, (a, b) in enumerate(spans):
        k = jev_tells.sid(i)
        gap = text[last:a]
        cut = k in reps and not reps[k]
        at_start = strip_next or last == 0 or "\n" in gap
        if strip_next and "\n" not in gap:
            gap = ""  # every line before this one in the paragraph was cut
        strip_next = False
        if cut:
            if not at_start:
                gap = ""  # a cut line takes its leading space with it
            strip_next = at_start
        out.append(gap)
        out.append(reps[k] if k in reps else text[a:b])
        last = b
    out.append(text[last:])
    return "".join(out)


def _writer(text: str, tagged: dict, cat: str, cfg: dict) -> dict:
    from bookforge.core.llm import llm_call
    tax = jev_tells.load_taxonomy()
    meta = jev_tells._lookup(tax, cat)
    ids = []
    for hit in tagged["hits"]:
        if hit["category"] == cat:
            for i in hit["ids"]:
                if i not in ids:
                    ids.append(i)
    by_id = {s["id"]: s["text"] for s in tagged["sentences"]}
    lines = {k: by_id[k] for k in ids if k in by_id}
    if not lines:
        return {}
    cut_note = ""
    if cfg.get("allow_cuts") and cat != "uniform_rhythm":
        cut_note = ("\nYou may set text to an empty string when the passage still makes sense without the line "
                    "and no later line depends on it.")
    user = (f"FLAW: {meta.get('name') or cat}. {meta.get('definition') or ''}\n"
            f"FIX: {meta.get('fix') or ''}\n\nPASSAGE:\n{jev_tells.doc_state(jev_tells.annotate(text))}\n\n"
            "FLAGGED LINES (one replacement per id; return a line unchanged if it is already fine):\n"
            + "\n".join(f"{k}: {v}" for k, v in lines.items())
            + cut_note
            + '\n\nReturn JSON: {"replacements": [{"id": "s01", "text": "..."}]}')
    data = llm_call(user, system=SYS, role="jev_revise", provider=cfg.get("provider"), model=cfg.get("model"),
                    json_mode=True, temperature=0.4, max_tokens=1200)
    if not isinstance(data, dict):
        return {}
    reps = {}
    for row in data.get("replacements") or []:
        if isinstance(row, dict) and row.get("id") in lines:
            reps[row["id"]] = (row.get("text") or "").strip()
    return reps


def _apply_category(text: str, tagged: dict, cat: str, cfg: dict, log_dir, cap: float,
                    report: dict, budget: dict | None = None) -> tuple[str, list]:
    """Return spliced text and the edits that passed the line guards. The caller may still undo them."""
    originals = {s["id"]: s["text"] for s in tagged["sentences"]}
    try:
        reps = _writer(text, tagged, cat, cfg)
    except Exception as e:
        report["rejected"].append({"category": cat, "reason": f"writer failed: {str(e)[:180]}"})
        return text, []
    changed = {k: v for k, v in reps.items() if k in originals and v != originals[k].strip()}
    if not changed:
        return text, []
    sents = jev_tells.annotate(text)
    fid_need = {k: v for k, v in changed.items() if v}
    cut_need = [k for k, v in changed.items() if not v]
    fid = _fidelity(sents, originals, fid_need, log_dir, cap) if fid_need else {}
    cut = _cuts(sents, cut_need, log_dir, cap) if cut_need else {}
    bar = float(cfg["fidelity_threshold"])
    loss_max = 1.0 - bar
    page_of, page_words = _page_budget(text, int(jev_tells.settings(None)["page_words"]))
    # The word-cut cap is cumulative over the whole run, not per category, so it is kept in
    # ``budget`` (page sizes of the original text, and words already cut per page).
    budget = budget if budget is not None else {}
    base_words = budget.setdefault("page_words", list(page_words))
    used = budget.setdefault("used", [0 for _ in base_words])
    while len(used) < len(page_words):
        used.append(0)
    trial = list(used)
    accepted, pending = {}, []
    for k, v in changed.items():
        old = originals[k]
        if _quotes(old) and any(q not in v for q in _quotes(old)):
            report["rejected"].append({"id": k, "category": cat, "reason": "dialogue not preserved"})
            continue
        if not v and not cfg.get("allow_cuts"):
            report["rejected"].append({"id": k, "category": cat, "reason": "cuts disabled"})
            continue
        if v:
            score = fid.get(k)
            if score is None or score < bar:
                shown = "none" if score is None else round(score, 3)
                report["rejected"].append({"id": k, "category": cat, "reason": f"fidelity {shown}"})
                continue
        else:
            score = cut.get(k)
            if score is None or score > loss_max:
                shown = "none" if score is None else round(score, 3)
                report["rejected"].append({"id": k, "category": cat, "reason": f"cut gate {shown}"})
                continue
        pi = page_of.get(k, 0)
        delta = max(0, len(old.split()) - len(v.split()))
        size = base_words[pi] if pi < len(base_words) else (page_words[pi] if pi < len(page_words) else 0)
        cap_words = size * float(cfg["max_cut_ratio"])
        if delta and (pi >= len(trial) or trial[pi] + delta > cap_words + 1e-9):
            report["rejected"].append({"id": k, "category": cat, "reason": "word-cut cap"})
            continue
        if pi < len(trial):
            trial[pi] += delta
        accepted[k] = v
        pending.append({"id": k, "category": cat, "before": old, "after": v or "(cut)", "words": delta})
    if not accepted:
        return text, []
    budget["pending_used"] = trial
    return _splice(text, accepted), pending


def revise(text: str, cfg: dict | None = None, log_dir=None, names: list[str] | None = None,
           force: bool = False) -> tuple[str, dict]:
    """Revise ``text``. ``force`` runs even when ``deai.jev_revise.enabled`` is false (CLI opt-in)."""
    cfg = revise_settings(cfg)
    if not cfg["enabled"] and not force:
        return text, _blank("skipped: disabled", enabled=False)
    original = text or ""
    tell_cfg = {"names": list(names or [])}
    cap = float(jev_tells.settings(None).get("cap_usd") or 1.0)
    report = _blank("ok", enabled=True)
    budget: dict = {}
    current = original
    try:
        for rnd in range(cfg["max_rounds"]):
            tagged = jev_tells.tag_text(current, cfg=tell_cfg, log_dir=log_dir)
            if not str(tagged.get("jev") or "").startswith("ok"):
                report["jev"] = tagged.get("jev") or "skipped"
                report["rejected"].append({"reason": report["jev"]})
                return original, report
            if not tagged["hits"]:
                break
            report["rounds"] = rnd + 1
            cats = sorted({h["category"] for h in tagged["hits"]},
                          key=lambda c: (-sum(int(h["severity"]) for h in tagged["hits"] if h["category"] == c), c))
            moved = False
            for cat in cats:
                before_count = tagged["counts"].get(cat, 0)
                before_sev = _severity(tagged)
                nxt, pending = _apply_category(current, tagged, cat, cfg, log_dir, cap, report, budget)
                if nxt == current:
                    continue
                again = jev_tells.tag_text(nxt, cfg=tell_cfg, log_dir=log_dir)
                if not str(again.get("jev") or "").startswith("ok"):
                    report["jev"] = again.get("jev") or "skipped"
                    report["rejected"].append({"category": cat, "reason": report["jev"]})
                    report["kept"] = []
                    return original, report
                after_count = again["counts"].get(cat, 0)
                after_sev = _severity(again)
                if after_count <= before_count and after_sev < before_sev:
                    current = nxt
                    tagged = again
                    report["kept"].extend(pending)
                    budget["used"] = budget.pop("pending_used", budget.get("used"))
                    moved = True
                else:
                    budget.pop("pending_used", None)
                    report["reverted"].append({
                        "category": cat, "reason": "category count or total severity did not fall"})
                    report["rejected"].append({"category": cat, "reason": "undone: burden did not fall"})
            if not moved:
                break
    except jev.JevUnavailable as e:
        report["jev"] = f"skipped: {e}"
        report["rejected"].append({"reason": report["jev"]})
        report["kept"] = []
        report["words_cut"] = 0
        return original, report
    report["words_cut"] = sum(int(row.get("words") or 0) for row in report["kept"])
    if current != original and _score_fell(original, current):
        report["reverted"].append({"reason": "edit guard: detector score fell past max_tell_drop"})
        report["rejected"].append({"reason": "edit guard reverted the page"})
        report["kept"] = []
        report["words_cut"] = 0
        return original, report
    return current, report


def _score_fell(before: str, after: str) -> bool:
    from bookforge.core.editor import _guard_cfg, _tell_score
    _mode, drop = _guard_cfg()
    return _tell_score(after) < _tell_score(before) - drop


def maybe_revise(text: str, ctx, step_dir=None) -> tuple[str, dict]:
    """deai_chapter hook. Does nothing when the reviser is disabled."""
    cfg = revise_settings(None)
    if not cfg["enabled"]:
        return text, _blank("skipped: disabled", enabled=False)
    names = ctx.cast_names() if hasattr(ctx, "cast_names") else None
    return revise(text, log_dir=jev_tells.project_log_dir(ctx), names=names, force=True)
