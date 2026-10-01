"""eval/copilot_eval.py — the beat windows match serve_meeting's, and the scoring says what a run got right."""
from eval.copilot_eval import DEFAULT_EXPECT, DEFAULT_FIXTURE, beats, load_segments, run_eval

EXPECT = {
    "kind": "feature_request",
    "expected": [{"id": "csv", "any": ["csv"]}, {"id": "sso", "any": ["sso", "okta"]}],
    "must_not_match": [{"id": "speed", "any": ["faster"]}],
}


def _segs(n):
    return [{"segment_id": f"seg-{i}", "speaker": "A", "text": f"line {i}", "start": float(i)} for i in range(n)]


def test_beats_run_every_cadence_lines_over_a_rolling_window_that_drops_a_line_after_three_passes():
    windows = [[s["segment_id"] for s in w] for w in beats(_segs(10), cadence=2)]
    assert windows[0] == ["seg-0", "seg-1"]
    assert windows[1] == ["seg-0", "seg-1", "seg-2", "seg-3"]
    assert windows[2] == ["seg-0", "seg-1", "seg-2", "seg-3", "seg-4", "seg-5"]
    assert "seg-0" not in windows[3]                       # three passes spent
    assert windows[-1] and windows[-1][-1] == "seg-9"      # a closing beat covers what is left


def test_each_line_carries_its_pass_number():
    first, second = list(beats(_segs(4), cadence=2))[:2]
    assert {s["rewrite_pass"] for s in first} == {1}
    assert {s["segment_id"]: s["rewrite_pass"] for s in second} == {"seg-0": 2, "seg-1": 2, "seg-2": 1, "seg-3": 1}


def _turn(cards, *, note_every=1, error=None):
    def card_turn(window):
        if error:
            yield {"type": "model-error", "error": {"message": error}}
        for s in window[::note_every]:
            yield {"type": "note", "note": {"id": s["segment_id"], "chapter": "" if not error else "Live Transcript"}}
        for c in cards:
            yield {"type": "card", "card": c}
    return card_turn


def test_scores_found_missed_and_wrongly_raised_cards():
    cards = [{"kind": "feature_request", "title": "CSV export", "body": "for finance"},
             {"kind": "feature_request", "title": "Faster app", "body": "it should be faster"},
             {"kind": "feature_request", "title": "Dark mode", "body": "nice to have"},
             {"kind": "person", "title": "Okta", "body": "not a feature_request kind"}]
    r = run_eval(_segs(8), EXPECT, card_turn=_turn(cards), cadence=4)
    assert r["expected_found"] == "1/2" and r["recall"] == 0.5
    assert r["false_positives"] == [{"id": "speed", "card": "Faster app"}]
    assert r["other_cards"] == ["Dark mode"]               # raised but neither expected nor forbidden
    assert next(f for f in r["found"] if f["id"] == "sso")["matched_by"] is None


def test_counts_short_and_errored_beats_and_lines_that_stayed_raw():
    short = run_eval(_segs(8), EXPECT, card_turn=_turn([], note_every=2), cadence=4)
    assert short["beats_short"] == short["beats"] and short["lines_left_raw"] > 0

    errored = run_eval(_segs(8), EXPECT, card_turn=_turn([], error="empty reply"), cadence=4)
    assert errored["beats_with_error"] == errored["beats"] and errored["errors"] == ["empty reply"]
    assert errored["lines_processed_by_model"] == 0 and errored["lines_left_raw"] == 8


def test_a_repeated_card_title_counts_once_and_latency_is_reported():
    ticks = iter(range(100))
    cards = [{"kind": "feature_request", "title": "CSV export", "body": "x"}]
    r = run_eval(_segs(8), EXPECT, card_turn=_turn(cards), cadence=4, clock=lambda: next(ticks) * 2.0)
    assert r["found"][0]["matched_by"] == "CSV export" and r["other_cards"] == []
    assert r["seconds_per_beat_median"] == 2.0


def test_the_bundled_fixture_and_labels_load():
    segs = load_segments(DEFAULT_FIXTURE)
    assert len(segs) >= 20 and all({"segment_id", "speaker", "text"} <= s.keys() for s in segs)
    import json
    expect = json.loads(DEFAULT_EXPECT.read_text())
    assert expect["expected"] and expect["must_not_match"]
