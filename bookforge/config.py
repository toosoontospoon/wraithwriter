"""Configuration.

No secrets live in code. API keys come from environment variables named in
each provider's ``api_key_env``. A user config file (``~/.bookforge/config.yaml``
or ``$BOOKFORGE_CONFIG``) is deep-merged over the defaults if it exists; this
module never writes it implicitly.
"""
import copy
import os
from pathlib import Path
from typing import Any, Dict

import yaml

# Featherless Gemma on the Hugging Face router. 32k context, no reasoning body.
# USD per 1M from the featherless.ai model page (HF router adds no markup).
GEMMA_MODEL_ID = "nbeerbower/Gemma4-Gutenberg-31B-Heretic:featherless-ai"
GEMMA_CONTEXT_TOKENS = 32768
# Leave completion room on that window. Packs over this are trimmed with the
# existing context caps (total_context_tokens / block_caps), not a new trimmer.
GEMMA_PROMPT_BUDGET = 24000
# Completion pad for this window. The library drafter headroom (6000) and the
# write step's +8000 pad stay on gpt-6-sol; they must not stack onto 32k.
GEMMA_MAX_HEADROOM = 1024
DEEPSEEK_FLASH = "deepseek/deepseek-v4.1-flash-1017"

DEFAULT_CONFIG: Dict[str, Any] = {
    "llm": {
        "providers": {
            "xai": {"kind": "openai", "base_url": "https://api.x.ai/v1", "api_key_env": "XAI_API_KEY"},
            "openrouter": {"kind": "openai", "base_url": "https://openrouter.ai/api/v1",
                           "api_key_env": "OPENROUTER_API_KEY"},
            "anthropic": {"kind": "anthropic", "base_url": "https://api.anthropic.com/v1",
                          "api_key_env": "ANTHROPIC_API_KEY"},
            "ollama": {"kind": "openai", "base_url": "http://localhost:11434/v1", "api_key_env": ""},
            # OpenAI-compatible router. Chat posts to {base_url}/chat/completions.
            # OpenRouter-only cache fields are not sent here (see core/llm.py cache_fields).
            "huggingface": {"kind": "openai", "base_url": "https://router.huggingface.co/v1",
                            "api_key_env": "HF_TOKEN"},
        },
        # Model tiers. Roles below point at a tier; override per tier with env vars
        # BOOKFORGE_<TIER>_MODEL / BOOKFORGE_<TIER>_PROVIDER (e.g. BOOKFORGE_PLANNER_MODEL).
        "models": {
            # High-intelligence: plot, outline, world/character arcs, critique, continuity judging.
            # Planning on gpt-6-sol with medium reasoning ($2/$10; reasoning bills as output).
            # Env: BOOKFORGE_PLANNER_REASONING_EFFORT (low|medium|high|none to omit).
            # Alternative planner: moonshotai/kimi-k3 ($0.50/$13.50, EQ-Bench CW #5; reasons heavily,
            # use effort low): BOOKFORGE_PLANNER_MODEL=moonshotai/kimi-k3 BOOKFORGE_PLANNER_REASONING_EFFORT=low
            "planner": {"provider": "openrouter", "model": "openai/gpt-6-sol",
                        "extra_body": {"reasoning": {"effort": "medium"}},
                        "max_tokens_multiplier": 1.0, "max_tokens_headroom": 4000},
            # Affordable strong prose: drafting/revising chapters from complete plans.
            # Fallback drafter: z-ai/glm-5.3 ($0.04/$4.80, EQ-Bench #6); switch with
            # BOOKFORGE_DRAFTER_MODEL=z-ai/glm-5.3.
            "drafter": {"provider": "openrouter", "model": "openai/gpt-6-sol",
                        # Extra request fields (OpenAI-compatible providers). Reasoning tokens bill as
                        # output and count against max_tokens; keep effort low and leave headroom.
                        # Env: BOOKFORGE_DRAFTER_REASONING_EFFORT (low|medium|high|none to omit).
                        # Medium since the 2026-10-12 redesign (one call per scene with the full story pack).
                        "extra_body": {"reasoning": {"effort": "medium"}},
                        # max_tokens sent = requested * max_tokens_multiplier + max_tokens_headroom
                        "max_tokens_multiplier": 1.0, "max_tokens_headroom": 6000},
            # Full-context editor: one editing pass per chapter at high reasoning (replaces polish,
            # re-voice and the de-AI LLM rewrite). Env: BOOKFORGE_EDITOR_MODEL / _REASONING_EFFORT.
            "editor": {"provider": "openrouter", "model": "openai/gpt-6-sol",
                       "extra_body": {"reasoning": {"effort": "high"}},
                       "max_tokens_multiplier": 1.0, "max_tokens_headroom": 24000},
            # Cheap bookkeeping: summaries, fact extraction, state updates.
            "utility": {"provider": "openrouter", "model": "google/gemini-2.5-flash"},
            # Optional prose tier. Library write/rerank stay on drafter (above).
            # Point those roles at this tier in the user config (see example_config).
            # context_tokens gates the 32k headroom clamp and the ~24k prompt cap.
            "gemma": {"provider": "huggingface", "model": GEMMA_MODEL_ID,
                      "max_tokens_multiplier": 1.0, "max_tokens_headroom": GEMMA_MAX_HEADROOM,
                      "context_tokens": GEMMA_CONTEXT_TOKENS},
        },
        "roles": {
            # brainstorm stays on xAI: web_search_call uses its model with the xAI responses API.
            "brainstorm": {"provider": "xai", "model": "grok-4.7"},
            "outline": {"tier": "planner"},
            "worldbuild": {"tier": "planner"},
            "character_design": {"tier": "planner"},
            "world_design": {"tier": "planner"},
            "plan": {"tier": "planner"},
            "craft_plan": {"tier": "planner"},
            "critique": {"tier": "planner"},
                        "audit": {"tier": "planner"},
            "write": {"tier": "drafter"},
            "revision": {"tier": "drafter"},
            "edit": {"tier": "editor"},
            "deai": {"tier": "utility"},   # cheap rewrite of flagged paragraphs only
            "rerank": {"tier": "drafter"},
            "summary": {"tier": "planner"},  # moved to Sol 2026-10-12 (utility model hallucinated canon)
            "state_update": {"tier": "planner"},  # moved to Sol 2026-10-12 (utility model hallucinated canon)
            "extract": {"tier": "planner"},  # moved to Sol 2026-10-12 (utility model hallucinated canon)
            "craft_extract": {"tier": "utility"},
            "deconflict": {"tier": "planner"},  # moved to Sol 2026-10-12 (utility model hallucinated canon)
            "talkie_fix": {"tier": "planner"},  # budgeted fact fix inside the talkie reword
        },
        "default_role": "summary",
        # USD per 1M tokens, optional. Unknown models are logged with cost 0.
        "prices": {
            "moonshotai/kimi-k3": {"input": 0.73, "output": 10.0, "cache_read": 0.55},
            "z-ai/glm-5.3": {"input": 0.079, "output": 4.8, "cache_read": 0.059},
            "openai/gpt-6-sol": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5},  # >=272k prompt: $4/$15
            "grok-4.7": {"input": 2.0, "output": 6.0},  # <200k prompt; >=200k bills $4/$12; web_search +$5/1k calls
            "google/gemini-2.5-flash": {"input": 0.3, "output": 2.5, "cache_read": 0.03, "cache_write": 0.383},
            # Key is the id sent on the wire, including the :featherless-ai route suffix.
            GEMMA_MODEL_ID: {"input": 0.48, "output": 1.44, "cache_read": 0.10},
        },
        # Prompt caching (see core/llm.py CACHE_BREAK). openai_explicit: only our breakpoints are cached,
        # so one-off prompts never pay OpenAI's 1.25x cache-write price. key "" = derived from project.
        "cache": {"enabled": True, "key": "", "openai_explicit": True, "openai_ttl": "30m", "ttl": ""},
        "budget_usd": None,
        "timeout": 180,
        "http_retries": 4,
        "json_retries": 2,
    },
    "embeddings": {"enabled": False, "model": "BAAI/bge-small-en-v1.5"},
    "context": {
        "total_context_tokens": 48000,
        "continuity_chars": 6000,
        "block_caps": {
            "instructions": 2000, "continuity": 2000, "chapter_frame": 600, "covered": 800,
            "canon": 3000, "continuity_state": 1200, "threads": 800, "characters": 3000,
            "world": 1500, "scene_sensory": 300, "summaries": 2500, "passages": 2500, "craft": 1500,
            "voices": 1500,
        },
        "scene_max_words_per_call": 6000,  # one call per scene
        "working_chunk_words": 500,
    },
    "brainstorm": {"web_research": True},
    "canon": {
        "conflict_policy": "plan_wins",   # plan_wins | canon_wins
        "allow_canon_wins": True,          # judge may promote a better new detail
        "review_confidence": 0.6,          # below this -> human review queue
        "critical_auto_confidence": 0.85,  # critical conflicts need this to auto-resolve
        "reextract_after_revise": True,
    },
    "revise": {"recritique": True, "max_passes": 1},
    # Human opening for the write step. True refuses to draft a chapter whose first scene
    # has no non-whitespace seed. raw_dir, when set, also receives chNN-vX-<family>-raw.txt.
    # beat_by_beat is off for the library drafter. The Gemma tier turns it on in the write
    # step. beats_per_call is 1, or 2 to draft a pair of beats in one call.
    # context_budget_tokens unset keeps today's write prompt. prior_words is the tail kept
    # when a budget is set (current scene so far, else the previous scene or chapter).
    "write": {"require_human_seed": True, "raw_dir": None, "beat_by_beat": False, "beats_per_call": 1,
              "context_budget_tokens": None, "prior_words": 1500},
    # Anti-AI pass. Detector hits ride the existing editor. The residual LLM rewrite is off
    # unless deai.rewrite_residual is true. external_detector is report-only (no key in the repo).
    "deai": {"max_flag_ratio": 0.0, "max_loops": 2, "min_severity": 2, "cluster": 3,
             "fail_on_tokens": True, "fragment_max_ratio": 0.02, "dash_policy": "fiction",
             "prompt_rules": True, "route_to_editor": True, "max_findings": 40,
             "edit_guard": "section", "max_tell_drop": 0.5, "rewrite_residual": False,
             "external_detector": {"provider": "none", "api_key_env": "PANGRAM_API_KEY",
                                   "cap_usd": 1.0, "mode": "report"}},
    # Post-draft screen (core/screen.py): code checks + Jev (TypeSafe System One, key TYPESAFE_API_KEY)
    # flag sections; the revise editor then reads only a revision packet. On by default; without
    # TYPESAFE_API_KEY the code checks still run and Jev is skipped. See README.
    "screen": {"enabled": True, "jev": True, "deai_questions": True, "model": "jev-1.13.0",
               "section_words": 600,
               "cap_usd": 1.0, "skip_edit_if_clean": True, "fallback_full_edit": True,
               "packet_system_files": ["style_bible.md"], "facts_dir": "", "timetable_file": "",
               "subjects": [], "session_subjects": {}, "afternoon_only": [], "event_times": {},
               "max_new_per_scene": 0, "default_rules": [], "canon": []},
    "plan_sync": {"enabled": True},  # resync stored scene plans after heavy revision (core/plan_sync.py)
    # Narrator re-voice pass inside the revise step (see core/voice.py). Off by default; enable per project.
    "voice": {"enabled": False, "role": "revision", "min_ratio": 0.0, "max_ratio": 1.0, "max_tell_drop": 0.3, "instructions": ""},
    # Style bible + voice sheets: style_bible.md and voices/*.yaml in the project dir, or bible_dir
    # (also settable per project as metadata.json "style_bible_dir").
    "style": {"bible_dir": None, "prefix_tokens": 2500, "planner_tokens": 2000, "planner_voice_tokens": 1500},
    # Craft layer (CRAFT_DESIGN.md). Default framework: Save the Cat beats + Swain scene/sequel cards.
    # Opt-in base-model reword (core/talkie_roll.py). Off by default: the pipeline step
    # makes no talkie, Jev or Sol calls until enabled. `bookforge talkie run` is an explicit opt-in.
    "talkie": {
        "enabled": False,
        "endpoint": "http://127.0.0.1:8080",
        "n_rerolls": 4,
        "parallel": 4,  # concurrent requests; match the server -np
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
    },
    "craft": {
        "enabled": True,
        "framework": "save_the_cat",       # save_the_cat | three_act | seven_point | story_grid | hero_journey
        "scene_sequel": True,
        "max_key_items_per_scene": 3,      # raise for denser plotting, lower for looser prose
        "min_setup_gap_chapters": 2,
        "rolling_refine": True,
        "rerank": False,                   # optional cost: 2 candidate endings for high-tension scenes
        "rerank_min_tension": 7,
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def config_path() -> Path:
    env = os.environ.get("BOOKFORGE_CONFIG")
    return Path(env) if env else Path.home() / ".bookforge" / "config.yaml"


_CACHE: Dict[str, Any] | None = None


def load_config(reload: bool = False) -> Dict[str, Any]:
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE
    cfg = DEFAULT_CONFIG
    p = config_path()
    if p.exists():
        cfg = _deep_merge(DEFAULT_CONFIG, yaml.safe_load(p.read_text()) or {})
    else:
        cfg = copy.deepcopy(DEFAULT_CONFIG)
    _CACHE = cfg
    return cfg


def set_config(cfg: Dict[str, Any]) -> None:
    """Override config (tests)."""
    global _CACHE
    _CACHE = _deep_merge(DEFAULT_CONFIG, cfg)


def example_config() -> Dict[str, Any]:
    """Ready-to-copy user config. Library defaults stay on Sol/Gemini; this file does not.

    ``write`` and ``rerank`` use the Gemma tier (Hugging Face router, 32k). Planner, fact,
    fix and flow roles use OpenRouter ``deepseek/deepseek-v4.1-flash-1017``. Brainstorm stays
    on xAI (web search). Talkie stays present and disabled: an opt-in fallback reword, not
    the default writer.
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    flash = {"provider": "openrouter", "model": DEEPSEEK_FLASH}
    for role in list(cfg["llm"]["roles"]):
        if role in ("write", "rerank"):
            cfg["llm"]["roles"][role] = {"tier": "gemma"}
        elif role != "brainstorm":
            cfg["llm"]["roles"][role] = dict(flash)
    cfg["talkie"]["enabled"] = False
    return cfg


_TEMPLATE_HEADER = (
    "# User config from `bookforge config-init` (written only when this file is missing).\n"
    "# Deep-merged over library defaults. Those defaults keep planner, drafter, editor on\n"
    "# openrouter openai/gpt-6-sol (reasoning medium; drafter headroom 6000) and utility on\n"
    "# google/gemini-2.5-flash. This file points write and rerank at Gemma on the Hugging Face\n"
    "# router (32k context; USD per 1M input 0.48, cached 0.10, output 1.44; no reasoning body)\n"
    "# and the other generative roles at openrouter deepseek/deepseek-v4.1-flash-1017.\n"
    "# talkie.enabled is false: talkie is an opt-in fallback reword, not the default writer.\n"
    "# Set HF_TOKEN in the environment. No API keys belong in this file.\n"
)


def write_template(path: Path | None = None) -> Path:
    """Write the key-free user template (explicit `bookforge config init`)."""
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        body = yaml.safe_dump(example_config(), sort_keys=False)
        p.write_text(_TEMPLATE_HEADER + body)
    return p


def declared_context_tokens(provider: str, model: str, config: dict | None = None) -> int | None:
    """Context window declared for this provider/model, if any."""
    config = config or load_config()
    llm = config.get("llm") or {}
    found = None
    entries = list((llm.get("models") or {}).values()) + list((llm.get("roles") or {}).values())
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("model") != model:
            continue
        prov = entry.get("provider")
        if prov and provider and prov != provider:
            continue
        if entry.get("context_tokens"):
            found = int(entry["context_tokens"])
    if model == GEMMA_MODEL_ID:
        return found or GEMMA_CONTEXT_TOKENS
    return found


def tight_context_model(provider: str, model: str, config: dict | None = None) -> bool:
    """True for the 32k Gemma window (and any model that declares a window that small)."""
    n = declared_context_tokens(provider, model, config)
    return n is not None and n <= GEMMA_CONTEXT_TOKENS


def adjusted_role_params(role: str, provider: str, model: str, config: dict | None = None) -> dict:
    """role_params, with reasoning removed and headroom clamped on a 32k model."""
    config = config or load_config()
    rp = role_params(role, config)
    extra = dict(rp["extra_body"] or {})
    headroom = int(rp["headroom"])
    if tight_context_model(provider, model, config):
        extra.pop("reasoning", None)
        headroom = min(headroom, GEMMA_MAX_HEADROOM)
    return {"extra_body": extra, "mult": float(rp["mult"]), "headroom": headroom}


def prompt_budget_for(provider: str, model: str, total_context_tokens: int | None,
                      config: dict | None = None) -> int | None:
    """Token budget for a write pack on a tight window, else None (no extra trim).

    Uses the project's ``total_context_tokens`` when it is lower than the ~24k cap
    that leaves completion room in a 32k window.
    """
    if not tight_context_model(provider, model, config):
        return None
    budget = GEMMA_PROMPT_BUDGET
    if total_context_tokens:
        budget = min(budget, int(total_context_tokens))
    return max(1, budget)


class RoleNotConfigured(KeyError):
    pass


def resolve_role(role: str, config: dict | None = None) -> tuple[str, str]:
    config = config or load_config()
    roles = config.get("llm", {}).get("roles", {})
    if role not in roles:
        fallback = config["llm"].get("default_role", "summary")
        print(f"[warn] LLM role '{role}' not configured; using role '{fallback}'")
        role = fallback
    entry = roles[role]
    tier = entry.get("tier")
    if tier and not (entry.get("provider") and entry.get("model")):
        t = dict(config["llm"].get("models", {}).get(tier, {}))
        t["provider"] = os.environ.get(f"BOOKFORGE_{tier.upper()}_PROVIDER") or t.get("provider")
        t["model"] = os.environ.get(f"BOOKFORGE_{tier.upper()}_MODEL") or t.get("model")
        if not (t.get("provider") and t.get("model")):
            raise RoleNotConfigured(f"tier '{tier}' for role '{role}' has no provider/model")
        return t["provider"], t["model"]
    return entry["provider"], entry["model"]


def role_params(role: str, config: dict | None = None) -> dict:
    """Tier request extras for a role: {"extra_body": {...}, "mult": float, "headroom": int}."""
    config = config or load_config()
    roles = config.get("llm", {}).get("roles", {})
    entry = roles.get(role) or roles.get(config["llm"].get("default_role", "summary"), {})
    tier = entry.get("tier")
    t = config["llm"].get("models", {}).get(tier, {}) if tier and not entry.get("model") else entry
    extra = copy.deepcopy(t.get("extra_body", {}) or {})
    eff = os.environ.get(f"BOOKFORGE_{tier.upper()}_REASONING_EFFORT") if tier else None
    if eff:
        if eff.lower() == "none":
            extra.pop("reasoning", None)
        else:
            extra["reasoning"] = {"effort": eff}
    return {"extra_body": extra, "mult": float(t.get("max_tokens_multiplier", 1.0)),
            "headroom": int(t.get("max_tokens_headroom", 0))}


def api_key(provider_cfg: dict) -> str:
    env = provider_cfg.get("api_key_env", "")
    if not env:
        return ""
    key = os.environ.get(env, "")
    if not key:
        raise RuntimeError(f"Missing API key: set environment variable {env}")
    return key
