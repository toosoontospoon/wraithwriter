"""Run-scoped dollar cap for Gemma and Jev. Talkie is free. Checked before the call."""
from __future__ import annotations

import json

from bookforge.core import jev
from bookforge.core.jev import PRICE_PER_MTOK


class BudgetExceeded(RuntimeError):
    """The next billable call would pass ``latin_pivot.cap_usd``. It was not sent."""


def estimate_tokens(*parts: str) -> int:
    """Prompt tokens, about four characters each. At least 1 for a non-empty bill."""
    chars = sum(len(p or "") for p in parts)
    if chars <= 0:
        return 0
    return max(1, chars // 4)


def jev_cost(state, questions) -> float:
    blob = json.dumps(state, ensure_ascii=False) + json.dumps(questions, ensure_ascii=False)
    return estimate_tokens(blob) * PRICE_PER_MTOK / 1e6


def gemma_prices(cfg: dict) -> dict:
    from bookforge.config import resolve_role
    _provider, model = resolve_role("write", cfg)
    prices = ((cfg.get("llm") or {}).get("prices") or {})
    hit = prices.get(model) or {}
    return {"input": float(hit.get("input") or 0.0), "output": float(hit.get("output") or 0.0),
            "model": model}


def gemma_cost(system: str, prompt: str, cfg: dict) -> float:
    prices = gemma_prices(cfg)
    return estimate_tokens(system or "", prompt or "") * prices["input"] / 1e6


def role_input_cost(role: str, system: str, prompt: str, cfg: dict) -> float:
    """Input-token estimate for a role, using that role's configured model price."""
    from bookforge.config import resolve_role
    _provider, model = resolve_role(role, cfg)
    prices = ((cfg.get("llm") or {}).get("prices") or {}).get(model) or {}
    return estimate_tokens(system or "", prompt or "") * float(prices.get("input") or 0.0) / 1e6


class Budget:
    def __init__(self, cap_usd: float):
        self.cap = float(cap_usd)
        self.spent = 0.0
        self.by_stage: dict[str, float] = {}
        self.calls = {"gemma": 0, "talkie": 0, "jev": 0}

    def ensure(self, stage: str, estimate: float) -> None:
        est = max(0.0, float(estimate))
        if self.spent + est > self.cap:
            raise BudgetExceeded(
                f"latin_pivot cap ${self.cap:.4f} would be exceeded "
                f"by {stage} (spent ${self.spent:.6f} + estimate ${est:.6f})"
            )

    def charge(self, stage: str, amount: float, kind: str) -> None:
        amount = max(0.0, float(amount))
        self.spent += amount
        self.by_stage[stage] = round(self.by_stage.get(stage, 0.0) + amount, 8)
        if kind in self.calls and kind != "talkie":
            self.calls[kind] += 1

    def note_talkie(self, stage: str = "translate") -> None:
        """Talkie is local and costs nothing. It still counts as a call."""
        self.calls["talkie"] += 1
        self.by_stage.setdefault(stage, 0.0)

    def charge_observed(self, stage: str, kind: str, estimate: float, observed: float) -> None:
        """Book the logged spend when the client recorded one, else the pre-call estimate."""
        amount = observed if observed > 0 else max(0.0, float(estimate))
        self.charge(stage, amount, kind)


def jev_spent(log_dir) -> float:
    try:
        return float(jev.spent(log_dir))
    except Exception:
        return 0.0
