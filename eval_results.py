"""Keeps every real-model eval run, so runs can be compared later.

Each run of advisor/evals.py or suggestions_evals.py writes its traces to
evals/results/<eval>/<started>_<model>_<commit>.jsonl and adds one row to
evals/results/runs.csv (pass_k is pass^k with k = runs). Both are committed:
the traces hold only fixture data, and keeping them means an old run can be
re-graded when a grader changes.
"""

import csv
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(ROOT, "evals", "results")
FIELDS = ["started", "eval", "model", "commit", "runs", "tasks", "pass_1", "pass_k",
          "graders", "traces", "note"]


def git_commit(cwd=ROOT):
    """Short HEAD hash, plus "-dirty" if tracked code has uncommitted changes.

    Saved results are not counted, or every run after the first would look
    dirty. Returns "" outside a git checkout.
    """
    def git(*args):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              check=True).stdout.strip()

    try:
        sha = git("rev-parse", "--short", "HEAD")
        changed = git("status", "--porcelain", "--untracked-files=no", "--", ".",
                      ":(exclude)evals/results")
    except (OSError, subprocess.CalledProcessError):
        return ""
    return sha + ("-dirty" if changed else "")


def trace_path(eval_name, started, model, commit, results_dir=RESULTS_DIR):
    """e.g. evals/results/advisor/2026-10-17_004512_gpt-5-nano_fcdd8f4.jsonl"""
    name = f"{started:%Y-%m-%d_%H%M%S}_{model}_{commit or 'no-git'}"
    return os.path.join(results_dir, eval_name, re.sub(r"[^\w.-]+", "-", name) + ".jsonl")


def open_traces(path):
    """Open a run's trace file for writing, creating its folder."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return open(path, "w", encoding="utf-8")


def record_run(eval_name, started, model, commit, runs, tasks, pass_1, pass_k, graders,
               traces="", note="", results_dir=RESULTS_DIR):
    """Append one run's summary to runs.csv, writing the header the first time."""
    if traces:
        try:
            traces = os.path.relpath(traces, results_dir)
        except ValueError:  # on another drive (Windows)
            traces = os.path.abspath(traces)
        traces = traces.replace(os.sep, "/")
    os.makedirs(results_dir, exist_ok=True)
    path = os.path.join(results_dir, "runs.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            writer.writeheader()
        writer.writerow({
            "started": started.isoformat(timespec="seconds"), "eval": eval_name,
            "model": model, "commit": commit, "runs": runs, "tasks": tasks,
            "pass_1": f"{pass_1:.2f}", "pass_k": f"{pass_k:.2f}",
            "graders": "; ".join(f"{k} {v}" for k, v in graders.items()),
            "traces": traces, "note": note,
        })
