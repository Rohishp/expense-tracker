"""Advisor agent: loop behaviour with a scripted fake model (no API, no DB),
plus a check that every eval scenario's ground truth holds in the real planner."""

import json

import pytest

from advisor import evals
from advisor.loop import run_advisor
from advisor.tools import apply_changes, dispatch, run_plan

TODAY = evals.TODAY
BASE = evals.BASE

CAMERA = {"op": "add_goal", "name": "Camera", "target_amount": 900,
          "deadline": "2027-01-14", "importance": "Nice-to-have"}


class ScriptedModel:
    """Returns pre-written replies in order, and records what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def __call__(self, messages, tools):
        self.seen.append(list(messages))
        return self.replies.pop(0) if self.replies else {"content": "thinking", "tool_calls": []}


def call(name, args=None, id_="c"):
    return {"content": None, "tool_calls": [{"id": id_, "name": name, "arguments": json.dumps(args or {})}]}


def test_happy_path_reads_simulates_and_answers():
    model = ScriptedModel([
        call("get_finances", id_="1"),
        call("simulate_plan", {"changes": [CAMERA]}, id_="2"),
        call("final_answer", {"verdict": "yes", "summary": "Fits.", "proposed_changes": [CAMERA]}, id_="3"),
    ])
    result = run_advisor("Camera?", BASE, model, TODAY)

    assert result.stopped_reason == "final_answer"
    assert result.verdict == "yes"
    assert result.proposed_changes == [CAMERA]
    assert result.tool_calls == 3
    # The simulate result fed back to the model shows the camera as funded.
    sim = json.loads(model.seen[2][-1]["content"])
    assert sim["diff"]["goals_changed"][0] == {"goal": "Camera", "change": "added",
                                               "status": "funded", "installment": 300.0}


def test_bad_tool_arguments_go_back_to_the_model_as_an_error():
    bad = dict(CAMERA, importance="Urgent")
    model = ScriptedModel([
        call("simulate_plan", {"changes": [bad]}),
        call("final_answer", {"verdict": "no", "summary": "x", "proposed_changes": []}),
    ])
    result = run_advisor("?", BASE, model, TODAY)
    error = json.loads(model.seen[1][-1]["content"])
    assert "importance must be one of" in error["error"]
    assert result.stopped_reason == "final_answer"


def test_invalid_proposal_does_not_end_the_loop():
    past = dict(CAMERA, deadline="2026-01-01")
    model = ScriptedModel([
        call("final_answer", {"verdict": "yes", "summary": "x", "proposed_changes": [past]}),
        call("final_answer", {"verdict": "yes", "summary": "x", "proposed_changes": [CAMERA]}),
    ])
    result = run_advisor("?", BASE, model, TODAY)
    assert result.proposed_changes == [CAMERA]
    assert result.tool_calls == 2


def test_step_limit_stops_a_model_that_never_finishes():
    model = ScriptedModel([call("get_finances")] * 20)
    result = run_advisor("?", BASE, model, TODAY, max_steps=4)
    assert result.stopped_reason == "max_steps"
    assert result.verdict is None
    assert result.tool_calls == 4


def test_simulation_never_changes_the_snapshot():
    before = json.dumps(BASE, sort_keys=True)
    dispatch("simulate_plan", json.dumps({"changes": [CAMERA, {"op": "remove_goal", "goal_id": 1}]}), BASE, TODAY)
    assert json.dumps(BASE, sort_keys=True) == before


def test_diff_reports_a_goal_that_becomes_unreachable():
    change = {"op": "update_goal", "goal_id": 1, "deadline": "2027-01-14"}
    result, _ = dispatch("simulate_plan", json.dumps({"changes": [change]}), BASE, TODAY)
    laptop = next(g for g in result["diff"]["goals_changed"] if g["goal"] == "Laptop")
    assert laptop["status"] == ["funded", "unreachable"]


@pytest.mark.parametrize("scenario", [s for s in evals.SCENARIOS if s["reference_changes"]],
                         ids=lambda s: s["id"])
def test_eval_reference_answers_hold_in_the_planner(scenario):
    plan = run_plan(apply_changes(BASE, scenario["reference_changes"], TODAY), TODAY)
    assert scenario["outcome"](plan)


def test_eval_trade_off_and_no_scenarios_really_do_not_fit_as_asked():
    as_asked = {
        "camera_too_soon": dict(CAMERA, target_amount=1500),
        "trip_impossible": {"op": "add_goal", "name": "Trip", "target_amount": 6000,
                            "deadline": "2026-12-15", "importance": "Must-have"},
    }
    for sid, change in as_asked.items():
        plan = run_plan(apply_changes(BASE, [change], TODAY), TODAY)
        assert plan["items"][-1]["status"] == "unreachable" or any(
            i["status"] == "unreachable" for i in plan["items"]), sid
    # Even with every other Goal dropped, the trip cannot fit.
    alone = apply_changes(BASE, [{"op": "remove_goal", "goal_id": 1}, {"op": "remove_goal", "goal_id": 2},
                                 as_asked["trip_impossible"]], TODAY)
    assert run_plan(alone, TODAY)["items"][0]["status"] == "unreachable"
