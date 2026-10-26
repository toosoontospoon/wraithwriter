"""Pick per-category Jev tell thresholds from planted pages.

Live mode calls Jev and writes the raw probabilities. ``--raw FILE`` scores a saved
file and does not call Jev. ``--write`` stores the chosen thresholds in
``bookforge/jev_templates/tell_taxonomy.json``.

A required tell counts as found when any sentence of that item is at or above the
threshold. An acceptable extra is neither a true positive nor a false positive.
Ties go to the higher threshold.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "jev_tells"
TAX_PATH = ROOT / "bookforge" / "jev_templates" / "tell_taxonomy.json"
# Page nouls scored on their own, against the category they gate.
PAGE_TARGETS = {"tidy_ending_page": "tidy_ending", "instead_contrast_page": "not_x_but_y"}


def grid() -> list[float]:
    return [round(0.5 + 0.05 * i, 2) for i in range(9)]


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


def _observations(raw: dict) -> list[tuple[list, list, dict]]:
    """(required, acceptable, probs) rows. Page nouls are extra rows when present."""
    rows = []
    for page in raw.get("pages") or []:
        labels = set(page.get("page_labels") or [])
        for item in page.get("items") or []:
            rows.append((list(item.get("required") or []), list(item.get("acceptable") or []),
                         dict(item.get("probs") or {})))
            labels |= set(item.get("required") or [])
        scores = page.get("page_scores") or {}
        for qid, target in PAGE_TARGETS.items():
            if qid not in scores:
                continue
            required = [qid] if target in labels else []
            rows.append((required, [], {qid: float(scores[qid])}))
    return rows


def _categories(rows, thresholds_floor: float = 0.5) -> list[str]:
    cats = set()
    for required, _acc, probs in rows:
        cats.update(required)
        cats.update(c for c, p in probs.items() if p >= thresholds_floor)
    return sorted(cats)


def _counts_at(rows, cat: str, th: float) -> tuple[int, int, int]:
    tp = fp = fn = 0
    for required, acceptable, probs in rows:
        pred = probs.get(cat, 0.0) >= th
        if cat in required:
            if pred:
                tp += 1
            else:
                fn += 1
        elif pred and cat not in acceptable:
            fp += 1
    return tp, fp, fn


def pick_thresholds(raw: dict) -> dict:
    """Per category: threshold in the grid with the best F1. Ties take the higher one."""
    rows = _observations(raw)
    chosen = {}
    detail = {}
    for cat in _categories(rows):
        best_th, best_f = grid()[0], -1.0
        for th in grid():
            _p, _r, f1 = prf(*_counts_at(rows, cat, th))
            if f1 > best_f + 1e-12 or abs(f1 - best_f) <= 1e-12 and th > best_th:
                best_th, best_f = th, f1
        tp, fp, fn = _counts_at(rows, cat, best_th)
        prec, rec, f1 = prf(tp, fp, fn)
        chosen[cat] = best_th
        detail[cat] = {"threshold": best_th, "tp": tp, "fp": fp, "fn": fn,
                       "p": round(prec, 4), "r": round(rec, 4), "f1": round(f1, 4)}
    tp = fp = fn = 0
    for cat, th in chosen.items():
        a, b, c = _counts_at(rows, cat, th)
        tp += a
        fp += b
        fn += c
    prec, rec, f1 = prf(tp, fp, fn)
    return {"thresholds": chosen, "categories": detail,
            "overall": {"tp": tp, "fp": fp, "fn": fn, "p": round(prec, 4), "r": round(rec, 4),
                        "f1": round(f1, 4)}}


def format_report(result: dict) -> str:
    lines = ["category  threshold  P  R  F1  tp  fp  fn"]
    for cat, row in sorted(result["categories"].items()):
        lines.append(f"{cat}  {row['threshold']:.2f}  {row['p']:.2f}  {row['r']:.2f}  "
                     f"{row['f1']:.2f}  {row['tp']}  {row['fp']}  {row['fn']}")
    o = result["overall"]
    lines.append(f"overall  —  {o['p']:.2f}  {o['r']:.2f}  {o['f1']:.2f}  {o['tp']}  {o['fp']}  {o['fn']}")
    return "\n".join(lines)


def write_thresholds(picks: dict, path: Path | None = None) -> list[str]:
    """Update Jev category and page-question thresholds. Code checks are left alone."""
    path = Path(path or TAX_PATH)
    tax = json.loads(path.read_text())
    code_ids = {row["id"] for row in tax.get("code_categories") or []}
    updated = []
    for group in ("categories", "page_questions"):
        for row in tax.get(group) or []:
            if row["id"] in picks and row["id"] not in code_ids:
                row["threshold"] = float(picks[row["id"]])
                updated.append(row["id"])
    path.write_text(json.dumps(tax, indent=2, ensure_ascii=False) + "\n")
    return updated


def _planted_rows(data: dict) -> list:
    return data.get("sentences") or data.get("items") or []


def collect_raw(paths: list[Path], log_dir=None) -> dict:
    """Run the tagger on planted pages and return probabilities for ``pick_thresholds``."""
    from bookforge.core import jev_tells
    cfg = jev_tells.settings(None)
    tax = jev_tells._apply_thresholds(jev_tells.load_taxonomy(), cfg)
    pages = []
    for path in paths:
        data = json.loads(Path(path).read_text())
        rows = _planted_rows(data)
        text = "\n\n".join(row[0] for row in rows)
        sents = jev_tells.annotate(text)
        grouped = jev_tells._pages_of(sents, text, cfg["page_words"]) or ([sents] if sents else [])
        probs: dict[tuple[str, str], float] = {}
        page_scores: dict[str, float] = {}
        for page in grouped:
            tagged = jev_tells.tag_page(page, cfg, log_dir, tax)
            for key, ps in (tagged.get("probs") or {}).items():
                if ps:
                    probs[key] = round(sum(ps) / len(ps), 4)
            for qid, p in (tagged.get("page_scores") or {}).items():
                page_scores[qid] = max(page_scores.get(qid, 0.0), p)
        for hit in jev_tells.code_hits(sents, cfg, tax):
            for sid in hit["ids"]:
                probs[(hit["category"], sid)] = 1.0
        items = []
        for row in rows:
            item_text, required, acceptable = row[0], row[1], row[2]
            ids = [s["id"] for s in sents if s["text"] == item_text or s["text"] in item_text or item_text in s["text"]]
            item_probs = {}
            for (cat, sid), p in probs.items():
                if sid in ids:
                    item_probs[cat] = max(item_probs.get(cat, 0.0), p)
            items.append({"text": item_text, "required": required, "acceptable": acceptable, "probs": item_probs})
        pages.append({"name": Path(path).stem, "items": items, "page_labels": data.get("page_labels") or [],
                      "page_scores": page_scores})
    return {"pages": pages}


def planted_paths(directory: Path | None = None) -> list[Path]:
    folder = Path(directory or FIXTURES)
    return sorted(folder.glob("planted_*.json"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Calibrate Jev tell thresholds on planted pages")
    ap.add_argument("--raw", type=Path, help="Score this saved probability file instead of calling Jev")
    ap.add_argument("--out", type=Path, default=Path("jev_tells_raw.json"), help="Where to write raw probabilities")
    ap.add_argument("--planted", type=Path, default=FIXTURES, help="Directory of planted_*.json pages")
    ap.add_argument("--write", action="store_true", help="Update thresholds in tell_taxonomy.json")
    ap.add_argument("--tax", type=Path, default=TAX_PATH)
    args = ap.parse_args(argv)
    if args.raw:
        raw = json.loads(args.raw.read_text())
    else:
        paths = planted_paths(args.planted)
        if not paths:
            raise SystemExit(f"no planted_*.json in {args.planted}")
        raw = collect_raw(paths)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(raw, indent=2) + "\n")
        print(f"wrote {args.out}")
    result = pick_thresholds(raw)
    print(format_report(result))
    if args.write:
        updated = write_thresholds(result["thresholds"], args.tax)
        print("updated " + ", ".join(updated))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
