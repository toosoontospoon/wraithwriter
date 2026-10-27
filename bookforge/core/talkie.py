"""llama.cpp ``/completion`` client for a base model (no chat template).

``set_fake(fn)`` installs a test double. ``fn(prompt, n_predict, seed, stop, ban_nl=..., **kw)``
returns a string or a dict with ``content``, ``stop_type`` and ``timings``. No network
is used while a fake is set.

Two-step length control (``generate``): ban newlines until about 0.8x the source
length, then continue to the first newline, capped near 1.25x, and trim to a
sentence end when the decode hits the token limit.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

import requests

TPW = 1.3  # tokens per word, from local benches of this model
STOPS = ["Exercise", "Pupil's version", "Master's version", "The pupil", "The master", "The persons"]

DEFAULTS = {
    "enabled": False,
    "endpoint": "http://127.0.0.1:8080",
    "n_rerolls": 4,
    "history_window": 2,
    "edit_budget": 0.15,
    "sol_tries": 2,
    "copy_max": 0.35,
    "splice_copy_max": 0.5,
    "splice_seeds": 2,
    "dedupe_jaccard": 0.4,
    "jev_threshold": 0.5,
    "fewshot_path": None,
    "temperature": 0.8,
    "min_p": 0.05,
    "repeat_penalty": 1.1,
    "timeout_s": 3600,
    "cap_usd": 0.50,
    "targeted": {"enabled": True, "edit_budget": 0.25, "rounds": 1},
}

_FAKE: Optional[Callable[..., object]] = None


class TalkieUnavailable(RuntimeError):
    """The local completion server is down, or the call failed."""


def set_fake(fn) -> None:
    global _FAKE
    _FAKE = fn


def _log(log_dir, rec: dict) -> None:
    if not log_dir:
        return
    p = Path(log_dir) / "talkie_calls.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def _cfg_get(cfg: dict | None, key: str):
    if cfg is not None and key in cfg:
        return cfg[key]
    return DEFAULTS[key]


def complete(prompt: str, n_predict: int, seed: int, stop, *, ban_nl: bool = False,
             temperature: float | None = None, min_p: float | None = None,
             repeat_penalty: float | None = None, timeout: float | None = None,
             endpoint: str | None = None, log_dir=None, cache_prompt: bool = True,
             cfg: dict | None = None) -> tuple[str, dict]:
    """One ``/completion`` call. Returns ``(text, log row)``."""
    temperature = DEFAULTS["temperature"] if temperature is None else temperature
    min_p = DEFAULTS["min_p"] if min_p is None else min_p
    repeat_penalty = DEFAULTS["repeat_penalty"] if repeat_penalty is None else repeat_penalty
    timeout = DEFAULTS["timeout_s"] if timeout is None else timeout
    endpoint = endpoint or _cfg_get(cfg, "endpoint")
    body = {
        "prompt": prompt,
        "n_predict": int(n_predict),
        "seed": int(seed),
        "stop": list(stop),
        "cache_prompt": bool(cache_prompt),
        "temperature": temperature,
        "min_p": min_p,
        "top_p": 1.0,
        "top_k": 0,
        "repeat_penalty": repeat_penalty,
        "repeat_last_n": 256,
    }
    if ban_nl:
        body["logit_bias"] = [["\n", False], ["\n\n", False], ["\n\n\n", False]]
    t0 = time.time()
    try:
        if _FAKE is not None:
            res = _FAKE(prompt, int(n_predict), int(seed), list(stop), ban_nl=ban_nl,
                        temperature=temperature, min_p=min_p, repeat_penalty=repeat_penalty,
                        timeout=timeout, endpoint=endpoint)
            if isinstance(res, str):
                res = {"content": res, "stop_type": "word", "timings": {}}
        else:
            url = str(endpoint).rstrip("/") + "/completion"
            r = requests.post(url, json=body, timeout=timeout,
                              headers={"Content-Type": "application/json"})
            if r.status_code != 200:
                raise TalkieUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
            res = r.json()
    except TalkieUnavailable:
        raise
    except AssertionError:
        raise
    except Exception as e:
        raise TalkieUnavailable(f"talkie completion failed: {e}") from e
    timings = res.get("timings") or {}
    rec = {
        "prompt_n": timings.get("prompt_n"),
        "gen_n": timings.get("predicted_n"),
        "tok_s": timings.get("predicted_per_second"),
        "wall": round(time.time() - t0, 3),
        "seed": int(seed),
        "n_predict": int(n_predict),
        "stop_type": res.get("stop_type"),
        "ban_nl": bool(ban_nl),
    }
    _log(log_dir, rec)
    return res.get("content") or "", rec


def trim_sentence(text: str) -> str:
    """Cut after the last sentence end. Leave the text alone if it has none."""
    import re
    marks = list(re.finditer(r'[.!?][”"\u2019]?(?=\s|$)', text))
    return text[:marks[-1].end()] if marks else text


def generate(prompt: str, source: str, seed: int, *, cfg: dict | None = None, log_dir=None,
             temperature: float | None = None, min_p: float | None = None,
             repeat_penalty: float | None = None, timeout: float | None = None,
             endpoint: str | None = None) -> dict:
    """Two-step completion aimed at the source paragraph's length. Returns seed, text, wall, gen_tokens."""
    words = max(1, len((source or "").split()))
    temperature = _cfg_get(cfg, "temperature") if temperature is None else temperature
    min_p = _cfg_get(cfg, "min_p") if min_p is None else min_p
    repeat_penalty = _cfg_get(cfg, "repeat_penalty") if repeat_penalty is None else repeat_penalty
    timeout = _cfg_get(cfg, "timeout_s") if timeout is None else timeout
    endpoint = endpoint or _cfg_get(cfg, "endpoint")
    kw = dict(temperature=temperature, min_p=min_p, repeat_penalty=repeat_penalty,
              timeout=timeout, endpoint=endpoint, log_dir=log_dir, cfg=cfg)
    t0 = time.time()
    n1 = max(20, int(0.8 * words * TPW))
    first, r1 = complete(prompt, n1, seed, STOPS, ban_nl=True, **kw)
    hit_stop = r1.get("stop_type") == "word"
    second, r2 = "", None
    if not hit_stop:
        n2 = max(10, int(1.25 * words * TPW) - int(r1.get("gen_n") or 0) + 15)
        second, r2 = complete(prompt + first, n2, seed + 7919, list(STOPS) + ["\n"], ban_nl=False, **kw)
    text = (first + second).split("\n")[0]
    if r2 is None or (r2 or {}).get("stop_type") == "limit" or hit_stop:
        text = trim_sentence(text)
    return {
        "seed": seed,
        "text": text.strip(),
        "wall": round(time.time() - t0, 3),
        "gen_tokens": int(r1.get("gen_n") or 0) + int((r2 or {}).get("gen_n") or 0),
    }
