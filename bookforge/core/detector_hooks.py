"""Report-only external detectors.

``external_check`` never edits the text and never logs an API key. The default
provider is ``none``. ``pangram`` reads the key's environment-variable *name*
from config and skips when that variable is unset. The HTTP call is not
implemented.
"""
from __future__ import annotations

import os

_FIELDS = ("provider", "status", "score", "spans", "cost_usd")


def _row(provider: str, status: str, score=None, spans=None, cost_usd: float = 0.0) -> dict:
    return {"provider": provider, "status": status, "score": score,
            "spans": list(spans or []), "cost_usd": float(cost_usd)}


def _none(text: str, cfg: dict) -> dict:
    return _row("none", "skipped")


def _pangram(text: str, cfg: dict) -> dict:
    env = str(cfg.get("api_key_env") or "PANGRAM_API_KEY")
    # TODO: HTTP POST `text` to the Pangram detector and fill score, spans, and cost_usd
    # from the response. Honor external_detector.cap_usd and keep mode "report" (do not
    # edit the chapter). Read the key only to see whether it is set. Never log, print,
    # or put the key in the returned dict.
    if not os.environ.get(env):
        return _row("pangram", "skipped: no key")
    return _row("pangram", "skipped: not implemented")


PROVIDERS = {"none": _none, "pangram": _pangram}


def external_check(text: str) -> dict:
    """Return ``{provider, status, score, spans, cost_usd}``. Does not modify ``text``."""
    from bookforge.config import load_config
    cfg = ((load_config().get("deai") or {}).get("external_detector") or {})
    name = str(cfg.get("provider") or "none").strip().lower()
    fn = PROVIDERS.get(name)
    if fn is None:
        row = _row(name, "skipped: unknown provider")
    else:
        row = fn(text, cfg)
    return {k: row.get(k) for k in _FIELDS}
