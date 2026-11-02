import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bookforge import config  # noqa: E402
from bookforge.core import jev, llm, talkie  # noqa: E402
from fakes import FakeLLM  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BOOKFORGE_CONFIG", str(tmp_path / "home" / "nope.yaml"))
    real_set = config.set_config

    def _set_config(cfg):
        # Library default is require_human_seed true. Existing drafts have no seed file.
        cfg = dict(cfg or {})
        write = dict(cfg.get("write") or {})
        write.setdefault("require_human_seed", False)
        cfg["write"] = write
        real_set(cfg)

    monkeypatch.setattr(config, "set_config", _set_config)
    _set_config({"llm": {"json_retries": 1}})
    yield
    llm.set_fake(None)
    jev.set_fake(None)
    talkie.set_fake(None)
    config._CACHE = None


@pytest.fixture
def block_network(monkeypatch):
    """Latin-pivot tests opt in. A real HTTP or subprocess call fails the test."""
    def boom(*_a, **_k):
        raise AssertionError("network or subprocess call in a latin_pivot test")

    import subprocess

    import requests

    from bookforge.core import jev, llm, talkie

    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(talkie.requests, "post", boom)
    monkeypatch.setattr(talkie.requests, "get", boom)
    monkeypatch.setattr(jev.requests, "post", boom)
    monkeypatch.setattr(jev.requests, "get", boom)
    monkeypatch.setattr(llm.requests, "post", boom)
    monkeypatch.setattr(llm.requests, "get", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    # talkie.manage defaults to true, so a run ends with talkie.stop_cmd. Record it, never run it.
    from bookforge.core.latin_pivot import talkie_lifecycle
    runs: list[str] = []
    monkeypatch.setattr(talkie_lifecycle, "default_runner", lambda cmd: runs.append(cmd))
    return runs


@pytest.fixture
def fake():
    f = FakeLLM()
    llm.set_fake(f)
    return f


@pytest.fixture
def project(tmp_path, fake):
    from bookforge.core.context import BookContext
    ctx = BookContext(tmp_path / "proj")
    ctx.metadata = {"prompt": "A boy joins an airship crew", "target_words": 1800}
    ctx.save_json("metadata.json", ctx.metadata)
    return ctx
