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

from llm.opencode import (
    OpenCodeHarness, OpenCodeServerError, _durable_transcript_lines, _opencode_config,
    _write_durable_transcript,
)


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
    """Stands in for _Server: run_turn builds the config BEFORE ever touching this, so a server that
    fails immediately on __enter__ still lets us assert on the config it was handed (``last_config``).
    Raises the SAME exception type a real failed server start raises (OpenCodeServerError) so run_turn's
    own `except OpenCodeServerError` catches it and yields a clean `done` event instead of propagating."""
    last_config: dict | None = None

    def __init__(self, cwd, config=None):
        type(self).last_config = config

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

    assert _BoomOnEnter.last_config["provider"]["local"]["models"] == {"llama3.2:3b": {"name": "llama3.2:3b"}}
    assert not (Path(tmp_path) / "opencode.json").exists()  # never a file in the worktree


def test_run_turn_prefers_an_explicit_model_over_the_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_LLM_BASE_URL", "http://host.docker.internal:11434/v1")
    monkeypatch.setenv("VEXA_LLM_MODEL", "should-not-be-used")
    monkeypatch.setattr("llm.opencode._Server", _BoomOnEnter)

    harness = OpenCodeHarness()
    list(harness.run_turn(tmp_path, "do something", model="gemma4:latest"))

    assert _BoomOnEnter.last_config["provider"]["local"]["models"] == {"gemma4:latest": {"name": "gemma4:latest"}}


def test_run_turn_reports_a_clean_error_with_no_completion_endpoint(tmp_path, monkeypatch):
    monkeypatch.delenv("VEXA_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    harness = OpenCodeHarness()
    events = list(harness.run_turn(tmp_path, "do something"))
    assert events == [{"type": "done", "ok": False,
                        "reply": "no completion endpoint: set VEXA_LLM_BASE_URL for the opencode runner"}]
    assert not (Path(tmp_path) / "opencode.json").exists()


# ── durable transcript (chat history survives the worker/opencode-server being torn down) ───────

def test_durable_transcript_lines_translates_user_and_assistant_messages():
    all_messages = [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "hello"}]},
        {"info": {"role": "assistant"}, "parts": [
            {"type": "text", "text": "hi there"},
            {"type": "tool", "tool": "read", "callID": "c1", "state": {"status": "completed", "output": "ok"}},
        ]},
    ]
    lines = _durable_transcript_lines(all_messages)
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"type": "user", "message": {"content": "hello"}}
    assert json.loads(lines[1]) == {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hi there"},
        {"type": "tool_use", "name": "read"},
    ]}}


def test_durable_transcript_lines_skips_pending_tool_calls_and_empty_messages():
    all_messages = [
        {"info": {"role": "user"}, "parts": []},  # no text — dropped
        {"info": {"role": "assistant"}, "parts": [
            {"type": "tool", "tool": "read", "callID": "c1", "state": {"status": "running"}},  # not done yet
        ]},  # no content survives — dropped
        {"info": {"role": "system"}, "parts": [{"type": "text", "text": "ignored"}]},  # unknown role — skipped
    ]
    assert _durable_transcript_lines(all_messages) == []


def test_write_durable_transcript_round_trips_through_workspace_reader(tmp_path):
    """Proves the actual contract claim: a write here must be readable by the control plane's
    EXISTING history() parser unmodified — the whole point of reusing claude-code's jsonl shape
    instead of growing a second format. Reproduced live before this fix: an opencode turn's reply
    rendered fine in the live SSE stream, but vanished completely on the next page reload."""
    from control_plane.workspace_reader import WorkspaceReader

    all_messages = [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "what's up"}]},
        {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "Not much!"}]},
    ]
    chat_root = tmp_path / ".system" / "2"
    _write_durable_transcript(chat_root, "ses_abc123", all_messages)

    (chat_root / ".claude" / "sessions").mkdir(parents=True)
    (chat_root / ".claude" / "sessions" / "chat-x.session").write_text("ses_abc123")
    reader = WorkspaceReader(str(tmp_path))
    turns = reader.history("2", "chat-x")
    assert turns == [
        {"role": "user", "text": "what's up"},
        {"role": "agent", "text": "Not much!", "ops": []},
    ]


def test_write_durable_transcript_is_a_full_rewrite_each_time(tmp_path):
    """all_messages carries the WHOLE session, not just the new turn — each write REPLACES the file
    (self-healing against a missed/partial earlier write), never appends and duplicates."""
    chat_root = tmp_path / ".system" / "2"
    _write_durable_transcript(chat_root, "ses_x", [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "one"}]},
    ])
    _write_durable_transcript(chat_root, "ses_x", [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "one"}]},
        {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "two"}]},
    ])
    path = chat_root / ".claude" / "projects" / "opencode" / "ses_x.jsonl"
    assert len(path.read_text().splitlines()) == 2


def test_owns_session_id_recognizes_the_servers_ses_shape():
    h = OpenCodeHarness()
    assert h.owns_session_id("ses_f09ae4465ffeCaGP1bTLowRcYL") is True
    assert h.owns_session_id("5c1f661a-fbd0-4e1e-b089-cdba34fc724b") is False  # claude-code's own shape
    assert h.owns_session_id("") is False


def test_write_durable_transcript_never_raises_on_an_unwritable_path(tmp_path):
    """Best-effort: a save failure must never surface as a turn error — the conversation already
    succeeded for the user; losing the durable copy is a shame, not their error."""
    chat_root = tmp_path / "blocked"
    chat_root.write_text("not a directory")  # collides with the .claude/... mkdir below
    _write_durable_transcript(chat_root, "ses_x", [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "hi"}]},
    ])  # must not raise


def test_server_hands_the_config_over_in_the_environment_not_as_a_file(tmp_path, monkeypatch):
    from llm.opencode import _Server
    monkeypatch.setattr("llm.opencode.harness_subprocess_env", lambda: {"PATH": "/bin"})
    env = _Server(str(tmp_path), {"provider": {"local": {}}})._env()
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"]) == {"provider": {"local": {}}}
    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"   # a repo's own opencode.json must not merge in
    assert list(tmp_path.iterdir()) == []


def test_opencode_config_carries_the_served_context_window(monkeypatch):
    """OpenCode records a context of 0 for a model it has no limit for, so it never compacts and the server
    silently drops the front of an over-long prompt. The window the endpoint loads rides in as ``limit``."""
    monkeypatch.setenv("VEXA_LLM_CONTEXT_TOKENS", "16384")
    monkeypatch.delenv("VEXA_LLM_MAX_TOKENS", raising=False)
    cfg = _opencode_config(base_url="http://x/v1", model="gemma4:latest", allowed_tools=[], mcp_config=None)
    assert cfg["provider"]["local"]["models"]["gemma4:latest"]["limit"] == {"context": 16384, "output": 4096}

    monkeypatch.setenv("VEXA_LLM_MAX_TOKENS", "2048")
    cfg = _opencode_config(base_url="http://x/v1", model="gemma4:latest", allowed_tools=[], mcp_config=None)
    assert cfg["provider"]["local"]["models"]["gemma4:latest"]["limit"]["output"] == 2048

    monkeypatch.setenv("VEXA_LLM_MAX_TOKENS", "999999")   # never lets a reply claim the whole window
    cfg = _opencode_config(base_url="http://x/v1", model="gemma4:latest", allowed_tools=[], mcp_config=None)
    assert cfg["provider"]["local"]["models"]["gemma4:latest"]["limit"]["output"] == 8192


def test_opencode_config_has_no_limit_when_the_window_is_unknown_or_unusable(monkeypatch):
    for value in (None, "", "0", "-5", "lots"):
        if value is None:
            monkeypatch.delenv("VEXA_LLM_CONTEXT_TOKENS", raising=False)
        else:
            monkeypatch.setenv("VEXA_LLM_CONTEXT_TOKENS", value)
        cfg = _opencode_config(base_url="http://x/v1", model="m", allowed_tools=[], mcp_config=None)
        assert "limit" not in cfg["provider"]["local"]["models"]["m"], value
