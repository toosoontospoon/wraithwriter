"""TypeSafe System One (Jev) client: probabilities only, never text.

Design rule: code first for anything rule-checkable; Jev only scores yes/no,
choice or score questions on unstructured text against a fixed template; a full LLM only for
open-ended work. Every call names its model (pinned, default jev-1.13.0). No OpenRouter.

Key: TYPESAFE_API_KEY from the environment. It is never printed or logged.
Each call is logged to state/jev_calls.jsonl and, as role "jev_screen", to costs.jsonl/usage.jsonl
so `bookforge costs` includes it.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

import requests

API = "https://api.typesafe.ai/v1/systemone"
PRICE_PER_MTOK = 0.042   # docs.typesafe.ai/models: input only; output tokens are free
KEY_ENV = "TYPESAFE_API_KEY"
AUTH_SCHEME = "Bearer"

_FAKE: Optional[Callable[[object, dict, str], dict]] = None   # tests: fn(state, questions, model) -> response


class JevUnavailable(RuntimeError):
    """No key, cap reached, or the API failed twice. Callers skip Jev and keep code checks."""


def set_fake(fn) -> None:
    global _FAKE
    _FAKE = fn


def available() -> bool:
    return _FAKE is not None or bool(os.environ.get(KEY_ENV))


def _log(log_dir: Optional[Path], rec: dict) -> None:
    if log_dir:
        p = Path(log_dir) / "jev_calls.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    if rec.get("ok"):
        try:
            from bookforge.config import load_config
            from bookforge.core import llm
            llm._log_usage("jev_screen", "typesafe", rec.get("served") or rec["requested"],
                           {"prompt_tokens": rec.get("input_tokens", 0), "completion_tokens": rec.get("output_tokens", 0),
                            "cost_usd": rec.get("cost_usd", 0.0)}, rec.get("latency_s", 0.0), load_config())
        except Exception:
            pass


def spent(log_dir: Optional[Path]) -> float:
    p = Path(log_dir) / "jev_calls.jsonl" if log_dir else None
    if not p or not p.exists():
        return 0.0
    tot = 0.0
    for line in p.read_text().splitlines():
        try:
            tot += float(json.loads(line).get("cost_usd", 0))
        except (ValueError, json.JSONDecodeError):
            pass
    return tot


def ask(state, questions: dict, model: str = "jev-1.13.0", tag: str = "", log_dir: Optional[Path] = None,
        timeout: float = 30.0, cap_usd: float = 1.0) -> dict:
    """One System One call; retries once on error. Raises JevUnavailable on failure."""
    if _FAKE is None and not os.environ.get(KEY_ENV):
        raise JevUnavailable(f"{KEY_ENV} not set")
    if spent(log_dir) >= cap_usd:
        raise JevUnavailable(f"Jev cap ${cap_usd:.2f} reached")
    body = {"model": model, "state": state, "questions": questions}
    err = None
    for attempt in (1, 2):
        t0 = time.time()
        try:
            if _FAKE is not None:
                res = _FAKE(state, questions, model)
            else:
                r = requests.post(API, json=body, timeout=timeout, headers={
                    "Authorization": f"{AUTH_SCHEME} {os.environ[KEY_ENV]}", "Content-Type": "application/json"})
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
                res = r.json()
            u = res.get("usage") or {}
            rec = {"ts": time.time(), "tag": tag, "requested": model, "served": res.get("model"),
                   "n_questions": len(questions), "input_tokens": int(u.get("input_tokens") or 0),
                   "output_tokens": int(u.get("output_tokens") or 0),
                   "cost_usd": round(int(u.get("input_tokens") or 0) * PRICE_PER_MTOK / 1e6, 8),
                   "latency_s": round(time.time() - t0, 3), "attempt": attempt, "ok": True}
            _log(log_dir, rec)
            res["_latency_s"] = rec["latency_s"]
            return res
        except Exception as e:  # network, HTTP, JSON
            err = str(e)[:300]
            # A test fake that rejects the payload should fail closed at once. Live HTTP
            # errors still retry once. Client errors other than 429 do not retry.
            if _FAKE is not None or ("HTTP 4" in err and "HTTP 429" not in err):
                break
            time.sleep(1.0)
    _log(log_dir, {"ts": time.time(), "tag": tag, "requested": model, "ok": False, "error": err, "cost_usd": 0})
    raise JevUnavailable(f"Jev call failed: {err}")


def noul(res: dict, qid: str) -> Optional[float]:
    a = (res.get("answers") or {}).get(qid) or {}
    return a.get("noul")


def choice(res: dict, qid: str) -> tuple[Optional[str], float]:
    a = (res.get("answers") or {}).get(qid) or {}
    probs = a.get("probabilities") or {}
    return a.get("choice"), float(a.get("confidence") or (max(probs.values()) if probs else 0.0))
