# llm — the detached LLM + agent-harness module

Everything vexa knows about model providers and coding-agent CLIs lives HERE, behind two
provider-agnostic ports. Product code (the meeting copilot, chat, routines) imports only the
front door (`llm/__init__.py`) and never names a vendor.

## The two ports (two call shapes)

| Port | Call shape | Used by | Selected by |
|---|---|---|---|
| `CompletionPort` | plain prompt→text HTTP call — no tools, no subprocess | meeting card beats | `VEXA_LLM_PROVIDER` |
| `HarnessPort` | a CLI coding agent over the mounted workspace — tool loop, sessions, streamed UnitEvents | post-meeting doc, chat, routines | `VEXA_LLM_CONTEXT_TOKENS` | the window the model server actually loads (Ollama: `OLLAMA_CONTEXT_LENGTH`); the `opencode` runner sizes and compacts its turns to it | unset → no compaction |
| `VEXA_RUNNER` |

Both are `typing.Protocol` (duck-typed, mirroring `core/runtime`'s `Backend` port); adapters are
selected env-driven in `registry.py` and constructor-injected everywhere, so tests use trivial
fakes.

## Adapters

- **Completions**: `openai_compat.py` (DEFAULT — OpenRouter, Ollama, vLLM, LM Studio, OpenAI, any
  gateway speaking `POST {base}/chat/completions`) · `anthropic_api.py` (the Messages dialect —
  api.anthropic.com, LiteLLM proxies, DeepSeek/GLM Anthropic-compatible endpoints) ·
  `claude_cli.py` (beats via the claude CLI on mounted SUBSCRIPTION credentials — no API key;
  slower per beat; for subscription-only deployments).
- **Harnesses**: `claude_code.py` (the `claude` CLI — argv build, stream-json parsing, `.claude/`
  continuity + skills wiring, credential preflight) · `opencode.py` (`opencode serve` driven over its
  HTTP API, one server per turn; points a fixed `local` provider at the deployment's
  OpenAI-compatible completion endpoint, so it needs no separate credentials; the per-turn tool
  permissions travel in `OPENCODE_CONFIG_CONTENT` with the working directory's own project config
  disabled — nothing is written into the worktree). Other open-source runners (Aider, Goose) slot in
  as new adapter files + one registry line.

Raw `httpx`, no vendor SDKs — the protocols are ~10 lines each and a pinned SDK is a heavier
supply-chain surface than the dialect itself.

## Configuration

| Env var | Meaning | Default |
|---|---|---|
| `VEXA_LLM_PROVIDER` | completion adapter: `openai-compat` \| `anthropic` | `openai-compat` |
| `VEXA_LLM_BASE_URL` | provider endpoint | anthropic: `https://api.anthropic.com`; openai-compat: **required** (falls back to `ANTHROPIC_BASE_URL`) |
| `VEXA_LLM_API_KEY` | credential (optional for local runtimes) | falls back `ANTHROPIC_AUTH_TOKEN` → `ANTHROPIC_API_KEY` |
| `VEXA_LLM_MODEL` | deployment-default model (free string) | empty → fail-loud at completion call |
| `VEXA_LLM_MAX_TOKENS` | Messages-API max_tokens | 4096 |
| `VEXA_RUNNER` | harness adapter key: `claude-code` \| `opencode` | `claude-code` |
| `ANTHROPIC_*`, `HOST_CLAUDE_CREDENTIALS` | claude-code adapter ONLY | — |

## Rules

- **This module imports NOTHING from product code** (`shared/`, `contracts`, `worker/`,
  `control_plane/`) — it must stay liftable into a standalone brick.
- Vendor names appear only in adapter files (`claude_code.py`, `anthropic_api.py`), never in
  `ports.py`/`registry.py` beyond registry keys.
- UnitEvent shapes (`message-delta` / `tool-call` / `tool-result` / `done{reply,sessionId,ok}` /
  `commit` and the `model-error` / `auth-error` builders in `errors.py`) are FROZEN — the terminal
  reducer and SSE relay consume them field-for-field.
- Session ids are OPAQUE per-harness tokens; an alien/stale id must yield `done.ok=False` (the
  engine's stale-resume retry heals it).

## Adding a provider / runner

1. New adapter file implementing the port (copy the closest existing one).
2. One line in `registry.py`'s table.
3. Unit test with a fake transport (`httpx.MockTransport`) or fake `exec_fn` — see
   `tests/test_llm_openai_compat.py` / `tests/test_llm_claude_code.py`.

## Running on a local model

- **Serve it natively on the host** (`deploy/compose/run-ollama-native.sh` for Ollama): Docker on a Mac has no GPU
  path. Point `VEXA_LLM_BASE_URL` at it, set `VEXA_RUNNER=opencode`, and name the model in `VEXA_LLM_MODEL`.
- **Tell the harness the window.** `VEXA_LLM_CONTEXT_TOKENS` must equal what the server loads
  (`run-ollama-native.sh` loads 16384). Left unset, OpenCode assumes no limit, never compacts, and the server
  silently drops the start of an over-long prompt — the system prompt and tool schema — so the model seems broken.
- **Two models.** `VEXA_LLM_MODEL` is the default for everything, including builds under `opencode`;
  `VEXA_MEETING_MODEL` overrides it for the live copilot, so a small fast model can run the notes while a larger one builds.
- **Measure before trusting a model**: `python -m eval.copilot_eval` and `python -m eval.build_eval`
  (see `eval/README.md`) score a model on the copilot and on build tasks.

