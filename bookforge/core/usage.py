"""Usage report over state/usage.jsonl (one row per LLM call; written by core/llm.py)."""
from __future__ import annotations

import json
from pathlib import Path


def load(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def summarize(rows: list[dict], by: str = "chapter") -> dict:
    agg: dict = {}
    for r in rows:
        k = str(r.get(by) or "-")
        a = agg.setdefault(k, {"calls": 0, "prompt": 0, "cached": 0, "write": 0, "completion": 0, "cost": 0.0})
        a["calls"] += 1
        a["prompt"] += int(r.get("prompt_tokens", 0))
        a["cached"] += int(r.get("cached_tokens", 0))
        a["write"] += int(r.get("cache_write_tokens", 0))
        a["completion"] += int(r.get("completion_tokens", 0))
        a["cost"] += float(r.get("cost_usd", 0))
    return agg


def report(path: Path, by: str = "chapter", chapter: str | None = None) -> str:
    rows = [r for r in load(path) if not chapter or r.get("chapter") == chapter]
    if not rows:
        return f"no usage rows in {path}"
    agg = summarize(rows, by)
    out = [f"{by:<14} {'calls':>5} {'prompt':>10} {'cached':>10} {'hit%':>5} {'cwrite':>9} {'out':>9} {'cost$':>9}"]
    tot = summarize(rows, "_all").get("-")
    for k, a in sorted(agg.items()) + [("TOTAL", tot)]:
        hit = 100 * a["cached"] / a["prompt"] if a["prompt"] else 0
        out.append(f"{k:<14} {a['calls']:>5} {a['prompt']:>10} {a['cached']:>10} {hit:>5.0f} {a['write']:>9} "
                   f"{a['completion']:>9} {a['cost']:>9.4f}")
    return "\n".join(out)
