"""openai_compat.py — the DEFAULT CompletionPort adapter: any OpenAI-compatible endpoint.

One dialect covers nearly every provider — OpenRouter, Ollama, vLLM, LM Studio, OpenAI itself, and
most gateways all speak ``POST {base}/chat/completions``. Raw httpx, no vendor SDK: the request is
~10 lines and a pinned SDK would be a heavier supply-chain surface than the protocol itself.

Config (constructor args win over env): ``VEXA_LLM_BASE_URL`` (required — e.g.
``https://openrouter.ai/api/v1``, ``http://ollama:11434/v1``; falls back to ``ANTHROPIC_BASE_URL``
for deployments that already point one at a multi-protocol gateway), ``VEXA_LLM_API_KEY`` (falls
back ``ANTHROPIC_AUTH_TOKEN`` → ``ANTHROPIC_API_KEY``; optional — local runtimes need none),
``VEXA_LLM_MODEL`` (the deployment-default model), ``VEXA_LLM_TIMEOUT_SEC`` (default 300 — a hosted
API answers in seconds, but CPU-only local inference (Ollama, vLLM on a laptop) over a real,
workspace-governed card prompt routinely runs past a naive 120s budget; this adapter is the one
every local-model deployment goes through, so the default has to fit that case, not just a hosted
API's).
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from llm.errors import LLMAuthError, LLMConfigError, LLMError
from llm.ports import CompletionResult


class OpenAICompatCompletion:
    name = "openai-compat"

    def __init__(self, *, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, timeout: Optional[float] = None,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        self._base = (base_url or os.environ.get("VEXA_LLM_BASE_URL")
                      or os.environ.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
        self._key = (api_key or os.environ.get("VEXA_LLM_API_KEY")
                     or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                     or os.environ.get("ANTHROPIC_API_KEY") or "")
        self._model = model or os.environ.get("VEXA_LLM_MODEL") or ""
        if timeout is None:
            timeout = float(os.environ.get("VEXA_LLM_TIMEOUT_SEC") or 300.0)
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def complete(self, prompt: str, *, system: Optional[str] = None,
                 model: Optional[str] = None, response_schema: Optional[dict] = None) -> CompletionResult:
        target = (model or "").strip() or self._model
        if not self._base:
            raise LLMConfigError(
                "no completion endpoint: set VEXA_LLM_BASE_URL (e.g. https://openrouter.ai/api/v1, "
                "http://ollama:11434/v1) — the openai-compat provider has no default host"
            )
        if not target:
            raise LLMConfigError(
                "no model: set VEXA_LLM_MODEL (deployment default) or a model in the workspace's "
                "agents/meeting.md"
            )
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        body: dict = {"model": target, "messages": messages}
        if response_schema is not None:
            # OpenAI's response_format shape — Ollama's OpenAI-compat layer honors it too (verified
            # live against a real local model: eliminates malformed/incomplete JSON at the token
            # level, not just via a stricter prompt). A provider that doesn't understand this field
            # at all would 400 on it, same as any other unsupported field — no separate try/except,
            # consistent with every other error this method already surfaces.
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": response_schema},
            }
        try:
            r = self._client.post(f"{self._base}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"completion transport failure against {self._base}: {exc}") from exc
        if r.status_code in (401, 403):
            raise LLMAuthError(f"{r.status_code} from {self._base}: {r.text[:300]}")
        if r.status_code >= 400:
            raise LLMError(f"{r.status_code} from {self._base}: {r.text[:300]}")
        try:
            choice = (r.json().get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
        except (ValueError, AttributeError, IndexError, TypeError) as exc:
            raise LLMError(f"malformed completion payload from {self._base}: {exc}") from exc
        return CompletionResult(text=str(text), model=target)
