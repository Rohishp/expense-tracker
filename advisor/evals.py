"""Eval set for the advisor: what-if questions with answers the planner can check.

Run against a real model:
    python -m advisor.evals --model <model-name> --runs 3

Each scenario is scored on:
  verdict   - did the agent reach the right yes / trade_off / no
  outcome   - do its proposed_changes, run through the planner, actually work
  mentions  - does the summary name a Goal the user must hear about
plus tool calls and step-limit hits. Extend SCENARIOS towards ~15.
"""

import argparse
import statistics
from datetime import date

from dotenv import load_dotenv

from advisor.loop import run_advisor
from advisor.tools import apply_changes, run_plan

TODAY = date(2026, 10, 16)

BASE = {
    "income": 2000.0,
    "obligations": [
        {"id": 1, "name": "Rent", "amount": 900.0},
        {"id": 2, "name": "Groceries", "amount": 300.0},
    ],
    "goals": [
        # 180 days -> 6 periods -> 400/month
        {"id": 1, "name": "Car licence", "target_amount": 2400.0, "saved_amount": 0.0,
         "deadline": "2027-04-14", "importance": "Must-have"},
        # 360 days -> 12 periods -> 100/month
        {"id": 2, "name": "Laptop", "target_amount": 1200.0, "saved_amount": 0.0,
         "deadline": "2027-10-11", "importance": "Should-have"},
    ],
}  # available 800, surplus 300


def _status(plan, name):
    return next((i["status"] for i in plan["items"] if i["name"] == name), None)


def _funded(plan, *names):
    return all(_status(plan, n) in ("funded", "fully_funded") for n in names)


SCENARIOS = [
    {
        "id": "camera_fits",
        "question": "Can I afford a camera for 900 euros by 2027-01-14?",
        "expected_verdict": "yes",
        "outcome": lambda p: _funded(p, "Car licence", "Laptop") and any("amera" in i["name"] for i in p["items"] if i["status"] == "funded"),
        "reference_changes": [{"op": "add_goal", "name": "Camera", "target_amount": 900,
                               "deadline": "2027-01-14", "importance": "Nice-to-have"}],
    },
    {
        "id": "camera_too_soon",
        "question": "Can I get a 1500 euro camera by 2027-01-14 without touching the car licence?",
        "expected_verdict": "trade_off",
        "outcome": lambda p: _funded(p, "Car licence") and any("amera" in i["name"] for i in p["items"] if i["status"] == "funded"),
        "reference_changes": [{"op": "add_goal", "name": "Camera", "target_amount": 1500,
                               "deadline": "2027-03-20", "importance": "Nice-to-have"}],
    },
    {
        "id": "rent_rise",
        "question": "My rent goes up by 200 euros. Is my car licence still on track?",
        "expected_verdict": "yes",
        "outcome": lambda p: _funded(p, "Car licence"),
        "reference_changes": [{"op": "update_obligation", "obligation_id": 1, "amount": 1100}],
    },
    {
        "id": "trip_impossible",
        "question": "I want to save 6000 euros for a trip by 2026-12-15 and keep everything else.",
        "expected_verdict": "no",
        "outcome": None,
        "reference_changes": None,
    },
    {
        "id": "licence_sooner",
        "question": "Can I move my car licence Deadline to 2027-01-14?",
        "expected_verdict": "trade_off",
        "must_mention": "Laptop",  # it becomes an Unreachable Goal
        "outcome": lambda p: _funded(p, "Car licence"),
        "reference_changes": [{"op": "update_goal", "goal_id": 1, "deadline": "2027-01-14"}],
    },
]


def score(scenario, result):
    s = {"verdict": result.verdict == scenario["expected_verdict"],
         "tool_calls": result.tool_calls,
         "hit_step_limit": result.stopped_reason == "max_steps"}
    if scenario.get("outcome") and result.proposed_changes:
        plan = run_plan(apply_changes(BASE, result.proposed_changes, TODAY), TODAY)
        s["outcome"] = scenario["outcome"](plan)
    elif scenario.get("outcome"):
        s["outcome"] = False
    if scenario.get("must_mention"):
        s["mentions"] = scenario["must_mention"].lower() in result.summary.lower()
    return s


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--runs", type=int, default=1, help="repeat to measure stability")
    args = parser.parse_args()

    load_dotenv()
    from advisor.openai_client import make_openai_caller

    call_model = make_openai_caller(args.model)
    rows = []
    for sc in SCENARIOS:
        for run in range(args.runs):
            result = run_advisor(sc["question"], BASE, call_model, TODAY)
            row = score(sc, result)
            rows.append(row)
            print(f"{sc['id']:<18} run {run}  {row}")

    def rate(key):
        vals = [r[key] for r in rows if key in r]
        return f"{sum(vals)}/{len(vals)}" if vals else "n/a"

    print("\nverdict", rate("verdict"), "| outcome", rate("outcome"),
          "| mentions", rate("mentions"), "| step-limit hits",
          sum(r["hit_step_limit"] for r in rows),
          "| median tool calls", statistics.median(r["tool_calls"] for r in rows))


if __name__ == "__main__":
    main()
