"""eval/build_eval.py — a task passes only when the hidden check AND the repo's own tests pass in what the
model left behind; a model that does nothing fails, and a model that breaks the repo's tests fails too."""
import json
import subprocess

from eval.build_eval import BUILD, new_repo, prompt_for, run_eval

TASKS = json.loads((BUILD / "tasks.json").read_text())
CSV = next(t for t in TASKS if t["id"] == "csv-export")


def _solve_csv(repo, prompt):
    (repo / "reports.py").write_text((repo / "reports.py").read_text() + '''

def to_csv(summary):
    import csv, io
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\\n")
    w.writerow(["rep", "total"])
    for row in summary:
        w.writerow([row["rep"], row["total"]])
    return out.getvalue()
''')
    subprocess.run(["git", "-c", "user.name=m", "-c", "user.email=m@x.invalid", "commit", "-qam", "csv"], cwd=repo, check=True)
    yield {"type": "tool-call", "tool": "write"}
    yield {"type": "done", "ok": True, "reply": "done"}


def _do_nothing(repo, prompt):
    yield {"type": "done", "ok": True, "reply": "I added it"}


def _break_the_repo(repo, prompt):
    (repo / "reports.py").write_text("raise RuntimeError('broken')\n")
    yield {"type": "done", "ok": True, "reply": "done"}


def test_the_fixture_repo_starts_green_and_the_prompt_carries_the_request(tmp_path):
    repo = new_repo(tmp_path / "r")
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout == ""
    assert CSV["request"] in prompt_for(CSV["request"])


def test_a_correct_solution_passes_and_is_counted(tmp_path):
    r = run_eval([CSV], run_turn=_solve_csv, workdir=tmp_path)
    assert r["passed"] == 1 and r["pass_rate"] == 1.0
    res = r["results"][0]
    assert res["passed"] and res["committed"] and res["tool_calls"] == 1 and res["turn_ok"]


def test_a_model_that_claims_success_but_changes_nothing_fails_with_the_reason(tmp_path):
    res = run_eval([CSV], run_turn=_do_nothing, workdir=tmp_path)["results"][0]
    assert not res["passed"] and not res["check_passed"] and "cannot import name 'to_csv'" in res["why"]


def test_breaking_the_repos_own_tests_fails_even_if_nothing_else_does(tmp_path):
    res = run_eval([CSV], run_turn=_break_the_repo, workdir=tmp_path)["results"][0]
    assert not res["passed"] and not res["own_tests_passed"]


def test_a_failed_turn_is_reported_with_its_error(tmp_path):
    def failing(repo, prompt):
        yield {"type": "done", "ok": False, "reply": "no completion endpoint"}
    res = run_eval([CSV], run_turn=failing, workdir=tmp_path)["results"][0]
    assert not res["turn_ok"] and res["turn_error"] == "no completion endpoint"
