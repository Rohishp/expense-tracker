"""Eval runs are saved where they can be compared later, and the saved ones add up.

No network: these never run an eval, only the code that saves one.
"""

import csv
import json
import os
import subprocess
from datetime import datetime

import pytest

import eval_results
from advisor import evals as advisor_evals
import suggestions_evals

STARTED = datetime(2026, 10, 17, 0, 45, 12)


def test_trace_path_names_the_run_by_time_model_and_commit(tmp_path):
    path = eval_results.trace_path("advisor", STARTED, "gpt-5-nano", "fcdd8f4", tmp_path)
    assert path == os.path.join(tmp_path, "advisor", "2026-10-17_004512_gpt-5-nano_fcdd8f4.jsonl")


def test_trace_path_keeps_odd_model_names_out_of_the_folder_structure(tmp_path):
    path = eval_results.trace_path("advisor", STARTED, "openai/gpt-5:nano", "", tmp_path)
    assert os.path.dirname(path) == os.path.join(tmp_path, "advisor")
    assert os.path.basename(path) == "2026-10-17_004512_openai-gpt-5-nano_no-git.jsonl"


def test_record_run_writes_the_header_once_and_one_row_per_run(tmp_path):
    traces = eval_results.trace_path("advisor", STARTED, "gpt-5-nano", "abc1234", tmp_path)
    for note in ("first", "second"):
        eval_results.record_run("advisor", STARTED, "gpt-5-nano", "abc1234", 3, 20, 0.6833, 0.4,
                                {"finished": "59/60", "verdict": "51/60"}, traces, note,
                                results_dir=tmp_path)

    with open(tmp_path / "runs.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["note"] for r in rows] == ["first", "second"]
    assert rows[0] == {
        "started": "2026-10-17T00:45:12", "eval": "advisor", "model": "gpt-5-nano",
        "commit": "abc1234", "runs": "3", "tasks": "20", "pass_1": "0.68", "pass_k": "0.40",
        "graders": "finished 59/60; verdict 51/60",
        "traces": "advisor/2026-10-17_004512_gpt-5-nano_abc1234.jsonl", "note": "first",
    }


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                   check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "evals" / "results").mkdir(parents=True)
    (tmp_path / "evals" / "results" / "runs.csv").write_text("started\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_git_commit_is_the_short_hash_on_a_clean_checkout(repo):
    assert eval_results.git_commit(repo) == subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


def test_git_commit_is_dirty_when_tracked_code_changed(repo):
    (repo / "app.py").write_text("x = 2\n")
    assert eval_results.git_commit(repo).endswith("-dirty")


def test_saved_results_and_untracked_files_do_not_make_a_run_dirty(repo):
    (repo / "evals" / "results" / "runs.csv").write_text("started\nrow\n")
    (repo / "scratch.txt").write_text("not tracked")
    assert not eval_results.git_commit(repo).endswith("-dirty")


def test_git_commit_is_empty_outside_git(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))  # don't find a repo above
    assert eval_results.git_commit(tmp_path) == ""
    assert eval_results.git_commit(tmp_path / "missing") == ""


def _saved_runs():
    path = os.path.join(eval_results.RESULTS_DIR, "runs.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["traces"] and not r["traces"].startswith("..")]


@pytest.mark.parametrize("row", _saved_runs(), ids=lambda r: r["traces"])
def test_every_saved_row_matches_its_traces(row):
    with open(os.path.join(eval_results.RESULTS_DIR, row["traces"]), encoding="utf-8") as f:
        records = [json.loads(line) for line in f]
    key, module = {"advisor": ("task", advisor_evals),
                   "suggestions": ("case", suggestions_evals)}[row["eval"]]
    by_task = {}
    for r in records:
        by_task.setdefault(r[key], []).append(r["score"])

    assert len(records) == int(row["runs"]) * int(row["tasks"]) and len(by_task) == int(row["tasks"])
    assert f"{module.pass_hat_k(by_task, 1):.2f}" == row["pass_1"]
    assert f"{module.pass_hat_k(by_task, int(row['runs'])):.2f}" == row["pass_k"]
