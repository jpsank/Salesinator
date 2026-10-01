import json
import subprocess
import sys


def _run(tmp_path, *args):
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([{"rep": "a", "amount": 2}, {"rep": "b", "amount": 6}]))
    return subprocess.run([sys.executable, "cli.py", str(rows), *args], capture_output=True, text=True)


def test_default_output_is_unchanged(tmp_path):
    assert _run(tmp_path).stdout.splitlines() == ["a: 2", "b: 6"]


def test_format_json_prints_the_summary_as_json(tmp_path):
    out = _run(tmp_path, "--format", "json")
    assert json.loads(out.stdout) == [{"rep": "a", "total": 2}, {"rep": "b", "total": 6}]
