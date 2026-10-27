"""Hugging Face router provider and the optional Gemma prose tier.

Drives the real OpenAI-compatible chat path with a monkeypatched requests.post.
No network.
"""
import json

from bookforge import config as C
from bookforge.core import llm
from bookforge.core.context import BookContext, count_tokens
from bookforge.core import pack
from bookforge.steps import write_chapter


GEMMA = C.GEMMA_MODEL_ID
FLASH = C.DEEPSEEK_FLASH
_FLASH_ROLES = (
    "plan", "outline", "extract", "deconflict", "state_update", "summary",
    "talkie_fix", "audit", "critique", "edit", "revision", "worldbuild",
    "character_design", "world_design", "craft_plan", "deai", "craft_extract",
)


class _Resp:
    status_code = 200
    text = ""

    def __init__(self, usage):
        self._usage = usage

    def json(self):
        return {"choices": [{"message": {"content": "continued."}}], "usage": self._usage}


def test_library_defaults_unchanged():
    llm_cfg = C.DEFAULT_CONFIG["llm"]
    assert llm_cfg["models"]["planner"]["provider"] == "openrouter"
    assert llm_cfg["models"]["planner"]["model"] == "openai/gpt-6-sol"
    assert llm_cfg["models"]["planner"]["extra_body"]["reasoning"]["effort"] == "medium"
    assert llm_cfg["models"]["drafter"]["provider"] == "openrouter"
    assert llm_cfg["models"]["drafter"]["model"] == "openai/gpt-6-sol"
    assert llm_cfg["models"]["drafter"]["extra_body"]["reasoning"]["effort"] == "medium"
    assert llm_cfg["models"]["drafter"]["max_tokens_headroom"] == 6000
    assert llm_cfg["models"]["editor"]["model"] == "openai/gpt-6-sol"
    assert llm_cfg["models"]["editor"]["extra_body"]["reasoning"]["effort"] == "high"
    assert llm_cfg["models"]["utility"]["model"] == "google/gemini-2.5-flash"
    assert llm_cfg["roles"]["write"]["tier"] == "drafter"
    assert llm_cfg["roles"]["rerank"]["tier"] == "drafter"
    assert llm_cfg["roles"]["plan"]["tier"] == "planner"
    assert C.DEFAULT_CONFIG["talkie"]["enabled"] is False
    hf = llm_cfg["providers"]["huggingface"]
    assert hf["kind"] == "openai"
    assert hf["base_url"] == "https://router.huggingface.co/v1"
    assert hf["api_key_env"] == "HF_TOKEN"
    price = llm_cfg["prices"][GEMMA]
    assert price["input"] == 0.48 and price["cache_read"] == 0.10 and price["output"] == 1.44


def test_provider_resolves_to_gemma_tier():
    C.set_config({"llm": {"roles": {"write": {"tier": "gemma"}, "rerank": {"tier": "gemma"}}}})
    assert C.resolve_role("write") == ("huggingface", GEMMA)
    assert C.resolve_role("rerank") == ("huggingface", GEMMA)
    # Planner role is still the library tier when the user file does not override it.
    assert C.resolve_role("plan") == ("openrouter", "openai/gpt-6-sol")


def test_example_gemma_write_sets_budget_and_beats():
    cfg = C.example_config()
    assert cfg["llm"]["roles"]["write"] == {"tier": "gemma"}
    assert cfg["write"]["context_budget_tokens"] == 24000
    assert cfg["write"]["beat_by_beat"] is True
    assert cfg["write"]["beats_per_call"] == 1
    assert cfg["talkie"]["enabled"] is False
    assert cfg["context"]["prior_text"] == "summaries_plus_prev_plan"
    assert C.DEFAULT_CONFIG["context"]["prior_text"] == "full"
    assert C.DEFAULT_CONFIG["write"]["context_budget_tokens"] is None
    assert C.DEFAULT_CONFIG["write"]["beat_by_beat"] is False
    assert C.DEFAULT_CONFIG["llm"]["roles"]["plan"]["tier"] == "planner"


def test_template_points_write_at_gemma_and_other_roles_at_deepseek(tmp_path):
    path = C.write_template(tmp_path / "config.yaml")
    data = __import__("yaml").safe_load(path.read_text())
    assert data["llm"]["providers"]["huggingface"]["base_url"] == "https://router.huggingface.co/v1"
    assert data["llm"]["providers"]["huggingface"]["api_key_env"] == "HF_TOKEN"
    assert data["llm"]["roles"]["write"] == {"tier": "gemma"}
    assert data["llm"]["roles"]["rerank"] == {"tier": "gemma"}
    assert data["llm"]["models"]["gemma"]["model"] == GEMMA
    assert data["llm"]["models"]["gemma"]["context_tokens"] == 32768
    assert data["llm"]["models"]["gemma"]["max_tokens_headroom"] == 1024
    assert "extra_body" not in data["llm"]["models"]["gemma"]
    for role in _FLASH_ROLES:
        assert data["llm"]["roles"][role] == {"provider": "openrouter", "model": FLASH}, role
    assert data["llm"]["roles"]["brainstorm"]["provider"] == "xai"
    assert data["talkie"]["enabled"] is False
    assert data["llm"]["prices"][GEMMA]["input"] == 0.48
    # Library defaults were not retargeted by building the template.
    assert C.DEFAULT_CONFIG["llm"]["roles"]["plan"]["tier"] == "planner"
    assert C.DEFAULT_CONFIG["llm"]["models"]["drafter"]["model"] == "openai/gpt-6-sol"


def test_huggingface_chat_omits_openrouter_fields_and_prices_the_routed_id(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    C.set_config({"llm": {"roles": {"write": {
        "provider": "huggingface",
        "model": GEMMA,
        "extra_body": {"reasoning": {"effort": "medium"}},
        "max_tokens_multiplier": 1.0,
        "max_tokens_headroom": 8000,
    }}, "cache": {"enabled": True, "key": "bookforge:proj"}}})
    usage = {"prompt_tokens": 10000, "completion_tokens": 200,
             "prompt_tokens_details": {"cached_tokens": 4000}}
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp(usage)

    monkeypatch.setattr(llm.requests, "post", fake_post)
    llm.set_fake(None)
    llm.set_cost_log(tmp_path / "costs.jsonl")
    text = llm.llm_call("hello", role="write", max_tokens=500, temperature=0.8)
    assert text == "continued."
    assert captured["url"] == "https://router.huggingface.co/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer hf_test_token"
    payload = captured["json"]
    assert payload["model"] == GEMMA
    assert "reasoning" not in payload
    assert "usage" not in payload
    assert "prompt_cache_key" not in payload
    assert "session_id" not in payload
    assert "prompt_cache_options" not in payload
    # 500 requested, headroom clamped from 8000 to 1024. Not 8000+.
    assert payload["max_tokens"] == 500 + C.GEMMA_MAX_HEADROOM
    assert payload["max_tokens"] < 8000

    row = json.loads((tmp_path / "state" / "usage.jsonl").read_text().strip())
    assert row["model"] == GEMMA
    assert row["provider"] == "huggingface"
    assert row["cost_source"] == "estimate"
    assert "latency_s" in row
    expected = (6000 * 0.48 + 4000 * 0.10 + 200 * 1.44) / 1e6
    assert abs(row["cost_usd"] - round(expected, 6)) < 1e-9
    assert abs(llm.estimate_cost(GEMMA, {"prompt_tokens": 10000, "cached_tokens": 4000,
                                         "completion_tokens": 200}, C.load_config()) - expected) < 1e-12


def test_cache_fields_empty_for_huggingface_router():
    fields = llm.cache_fields(GEMMA, {"enabled": True, "key": "k"},
                              {"base_url": "https://router.huggingface.co/v1"})
    assert fields == {}
    # OpenRouter behaviour is unchanged.
    f = llm.cache_fields("openai/gpt-6-sol", {"enabled": True, "key": "k"},
                         {"base_url": "https://openrouter.ai/api/v1"})
    assert f["usage"] == {"include": True}
    assert f["prompt_cache_key"] == "k"


def test_gemma_write_skips_8000_pad_and_trims_oversize_pack(tmp_path):
    C.set_config({"llm": {"roles": {"write": {"tier": "gemma"}}}})
    assert write_chapter.write_max_tokens(1000) == int(1000 * 2.2)
    assert write_chapter.write_max_tokens(1000) < 8000
    C.set_config({})
    assert write_chapter.write_max_tokens(1000) == int(1000 * 2.2) + 8000

    C.set_config({"llm": {"roles": {"write": {"tier": "gemma"}}}})
    ctx = BookContext(tmp_path / "proj")
    system = "STORY " + ("alpha " * 50000)
    user = ("CHAPTER SO FAR\n" + ("beta " * 20000) + "\nTAIL_MARKER the beats end here")
    assert count_tokens(system) + count_tokens(user) > C.GEMMA_PROMPT_BUDGET
    system2, user2 = pack.fit_write_context(ctx, system, user, "huggingface", GEMMA)
    assert count_tokens(system2) + count_tokens(user2) <= C.GEMMA_PROMPT_BUDGET
    assert "TAIL_MARKER" in user2
    # A non-tight model is not trimmed.
    same_s, same_u = pack.fit_write_context(ctx, system, user, "openrouter", "openai/gpt-6-sol")
    assert same_s == system and same_u == user
