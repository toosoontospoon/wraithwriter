"""Start and stop a local Talkie server around a latin-pivot run.

The subprocess runner is injectable. The default runner is the only place that
calls ``subprocess.run``, and tests never use it. ``stop`` runs from ``finally``
when this run started the server or ``stop_after`` is true, including exceptions,
KeyboardInterrupt, and a budget abort.
"""
from __future__ import annotations

import time
from typing import Callable

from bookforge.core.talkie import TalkieUnavailable

_PING_FAKE: Callable | None = None


def set_ping_fake(fn) -> None:
    """Test hook. ``fn(endpoint) -> (ok, message)``. ``None`` clears it."""
    global _PING_FAKE
    _PING_FAKE = fn


def ping_endpoint(endpoint: str, timeout: float = 5.0) -> tuple[bool, str]:
    if _PING_FAKE is not None:
        return _PING_FAKE(endpoint)
    from bookforge.core import talkie
    return talkie.ping(endpoint, timeout=timeout)


def default_runner(cmd: str):
    import subprocess
    return subprocess.run(cmd, shell=True, check=False)


def effective_stop_after(talkie_cfg: dict) -> bool:
    """``stop_after`` null follows ``manage`` (true when manage is true)."""
    if talkie_cfg.get("stop_after") is None:
        return bool(talkie_cfg.get("manage"))
    return bool(talkie_cfg.get("stop_after"))


class TalkieSession:
    def __init__(self, talkie_cfg: dict, *, runner: Callable | None = None,
                 ping: Callable | None = None, sleep=None, now=None, interval: float = 0.25):
        cfg = talkie_cfg or {}
        self.endpoint = cfg.get("endpoint") or "http://127.0.0.1:8080"
        self.manage = bool(cfg.get("manage"))
        self.start_cmd = (cfg.get("start_cmd") or "").strip()
        self.stop_cmd = cfg.get("stop_cmd") or "docker stop talkie"
        self.stop_after = effective_stop_after(cfg)
        self.timeout = float(cfg.get("start_timeout_s") or 60)
        self.runner = runner or default_runner
        self.ping = ping or ping_endpoint
        self.sleep = sleep or time.sleep
        self.now = now or time.monotonic
        self.interval = interval
        self.started = False
        self._stopped = False

    def __enter__(self):
        try:
            self._bring_up()
            return self
        except BaseException:
            self._stop()
            raise

    def __exit__(self, exc_type, exc, tb):
        self._stop()
        return False

    def _bring_up(self) -> None:
        ok, message = self.ping(self.endpoint)
        if ok:
            return
        if not self.manage:
            raise TalkieUnavailable(f"talkie is down at {self.endpoint} and talkie.manage is false: {message}")
        if not self.start_cmd:
            raise TalkieUnavailable("talkie is down and talkie.start_cmd is empty")
        self.runner(self.start_cmd)
        self.started = True
        deadline = self.now() + self.timeout
        while self.now() < deadline:
            ok, message = self.ping(self.endpoint)
            if ok:
                return
            self.sleep(self.interval)
        raise TalkieUnavailable(
            f"talkie did not come up at {self.endpoint} within {self.timeout:.0f}s: {message}")

    def _stop(self) -> None:
        if self._stopped:
            return
        if not (self.started or self.stop_after):
            return
        self._stopped = True
        self.runner(self.stop_cmd)
