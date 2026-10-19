"""De-AI pipeline: deterministic scrub + lint, then targeted LLM rewrite of flagged paragraphs."""
from __future__ import annotations

import re
from pathlib import Path

STYLE_DIR = Path(__file__).resolve().parent.parent / "style"


def rules_text() -> str:
    return (STYLE_DIR / "deai_rules.md").read_text()


def banned_words() -> list[str]:
    return [w.strip().lower() for w in (STYLE_DIR / "banned_words.txt").read_text().splitlines()
            if w.strip() and not w.startswith("#")]


# ------------------------------------------------------------------ scrub
def scrub(text: str) -> str:
    """Token-level scrub: dashes, smart quotes, ellipsis char. Idempotent."""
    t = text
    t = t.replace("\u201c", '"').replace("\u201d", '"').replace("\u201e", '"')
    t = t.replace("\u2018", "'").replace("\u2019", "'").replace("\u201a", "'")
    t = t.replace("\u2026", "...")
    t = re.sub(r"(\d)\s*\u2013\s*(\d)", r"\1-\2", t)        # ranges 1990-1995
    t = re.sub(r"\s*[\u2014\u2013]+\s*(?=[\"'])", "...", t)  # interrupted speech before close quote
    t = re.sub(r"(?<=[\"'])\s*[\u2014\u2013]+\s*", "", t)     # dash right after open quote
    t = re.sub(r"\s*[\u2014\u2013]+\s*$", ".", t, flags=re.M)  # trailing dash at line end
    t = re.sub(r"\s*--+\s*", ", ", t)
    t = re.sub(r"\s*[\u2014\u2013]+\s*", ", ", t)
    t = re.sub(r",\s*([.,;:!?])", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t


# ------------------------------------------------------------------ lint
NOT_BUT = [
    re.compile(r"\bnot\b[^.!?;\n]{1,60}?,?\s*\bbut\b", re.I),
    re.compile(r"\b(it|this|that)(?:'s| is| was)\s+not\b[^.!?\n]{1,60}?[.,;]\s*(it|this|that)(?:'s| is| was)\b", re.I),
    re.compile(r"\bless\b[^.!?\n]{1,40}?,\s*more\b", re.I),
    re.compile(r"\bisn't (?:just |only )?(?:about )?[^.!?\n]{1,50}?[,;]\s*(?:it's|it is)\b", re.I),
]
_DIALOGUE = re.compile(r'"[^"\n]*"')
_VERBISH = re.compile(
    r"\b(is|was|were|are|am|be|been|had|has|have|did|do|does|could|would|should|might|must|can|will|"
    r"\w+ed|\w+s|went|came|saw|said|took|made|felt|knew|thought|ran|stood|sat|got|let|put|held|left|"
    r"kept|told|found|gave|began|seemed|looked|turned|moved|fell|rose|heard|brought)\b", re.I)


def split_paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _sentences(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", s) if x.strip()]


def lint_paragraph(p: str, names: list[str] | None = None, prev: str = "") -> list[dict]:
    issues = []
    if re.search(r"[\u2014\u2013\u201c\u201d\u2018\u2019\u2026]", p):
        issues.append({"rule": "token", "detail": "em/en dash, smart quote or ellipsis char"})
    for rx in NOT_BUT:
        m = rx.search(p)
        if m:
            issues.append({"rule": "not_x_but_y", "detail": m.group(0)[:80]})
            break
    low = p.lower()
    for w in banned_words():
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            issues.append({"rule": "stock_word", "detail": w})
    narration = _DIALOGUE.sub(" ", p)
    for s in _sentences(narration):
        words = re.findall(r"[A-Za-z']+", s)
        if 0 < len(words) <= 6 and not _VERBISH.search(s):
            issues.append({"rule": "fragment", "detail": s[:60]})
    if names and prev:
        first = _sentences(narration)[:1]
        if first and re.match(r"^(He|She|They|It|His|Her)\b", first[0]):
            present = {n for n in names if re.search(r"\b" + re.escape(n) + r"\b", prev)}
            if len(present) >= 2:
                issues.append({"rule": "unclear_referent",
                               "detail": f"opens with pronoun after {sorted(present)}"})
    return issues


def lint(text: str, names: list[str] | None = None) -> dict:
    paras = split_paragraphs(text)
    flagged, counts = [], {}
    for i, p in enumerate(paras):
        iss = lint_paragraph(p, names, paras[i - 1] if i else "")
        for x in iss:
            counts[x["rule"]] = counts.get(x["rule"], 0) + 1
        if iss:
            flagged.append({"index": i, "issues": iss})
    words = max(1, len(text.split()))
    return {"paragraphs": len(paras), "flagged": flagged, "counts": counts,
            "per_1k": {k: round(v * 1000 / words, 2) for k, v in counts.items()},
            "total_issues": sum(counts.values())}


def tells_score(text: str) -> float:
    """0-10, 10 = no AI tells. Used inside critique. Uses the catalog detector."""
    from bookforge.core import detector
    return detector.detect(text)["score"]


# ------------------------------------------------------------------ rewrite (stage B of the anti-AI pass)
def _deai_cfg() -> dict:
    from bookforge.config import load_config
    return load_config().get("deai", {}) or {}


def _flag_instructions(hits: list[dict]) -> str:
    seen, lines = set(), []
    for h in sorted(hits, key=lambda h: -h["severity"]):
        if h["rule"] == "em_dash":
            continue
        key = (h["rule"], h["span"].lower())
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {h['rule']}: {h['reason']}\n  FIX: {h['fix']}")
    return "\n".join(lines)


def _weight(hits) -> float:
    from bookforge.core.detector import SEV_WEIGHT
    return sum(SEV_WEIGHT.get(h["severity"], 1) for h in hits if h["rule"] != "em_dash")


def _similar(a: str, b: str, thr: float = 0.8) -> bool:
    import difflib
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= thr or (
        len(a) > 15 and a.lower().strip(" .") in b.lower())


def rewrite_flagged(text: str, names: list[str] | None = None, llm=None, persona: str = "",
                    allow: list[str] | None = None, voice_notes: str = "", max_loops: int | None = None
                    ) -> tuple[str, dict]:
    """Anti-AI pass. Stage A: deterministic detector + safe autofix (token scrub).
    Stage B: cheap LLM rewrites ONLY paragraphs that need it, with per-flag instructions;
    re-detect and loop (max ``deai.max_loops``, default 2). A rewrite is kept only if it lowers
    that paragraph's weighted tell count and keeps length within bounds."""
    from bookforge.core import detector
    from bookforge.core.llm import llm_call
    llm = llm or llm_call
    cfg = _deai_cfg()
    loops = int(max_loops if max_loops is not None else cfg.get("max_loops", 2))
    min_sev = int(cfg.get("min_severity", 2))
    cluster = int(cfg.get("cluster", 3))
    text, auto = detector.autofix(text)
    det_before = detector.detect(text, allow, names)
    before = lint(text, names)
    before["detector"] = det_before
    paras = split_paragraphs(text)
    changed: list[int] = []
    system = ((persona + "\n\n") if persona else "") + (
        "You are a line editor removing AI-prose tells from fiction. Apply these rules exactly:\n" + rules_text()
        + ("\n\nCHARACTER VOICE NOTES (preserve these voices):\n" + voice_notes if voice_notes else ""))
    loops_run = 0
    for loop in range(loops):
        live = [i for i in range(len(paras)) if paras[i].strip()]
        det = detector.detect("\n\n".join(paras[i] for i in live), allow, names)
        todo = []
        for j, i in enumerate(live):
            hs = det["flagged_paragraphs"].get(j, [])
            legacy = [x for x in lint_paragraph(paras[i], names, paras[i - 1] if i else "")
                      if x["rule"] not in ("token", "fragment")]
            if (hs and detector.needs_rewrite(hs, min_sev, cluster)) or legacy:
                todo.append((i, hs, legacy))
        if not todo:
            break
        loops_run += 1
        for i, hs, legacy in todo:
            ctx_prev = paras[i - 1] if i else ""
            ctx_next = paras[i + 1] if i + 1 < len(paras) else ""
            problems = _flag_instructions(hs)
            extra = "\n".join(f"- {x['rule']}: {x['detail']}" for x in legacy
                               if not any(h["rule"] == x["rule"] for h in hs))
            problems = "\n".join(x for x in (problems, extra) if x)
            prompt = (f"PREVIOUS PARAGRAPH (context only, do not output):\n{ctx_prev}\n\n"
                      f"PARAGRAPH TO FIX:\n{paras[i]}\n\nPROBLEMS FOUND:\n{problems}\n\n"
                      f"NEXT PARAGRAPH (context only, do not output):\n{ctx_next}\n\n"
                      "Rewrite ONLY this paragraph so each listed problem is gone. Fix only what is listed. "
                      "Keep every event, fact, name and the meaning of every line of dialogue; keep each "
                      "speaker's voice and dialect. Keep the length within 25%. Do not introduce new clichés, "
                      "em dashes, or contrast frames, and do not repeat anything from the context paragraphs. "
                      "If the paragraph is only a filler pause with no event or line of dialogue (e.g. 'A beat.'), "
                      "output exactly DELETE. Output only the paragraph.")
            new = scrub(llm(prompt, system=system, role="deai", max_tokens=1200, temperature=0.4)).strip()
            old_n, new_n = len(paras[i].split()), len(new.split())
            if new.strip().strip(".").upper() == "DELETE":
                if old_n <= 6 and '"' not in paras[i]:
                    paras[i] = ""
                    changed.append(i)
                continue
            if not new or "\n\n" in new.strip() or not (0.7 * old_n <= new_n <= 1.35 * old_n + 5):
                continue
            if any(c and _similar(new, c) for c in (ctx_prev, ctx_next)):
                continue
            new_hits = detector.detect(new, allow, names)["hits"]
            new_legacy = [x for x in lint_paragraph(new, names, ctx_prev) if x["rule"] not in ("token", "fragment")]
            if (_weight(new_hits) + len(new_legacy)) < (_weight(hs) + len(legacy)):
                paras[i] = new
                if i not in changed:
                    changed.append(i)
    out = "\n\n".join(p for p in paras if p.strip())
    after = lint(out, names)
    det_after = detector.detect(out, allow, names)
    after["detector"] = det_after
    return out, {"before": before, "after": after, "rewritten_paragraphs": changed, "autofix": auto,
                 "loops": loops_run, "score_before": det_before["score"], "score_after": det_after["score"],
                 "residual": [{k: h[k] for k in ("para", "rule", "severity", "span")} for h in det_after["hits"]
                              if h["severity"] >= 2][:50]}
