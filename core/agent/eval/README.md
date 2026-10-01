# agent-api eval

Offline evaluation assets for agent-api behavior. Replay fixtures and conversion tools live under `replay/`.

## Scoring a model

Both scripts run from `core/agent` against whatever endpoint the environment names, and print one JSON object.

**`copilot_eval`** replays a labelled sales call through the same beat loop the live copilot runs, with the real
`meeting_card_turn` and the seed `agents/meeting.md` policy, and scores what comes back:

```bash
VEXA_LLM_PROVIDER=openai-compat VEXA_LLM_BASE_URL=http://localhost:11434/v1 VEXA_LLM_MODEL=gemma4:latest \
  python -m eval.copilot_eval            # [--fixture F --expect E --workspace W --cadence N]
```

It reports lines the model processed vs. left at their raw baseline text, beats that errored or came back short,
which expected `feature_request` cards were found, which must-not-match ones (a vague wish, a complaint, a question
about something that exists) were wrongly raised, and seconds per beat. The call and its labels are
`replay/sales-call-feature-requests.{jsonl,expect.json}`.

**`build_eval`** hands each task in `build/tasks.json` — a feature request in the customer's words — to the harness in a
throwaway copy of `build/fixture-repo`, with a real build turn's tools, then runs a hidden check from `build/checks`
(never shown to the model) plus the repo's own tests:

```bash
VEXA_RUNNER=opencode VEXA_LLM_BASE_URL=http://localhost:11434/v1 VEXA_LLM_MODEL=gemma4:latest \
  VEXA_LLM_CONTEXT_TOKENS=16384 python -m eval.build_eval [--task csv-export]
```

Model output varies run to run, so repeat a run before reading anything into a difference between two
configurations. Neither script writes to a real workspace or repo.

### Baseline: `gemma4:latest` on the dev machine (2026-10-01)

Native Ollama on Apple Silicon, 16384-token context, one machine shared with the rest of the stack. Treat these as a starting
point to beat, not a verdict: a handful of runs each.

| | result |
|---|---|
| `copilot_eval`, 4 runs | found 3/3 expected feature requests in 3 runs and 2/3 in the first (it missed the SSO request); 0 wrongly raised cards in every run; the first run had 2 of its 7 beats come back with 1 of 12 lines processed; ~43–55 s per beat |
| `build_eval`, 1 run | **1 of 3 tasks passed**, 20 min in total: `csv-export` passed after 851 s and 38 tool calls (and left its work uncommitted); `cli-format-flag` failed its check; `reject-negative-amounts` broke the fixture repo's own tests |

So today a local model of this size can keep up with the live copilot's notes and cards, taking close to a minute per beat,
but cannot be trusted to build a feature unattended. Re-run both after changing the model, the context window or the prompts.

