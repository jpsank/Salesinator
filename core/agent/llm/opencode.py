"""opencode.py — the OpenCode harness ADAPTER (server-mode driven, not a one-shot CLI call).

Everything this codebase knows about the ``opencode`` CLI lives in THIS file. Unlike
``claude_code.py`` (one subprocess per turn, stdout parsed as it streams), this adapter drives a
LOCAL ``opencode serve`` process over its HTTP API — a real architectural difference, not a style
choice: OpenCode's one-shot ``opencode run`` CLI has no way to hold a gated tool call open for later
approval (verified live: with a tool's permission set to ``"ask"`` and stdin closed, ``opencode run``
hangs forever — there's no timeout, no auto-deny, nothing to catch). The server API's
``POST /session/{id}/permission/{requestID}/reply`` does exactly what Vexa's own tool-gate mechanism
needs: the call is held, listable via ``GET /permission``, and only proceeds once answered — verified
live end-to-end (a held ``write`` call, listed, approved by id, the file only appearing on disk after).

One server per turn (started fresh in ``run_turn``, torn down after): matches ``HarnessPort``'s
stateless-per-call shape (no background process to keep alive across turns, no port contention
between concurrent worker containers) and the server's own startup cost is trivial next to the model
inference every turn already pays.

Credentials / model routing: this adapter has NO Anthropic-specific credential story — it points a
fixed ``local`` provider at whatever OpenAI-compatible endpoint the deployment already configures for
completions (``VEXA_LLM_BASE_URL`` / ``ANTHROPIC_BASE_URL``, the same vars ``openai_compat.py`` reads),
so a deployment that's already set up for a local/open-source model needs no separate OpenCode-specific
config.

Tool scoping: Claude Code's ``--allowedTools`` has no OpenCode CLI-flag equivalent — permissions are a
per-agent CONFIG (``allow`` / ``ask`` / ``deny``), written into ``opencode.json`` per turn from the
unit's resolved ``allowed_tools`` (see ``shared/tools.py``). ``auto``-grant tools → ``allow``;
anything not explicitly granted → ``deny`` (fail-closed, matches ``apply_tool_grant``'s own
philosophy). Vexa's ``gate`` grant has no ``allowed_tools`` representation to translate FROM today
(``shared/tools.py`` only emits ``auto`` tools into the allow-set; ``gate`` tools are tracked
separately as ``ToolGrant.gated`` and never reach this adapter as an OpenCode-CLI-shaped name) — so
this adapter does not yet wire ``gated`` names to OpenCode's ``"ask"`` state. It's built to: the
permission-polling loop in ``run_turn`` already treats ANY unexpected pending permission (one for a
tool this adapter didn't pre-approve) as a gate to surface, not just ones it already knows about —
but the caller-side wiring (turning ``ToolGrant.gated`` into a real proactive-card here) is the next
piece, not yet built.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterable, Iterator, Optional

import httpx

from llm.errors import LLMError
from llm.ports import harness_subprocess_env

OPENCODE_HOST = "127.0.0.1"
OPENCODE_PORT = int(os.environ.get("VEXA_OPENCODE_PORT") or "4096")
# The one provider id this adapter ever configures — deliberately fixed rather than caller-supplied,
# so a `model` string stays a bare tag (`gemma4:latest`, matching VEXA_LLM_MODEL's own shape
# elsewhere) instead of every caller needing to know OpenCode's `provider/model` convention.
LOCAL_PROVIDER_ID = "local"
_SERVER_READY_TIMEOUT_SEC = 30.0
_PERMISSION_POLL_SEC = 1.0

# Claude-Code-shaped tool name (what shared/tools.py's ToolGrant.allowed_tools / engine.py's
# defaults emit) → the OpenCode permission key it maps to. read/write/edit's "allow" path AND the
# "ask" → GET /permission → POST .../reply → tool actually runs round trip are both verified live
# against a real server. bash/glob/grep/webfetch/websearch follow OpenCode's own documented tool
# names but were NOT individually re-verified the same way — worth a spot-check before relying on
# `gate` for one of these specifically.
_BUILTIN_PERMISSION_KEYS = {
    "Read": "read", "Write": "write", "Edit": "edit", "Bash": "bash",
    "Glob": "glob", "Grep": "grep", "WebFetch": "webfetch", "WebSearch": "websearch",
}


class OpenCodeServerError(LLMError):
    """The local ``opencode serve`` process failed to start, or the API returned something this
    adapter can't make sense of."""


def _permission_key_for(tool_name: str) -> Optional[str]:
    """A Claude-Code-shaped granted tool name (``"Read"``, ``"mcp__web-search"``, ...) → the
    OpenCode permission key it should map to, or ``None`` for a name this adapter doesn't yet know
    how to translate (never silently mis-mapped — the caller decides what an unmapped name means)."""
    if tool_name in _BUILTIN_PERMISSION_KEYS:
        return _BUILTIN_PERMISSION_KEYS[tool_name]
    if tool_name.startswith("mcp__"):
        # An MCP server's tools aren't individually named here (shared/tools.py grants the whole
        # server, not per-tool) — OpenCode's permission config accepts glob keys (confirmed in its
        # own docs for bash sub-patterns and external_directory), so grant the server's namespace
        # rather than guessing OpenCode's own per-tool naming convention for MCP-sourced tools.
        return f"{tool_name[len('mcp__'):]}*"
    return None


def _mcp_servers_from_claude_config(mcp_config_path: Optional[str]) -> dict:
    """Read the ``.mcp.json`` ``shared/tools.py``'s ``apply_tool_grant`` already wrote (Claude
    Code's ``--mcp-config`` shape: ``{"mcpServers": {name: {command, args, env} | {url, type}}}``)
    and translate each entry into OpenCode's ``opencode.json`` ``"mcp"`` shape. Local (stdio)
    servers translate cleanly (OpenCode wants ``command`` as ONE argv array, not split
    command+args); a remote (``url``) entry is not yet translated — OpenCode's remote-MCP config
    shape wasn't verified against a real server the way the local/stdio path was, so a url-shaped
    entry is skipped with a loud comment here rather than guessed at silently."""
    if not mcp_config_path:
        return {}
    try:
        raw = json.loads(Path(mcp_config_path).read_text())
    except (OSError, ValueError):
        return {}
    out: dict = {}
    for name, spec in (raw.get("mcpServers") or {}).items():
        if "command" in spec:
            argv = [spec["command"], *spec.get("args", [])]
            entry: dict = {"type": "local", "command": argv}
            if spec.get("env"):
                entry["environment"] = spec["env"]
            out[name] = entry
        # else: a url-shaped (remote) MCP entry — not translated (see docstring above).
    return out


def _opencode_config(*, base_url: str, model: Optional[str], allowed_tools: Iterable[str],
                     mcp_config: Optional[str]) -> dict:
    """The ``opencode.json`` this adapter writes fresh for every turn: the one fixed local provider
    (pointed at whatever completion endpoint this deployment already configures), a fail-closed
    permission map built from the unit's resolved toolbelt, and the turn's granted MCP servers."""
    models = {model: {"name": model}} if model else {}
    permission: dict = {"*": "deny"}
    for tool_name in allowed_tools:
        key = _permission_key_for(tool_name)
        if key:
            permission[key] = "allow"
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            LOCAL_PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Vexa local completion endpoint",
                "options": {"baseURL": base_url},
                "models": models,
            },
        },
        "permission": permission,
        "mcp": _mcp_servers_from_claude_config(mcp_config),
    }


class _Server:
    """One ``opencode serve`` process, started fresh and torn down per turn (see module docstring
    for why per-turn rather than a shared background process). Blocks in ``__enter__`` until the
    HTTP API actually answers — a caller must never race a server that hasn't finished booting."""

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd
        self._proc: Optional[subprocess.Popen] = None
        self.base_url = f"http://{OPENCODE_HOST}:{OPENCODE_PORT}"

    def __enter__(self) -> "_Server":
        self._proc = subprocess.Popen(
            ["opencode", "serve", "--hostname", OPENCODE_HOST, "--port", str(OPENCODE_PORT)],
            cwd=self._cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=harness_subprocess_env(),
        )
        deadline = time.monotonic() + _SERVER_READY_TIMEOUT_SEC
        with httpx.Client(timeout=2.0) as probe:
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    out = self._proc.stdout.read() if self._proc.stdout else ""
                    raise OpenCodeServerError(f"opencode serve exited during startup: {out[-500:]}")
                try:
                    if probe.get(f"{self.base_url}/doc").status_code == 200:
                        return self
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
        self.__exit__(None, None, None)
        raise OpenCodeServerError(f"opencode serve did not become ready within {_SERVER_READY_TIMEOUT_SEC}s")

    def __exit__(self, *exc) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()


def _drive_turn(client: httpx.Client, session_id: str, prompt: str, model: Optional[str],
                result: dict) -> None:
    """Runs in its own thread: POSTs the message and blocks until the whole turn (including any
    gated tool calls held mid-flight) finishes. ``result`` is a plain dict the caller thread reads
    from after joining — simpler than a queue for a single one-shot outcome."""
    try:
        body: dict = {"parts": [{"type": "text", "text": prompt}]}
        if model:
            body["model"] = {"providerID": LOCAL_PROVIDER_ID, "modelID": model}
        resp = client.post(f"/session/{session_id}/message", json=body, timeout=None)
        resp.raise_for_status()
        result["message"] = resp.json()
    except httpx.HTTPError as exc:
        result["error"] = str(exc)


def _tool_events(part: dict) -> Iterator[dict]:
    state = part.get("state") or {}
    status = state.get("status")
    if status not in ("completed", "error"):
        return  # still running/pending (a gate awaiting approval) — nothing to emit yet
    call_id = part.get("callID", "")
    yield {"type": "tool-call", "tool": part.get("tool", ""), "args": state.get("input", {}), "callId": call_id}
    ok = status == "completed"
    summary = state.get("output") if ok else state.get("error")
    yield {"type": "tool-result", "callId": call_id, "ok": ok,
           "summary": " ".join(str(summary or "").split())[:80]}


def _message_events(message: dict, *, seen_call_ids: set) -> Iterator[dict]:
    for part in message.get("parts", []) or []:
        pt = part.get("type")
        if pt == "text" and part.get("text"):
            yield {"type": "message-delta", "text": part["text"]}
        elif pt == "tool" and part.get("callID") not in seen_call_ids:
            seen_call_ids.add(part.get("callID"))
            yield from _tool_events(part)


def _durable_transcript_lines(all_messages: list[dict]) -> list[str]:
    """Translate OpenCode's ``GET /session/{id}/message`` response into the SAME jsonl line shape
    ``workspace_reader.history()`` already parses (claude-code's own transcript format, which its
    CLI happens to write for free) — the control plane's one history CONTRACT; this harness conforms
    to it rather than the reader growing a second, opencode-specific parser."""
    lines: list[str] = []
    for msg in all_messages:
        info = msg.get("info") or {}
        parts = msg.get("parts") or []
        if info.get("role") == "user":
            text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
            if text.strip():
                lines.append(json.dumps({"type": "user", "message": {"content": text}}))
        elif info.get("role") == "assistant":
            content: list[dict] = []
            for p in parts:
                if p.get("type") == "text" and p.get("text"):
                    content.append({"type": "text", "text": p["text"]})
                elif p.get("type") == "tool" and (p.get("state") or {}).get("status") in ("completed", "error"):
                    content.append({"type": "tool_use", "name": p.get("tool", "")})
            if content:
                lines.append(json.dumps({"type": "assistant", "message": {"content": content}}))
    return lines


def _write_durable_transcript(chat_root: Path, session_id: str, all_messages: list[dict]) -> None:
    """OpenCode's own session lives only inside this turn's ``opencode serve`` subprocess — gone the
    moment the worker is torn down (idle timeout, restart, redeploy). Reproduced live: a worker
    recreation silently erased a session's entire history, no error anywhere, because nothing durable
    was ever written for it. Writes the FULL session (not just this turn — ``all_messages`` already
    carries the whole thing) so a later turn's rewrite self-heals a missed/partial write; best-effort
    because a conversation that already happened must never be lost over a failed SAVE of its copy."""
    try:
        lines = _durable_transcript_lines(all_messages)
        if not lines:
            return
        path = chat_root / ".claude" / "projects" / "opencode" / f"{session_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
    except OSError:
        pass  # the turn already succeeded for the user — a save failure here is never their error


class OpenCodeHarness:
    """``HarnessPort`` adapter for the OpenCode CLI/server."""

    name = "opencode"

    def __init__(self) -> None:
        self._chat_root: Optional[Path] = None

    def run_turn(self, work: Path, prompt: str, *, allowed_tools: Iterable[str] = (),
                 session: Optional[str] = None, model: Optional[str] = None,
                 mcp_config: Optional[str] = None) -> Iterator[dict]:
        base_url = (os.environ.get("VEXA_LLM_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL") or "")
        if not base_url:
            yield {"type": "done", "ok": False,
                  "reply": "no completion endpoint: set VEXA_LLM_BASE_URL for the opencode runner"}
            return
        # `model` has no caller-independent source the way base_url does (ANTHROPIC_BASE_URL is a
        # real fallback chain) — a caller that doesn't thread one through (e.g. submit_implementation's
        # dispatch body has no `model` field at all) silently got model=None, which built an opencode.json
        # with an EMPTY provider.models map. OpenCode then reports "ProviderNoProvidersError: No
        # providers are available" — reproduced live, the turn failing before it ever reached the
        # model. VEXA_LLM_MODEL is the same deployment-default every other adapter already falls back
        # to (openai_compat.py's own `self._model`), so this is consistent, not a new convention.
        if not model:
            model = os.environ.get("VEXA_LLM_MODEL") or None

        config_path = Path(work) / "opencode.json"
        config_path.write_text(json.dumps(
            _opencode_config(base_url=base_url, model=model, allowed_tools=allowed_tools,
                             mcp_config=mcp_config)
        ))

        try:
            with _Server(str(work)) as server:
                with httpx.Client(base_url=server.base_url, timeout=10.0) as client:
                    if session:
                        session_id = session
                    else:
                        created = client.post("/session", json={})
                        created.raise_for_status()
                        session_id = created.json()["id"]

                    result: dict = {}
                    turn_started_ms = int(time.time() * 1000)
                    driver = threading.Thread(
                        target=_drive_turn, args=(client, session_id, prompt, model, result), daemon=True,
                    )
                    driver.start()

                    seen_call_ids: set = set()
                    seen_permission_ids: set = set()
                    while driver.is_alive():
                        time.sleep(_PERMISSION_POLL_SEC)
                        # The session-scoped GET (documented in /doc's OpenAPI spec) actually falls
                        # through to the server's own web-UI SPA (verified live — it returns an HTML
                        # page, not JSON, despite the spec). The GLOBAL endpoint is the one that
                        # really answers JSON; filtered to this turn's session below.
                        try:
                            pending = client.get("/permission").json()
                        except (httpx.HTTPError, ValueError):
                            pending = []
                        for perm in pending:
                            if perm.get("sessionID") != session_id:
                                continue
                            pid = perm.get("id")
                            if pid in seen_permission_ids:
                                continue
                            seen_permission_ids.add(pid)
                            # A tool this turn's config didn't pre-approve landed here anyway — an
                            # OpenCode-native "ask"/unrecognized-tool case. Surfacing it as a
                            # tool-call lets the existing UnitEvent consumer see SOMETHING happened;
                            # turning it into a real proactive-card (Vexa's actual gate UX) and
                            # replying via POST .../permission/{id}/reply is the next piece, not
                            # built yet — until then this turn will eventually time out waiting on
                            # a call that's never approved, same as any other unimplemented gate.
                            yield {"type": "tool-call", "tool": perm.get("permission", ""),
                                  "args": (perm.get("metadata") or {}), "callId": pid}
                    driver.join()

                    if "error" in result:
                        yield {"type": "done", "ok": False, "reply": result["error"], "sessionId": session_id}
                        return
                    # The POST response only carries the FINAL message of the turn — verified live:
                    # a turn that calls a tool splits across TWO linked messages (one holding the
                    # tool-use steps, a later one — the POST's own response, `parentID`-linked back
                    # to the first — holding just the closing text), so trusting the POST body alone
                    # silently drops every tool-call event. Re-fetching the full list and walking
                    # every message this turn actually created (by creation time, not just the one
                    # the POST happened to return) is the reliable path — the same one a raw
                    # GET /session/{id}/message call was confirmed to work over live.
                    try:
                        all_messages = client.get(f"/session/{session_id}/message").json()
                    except (httpx.HTTPError, ValueError):
                        all_messages = []
                    if self._chat_root is not None:
                        # Durably — not deferred — since the worker (and this opencode server with
                        # it) can be torn down any time after this turn returns.
                        _write_durable_transcript(self._chat_root, session_id, all_messages)
                    reply_parts: list[str] = []
                    for msg in all_messages:
                        info = msg.get("info", {})
                        if info.get("role") != "assistant" or info.get("time", {}).get("created", 0) < turn_started_ms:
                            continue
                        for ev in _message_events(msg, seen_call_ids=seen_call_ids):
                            yield ev
                            if ev["type"] == "message-delta":
                                reply_parts.append(ev["text"])
                    yield {"type": "done", "ok": True, "reply": "".join(reply_parts), "sessionId": session_id}
        except OpenCodeServerError as exc:
            yield {"type": "done", "ok": False, "reply": str(exc)}

    def prepare(self, work: Path, chat_root: Optional[Path] = None) -> None:
        # No symlink/skills wiring needed (OpenCode's own session store IS the live continuity
        # mechanism — resume works without this) — but run_turn needs chat_root to know WHERE to
        # durably persist each turn's transcript (see _write_durable_transcript), since nothing else
        # threads it through to run_turn's own signature.
        self._chat_root = chat_root

    def transcript_bytes(self, work: Path, session_id: str) -> int:
        return 0  # resume-budget check only (_resume_id) — always "under budget" for opencode, same
                  # as before this fix; the durable file written in run_turn doesn't feed this check

    def owns_session_id(self, sid: str) -> bool:
        return sid.startswith("ses_")  # the opencode server's own id shape

    def preflight(self) -> Optional[str]:
        base_url = os.environ.get("VEXA_LLM_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
        if not base_url:
            return "VEXA_RUNNER=opencode but no VEXA_LLM_BASE_URL/ANTHROPIC_BASE_URL is set"
        return None
