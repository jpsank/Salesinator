"""copilot_eval.py — how well a model runs the live meeting copilot, measured on a labelled call.

Replays a transcript through the same beat loop ``serve_meeting`` runs (a beat every ``cadence_segments``
lines over a rolling window of lines still under three passes) with the REAL ``meeting_card_turn`` and
whichever completion endpoint the environment names, then scores what came back:

* **processing** — how many lines the model returned a processed note for, how many beats errored or came
  back short, how many lines only ever kept their raw baseline text;
* **tagging** — which expected ``feature_request`` cards were found, and which must-not-match ones (a vague
  wish, a complaint, a question about something that already exists) were wrongly raised;
* **latency** — per-beat wall time.

Run it from ``core/agent`` against a local model::

    VEXA_LLM_PROVIDER=openai-compat VEXA_LLM_BASE_URL=http://localhost:11434/v1 VEXA_LLM_MODEL=gemma4:latest \\
        python -m eval.copilot_eval

``--fixture`` / ``--expect`` select another call; the policy comes from the seed ``agents/meeting.md`` unless
``--workspace`` names a different workspace directory.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator

from shared.agent_config import load_meeting_config
from worker.meeting import meeting_card_turn

HERE = Path(__file__).resolve().parent
DEFAULT_FIXTURE = HERE / "replay" / "sales-call-feature-requests.jsonl"
DEFAULT_EXPECT = HERE / "replay" / "sales-call-feature-requests.expect.json"
DEFAULT_WORKSPACE = HERE.parent / "workspace-seeds" / "default"
FALLBACK_CHAPTER = "Live Transcript"      # the chapter worker.meeting stamps on a baseline/fallback note
MAX_PASSES = 3


def load_segments(path: Path) -> list[dict]:
    segments = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if line.strip():
            d = json.loads(line)
            segments.append({"segment_id": f"seg-{i}", "speaker": d["speaker"], "text": d["text"], "start": d.get("start", i * 5.0)})
    return segments


def beats(segments: list[dict], cadence: int) -> Iterator[list[dict]]:
    """The windows ``serve_meeting`` would hand each beat: every ``cadence`` new lines (or at a new speaker is
    ignored here — cadence only, so runs are comparable), over the lines still under three passes."""
    window: list[dict] = []
    fresh = 0

    def stage() -> list[dict]:
        return [{**s, "rewrite_pass": s["_passes"] + 1} for s in window if s["_passes"] < MAX_PASSES]

    def spent(sent: list[dict]) -> None:
        sent_ids = {s["segment_id"] for s in sent}
        for s in window:
            if s["segment_id"] in sent_ids:
                s["_passes"] += 1
        window[:] = [s for s in window if s["_passes"] < MAX_PASSES]

    for seg in segments:
        window.append({**seg, "_passes": 0})
        fresh += 1
        if fresh >= cadence:
            fresh = 0
            sent = stage()
            yield sent
            spent(sent)
    final = stage()
    if final:
        yield final


def _hit(card: dict, any_of: Iterable[str]) -> bool:
    text = f"{card.get('title', '')} {card.get('body', '')}".lower()
    return any(k.lower() in text for k in any_of)


def run_eval(
    segments: list[dict], expect: dict, *, card_turn: Callable[[list[dict]], Iterator[dict]], cadence: int = 4,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    kind = expect.get("kind", "feature_request")
    beat_rows: list[dict] = []
    processed_by_model: set[str] = set()
    cards: list[dict] = []
    seen_titles: set[str] = set()
    for window in beats(segments, cadence):
        started = clock()
        events = list(card_turn(window))
        elapsed = clock() - started
        notes = [e["note"] for e in events if e["type"] == "note"]
        model_notes = [n for n in notes if n.get("chapter") != FALLBACK_CHAPTER]
        errors = [e["error"]["message"] for e in events if e["type"] in ("model-error", "auth-error")]
        processed_by_model.update(n["id"] for n in model_notes)
        for e in events:
            if e["type"] == "card" and e["card"].get("kind") == kind:
                title = str(e["card"].get("title", "")).strip().casefold()
                if title not in seen_titles:
                    seen_titles.add(title)
                    cards.append(e["card"])
        beat_rows.append({
            "lines": len(window), "model_notes": len(model_notes), "errors": errors, "seconds": round(elapsed, 2),
            "short": len(model_notes) < len(window),
        })

    found = [{"id": x["id"], "matched_by": next((c["title"] for c in cards if _hit(c, x["any"])), None)}
             for x in expect.get("expected", [])]
    false_positives = [
        {"id": x["id"], "card": c["title"]}
        for x in expect.get("must_not_match", []) for c in cards if _hit(c, x["any"])
    ]
    judged = [x["any"] for x in expect.get("expected", []) + expect.get("must_not_match", [])]
    seconds = [b["seconds"] for b in beat_rows]
    n_expected = len(found)
    n_found = sum(1 for f in found if f["matched_by"])
    return {
        "lines": len(segments),
        "beats": len(beat_rows),
        "beats_with_error": sum(1 for b in beat_rows if b["errors"]),
        "beats_short": sum(1 for b in beat_rows if b["short"]),
        "lines_processed_by_model": len(processed_by_model),
        "lines_left_raw": len(segments) - len(processed_by_model),
        "expected_found": f"{n_found}/{n_expected}",
        "recall": round(n_found / n_expected, 3) if n_expected else None,
        "false_positives": false_positives,
        "other_cards": [c["title"] for c in cards if not any(_hit(c, any_of) for any_of in judged)],
        "errors": sorted({e for b in beat_rows for e in b["errors"]}),
        "seconds_total": round(sum(seconds), 1),
        "seconds_per_beat_median": round(statistics.median(seconds), 1) if seconds else None,
        "found": found,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Score a model on the live meeting copilot.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--expect", type=Path, default=DEFAULT_EXPECT)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE, help="directory holding agents/meeting.md")
    parser.add_argument("--cadence", type=int, default=None, help="lines per beat (default: the workspace's cadence_segments)")
    args = parser.parse_args(argv)

    cfg = load_meeting_config(args.workspace)
    segments = load_segments(args.fixture)
    expect = json.loads(args.expect.read_text(encoding="utf-8"))
    result = run_eval(
        segments, expect, cadence=args.cadence or cfg.cadence_segments,
        card_turn=lambda window: meeting_card_turn(
            args.workspace, window, model=cfg.model, card_kinds=cfg.card_kinds, steering=cfg.steering,
            polish_rules=cfg.polish_rules, tag_rules=cfg.tag_rules,
        ),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
