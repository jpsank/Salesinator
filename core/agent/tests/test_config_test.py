"""Settings → Models "Test" buttons — the on-demand credential tests (control_plane.config_test).

Grades the exact failure modes observed live on 2026-07-09: stale Keychain export (expired
subscription file), zero-balance external transcription token (402 per segment), rejected
token, unreachable backend, and the happy paths.
"""
import json

from control_plane import config_test as ct


# ── subscription file ─────────────────────────────────────────────────────────────────────────

def _write_creds(tmp_path, expires_ms):
    p = tmp_path / "creds.json"
    p.write_text(json.dumps({"claudeAiOauth": {"expiresAt": expires_ms}}))
    return str(p)


def test_subscription_missing_file(tmp_path):
    out = ct.test_subscription_credentials(str(tmp_path / "absent"))
    assert not out["ok"] and "HOST_CLAUDE_CREDENTIALS" in out["summary"]


def test_subscription_expired_carries_remedy(tmp_path):
    out = ct.test_subscription_credentials(_write_creds(tmp_path, 1_000_000), now=2_000.0)
    assert not out["ok"] and out.get("expired") is True
    assert ct.KEYCHAIN_REFRESH in out["summary"]  # the fix ships WITH the failure


def test_subscription_valid_reports_hours_left(tmp_path):
    out = ct.test_subscription_credentials(_write_creds(tmp_path, 10 * 3600 * 1000), now=0.0)
    assert out["ok"] and out["expires_in_hours"] == 10.0


def test_subscription_garbage_file(tmp_path):
    p = tmp_path / "creds.json"
    p.write_text("not json")
    out = ct.test_subscription_credentials(str(p))
    assert not out["ok"] and ct.KEYCHAIN_REFRESH in out["summary"]


# ── custom endpoint ───────────────────────────────────────────────────────────────────────────

def test_custom_endpoint_auth_failure():
    out = ct.test_custom_endpoint("https://gw.example", "bad-key",
                                  post=lambda u, p, h: (401, "{}"))
    assert not out["ok"] and "Authentication FAILED" in out["summary"]


def test_custom_endpoint_ok_anthropic_dialect():
    calls = []
    def post(url, payload, headers):
        calls.append(url)
        return 200, "{}"
    out = ct.test_custom_endpoint("https://gw.example/", "k", "m1", post=post)
    assert out["ok"] and calls == ["https://gw.example/v1/messages"]


def test_custom_endpoint_falls_back_to_openai_dialect():
    def post(url, payload, headers):
        return (404, "") if url.endswith("/v1/messages") else (200, "{}")
    out = ct.test_custom_endpoint("https://gw.example", "k", post=post)
    assert out["ok"]


def test_custom_endpoint_unreachable():
    def post(url, payload, headers):
        raise OSError("connection refused")
    out = ct.test_custom_endpoint("https://gw.example", "k", post=post)
    assert not out["ok"] and "unreachable" in out["summary"]


def test_custom_endpoint_does_not_double_v1_when_base_already_has_it():
    """Reproduced live: VEXA_LLM_BASE_URL always includes /v1 (OpenAI SDK convention — see
    llm/openai_compat.py), but this function used to blindly append /v1/messages and
    /v1/chat/completions regardless, building .../v1/v1/chat/completions — a 404 against a real
    local Ollama even though the identical request one /v1 shorter succeeded."""
    seen = []
    def post(url, payload, headers):
        seen.append(url)
        if url.endswith("/v1/messages"):
            return (404, "")
        return (200, "{}")
    out = ct.test_custom_endpoint("http://ollama:11434/v1", "", post=post)
    assert out["ok"], out
    # The OpenAI-compat fallback matches the real adapter exactly (llm/openai_compat.py:
    # f"{self._base}/chat/completions" where self._base already ends in /v1) — only the
    # Anthropic-dialect attempt needed the /v1 stripped first, since IT appends its own /v1/messages.
    assert seen == ["http://ollama:11434/v1/messages", "http://ollama:11434/v1/chat/completions"]


def test_custom_endpoint_without_v1_still_appends_it():
    """The pre-existing ANTHROPIC_BASE_URL shape (no /v1) is unchanged by the fix above."""
    seen = []
    def post(url, payload, headers):
        seen.append(url)
        return (200, "{}")
    out = ct.test_custom_endpoint("https://gw.example", "k", post=post)
    assert out["ok"]
    assert seen == ["https://gw.example/v1/messages"]


def test_run_models_test_routes_custom_vs_subscription(tmp_path):
    out = ct.run_models_test({"mode": "custom", "base_url": "https://gw", "api_key": "k"},
                             env={}, post=lambda u, p, h: (200, "{}"))
    assert out["mode"] == "custom" and out["ok"]
    out = ct.run_models_test({}, env={}, creds_path=str(tmp_path / "absent"))
    assert out["mode"] == "subscription" and not out["ok"]
    # secrets never echo in provenance
    out = ct.run_models_test({"mode": "custom", "base_url": "https://gw", "api_key": "SECRET"},
                             env={}, post=lambda u, p, h: (200, "{}"))
    assert "api_key" not in out["config"] and "SECRET" not in json.dumps(out)


def test_run_models_test_routes_local_when_runner_is_not_claude_code():
    """Reproduced live: a deployment with agent_runner=opencode (chat routed through a local
    OpenAI-compatible endpoint) still reported mode=subscription here — this test only ever
    checked ANTHROPIC_*/the mounted Claude credentials file, with zero awareness opencode exists.
    Deployment default (no user mode override) + a non-claude-code runner must test VEXA_LLM_*,
    not Claude credentials the deployment never touches."""
    env = {"VEXA_LLM_BASE_URL": "http://host.docker.internal:11434/v1", "VEXA_LLM_MODEL": "gemma4:latest"}
    # Strict: 404s a doubled /v1/v1/... path — only the exact real-adapter shape succeeds, so this
    # test would have caught the live double-/v1 bug, not just exercised the "local" branch.
    def post(u, p, h):
        return (200, "{}") if u in ("http://host.docker.internal:11434/v1/messages",
                                     "http://host.docker.internal:11434/chat/completions") else (404, "")
    out = ct.run_models_test({}, env=env, runner="opencode", post=post)
    assert out["mode"] == "local" and out["ok"]

    # An unreachable local endpoint must fail loud as "local", never silently read as a healthy
    # Claude subscription (the exact bug this closes).
    def _refused(u, p, h):
        raise ConnectionRefusedError("refused")
    out = ct.run_models_test({}, env=env, runner="opencode", post=_refused)
    assert out["mode"] == "local" and not out["ok"]


def test_run_models_test_user_override_wins_over_the_deployment_runner():
    """A user's OWN explicit choice always says what THEY asked for, regardless of the deployment's
    default harness — subscription or custom, verbatim, never silently reinterpreted as local."""
    out = ct.run_models_test({"mode": "subscription"}, env={}, runner="opencode",
                             creds_path="/does/not/exist")
    assert out["mode"] == "subscription"
    out = ct.run_models_test({"mode": "custom", "base_url": "https://gw", "api_key": "k"},
                             env={}, runner="opencode", post=lambda u, p, h: (200, "{}"))
    assert out["mode"] == "custom"


def test_run_models_test_claude_code_runner_keeps_old_behavior():
    """runner="claude-code" (or unset, the deployment default) is unchanged from before this fix."""
    out = ct.run_models_test({}, env={}, runner="claude-code", creds_path="/does/not/exist")
    assert out["mode"] == "subscription" and not out["ok"]


# ── available-model suggestions (Settings → Models' Chat/Meeting model fields) ──────────────────

def test_list_available_models_claude_code_default_and_subscription_use_the_known_aliases():
    out = ct.list_available_models({}, env={}, runner="claude-code")
    assert out["models"] == ["sonnet", "opus", "haiku"]
    out = ct.list_available_models({"mode": "subscription"}, env={}, runner="opencode")
    assert out["models"] == ["sonnet", "opus", "haiku"]  # explicit override wins over the runner


def test_list_available_models_custom_mode_queries_the_real_endpoint():
    def get(url, headers):
        assert url == "https://gw.example/v1/models"
        assert headers == {"Authorization": "Bearer k"}
        return 200, json.dumps({"data": [{"id": "qwen3"}, {"id": "deepseek-v4"}]})
    out = ct.list_available_models({"mode": "custom", "base_url": "https://gw.example", "api_key": "k"},
                                   env={}, get=get)
    assert out["models"] == ["qwen3", "deepseek-v4"] and out["source"] == "https://gw.example"


def test_list_available_models_routes_local_when_runner_is_not_claude_code():
    """Deployment default (no user override) + a non-claude-code runner must query VEXA_LLM_BASE_URL
    — the same endpoint a real turn actually hits — never the claude-code alias list."""
    env = {"VEXA_LLM_BASE_URL": "http://localhost:11434/v1", "VEXA_LLM_API_KEY": ""}
    def get(url, headers):
        assert url == "http://localhost:11434/v1/models"
        return 200, json.dumps({"data": [{"id": "gemma4:latest"}]})
    out = ct.list_available_models({}, env=env, runner="opencode", get=get)
    assert out["models"] == ["gemma4:latest"]


def test_list_available_models_does_not_double_v1_when_base_already_has_it():
    def get(url, headers):
        return (200, json.dumps({"data": []})) if url == "http://ollama:11434/v1/models" else (404, "")
    out = ct.list_available_models({"mode": "custom", "base_url": "http://ollama:11434/v1"}, env={}, get=get)
    assert out["models"] == []  # would have 404'd on a doubled /v1/v1/models — empty, not an error


def test_list_available_models_unreachable_endpoint_is_an_empty_list_not_a_crash():
    def get(url, headers):
        raise ConnectionRefusedError("refused")
    out = ct.list_available_models({"mode": "custom", "base_url": "https://gw.example"}, env={}, get=get)
    assert out == {"models": [], "source": "https://gw.example"}


def test_list_available_models_custom_mode_without_base_url_is_empty():
    out = ct.list_available_models({"mode": "custom"}, env={})
    assert out == {"models": [], "source": ""}


# ── transcription backend ─────────────────────────────────────────────────────────────────────

def _balance(email, minutes):
    return 200, json.dumps({"email": email, "balance_minutes": minutes})


# The 2026-07-19 recurrence, as a permanent pair: two tokens, both reporting balance 0.0 —
# one exhausted (every request 402s), one billing-exempt (transcribes fine). NO balance
# threshold and NO account name can tell them apart; only the endpoint's answer to real audio
# can. Neither row names any specific account: identity must never be the oracle.

def test_transcription_exhausted_token_fails_loud_despite_valid_auth():
    out = ct.run_transcription_test(
        "https://transcription.vexa.ai", "tok", "settings",
        get=lambda u, h: _balance("someone@gmail.com", 0.0),
        probe=lambda e, t: (402, '{"detail":"Insufficient balance"}'))
    assert not out["ok"] and "402" in out["summary"] and out["source"] == "settings"
    assert "NO transcript" in out["summary"], "the verdict must name the consequence"


def test_transcription_zero_balance_but_transcribing_token_is_green():
    """A billing-exempt account reports 0.0 minutes and transcribes perfectly — the round-trip
    must green it where a balance threshold would condemn it."""
    out = ct.run_transcription_test(
        "https://transcription.vexa.ai", "tok", "env",
        get=lambda u, h: _balance("svc-account@example.com", 0.0),
        probe=lambda e, t: (200, '{"text":"probe"}'))
    assert out["ok"], "zero balance alone must never fail a token that transcribes"
    assert "svc-account@example.com" in out["summary"]


def test_transcription_funded_external_ok():
    out = ct.run_transcription_test(
        "https://transcription.vexa.ai", "tok", "env",
        get=lambda u, h: _balance("someone@gmail.com", 42.5),
        probe=lambda e, t: (200, '{"text":"probe"}'))
    assert out["ok"] and "someone@gmail.com" in out["summary"]


def test_transcription_rejected_token():
    out = ct.run_transcription_test("https://x", "bad", "env", get=lambda u, h: (403, ""),
                                    probe=lambda e, t: (403, ""))
    assert not out["ok"] and "REJECTED" in out["summary"]


def test_transcription_backend_5xx_is_red():
    out = ct.run_transcription_test("https://t", "tok", "env", get=lambda u, h: (404, ""),
                                    probe=lambda e, t: (503, ""))
    assert not out["ok"] and "503" in out["summary"]


def test_transcription_strips_v1_path_for_balance_probe():
    seen = []
    def get(url, headers):
        seen.append(url)
        return _balance("someone@example.com", 5.0)
    ct.run_transcription_test("https://t.vexa.ai/v1/audio/transcriptions", "tok", "env", get=get,
                              probe=lambda e, t: (200, "{}"))
    assert seen == ["https://t.vexa.ai/balance"]


def test_transcription_no_backend_and_no_token():
    out = ct.run_transcription_test("", "", "env")
    assert not out["ok"] and "No transcription backend" in out["summary"]
    out = ct.run_transcription_test("https://t", "", "env")
    assert not out["ok"] and "NO token" in out["summary"]


def test_transcription_unreachable():
    def boom(endpoint, token):
        raise OSError("timeout")
    out = ct.run_transcription_test("https://t", "tok", "env", get=lambda u, h: (404, ""),
                                    probe=boom)
    assert not out["ok"] and "unreachable" in out["summary"]


def test_native_safe_url_rewrites_host_docker_internal_when_not_containerized():
    """Reproduced live: agent-api's own transcription test resolved a per-user URL of
    host.docker.internal:8083 (correct for a bot CONTAINER) and failed with a real DNS error
    ("nodename nor servname provided") once agent-api itself started running natively."""
    assert ct._native_safe_url("http://host.docker.internal:8083", in_docker=False) == "http://localhost:8083"
    # A dockerized agent-api is the intended caller shape for this URL — leave it alone.
    assert ct._native_safe_url("http://host.docker.internal:8083", in_docker=True) == "http://host.docker.internal:8083"
    # Nothing to rewrite — passes through unchanged either way.
    assert ct._native_safe_url("https://api.vexa.ai", in_docker=False) == "https://api.vexa.ai"


def test_transcription_rewrites_host_docker_internal_for_a_native_process():
    seen = []
    def get(u, h):
        seen.append(u)
        return 200, json.dumps({"email": "a@b.com"})
    def probe(endpoint, token):
        seen.append(endpoint)
        return 200, "ok"
    out = ct.run_transcription_test("http://host.docker.internal:8083", "tok", "settings",
                                    get=get, probe=probe)
    assert out["ok"]
    assert all("host.docker.internal" not in u for u in seen)
    assert seen[0] == "http://localhost:8083/balance"


def test_transcription_balance_failure_never_blocks_the_verdict():
    """/balance is a courtesy account lookup, never the oracle — a gateway without it (or one
    that errors) must not stop the round-trip from grading the backend."""
    def boom(url, headers):
        raise OSError("no /balance here")
    out = ct.run_transcription_test("https://t", "tok", "env", get=boom,
                                    probe=lambda e, t: (200, "{}"))
    assert out["ok"]


# ── C2 (#511): a non-Vexa backend is graded by the endpoint BOTS use, never "reachable" ──────────
# No /balance means "not a Vexa gateway", which is not a verdict on the operator's question. The
# round-trip runs the bot's own first-chunk request (same probe body as the boot preflight), so
# the wizard's green means "a bot will transcribe" on ANY OpenAI-compatible endpoint.

_NO_BALANCE = lambda u, h: (404, "")  # noqa: E731 — the non-Vexa signature, reused by every row


def test_transcription_openai_wrong_key_is_red():
    """A1/A2: a rejected key on an OpenAI-compatible endpoint is RED at the click — it used to
    return green-unverified and fail mid-meeting."""
    out = ct.run_transcription_test("https://api.openai.com", "sk-wrong", "settings",
                                    get=_NO_BALANCE, probe=lambda e, t: (401, ""))
    assert not out["ok"], "a rejected key must never test green"
    assert "REJECTED" in out["summary"] and out["status"] == 401
    assert out.get("unverified") is None, "there is no longer an unverified green"


def test_transcription_openai_good_key_is_green_and_names_the_endpoint():
    out = ct.run_transcription_test("https://api.openai.com", "sk-good", "settings",
                                    get=_NO_BALANCE, probe=lambda e, t: (200, '{"text":""}'))
    assert out["ok"]
    assert "/v1/audio/transcriptions" in out["summary"]


def test_transcription_openai_wrong_url_is_red():
    out = ct.run_transcription_test("https://api.openai.com/wrong", "sk-good", "env",
                                    get=_NO_BALANCE, probe=lambda e, t: (404, ""))
    assert not out["ok"] and "URL shape" in out["summary"]


def test_transcription_probe_hits_the_transcriptions_path_once():
    """C4 (A5) at this consumer: base URL and full-path URL must hit the SAME endpoint once with
    the configured token (the /balance lookup's X-API-Key is Vexa-gateway-only)."""
    for configured in ("https://api.openai.com", "https://api.openai.com/v1/audio/transcriptions"):
        seen = []
        def probe(endpoint, token):
            seen.append((endpoint, token))
            return 200, "{}"
        out = ct.run_transcription_test(configured, "sk-good", "env", get=_NO_BALANCE, probe=probe)
        assert out["ok"], f"{configured} must verify green"
        assert seen == [("https://api.openai.com/v1/audio/transcriptions", "sk-good")], (
            f"{configured} → {seen}"
        )
