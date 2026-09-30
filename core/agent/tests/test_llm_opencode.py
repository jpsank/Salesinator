"""llm/opencode.py — the OpenCode harness adapter. Zero coverage existed before this file; the gap
was real: reproduced live, a real dispatched feature-implementer turn failed with OpenCode's own
"ProviderNoProvidersError: No providers are available" because run_turn's `model` parameter had no
environment fallback (unlike `base_url`, two lines above it) — a caller that doesn't thread a model
through explicitly (submit_implementation's dispatch body has no `model` field at all) silently built
an opencode.json with an EMPTY provider.models map.
"""
from __future__ import annotations

import json
from pathlib import Path

from llm.opencode import OpenCodeHarness, OpenCodeServerError, _opencode_config


def test_opencode_config_includes_the_model_when_given():
    cfg = _opencode_config(base_url="http://host.docker.internal:11434/v1", model="llama3.2:3b",
                           allowed_tools=(), mcp_config=None)
    assert cfg["provider"]["local"]["models"] == {"llama3.2:3b": {"name": "llama3.2:3b"}}


def test_opencode_config_models_is_empty_when_model_is_none():
    """The exact shape that broke live — OpenCode reports "no providers available" for this."""
    cfg = _opencode_config(base_url="http://host.docker.internal:11434/v1", model=None,
                           allowed_tools=(), mcp_config=None)
    assert cfg["provider"]["local"]["models"] == {}


class _BoomOnEnter:
    """Stands in for _Server: run_turn writes opencode.json BEFORE ever touching this, so a server
    that fails immediately on __enter__ still lets us assert on the written config. Raises the SAME
    exception type a real failed server start raises (OpenCodeServerError) so run_turn's own
    `except OpenCodeServerError` catches it and yields a clean `done` event instead of propagating."""
    def __init__(self, *_a, **_kw):
        pass

    def __enter__(self):
        raise OpenCodeServerError("stub — never actually starts a real opencode process")

    def __exit__(self, *exc):
        return False


def test_run_turn_falls_back_to_vexa_llm_model_env_when_no_model_given(tmp_path, monkeypatch):
    """The actual fix: a caller (like submit_implementation's dispatch, which has no `model` field
    at all) that passes model=None must still get a real, non-empty provider.models map — matching
    VEXA_LLM_BASE_URL's own existing env-fallback behavior two lines above."""
    monkeypatch.setenv("VEXA_LLM_BASE_URL", "http://host.docker.internal:11434/v1")
    monkeypatch.setenv("VEXA_LLM_MODEL", "llama3.2:3b")
    monkeypatch.setattr("llm.opencode._Server", _BoomOnEnter)

    harness = OpenCodeHarness()
    events = list(harness.run_turn(tmp_path, "do something", model=None))
    assert events == [{"type": "done", "ok": False, "reply": "stub — never actually starts a real opencode process"}]

    written = json.loads((Path(tmp_path) / "opencode.json").read_text())
    assert written["provider"]["local"]["models"] == {"llama3.2:3b": {"name": "llama3.2:3b"}}


def test_run_turn_prefers_an_explicit_model_over_the_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_LLM_BASE_URL", "http://host.docker.internal:11434/v1")
    monkeypatch.setenv("VEXA_LLM_MODEL", "should-not-be-used")
    monkeypatch.setattr("llm.opencode._Server", _BoomOnEnter)

    harness = OpenCodeHarness()
    list(harness.run_turn(tmp_path, "do something", model="gemma4:latest"))

    written = json.loads((Path(tmp_path) / "opencode.json").read_text())
    assert written["provider"]["local"]["models"] == {"gemma4:latest": {"name": "gemma4:latest"}}


def test_run_turn_reports_a_clean_error_with_no_completion_endpoint(tmp_path, monkeypatch):
    monkeypatch.delenv("VEXA_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    harness = OpenCodeHarness()
    events = list(harness.run_turn(tmp_path, "do something"))
    assert events == [{"type": "done", "ok": False,
                        "reply": "no completion endpoint: set VEXA_LLM_BASE_URL for the opencode runner"}]
    assert not (Path(tmp_path) / "opencode.json").exists()  # fails before writing anything
