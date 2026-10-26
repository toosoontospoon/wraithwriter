"""De-AI pipeline: deterministic scrub + lint, then targeted LLM rewrite of flagged paragraphs."""
from __future__ import annotations

import re
from pathlib import Path

STYLE_DIR = Path(__file__).resolve().parent.parent / "style"

# Fiction overrides the email register in the vendored skill. Quoted dialogue may
# break the banlist and the fragment budget; narration may not.
FICTION_NOTE = (
    "FICTION: The style bible's narrator voice overrides any email register. "
    "Do not add a human-slip texture rule. "
    "Banned words and sentence fragments are allowed inside quoted character dialogue."
)

# Cache-stable summary of rules_text(). Kept under ~700 tokens so it can sit in the
# book-wide system prefix. Byte-for-byte the same for every call in a book.
PROSE_RULES = """PROSE RULES:
Narration follows the style bible's narrator voice. That voice overrides any email register. Do not add a human-slip texture rule. Quoted character dialogue may use banned words and fragments; narration may not.
- Banlist: no skill-banlist words or assistant phrases in narration.
- Complete sentences in narration, at least 98 percent with a subject and a finite verb.
- Clear referents and clear agency. Who does what to whom. No hedge stacks.
- No rule of three, no mirrored pairs, no "not X but Y", no soft "X, not Y" or "rather than X, Y".
- No polished thesis-style chapter or scene opener. Start inside a concrete moment.
- No tidy parallel-list or tricolon ending. End on an action, an image, or a line.
- Straight quotes. No em dashes or en dashes."""


def rules_text() -> str:
    """deai_rules.md, the vendored skill extract, and the fiction note."""
    parts = [
        (STYLE_DIR / "deai_rules.md").read_text().strip(),
        (STYLE_DIR / "vendor" / "de-ai-revision" / "rules_extract.md").read_text().strip(),
        FICTION_NOTE,
    ]
    return "\n\n".join(parts) + "\n"


def prose_rules_block() -> str:
    """Compact PROSE RULES for the cached system prefix. Empty when the flag is off."""
    from bookforge.config import load_config
    if not (load_config().get("deai") or {}).get("prompt_rules", True):
        return ""
    return PROSE_RULES


def banned_words() -> list[str]:
    return [w.strip().lower() for w in (STYLE_DIR / "banned_words.txt").read_text().splitlines()
            if w.strip() and not w.startswith("#")]


# ------------------------------------------------------------------ scrub
def _vendor():
    """Load the vendored scrubber by path so the file stays standalone."""
    import importlib.util
    import sys
    path = STYLE_DIR / "vendor" / "token_scrub" / "scrub.py"
    name = "bookforge_vendored_token_scrub"
    cached = sys.modules.get(name)
    if cached is not None and getattr(cached, "__file__", None) == str(path):
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def scrub(text: str) -> str:
    """Token-level scrub in fiction mode: dialogue-safe dashes, then the other
    Unicode tells. Idempotent. ``deai.dash_policy`` is ``fiction``; that is the
    only policy this function applies."""
    return _vendor().scrub(text, mode="fiction")[0]


def scrub_report(text: str) -> tuple[str, dict]:
    """Fiction scrub plus the vendor report counts."""
    out, report = _vendor().scrub(text, mode="fiction")
    return out, dict(report.counts)


def scrub_obj(obj):
    """Scrub string values inside a parsed JSON-like object. Keys are kept.
    Pass a dict or list, not a raw JSON string: scrubbing the serialized text
    would rewrite characters that JSON uses for quoting."""
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, list):
        return [scrub_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub_obj(v) for k, v in obj.items()}
    return obj


def count_token_tells(text: str) -> dict:
    """Remaining mechanical-token codepoints, keyed by tell name."""
    return _vendor().count_tells(text)


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


_QMAP = str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2018": "'", "\u2019": "'",
                     "\u00ab": '"', "\u00bb": '"'})


def lint_paragraph(p: str, names: list[str] | None = None, prev: str = "") -> list[dict]:
    issues = []
    if re.search(r"[\u2014\u2013\u201c\u201d\u2018\u2019\u2026]", p):
        issues.append({"rule": "token", "detail": "em/en dash, smart quote or ellipsis char"})
    for rx in NOT_BUT:
        m = rx.search(p)
        if m:
            issues.append({"rule": "not_x_but_y", "detail": m.group(0)[:80]})
            break
    # Stock words and fragments are narration-only. Quoted character dialogue is exempt.
    narration = _DIALOGUE.sub(" ", p.translate(_QMAP))
    low = narration.lower()
    for w in banned_words():
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            issues.append({"rule": "stock_word", "detail": w})
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


_LINT_SEV = {"not_x_but_y": 3, "stock_word": 2, "unclear_referent": 2, "fragment": 1}
_SOFT_RULES = {"polished_intro", "parallel_list_ending", "soft_contrast"}


def _in_dialogue(paragraph: str, start: int) -> bool:
    return paragraph[:max(0, start)].count('"') % 2 == 1


def _para_scenes(paras: list[str]) -> list[int]:
    scene = 1
    out = []
    for p in paras:
        out.append(scene)
        if re.fullmatch(r"\s*(\* \* \*|={3,}|#+.*)\s*", p or ""):
            scene += 1
    return out


def located_hits(text: str, names: list[str] | None = None, allow: list[str] | None = None,
                 min_severity: int | None = None) -> list[dict]:
    """Detector and lint hits at or above ``deai.min_severity``, narration-only for dialogue.

    Each row is a located finding: scene, paragraph index, evidence span, rule id, fix.
    Capped at ``deai.max_findings`` (default 40). Empty when ``deai.route_to_editor`` is false.
    Em-dash hits are omitted; the scrubber removes those.
    """
    from bookforge.core import detector
    cfg = _deai_cfg()
    if not cfg.get("route_to_editor", True):
        return []
    min_sev = int(cfg.get("min_severity", 2) if min_severity is None else min_severity)
    cap = int(cfg.get("max_findings", 40))
    norm = text.translate(_QMAP)
    rep = detector.detect(text, allow, names)
    paras = detector.split_paragraphs(norm)
    scenes = _para_scenes(paras)
    rows, seen = [], set()
    for h in rep["hits"]:
        if h["severity"] < min_sev or h["rule"] == "em_dash":
            continue
        para = paras[h["para"]] if 0 <= h["para"] < len(paras) else ""
        if para and _in_dialogue(para, h.get("start", 0)):
            continue
        key = (h["para"], h["rule"], (h.get("span") or "").lower())
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "rule": h["rule"], "severity": h["severity"], "para": h["para"],
            "scene": scenes[h["para"]] if h["para"] < len(scenes) else 1,
            "span": (h.get("span") or "")[:120], "reason": h.get("reason") or h["rule"],
            "fix": h.get("fix") or "", "paragraph": para,
            "confirm": h["rule"] in _SOFT_RULES,
        })
    for i, p in enumerate(paras):
        if re.fullmatch(r"\s*(\* \* \*|={3,}|#+.*)\s*", p or ""):
            continue
        prev = paras[i - 1] if i else ""
        for issue in lint_paragraph(p, names, prev):
            sev = _LINT_SEV.get(issue["rule"], 1)
            if sev < min_sev or issue["rule"] == "token":
                continue
            if any(r["para"] == i and (r["rule"] == issue["rule"] or issue["detail"].lower() in r["span"].lower())
                   for r in rows):
                continue
            detail = issue["detail"][:120]
            key = (i, issue["rule"], detail.lower())
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "rule": issue["rule"], "severity": sev, "para": i,
                "scene": scenes[i] if i < len(scenes) else 1,
                "span": detail, "reason": f"{issue['rule']}: {detail}",
                "fix": "Fix this narration tell. Leave quoted dialogue alone.",
                "paragraph": p, "confirm": False,
            })
    rows.sort(key=lambda r: (-r["severity"], r["para"], r["rule"]))
    return rows[:cap]


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
                    allow: list[str] | None = None, voice_notes: str = "", max_loops: int | None = None,
                    severe_only: bool = False,
                    ) -> tuple[str, dict]:
    """Anti-AI pass. Stage A: deterministic detector + safe autofix (token scrub).
    Stage B: cheap LLM rewrites ONLY paragraphs that need it, with per-flag instructions;
    re-detect and loop (max ``deai.max_loops``, default 2). A rewrite is kept only if it lowers
    that paragraph's weighted tell count and keeps length within bounds.
    ``severe_only`` (the residual path) rewrites just paragraphs that still have a
    severity >= min_severity narration hit, excluding em dashes."""
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
            if severe_only:
                keep = [h for h in hs if h["severity"] >= min_sev and h["rule"] != "em_dash"]
                if keep:
                    todo.append((i, keep, []))
            elif (hs and detector.needs_rewrite(hs, min_sev, cluster)) or legacy:
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
