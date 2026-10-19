"""Prompt caching: breakpoints, explicit OpenAI mode, usage parsing, cost estimate, usage report."""
import json

from bookforge.core import llm, usage


def test_breakpoints_from_system_and_cache_break():
    msgs = llm.build_messages("SYS", "PACK" + llm.CACHE_BREAK + "TASK", "openai/gpt-6-sol", {"enabled": True})
    assert msgs[0]["content"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert msgs[1]["content"][0] == {"type": "text", "text": "PACK", "cache_control": {"type": "ephemeral"}}
    assert msgs[1]["content"][1] == {"type": "text", "text": "TASK"}


def test_caching_off_strips_markers():
    msgs = llm.build_messages("SYS", "A" + llm.CACHE_BREAK + "B", "x/y", {"enabled": False})
    assert msgs[1]["content"] == "A\n\nB" and "CACHE_BREAK" not in json.dumps(msgs)


def test_at_most_four_breakpoints():
    p = llm.CACHE_BREAK.join(["a", "b", "c", "d", "e", "f"])
    msgs = llm.build_messages("S", p, "anthropic/x", {"enabled": True})
    assert sum("cache_control" in b for m in msgs for b in m["content"]) == 4


def test_openai_explicit_mode_and_session():
    f = llm.cache_fields("openai/gpt-6-sol", {"enabled": True, "key": "k"}, {"base_url": "https://openrouter.ai/api/v1"})
    assert f["prompt_cache_options"]["mode"] == "explicit" and f["session_id"] == "k" and f["usage"] == {"include": True}
    assert "prompt_cache_options" not in llm.cache_fields("google/gemini-2.5-flash", {"enabled": True}, {})


def test_usage_normalize_and_estimate():
    u = llm.normalize_usage({"prompt_tokens": 10000, "completion_tokens": 100,
                             "prompt_tokens_details": {"cached_tokens": 8000, "cache_write_tokens": 0}, "cost": 0.0123})
    assert u["cached_tokens"] == 8000 and u["cost_usd"] == 0.0123
    cfg = {"llm": {"prices": {"m": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5}}}}
    est = llm.estimate_cost("m", {"prompt_tokens": 10000, "cached_tokens": 8000, "completion_tokens": 100}, cfg)
    assert abs(est - (2000 * 2.0 + 8000 * 0.2 + 100 * 10.0) / 1e6) < 1e-9


def test_usage_report(tmp_path):
    p = tmp_path / "usage.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"chapter": "ch03", "step": "write", "prompt_tokens": 1000, "cached_tokens": 800, "completion_tokens": 10, "cost_usd": 0.01},
        {"chapter": "ch03", "step": "revise", "prompt_tokens": 1000, "cached_tokens": 0, "completion_tokens": 10, "cost_usd": 0.02}]))
    out = usage.report(p, by="step")
    assert "write" in out and "TOTAL" in out and "0.0300" in out


def test_one_system_prompt_for_all_calls_and_chapters():
    import inspect
    from bookforge.core import pack
    src = inspect.getsource(pack.system_for)
    for volatile in ("time", "random", "chapter['id']", "chapter[\"id\"]"):
        assert volatile not in src
