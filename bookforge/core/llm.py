"""LLM access: provider adapters, schema-validated JSON, retries, cost tracking.

Tests install a fake with ``set_fake(fn)``; ``fn(role, system, prompt, json_mode)``
returns a string or a dict/list. No network is touched while a fake is set.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional, Type

import requests
from pydantic import BaseModel, ValidationError

from bookforge.config import adjusted_role_params, api_key, load_config, resolve_role

AUTH_SCHEME = "Bearer"  # HTTP auth scheme; the key itself always comes from the environment


class LLMError(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class TransientHTTPError(LLMError):
    pass


_FAKE: Optional[Callable[..., Any]] = None
_COST_LOG: Optional[Path] = None
_USAGE_LOG: Optional[Path] = None
_USAGE_CTX: dict = {}
_SPENT = 0.0

# Prompt caching. Builders put CACHE_BREAK between the reusable prefix and the call-specific tail;
# the provider turns each segment before a break into a cache breakpoint (cache_control, which
# OpenRouter maps to OpenAI's prompt_cache_breakpoint). The system prompt is always a breakpoint.
CACHE_BREAK = "\n\n<<<CACHE_BREAK>>>\n\n"


def set_usage_context(**kw) -> None:
    """Tag subsequent usage rows (e.g. chapter="ch03", step="write")."""
    _USAGE_CTX.clear()
    _USAGE_CTX.update({k: v for k, v in kw.items() if v is not None})


def strip_breaks(text: str) -> str:
    return (text or "").replace(CACHE_BREAK, "\n\n")


def set_fake(fn: Optional[Callable[..., Any]]) -> None:
    global _FAKE
    _FAKE = fn


def set_cost_log(path: Optional[Path]) -> None:
    """Point usage logging at a project's costs.jsonl and load prior spend."""
    global _COST_LOG, _SPENT
    global _USAGE_LOG
    _COST_LOG = Path(path) if path else None
    _USAGE_LOG = (_COST_LOG.parent / "state" / "usage.jsonl") if _COST_LOG else None
    _SPENT = 0.0
    if _COST_LOG and _COST_LOG.exists():
        for line in _COST_LOG.read_text().splitlines():
            try:
                _SPENT += float(json.loads(line).get("cost_usd", 0))
            except (ValueError, json.JSONDecodeError):
                pass


def spent() -> float:
    return _SPENT


# ------------------------------------------------------------------ providers
class Provider:
    def __init__(self, name: str, cfg: dict, timeout: int):
        self.name, self.cfg, self.timeout = name, cfg, timeout

    def chat(self, model, system, prompt, temperature, max_tokens, json_mode) -> tuple[str, dict]:
        raise NotImplementedError


def _post(url, payload, headers, timeout):
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        raise TransientHTTPError(str(e)) from e
    if r.status_code == 429 or r.status_code >= 500:
        raise TransientHTTPError(f"HTTP {r.status_code}: {r.text[:300]}")
    if r.status_code >= 400:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


class OpenAICompatible(Provider):
    def chat(self, model, system, prompt, temperature, max_tokens, json_mode):
        headers = {"Content-Type": "application/json"}
        key = api_key(self.cfg)
        if key:
            headers["Authorization"] = f"{AUTH_SCHEME} {key}"
        cache = getattr(self, "cache", None) or {}
        payload = {"model": model, "messages": build_messages(system, prompt, model, cache),
                   "temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        payload.update(cache_fields(model, cache, self.cfg))
        payload.update(getattr(self, "extra_body", None) or {})
        data = _post(f"{self.cfg['base_url'].rstrip('/')}/chat/completions", payload, headers, self.timeout)
        return data["choices"][0]["message"]["content"] or "", normalize_usage(data.get("usage", {}) or {})


def _blocks(text: str, mark: bool, cc: dict) -> list[dict]:
    segs = text.split(CACHE_BREAK)
    out = []
    for i, seg in enumerate(segs):
        if not seg:
            continue
        b = {"type": "text", "text": seg}
        if mark and i < len(segs) - 1:
            b["cache_control"] = dict(cc)
        out.append(b)
    return out


def build_messages(system: str, prompt: str, model: str, cache: dict) -> list[dict]:
    """Chat messages. With caching on, the system prompt and every prompt segment before a CACHE_BREAK
    become breakpoints (max 4 in total, Anthropic's limit); without it, breaks are just blank lines."""
    on = bool(cache.get("enabled"))
    cc = {"type": "ephemeral"}
    if cache.get("ttl") and model.startswith("anthropic/"):
        cc["ttl"] = cache["ttl"]
    msgs = []
    if system:
        if on:
            sysb = _blocks(system + CACHE_BREAK, True, cc)
            msgs.append({"role": "system", "content": sysb})
        else:
            msgs.append({"role": "system", "content": strip_breaks(system)})
    if on and CACHE_BREAK in prompt:
        ub = _blocks(prompt, True, cc)
        n_marked = sum(1 for m in msgs for b in m["content"] if "cache_control" in b)
        marks = [b for b in ub if "cache_control" in b]
        for b in marks[:max(0, len(marks) - (4 - n_marked))]:
            b.pop("cache_control")
        msgs.append({"role": "user", "content": ub})
    else:
        msgs.append({"role": "user", "content": strip_breaks(prompt)})
    return msgs


def _openrouter(pcfg: dict) -> bool:
    return "openrouter" in (pcfg.get("base_url") or "")


def cache_fields(model: str, cache: dict, pcfg: dict) -> dict:
    """OpenRouter-only cache routing. Other OpenAI-compatible routers (Hugging Face) get none of these."""
    out = {}
    if _openrouter(pcfg):
        out["usage"] = {"include": True}          # OpenRouter returns real cost incl. cache discounts
    if not cache.get("enabled"):
        return out
    if not _openrouter(pcfg):
        return out
    if cache.get("key"):
        out["prompt_cache_key"] = cache["key"]    # OpenAI cache routing; OpenRouter also uses it as session
        out["session_id"] = cache["key"]          # OpenRouter sticky provider routing
    if model.startswith("openai/") and cache.get("openai_explicit", True):
        # Explicit mode: ONLY our breakpoints are cached, so one-off prompts don't pay the 1.25x write.
        out["prompt_cache_options"] = {"mode": "explicit", "ttl": cache.get("openai_ttl", "30m")}
    return out


def normalize_usage(u: dict) -> dict:
    d = u.get("prompt_tokens_details") or {}
    out = dict(u)
    out["cached_tokens"] = int(d.get("cached_tokens") or u.get("cached_tokens") or 0)
    out["cache_write_tokens"] = int(d.get("cache_write_tokens") or u.get("cache_write_tokens") or 0)
    if u.get("cost") is not None:
        out["cost_usd"] = float(u["cost"])
    return out


class Anthropic(Provider):
    """Native adapter so the stable system prefix can be cached explicitly."""

    def chat(self, model, system, prompt, temperature, max_tokens, json_mode):
        headers = {"Content-Type": "application/json", "x-api-key": api_key(self.cfg),
                   "anthropic-version": "2023-06-01"}
        payload = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
                   "messages": [{"role": "user", "content": _blocks(prompt, True, {"type": "ephemeral"})
                                 if CACHE_BREAK in prompt else prompt}]}
        system = strip_breaks(system)
        if system:
            payload["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        data = _post(f"{self.cfg['base_url'].rstrip('/')}/messages", payload, headers, self.timeout)
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        u = data.get("usage", {}) or {}
        return text, {"prompt_tokens": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                      + u.get("cache_creation_input_tokens", 0),
                      "completion_tokens": u.get("output_tokens", 0),
                      "cached_tokens": u.get("cache_read_input_tokens", 0),
                      "cache_write_tokens": u.get("cache_creation_input_tokens", 0)}


def get_provider(name: str, config: dict) -> Provider:
    pcfg = config["llm"]["providers"][name]
    kind = pcfg.get("kind", "openai")
    cls = Anthropic if kind == "anthropic" else OpenAICompatible
    return cls(name, pcfg, config["llm"].get("timeout", 180))


# ------------------------------------------------------------------ helpers
def strip_model_wrapper(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
    text = re.sub(r"^```(?:json|text)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def parse_json_loose(text: str):
    text = strip_model_wrapper(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # take the outermost {...} or [...]
    for o, c in (("{", "}"), ("[", "]")):
        i, j = text.find(o), text.rfind(c)
        if i != -1 and j > i:
            cand = text[i:j + 1]
            cand = re.sub(r",\s*([}\]])", r"\1", cand)  # trailing commas
            try:
                return json.loads(cand)
            except json.JSONDecodeError:
                continue
    raise ValueError("no parseable JSON in model output")


def cache_settings(config) -> dict:
    c = dict(config["llm"].get("cache") or {})
    c.setdefault("enabled", True)
    if c.get("enabled") and not c.get("key") and _COST_LOG:
        c["key"] = "bookforge:" + _COST_LOG.parent.name[:200]
    return c


def estimate_cost(model, usage, config) -> float:
    """Cost from config prices, cache-aware (used when the provider doesn't return a cost)."""
    price = config["llm"].get("prices", {}).get(model, {})
    pin, pout = float(price.get("input", 0)), float(price.get("output", 0))
    # `cached` is accepted as an alias of `cache_read` (the featherless page says "cached").
    pread = float(price.get("cache_read", price.get("cached", pin)))
    pwrite = float(price.get("cache_write", pin))
    pt = int(usage.get("prompt_tokens", 0) or 0)
    ct = int(usage.get("completion_tokens", 0) or 0)
    cr = int(usage.get("cached_tokens", 0) or 0)
    cw = int(usage.get("cache_write_tokens", 0) or 0)
    return (max(0, pt - cr - cw) * pin + cr * pread + cw * pwrite + ct * pout) / 1e6


def _log_usage(role, prov, model, usage, latency, config):
    global _SPENT
    pt = int(usage.get("prompt_tokens", 0) or 0)
    ct = int(usage.get("completion_tokens", 0) or 0)
    est = estimate_cost(model, usage, config)
    cost = float(usage["cost_usd"]) if usage.get("cost_usd") is not None else est
    _SPENT += cost
    row = {"ts": time.time(), "role": role, "provider": prov, "model": model,
           "prompt_tokens": pt, "completion_tokens": ct,
           "cached_tokens": int(usage.get("cached_tokens", 0) or 0),
           "cache_write_tokens": int(usage.get("cache_write_tokens", 0) or 0),
           "reasoning_tokens": int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0),
           "latency_s": round(latency, 2), "cost_usd": round(cost, 6), "cost_source":
               "provider" if usage.get("cost_usd") is not None else "estimate", **_USAGE_CTX}
    for path in (_COST_LOG, _USAGE_LOG):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as f:
                f.write(json.dumps(row) + "\n")


def _raw_call(role, system, prompt, temperature, max_tokens, json_mode, provider, model):
    config = load_config()
    if _FAKE is not None:
        out = _FAKE(role=role, system=strip_breaks(system or ""), prompt=strip_breaks(prompt), json_mode=json_mode)
        if not isinstance(out, str):
            out = json.dumps(out)
        _log_usage(role, "fake", "fake", {"prompt_tokens": (len(system or "") + len(prompt)) // 4,
                                          "completion_tokens": len(out) // 4}, 0.0, config)
        return out
    budget = config["llm"].get("budget_usd")
    if budget is not None and _SPENT >= float(budget):
        raise BudgetExceeded(f"Budget ${budget} reached (spent ${_SPENT:.2f})")
    if not (provider and model):
        provider, model = resolve_role(role, config)
    prov = get_provider(provider, config)
    rp = adjusted_role_params(role, provider, model, config)
    prov.extra_body = rp["extra_body"]
    prov.cache = cache_settings(config)
    max_tokens = int(max_tokens * rp["mult"]) + rp["headroom"]
    retries = int(config["llm"].get("http_retries", 4))
    last = None
    for attempt in range(retries):
        try:
            t0 = time.time()
            text, usage = prov.chat(model, system, prompt, temperature, max_tokens, json_mode)
            _log_usage(role, provider, model, usage, time.time() - t0, config)
            return text
        except TransientHTTPError as e:
            last = e
            wait = min(60, 2 ** attempt * 2)
            print(f"[llm] transient error [{provider}/{model}] attempt {attempt + 1}: {e}; retry in {wait}s")
            time.sleep(wait)
    raise LLMError(f"LLM call failed after {retries} attempts [{provider}/{model}]: {last}")


def llm_call(prompt: str, temperature: float = 0.7, json_mode: bool = False, role: str = "summary",
             provider: str | None = None, model: str | None = None, max_tokens: int = 4000,
             system: str = "", schema: Type[BaseModel] | None = None, raw_dir: Path | None = None):
    """Chat completion. With ``schema`` (implies JSON) returns a validated model instance.

    JSON failures are retried with the parse/validation error fed back to the model;
    the raw text of every failed attempt is saved to ``raw_dir`` when given.
    """
    if schema is not None:
        json_mode = True
    if not json_mode:
        return strip_model_wrapper(_raw_call(role, system, prompt, temperature, max_tokens, False,
                                             provider, model))
    tries = int(load_config()["llm"].get("json_retries", 2)) + 1
    p, err = prompt, None
    for attempt in range(tries):
        text = _raw_call(role, system, p, temperature if attempt == 0 else 0.2, max_tokens, True,
                         provider, model)
        try:
            data = parse_json_loose(text)
            if schema is not None:
                if isinstance(data, list) and "verdicts" in schema.model_fields:
                    data = {"verdicts": data}
                elif isinstance(data, list) and "facts" in schema.model_fields:
                    data = {"facts": data}
                return schema.model_validate(data)
            return data
        except (ValueError, ValidationError) as e:
            err = e
            if raw_dir:
                Path(raw_dir).mkdir(parents=True, exist_ok=True)
                (Path(raw_dir) / f"{role}_bad_json_{attempt}.txt").write_text(text)
            p = (prompt + "\n\nYour previous reply was not valid for the required JSON schema. "
                 f"Error: {str(e)[:800]}\nReply again with ONLY the corrected JSON.")
    raise LLMError(f"Invalid JSON from role '{role}' after {tries} attempts: {err}")


def web_search_call(prompt: str, temperature: float = 0.7) -> dict:
    """xAI responses API with web search. Returns {"text", "sources"}."""
    if _FAKE is not None:
        out = _FAKE(role="web_search", system="", prompt=prompt, json_mode=False)
        return out if isinstance(out, dict) else {"text": str(out), "sources": []}
    config = load_config()
    prov_name, model_name = resolve_role("brainstorm", config)
    prov = config["llm"]["providers"]["xai"]
    headers = {"Content-Type": "application/json", "Authorization": f"{AUTH_SCHEME} {api_key(prov)}"}
    payload = {"model": model_name, "input": [{"role": "user", "content": prompt}],
               "tools": [{"type": "web_search"}], "temperature": temperature}
    for attempt in range(3):
        try:
            data = _post(f"{prov['base_url'].rstrip('/')}/responses", payload, headers, 120)
            text, sources = "", []
            for item in data.get("output", []):
                if item.get("type") == "message":
                    for c in item.get("content", []):
                        if c.get("type") == "output_text":
                            text, sources = c["text"], c.get("annotations", [])
            return {"text": text, "sources": sources}
        except TransientHTTPError as e:
            print(f"[web_search] attempt {attempt + 1}: {e}")
            time.sleep(2 ** attempt * 2)
    raise LLMError("Web search failed after retries")
