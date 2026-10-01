"""build_eval.py — can a model turn a customer's request into working code in a repo?

The build step of the request→PR pipeline, scored alone: each task in ``build/tasks.json`` is a feature request in
the customer's words. The harness is handed a throwaway copy of ``build/fixture-repo`` (a git repo) with the same
kind of tool grant a real build turn gets, and afterwards a hidden check from ``build/checks`` — which the model
never sees — runs against whatever it left behind. A task passes when that check and the repo's own tests pass.

Run it from ``core/agent`` (needs the ``opencode`` binary and a model server)::

    VEXA_LLM_BASE_URL=http://localhost:11434/v1 VEXA_LLM_MODEL=gemma4:latest VEXA_LLM_CONTEXT_TOKENS=16384 \\
        python -m eval.build_eval [--task csv-export] [--runner opencode]

The result is one JSON object: per-task pass/fail, tool calls, wall time, and why a failure failed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator

from llm.registry import harness_from_env

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
# A real build turn's tools (worker.engine chat tools) minus the web ones — the eval has no network need.
TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "Bash")
PYTEST_TIMEOUT_SEC = 120


def prompt_for(request: str) -> str:
    return (
        f"A customer explicitly asked for this product capability:\n\n{request}\n\n"
        "Implement a minimal, working version in this repository, with tests if the repo has a test suite. "
        "Commit your changes with a clear message. Do NOT push."
    )


def new_repo(dest: Path) -> Path:
    shutil.copytree(BUILD / "fixture-repo", dest)
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.name=eval", "-c", "user.email=eval@example.invalid", "commit", "-qm", "fixture"]):
        subprocess.run(cmd, cwd=dest, check=True, capture_output=True)
    return dest


def _pytest(repo: Path, *targets: str) -> tuple[bool, str]:
    try:
        run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", *targets], cwd=repo,
                             capture_output=True, text=True, timeout=PYTEST_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {PYTEST_TIMEOUT_SEC}s"
    return run.returncode == 0, (run.stdout + run.stderr).strip()[-800:]


def grade(repo: Path, check: str) -> dict:
    """The hidden check plus the repo's own tests, run in the repo the model left behind."""
    # Read before pytest runs, which leaves caches behind that would count as uncommitted work.
    committed = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout.strip() == ""
    hidden = repo / "_hidden_check.py"
    shutil.copyfile(BUILD / check, hidden)
    try:
        check_ok, check_out = _pytest(repo, hidden.name)
    finally:
        hidden.unlink(missing_ok=True)
    own_ok, own_out = _pytest(repo, "tests")
    return {
        "passed": check_ok and own_ok,
        "check_passed": check_ok, "own_tests_passed": own_ok, "committed": committed,
        "why": None if check_ok and own_ok else (check_out if not check_ok else own_out),
    }


def run_task(task: dict, *, run_turn: Callable[[Path, str], Iterable[dict]], workdir: Path,
             clock: Callable[[], float] = time.monotonic) -> dict:
    repo = new_repo(workdir / task["id"])
    started = clock()
    tool_calls = 0
    done: dict = {}
    for ev in run_turn(repo, prompt_for(task["request"])):
        if ev.get("type") == "tool-call":
            tool_calls += 1
        elif ev.get("type") == "done":
            done = ev
    return {"id": task["id"], "seconds": round(clock() - started, 1), "tool_calls": tool_calls,
            "turn_ok": bool(done.get("ok")), "turn_error": None if done.get("ok") else str(done.get("reply", "no result"))[:300],
            **grade(repo, task["check"])}


def run_eval(tasks: list[dict], *, run_turn: Callable[[Path, str], Iterable[dict]], workdir: Path) -> dict:
    results = [run_task(t, run_turn=run_turn, workdir=workdir) for t in tasks]
    passed = sum(1 for r in results if r["passed"])
    return {"tasks": len(results), "passed": passed, "pass_rate": round(passed / len(results), 3) if results else None,
            "seconds_total": round(sum(r["seconds"] for r in results), 1), "results": results}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Score a model on small build tasks in a fixture repo.")
    parser.add_argument("--task", action="append", help="task id to run (repeatable); default all")
    parser.add_argument("--tasks", type=Path, default=BUILD / "tasks.json")
    args = parser.parse_args(argv)

    tasks = json.loads(args.tasks.read_text(encoding="utf-8"))
    if args.task:
        tasks = [t for t in tasks if t["id"] in args.task]
    harness = harness_from_env()

    def run_turn(repo: Path, prompt: str) -> Iterator[dict]:
        return harness.run_turn(repo, prompt, allowed_tools=list(TOOLS))

    with tempfile.TemporaryDirectory(prefix="build-eval-") as tmp:
        print(json.dumps(run_eval(tasks, run_turn=run_turn, workdir=Path(tmp)), indent=2))


if __name__ == "__main__":
    main()
