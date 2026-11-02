"""Latin-pivot stages. Client calls stay at the stage boundary, behind fakes."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from bookforge.config import load_config
from bookforge.core import jev, llm, talkie
from bookforge.core.latin_pivot.budget import Budget, BudgetExceeded, gemma_cost, jev_cost, jev_spent, role_input_cost
from bookforge.core.latin_pivot.fidelity import evaluate as evaluate_fidelity
from bookforge.core.latin_pivot.picker import choice_questions, choice_state, decide, sentence_around
from bookforge.core.latin_pivot.seeds import (
    SeedLost,
    assert_seeds_preserved,
    is_seed_only,
    map_outside_seeds,
    repair_seeds,
    strip_markers,
)
from bookforge.core.latin_pivot.synonyms import WordNetSynonymSource, suggest
from bookforge.core.latin_pivot.tagger import find_candidates, load_tell_terms, tag_questions, tag_state
from bookforge.core.latin_pivot.talkie_lifecycle import TalkieSession
from bookforge.core.latin_pivot.motifs import (
    corrective_note,
    latin_hits,
    match_always_flag,
    motif_density,
    motif_instruction,
    still_question,
    still_state,
)
from bookforge.core.latin_pivot.translate import (
    REPEAT_PENALTY,
    RETRY_TEMPERATURE,
    STOPS,
    TEMPERATURE,
    TOP_K,
    build_prompt,
    draft_instruction,
    latin_word_count,
    load_fewshot,
    n_predict_for,
    split_paragraphs,
)

STAGES = ["latin", "translate", "fidelity", "tag", "synonyms", "pick", "scrub"]


def enabled(ctx=None, cfg=None) -> bool:
    """True when this write should run the latin-pivot pipeline."""
    if ctx is not None and getattr(ctx, "latin_pivot", False):
        return True
    cfg = cfg or load_config()
    mode = str((cfg.get("draft") or {}).get("mode") or "standard").strip().lower()
    return mode == "latin_pivot"


def stage_range(from_stage: str | None, to_stage: str | None) -> list[str]:
    start = from_stage or STAGES[0]
    end = to_stage or STAGES[-1]
    if start not in STAGES or end not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}")
    if STAGES.index(start) > STAGES.index(end):
        raise ValueError(f"from-stage {start} is after to-stage {end}")
    return STAGES[STAGES.index(start): STAGES.index(end) + 1]


def output_dir(project: Path, chapter: str, scene: int | None, cfg: dict, override: str | Path | None = None) -> Path:
    if override:
        return Path(override)
    custom = (cfg.get("latin_pivot") or {}).get("out_dir")
    if custom:
        return Path(custom)
    leaf = chapter if scene is None else f"{chapter}-s{scene}"
    return Path(project) / "runs" / "latin_pivot" / leaf


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _done(path: Path) -> dict[tuple[int, str], dict]:
    return {(int(rec["index"]), rec["input_hash"]): rec for rec in _records(path)}


def _latest(path: Path) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for rec in _records(path):
        out[int(rec["index"])] = rec
    return out


def _append(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _join(records: dict[int, dict], count: int, key: str = "output") -> str:
    body = "\n\n".join(records[i][key] for i in range(count))
    return (body + "\n") if body else ""


def _lp(cfg: dict) -> dict:
    return cfg.get("latin_pivot") or {}


def _jev_model(cfg: dict) -> str:
    return ((cfg.get("screen") or {}).get("model") or "jev-1.13.0")


def _needs_talkie(stages: list[str]) -> bool:
    return bool(set(stages) & {"translate", "fidelity"})


def cost_estimate(latin_text: str | None, cfg: dict, stages: list[str], *, no_jev: bool = False,
                  have_latin: bool = True) -> dict:
    """Offline estimate. Talkie is $0. Tag and pick depend on candidates and are not included."""
    paras = split_paragraphs(latin_text or "")
    words = latin_word_count(latin_text or "")
    minutes = (words / 1000.0) * 13.0 if "translate" in stages else 0.0
    jev_usd = 0.0
    fidelity_on = "fidelity" in stages and not no_jev and bool((_lp(cfg).get("fidelity") or {}).get("jev", True))
    if fidelity_on:
        from bookforge.core.latin_pivot.fidelity import fidelity_questions, fidelity_state
        for para in paras:
            if is_seed_only(para):
                continue
            jev_usd += jev_cost(fidelity_state(para, para), fidelity_questions())
    gemma = 0.0
    if "latin" in stages and not have_latin:
        gemma = None
    return {
        "stages": list(stages),
        "paragraphs": len(paras),
        "talkie_usd": 0.0,
        "talkie_minutes_est": round(minutes, 2),
        "jev_usd_est": round(jev_usd, 8),
        "gemma_usd_est": gemma,
        "cost_usd_est": round(jev_usd + (gemma or 0.0), 8),
        "note": "Tag and pick costs depend on tell-list candidates and are not included. "
                "Talkie is about 13 minutes per 1000 Latin words on an 8-thread CPU at Q4.",
    }


def format_motif_density(density: dict | None) -> str:
    """One line per text. Missing blocks print as zero."""
    density = density or {}
    lines = []
    for key in ("latin", "english_raw", "final"):
        block = density.get(key) or {"hits": 0, "words": 0, "per_1k": 0.0}
        lines.append(
            f"motif_density {key}: {block['per_1k']:.1f} per_1k "
            f"({block['hits']} hits / {block['words']} words)"
        )
    return "\n".join(lines)


def _density_report(latin: str, english_raw: str, final: str, lp: dict) -> dict:
    ban = lp.get("motif_ban") or {}
    phrases = (lp.get("always_flag") or {}).get("phrases")
    patterns = ban.get("latin_patterns")

    def english(text: str):
        return match_always_flag(text, phrases)

    return {
        "latin": motif_density(latin or "", lambda text: latin_hits(text, patterns)),
        "english_raw": motif_density(english_raw or "", english),
        "final": motif_density(final or "", english),
    }


def _density_from_files(folder: Path, lp: dict, latin_preview: str = "") -> dict:
    def read(name: str, fallback: str = "") -> str:
        path = folder / name
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return fallback

    return _density_report(
        read("latin.txt", latin_preview),
        read("english_raw.txt"),
        read("final.txt"),
        lp,
    )


def format_estimate(est: dict) -> str:
    gemma = "unknown" if est["gemma_usd_est"] is None else f"{est['gemma_usd_est']:.6f}"
    lines = [
        "stages: " + ", ".join(est["stages"]),
        f"paragraphs: {est['paragraphs']}",
        f"cost_usd_est: {est['cost_usd_est']:.6f}",
        "talkie_usd: 0",
        f"talkie_minutes_est: {est['talkie_minutes_est']:.2f}",
        f"jev_usd_est: {est['jev_usd_est']:.6f}",
        f"gemma_usd_est: {gemma}",
        est["note"],
    ]
    if est.get("motif_density") is not None:
        lines.append(format_motif_density(est["motif_density"]))
    return "\n".join(lines)


def _translate_one(para: str, *, fewshot: dict, endpoint: str, seed: int, temperature: float,
                   log_dir: Path, budget: Budget) -> str:
    if is_seed_only(para):
        return para
    prompt = build_prompt(para, fewshot=fewshot)
    n_predict = n_predict_for(para)
    budget.ensure("translate", 0.0)
    text, _rec = talkie.complete(
        prompt, n_predict, int(seed), STOPS,
        temperature=temperature, top_k=TOP_K, repeat_penalty=REPEAT_PENALTY,
        cache_prompt=True, endpoint=endpoint, log_dir=log_dir,
    )
    budget.note_talkie("translate")
    return (text or "").strip()


def _with_seeds(para: str, english: str, *, translate_again) -> str:
    fixed, status = repair_seeds(para, english)
    if status != "lost":
        return fixed
    fixed, status = repair_seeds(para, translate_again())
    if status == "lost":
        raise SeedLost(f"paragraph lost <<SEED>> markers after retry: {para[:80]!r}")
    return fixed


def _draft_latin(ctx, chapter, scene, so_far: str, step_dir: Path, cfg: dict, budget: Budget) -> str:
    from bookforge.core import pack
    from bookforge.core.human_seed import seed_for_scene
    from bookforge.craft import engine as craft
    from bookforge.steps import write_chapter as wc

    opening = seed_for_scene(ctx, chapter, scene) if ctx is not None else ""
    brief = craft.brief(ctx, chapter, scene) if ctx is not None else ""
    card = {**scene, "craft_brief": brief} if brief else dict(scene)
    seed_lines = [ln for ln in (opening or "").splitlines() if ln.strip()] if opening else []
    ban = ((cfg.get("latin_pivot") or {}).get("motif_ban") or {})
    ban_note = ""
    if ban.get("enabled", True):
        ban_note = motif_instruction(ban.get("english"), ban.get("latin"))
    plan_blob = pack.chapter_pack(ctx, chapter) + pack.chapter_plan_block(chapter, scene) + ban_note
    plan_est = role_input_cost("plan", "", plan_blob, cfg)
    budget.ensure("latin", plan_est)
    before = llm.spent()
    beats = wc._plan_beats(ctx, chapter, card, step_dir, extra=ban_note)
    budget.charge_observed("latin", "gemma", plan_est, llm.spent() - before)
    scene_for_write = {**scene, "beats": beats, "craft_brief": brief}
    groups = wc._beat_groups(ctx, scene_for_write)
    pieces = []
    extra = draft_instruction(seed_lines)
    if ban_note:
        extra = extra + "\n\n" + ban_note
    from bookforge.config import resolve_role
    patterns = ban.get("latin_patterns")
    regen = bool(ban.get("enabled", True)) and str(ban.get("action") or "regenerate") == "regenerate"
    limit = int(ban.get("max_regens", 1))
    for i, group in enumerate(groups, 1):
        words = _ceiling(group)
        system, prompt = pack.build_write(ctx, chapter, scene_for_write, group, so_far, words, i, len(groups))
        if opening and i == 1:
            from bookforge.core.human_seed import continuation_note
            prompt += "\n\n" + continuation_note(opening)
        prompt += "\n\n" + extra
        provider, model = resolve_role("write", cfg)
        system, prompt = pack.fit_write_context(ctx, system, prompt, provider, model)
        est = gemma_cost(system, prompt, cfg)
        budget.ensure("latin", est)
        before = llm.spent()
        out = llm.llm_call(prompt, system=system, role="write", temperature=0.8,
                           max_tokens=wc.write_max_tokens(words))
        budget.charge_observed("latin", "gemma", est, llm.spent() - before)
        if regen:
            for _attempt in range(max(0, limit)):
                hits = latin_hits(out or "", patterns)
                if not hits:
                    break
                prompt_r = prompt + "\n\n" + corrective_note(hits)
                est = gemma_cost(system, prompt_r, cfg)
                budget.ensure("latin", est)
                before = llm.spent()
                out = llm.llm_call(prompt_r, system=system, role="write", temperature=0.8,
                                   max_tokens=wc.write_max_tokens(words))
                budget.charge_observed("latin", "gemma", est, llm.spent() - before)
        pieces.append((out or "").strip())
    return "\n\n".join(p for p in pieces if p)


def _ceiling(beats: list[str]) -> int:
    from bookforge.core import length as length_mod
    return length_mod.ceiling(sum(length_mod.beat_words(b) for b in beats))


def _scrub_paragraph(text: str) -> str:
    from bookforge.core import detector

    def fix(chunk: str) -> str:
        if not chunk:
            return chunk
        return detector.autofix(chunk)[0]

    return map_outside_seeds(text, fix)


def _billed_ask(budget: Budget, stage: str, folder: Path):
    def ask(state, questions, model="jev-1.13.0", tag="", log_dir=None, timeout=30.0, cap_usd=1.0):
        est = jev_cost(state, questions)
        budget.ensure(stage, est)
        target = Path(log_dir) if log_dir is not None else folder
        before = jev_spent(target)
        res = jev.ask(state, questions, model=model, tag=tag, log_dir=target, timeout=timeout,
                      cap_usd=budget.cap)
        budget.charge_observed(stage, "jev", est, jev_spent(target) - before)
        return res

    return ask


def execute(*, project, chapter: str, scene: int | None = None, latin_file: str | Path | None = None,
            from_stage: str | None = None, to_stage: str | None = None, out_dir: str | Path | None = None,
            no_jev: bool = False, dry_run: bool = False, cap_usd: float | None = None, cfg: dict | None = None,
            synonym_source=None, runner=None, ping=None, ctx=None, chapter_obj=None, scene_obj=None,
            so_far: str = "", step_dir: Path | None = None) -> dict:
    """Run the selected stages. ``dry_run`` prints the plan and does not call anyone."""
    cfg = cfg or load_config()
    lp = _lp(cfg)
    stages = stage_range(from_stage, to_stage)
    folder = output_dir(Path(project), chapter, scene, cfg, out_dir)
    given = Path(latin_file) if latin_file else (Path(lp["latin_file"]) if lp.get("latin_file") else None)
    have_latin = bool(given and given.exists()) or (folder / "latin.txt").exists()
    latin_preview = ""
    if given and given.exists():
        latin_preview = given.read_text(encoding="utf-8")
    elif (folder / "latin.txt").exists():
        latin_preview = (folder / "latin.txt").read_text(encoding="utf-8")
    estimate = cost_estimate(latin_preview, cfg, stages, no_jev=no_jev,
                             have_latin=have_latin or "latin" not in stages)
    if dry_run:
        estimate["motif_density"] = _density_from_files(folder, lp, latin_preview)
        print(format_estimate(estimate))
        return {"dry_run": True, "out_dir": folder, "estimate": estimate, "final": ""}

    folder.mkdir(parents=True, exist_ok=True)
    cap = float(lp.get("cap_usd", 0.50) if cap_usd is None else cap_usd)
    budget = Budget(cap)
    timings: dict[str, float] = {}
    flagged: list[int] = []
    side = {"latin_motif_hits": [], "always_flag": []}
    aborted = None
    final = ""
    talkie_cfg = dict(lp.get("talkie") or {})
    need = _needs_talkie(stages)
    session_cm = TalkieSession(talkie_cfg, runner=runner, ping=ping) if need else None

    def _run():
        nonlocal final
        latin = _stage_latin(stages, folder, given, ctx, chapter_obj, scene_obj, so_far, step_dir, cfg, budget, timings, side)
        paras = split_paragraphs(latin)
        english = _stage_translate(stages, folder, paras, lp, cfg, budget, timings)
        english = _stage_fidelity(stages, folder, paras, english, lp, cfg, budget, timings, flagged, no_jev)
        tagged = _stage_tag(stages, folder, english, lp, cfg, budget, timings, no_jev, side)
        with_syn = _stage_synonyms(stages, folder, tagged, lp, timings, synonym_source)
        swapped = _stage_pick(stages, folder, with_syn, lp, cfg, budget, timings, no_jev)
        final = _stage_scrub(stages, folder, latin, swapped, timings)

    try:
        if session_cm is not None:
            with session_cm:
                _run()
        else:
            _run()
    except BudgetExceeded as exc:
        aborted = str(exc)
        raise
    finally:
        report = {
            "chapter": chapter,
            "scene": scene,
            "stages": stages,
            "timings_s": {k: round(v, 4) for k, v in timings.items()},
            "calls": dict(budget.calls),
            "cost_by_stage": dict(budget.by_stage),
            "spent_usd": round(budget.spent, 8),
            "cap_usd": budget.cap,
            "flagged_paragraphs": flagged,
            "aborted_reason": aborted,
            "dry_run": False,
            "estimate": estimate,
            "latin_motif_hits": list(side.get("latin_motif_hits") or []),
            "always_flag_count": len(side.get("always_flag") or []),
            "motif_density": _density_from_files(folder, lp, ""),
        }
        (folder / "run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {"dry_run": False, "out_dir": folder, "final": final, "report": report, "estimate": estimate}


def _latin_motif_rows(text: str, lp: dict) -> list[dict]:
    ban = lp.get("motif_ban") or {}
    if not ban.get("enabled", True):
        return []
    hits = latin_hits(text, ban.get("latin_patterns"))
    return [{"paragraph_index": hit["paragraph_index"], "word": hit["word"], "sentence": hit["sentence"]}
            for hit in hits]


def _write_latin_motifs(folder: Path, rows: list[dict]) -> None:
    if not rows:
        return
    (folder / "latin_motif_hits.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _stage_latin(stages, folder, given, ctx, chapter_obj, scene_obj, so_far, step_dir, cfg, budget, timings, side) -> str:
    path = folder / "latin.txt"
    if "latin" not in stages:
        if not path.exists():
            raise FileNotFoundError(f"latin stage skipped and {path} is missing")
        text = path.read_text(encoding="utf-8")
        rows = _latin_motif_rows(text, _lp(cfg))
        side["latin_motif_hits"] = rows
        _write_latin_motifs(folder, rows)
        return text
    t0 = time.perf_counter()
    if given is not None:
        text = Path(given).read_text(encoding="utf-8")
    elif ctx is not None and chapter_obj is not None and scene_obj is not None:
        work = Path(step_dir) if step_dir is not None else folder
        text = _draft_latin(ctx, chapter_obj, scene_obj, so_far or "", work, cfg, budget)
    elif path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        raise FileNotFoundError("latin stage needs a project scene or --latin-file")
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    rows = _latin_motif_rows(text, _lp(cfg))
    side["latin_motif_hits"] = rows
    _write_latin_motifs(folder, rows)
    timings["latin"] = time.perf_counter() - t0
    return text


def _stage_translate(stages, folder, paras, lp, cfg, budget, timings) -> list[str]:
    path = folder / "english_raw.txt"
    ck = folder / "checkpoints" / "translate.jsonl"
    if "translate" not in stages:
        fid = _latest(folder / "checkpoints" / "fidelity.jsonl")
        if paras and len(fid) == len(paras):
            return [fid[i]["output"] for i in range(len(paras))]
        if path.exists():
            return split_paragraphs(path.read_text(encoding="utf-8"))
        latest = _latest(ck)
        if len(latest) == len(paras) and paras:
            return [latest[i]["output"] for i in range(len(paras))]
        raise FileNotFoundError("translate stage skipped and english_raw.txt is missing")
    t0 = time.perf_counter()
    fewshot = load_fewshot(lp.get("fewshot_path"))
    endpoint = (lp.get("talkie") or {}).get("endpoint") or "http://127.0.0.1:8080"
    done = _done(ck)
    latest = _latest(ck)
    for index, para in enumerate(paras):
        key = text_hash(para)
        if (index, key) in done:
            latest[index] = done[(index, key)]
            continue
        base = 1000 + index * 17

        def again(paragraph=para, seed=base + 17):
            return _translate_one(paragraph, fewshot=fewshot, endpoint=endpoint, seed=seed,
                                  temperature=RETRY_TEMPERATURE, log_dir=folder, budget=budget)

        if is_seed_only(para):
            english = para
        else:
            first = _translate_one(para, fewshot=fewshot, endpoint=endpoint, seed=base,
                                   temperature=TEMPERATURE, log_dir=folder, budget=budget)
            english = _with_seeds(para, first, translate_again=again)
        rec = {"index": index, "input_hash": key, "output": english}
        _append(ck, rec)
        latest[index] = rec
    text = _join(latest, len(paras))
    path.write_text(text, encoding="utf-8")
    timings["translate"] = time.perf_counter() - t0
    return [latest[i]["output"] for i in range(len(paras))]


def _stage_fidelity(stages, folder, paras, english, lp, cfg, budget, timings, flagged, no_jev) -> list[str]:
    if "fidelity" not in stages:
        return english
    t0 = time.perf_counter()
    fid = lp.get("fidelity") or {}
    jev_on = bool(fid.get("jev", True)) and not no_jev
    ck = folder / "checkpoints" / "fidelity.jsonl"
    done = _done(ck)
    latest = _latest(ck)
    out_path = folder / "fidelity.jsonl"
    rows = []
    chosen: list[str] = []
    ask = _billed_ask(budget, "fidelity", folder)
    for index, para in enumerate(paras):
        src = english[index]
        key = text_hash(para + "\n" + src)
        if (index, key) in done:
            rec = done[(index, key)]
            latest[index] = rec
            chosen.append(rec["output"])
            if rec.get("flagged"):
                flagged.append(index)
            rows.append(rec)
            continue
        base = 1000 + index * 17

        def retranslate(n, paragraph=para, seed0=base):
            fewshot = load_fewshot(lp.get("fewshot_path"))
            endpoint = (lp.get("talkie") or {}).get("endpoint") or "http://127.0.0.1:8080"
            raw = _translate_one(paragraph, fewshot=fewshot, endpoint=endpoint,
                                 seed=seed0 + 10007 * n, temperature=RETRY_TEMPERATURE,
                                 log_dir=folder, budget=budget)
            return _with_seeds(paragraph, raw, translate_again=lambda: _translate_one(
                paragraph, fewshot=fewshot, endpoint=endpoint, seed=seed0 + 10007 * n + 1,
                temperature=RETRY_TEMPERATURE, log_dir=folder, budget=budget))

        result = evaluate_fidelity(
            para, src, ratio_min=float(fid.get("ratio_min", 0.9)), ratio_max=float(fid.get("ratio_max", 2.2)),
            jev_on=jev_on, threshold=float(fid.get("jev_threshold", 0.6)), retries=int(fid.get("retries", 1)),
            ask=ask, retranslate=retranslate, model=_jev_model(cfg), log_dir=folder, cap_usd=budget.cap,
        )
        rec = {"index": index, "input_hash": key, "output": result["text"], "flagged": result["flagged"],
               "heuristic": result["heuristic"], "heuristic_ok": result["heuristic_ok"],
               "jev_p": result["jev_p"], "jev_mode": result["jev_mode"],
               "attempts": result["attempts"], "passed": result["passed"]}
        _append(ck, rec)
        latest[index] = rec
        chosen.append(result["text"])
        if result["flagged"]:
            flagged.append(index)
        rows.append(rec)
    out_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    timings["fidelity"] = time.perf_counter() - t0
    return chosen


def _flag_rows(index: int, hits: list[dict]) -> list[dict]:
    rows = []
    for hit in hits:
        rows.append({
            "paragraph_index": index,
            "sentence_index": int(hit.get("sentence_index") or 0),
            "sentence": hit.get("sentence") or "",
            "motif": hit.get("motif") or "",
            "matched_text": hit.get("matched_text") or "",
            "span": [int(hit["start"]), int(hit["end"])],
            "ambiguous": bool(hit.get("ambiguous")),
        })
    return rows


def _resolve_still(hits: list[dict], *, classifier: str, jev_on: bool, ask, cfg, budget) -> list[dict]:
    """Keep stillness hits. Ambiguous ``still`` may take one capped Jev noul."""
    kept = []
    for hit in hits:
        if str(hit.get("motif") or "").lower() != "still" or hit.get("sense") is True:
            item = dict(hit)
            if str(hit.get("motif") or "").lower() != "still":
                item["ambiguous"] = False
            kept.append(item)
            continue
        if hit.get("sense") is False:
            continue
        use_jev = jev_on and str(classifier or "jev").lower() == "jev"
        if not use_jev:
            item = dict(hit)
            item["ambiguous"] = True
            kept.append(item)
            continue
        try:
            res = ask(still_state(hit.get("sentence") or ""), still_question(hit.get("sentence") or ""),
                      model=_jev_model(cfg), tag="latin_pivot:still", log_dir=None, cap_usd=budget.cap)
        except jev.JevUnavailable:
            item = dict(hit)
            item["ambiguous"] = True
            kept.append(item)
            continue
        score = jev.noul(res, "stillness")
        if score is None or float(score) < 0.5:
            continue
        item = dict(hit)
        item["sense"] = True
        item["ambiguous"] = False
        kept.append(item)
    return kept


def _drop_flagged_candidates(candidates: list[dict], flags: list[dict]) -> list[dict]:
    spans = [(int(hit["start"]), int(hit["end"])) for hit in flags]
    kept = []
    for candidate in candidates:
        start, end = int(candidate["start"]), int(candidate["end"])
        if any(start < stop and begin < end for begin, stop in spans):
            continue
        kept.append(candidate)
    return kept


def _stage_tag(stages, folder, english, lp, cfg, budget, timings, no_jev, side) -> list[dict]:
    tag_cfg = lp.get("tag") or {}
    if "tag" not in stages:
        return [{"index": i, "text": text, "kept": []} for i, text in enumerate(english)]
    t0 = time.perf_counter()
    terms = load_tell_terms()
    limit = int(tag_cfg.get("max_per_paragraph", 12))
    skip = bool(tag_cfg.get("skip_dialogue", False))
    threshold = float(tag_cfg.get("threshold", 0.6))
    jev_on = not no_jev
    flag_cfg = lp.get("always_flag") or {}
    flag_on = bool(flag_cfg.get("enabled", True))
    phrases = flag_cfg.get("phrases")
    classifier = flag_cfg.get("still_classifier", "jev")
    ck = folder / "checkpoints" / "tag.jsonl"
    done = _done(ck)
    rows = []
    packed = []
    all_flags: list[dict] = []
    ask = _billed_ask(budget, "tag", folder)
    for index, text in enumerate(english):
        key = text_hash(text)
        if (index, key) in done:
            rec = done[(index, key)]
            rows.append(rec)
            packed.append({"index": index, "text": text, "kept": rec.get("kept") or []})
            all_flags.extend(rec.get("always_flag") or [])
            continue
        raw_flags = match_always_flag(text, phrases) if flag_on else []
        flags = _resolve_still(raw_flags, classifier=classifier, jev_on=jev_on, ask=ask,
                               cfg=cfg, budget=budget) if raw_flags else []
        flag_rows = _flag_rows(index, flags)
        candidates = _drop_flagged_candidates(
            find_candidates(text, terms, skip_dialogue=skip, limit=limit), flags)
        mode = "off"
        if candidates and jev_on:
            try:
                res = ask(tag_state(text), tag_questions(candidates), model=_jev_model(cfg),
                          tag="latin_pivot:tag", log_dir=folder, cap_usd=budget.cap)
                mode = "noul"
                for i, hit in enumerate(candidates):
                    raw = jev.noul(res, f"c{i}")
                    hit["p"] = None if raw is None else float(raw)
                    hit["kept"] = hit["p"] is not None and hit["p"] >= threshold
            except jev.JevUnavailable:
                mode = "unavailable"
                for hit in candidates:
                    hit["p"] = None
                    hit["kept"] = False
        else:
            for hit in candidates:
                hit["p"] = None
                hit["kept"] = False
            mode = "off" if not jev_on else mode
        kept = [h for h in candidates if h.get("kept")]
        rec = {"index": index, "input_hash": key, "output": text, "candidates": candidates,
               "kept": kept, "always_flag": flag_rows, "jev_mode": mode}
        _append(ck, rec)
        rows.append(rec)
        packed.append({"index": index, "text": text, "kept": kept})
        all_flags.extend(flag_rows)
    (folder / "tags.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                       encoding="utf-8")
    (folder / "flag_for_edit.json").write_text(
        json.dumps(all_flags, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    side["always_flag"] = all_flags
    timings["tag"] = time.perf_counter() - t0
    return packed


def _stage_synonyms(stages, folder, packed, lp, timings, synonym_source) -> list[dict]:
    if "synonyms" not in stages:
        for row in packed:
            for hit in row.get("kept") or []:
                hit.setdefault("synonyms", [])
        return packed
    t0 = time.perf_counter()
    limit = int((lp.get("synonyms") or {}).get("max", 8))
    tells = {t.lower() for t in load_tell_terms()}
    source = synonym_source
    rows = []
    ck = folder / "checkpoints" / "synonyms.jsonl"
    done = _done(ck)
    for row in packed:
        blob = row["text"] + "\n" + json.dumps(row.get("kept") or [], ensure_ascii=False, sort_keys=True)
        key = text_hash(blob)
        if (row["index"], key) in done:
            saved = done[(row["index"], key)]
            row["kept"] = saved.get("kept") or []
            rows.append(saved)
            continue
        kept = []
        for hit in row.get("kept") or []:
            if source is None:
                source = WordNetSynonymSource()
            hit = dict(hit)
            hit["synonyms"] = suggest(hit["text"], source, tells, limit=limit)
            kept.append(hit)
        row["kept"] = kept
        rec = {"index": row["index"], "input_hash": key, "output": row["text"], "kept": kept}
        _append(ck, rec)
        rows.append(rec)
    (folder / "synonyms.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                           encoding="utf-8")
    timings["synonyms"] = time.perf_counter() - t0
    return packed


def _stage_pick(stages, folder, packed, lp, cfg, budget, timings, no_jev) -> list[str]:
    if "pick" not in stages:
        return [row["text"] for row in packed]
    t0 = time.perf_counter()
    margin = float((lp.get("pick") or {}).get("margin", 0.1))
    ck = folder / "checkpoints" / "pick.jsonl"
    done = _done(ck)
    rows = []
    texts = []
    ask = _billed_ask(budget, "pick", folder)
    jev_on = not no_jev
    for row in packed:
        key = text_hash(row["text"] + "\n" + json.dumps(row.get("kept") or [], ensure_ascii=False, sort_keys=True))
        if (row["index"], key) in done:
            rec = done[(row["index"], key)]
            texts.append(rec["output"])
            rows.append(rec)
            continue
        text = row["text"]
        decisions = []
        edits = []
        for hit in row.get("kept") or []:
            synonyms = list(hit.get("synonyms") or [])
            sentence = sentence_around(text, int(hit["start"]), int(hit["end"]))
            if not synonyms or not jev_on:
                decision = {"before": hit["text"], "after": hit["text"], "swapped": False,
                            "reason": "no_synonym" if not synonyms else "jev_off",
                            "probabilities": {}, "sentence": sentence, "start": hit["start"], "end": hit["end"]}
                decisions.append(decision)
                continue
            options = [hit["text"]] + [s for s in synonyms if s != hit["text"]]
            try:
                res = ask(choice_state(sentence, options), choice_questions(options), model=_jev_model(cfg),
                          tag="latin_pivot:pick", log_dir=folder, cap_usd=budget.cap)
            except jev.JevUnavailable:
                decision = {"before": hit["text"], "after": hit["text"], "swapped": False,
                            "reason": "jev_unavailable", "probabilities": {}, "sentence": sentence,
                            "start": hit["start"], "end": hit["end"]}
                decisions.append(decision)
                continue
            chosen, _conf = jev.choice(res, "pick")
            probs = ((res.get("answers") or {}).get("pick") or {}).get("probabilities") or {}
            decision = decide(hit["text"], synonyms, probs, chosen, margin)
            decision.update({"before": hit["text"], "sentence": sentence,
                             "start": hit["start"], "end": hit["end"]})
            decisions.append(decision)
            if decision["swapped"]:
                edits.append(decision)
        for edit in sorted(edits, key=lambda item: int(item["start"]), reverse=True):
            start, end = int(edit["start"]), int(edit["end"])
            if text[start:end] != edit["before"]:
                continue
            text = text[:start] + edit["after"] + text[end:]
        rec = {"index": row["index"], "input_hash": key, "output": text, "swaps": decisions}
        _append(ck, rec)
        rows.append(rec)
        texts.append(text)
    (folder / "swaps.jsonl").write_text("".join(json.dumps({
        "index": r["index"], "input_hash": r["input_hash"], "swaps": r.get("swaps") or []}
        , ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    timings["pick"] = time.perf_counter() - t0
    return texts


def _stage_scrub(stages, folder, latin: str, paragraphs: list[str], timings) -> str:
    if "scrub" not in stages:
        return "\n\n".join(paragraphs)
    t0 = time.perf_counter()
    ck = folder / "checkpoints" / "scrub.jsonl"
    done = _done(ck)
    latest: dict[int, dict] = {}
    for index, text in enumerate(paragraphs):
        key = text_hash(text)
        if (index, key) in done:
            latest[index] = done[(index, key)]
            continue
        cleaned = _scrub_paragraph(text)
        rec = {"index": index, "input_hash": key, "output": cleaned}
        _append(ck, rec)
        latest[index] = rec
    marked = _join(latest, len(paragraphs)).rstrip("\n")
    final = strip_markers(marked)
    assert_seeds_preserved(latin, final)
    (folder / "final.txt").write_text(final + ("\n" if final else ""), encoding="utf-8")
    from bookforge.core import detector
    report = detector.detect(final)
    report.pop("flagged_paragraphs", None)
    (folder / "detect.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (folder / "detect.txt").write_text(detector.format_report(report, final), encoding="utf-8")
    timings["scrub"] = time.perf_counter() - t0
    return final


def produce_scene(ctx, chapter, scene, so_far: str, step_dir) -> str:
    """Write-step entry. Returns the stripped English for this scene."""
    result = execute(
        project=ctx.project_dir, chapter=chapter["id"], scene=scene.get("scene_number"),
        ctx=ctx, chapter_obj=chapter, scene_obj=scene, so_far=so_far or "", step_dir=step_dir,
    )
    return result.get("final") or ""
