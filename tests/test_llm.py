import pytest

from bookforge.core import llm
from bookforge.core.schema import DeconflictResult


def test_json_repair_retry(fake):
    seq = iter(["not json at all", '```json\n{"verdicts": [{"fact_index": 0, "verdict": "consistent",}]}\n```'])
    fake.hooks["deconflict"] = lambda p, s: next(seq)
    r = llm.llm_call("x", role="deconflict", schema=DeconflictResult)
    assert r.verdicts[0].verdict == "consistent"
    assert "not valid for the required JSON schema" in fake.calls[-1][1]


def test_json_gives_up(fake):
    fake.hooks["deconflict"] = lambda p, s: '{"verdicts": [{"verdict": "maybe"}]}'
    with pytest.raises(llm.LLMError):
        llm.llm_call("x", role="deconflict", schema=DeconflictResult)


def test_missing_key_is_clear(monkeypatch):
    from bookforge.config import api_key
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        api_key({"api_key_env": "OPENROUTER_API_KEY"})


def test_budget_cap(monkeypatch):
    from bookforge import config
    config.set_config({"llm": {"budget_usd": 0}})
    with pytest.raises(llm.BudgetExceeded):
        llm.llm_call("x", role="summary")
