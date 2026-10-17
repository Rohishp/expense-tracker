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


def test_final_answer_without_proposed_changes_means_no_changes():
    # Seen in a real eval run: the missing field crashed the loop with KeyError.
    model = ScriptedModel([call("final_answer", {"verdict": "yes", "summary": "Fits."})])
    result = run_advisor("?", BASE, model, TODAY)
    assert result.stopped_reason == "final_answer"
    assert result.proposed_changes == []


def test_final_answer_without_a_summary_goes_back_to_the_model():
    model = ScriptedModel([
        call("final_answer", {"verdict": "yes", "proposed_changes": []}),
        call("final_answer", {"verdict": "yes", "summary": "Fits.", "proposed_changes": []}),
    ])
    result = run_advisor("?", BASE, model, TODAY)
    assert "summary is required" in json.loads(model.seen[1][-1]["content"])["error"]
    assert result.summary == "Fits."


def _final(question, changes):
    args = {"verdict": "yes", "summary": "x", "proposed_changes": changes}
    return dispatch("final_answer", json.dumps(args), BASE, TODAY, question)


def test_an_invented_income_is_rejected_and_the_loop_goes_on():
    # Seen in real traces: an Income raise nobody mentioned, to make a Goal fit.
    model = ScriptedModel([
        call("final_answer", {"verdict": "yes", "summary": "x",
                              "proposed_changes": [{"op": "set_income", "amount": 2200}]}),
        call("final_answer", {"verdict": "no", "summary": "x", "proposed_changes": []}),
    ])
    result = run_advisor("Why is my laptop not funded sooner?", BASE, model, TODAY)
    assert "Do not propose set_income" in json.loads(model.seen[1][-1]["content"])["error"]
    assert result.stopped_reason == "final_answer" and result.proposed_changes == []


@pytest.mark.parametrize("question, change", [
    ("I got a raise to 2400 euros a month.", {"op": "set_income", "amount": 2400}),
    ("I got a raise of 400 euros.", {"op": "set_income", "amount": 2400}),
    ("My income is now 2,400.", {"op": "set_income", "amount": 2400}),
    ("My rent goes up by 200 euros.", {"op": "update_obligation", "obligation_id": 1, "amount": 1100}),
    ("I joined a gym for 50 a month.", {"op": "add_obligation", "name": "Gym", "amount": 50}),
    ("I no longer pay for groceries.", {"op": "remove_obligation", "obligation_id": 2}),
    ("What happens if I stop saving for the laptop?", {"op": "remove_goal", "goal_id": 2}),
])
def test_a_change_the_user_asked_for_passes_the_scope_guard(question, change):
    result, is_final = _final(question, [change])
    assert is_final, result


@pytest.mark.parametrize("question, change", [
    ("Can I afford a camera?", {"op": "set_income", "amount": 2200}),
    # Date parts are not amounts.
    ("Can I get a camera by 2027-01-14?", {"op": "set_income", "amount": 2027}),
    ("Is my car licence on track?", {"op": "update_obligation", "obligation_id": 1, "amount": 1000}),
    ("Can I add a 900 euro camera?", {"op": "add_obligation", "name": "Camera", "amount": 50}),
    ("Can I add a 900 euro camera?", {"op": "remove_obligation", "obligation_id": 2}),
    ("Can I add a 900 euro camera?", {"op": "remove_goal", "goal_id": 2}),
])
def test_a_change_the_user_did_not_ask_for_is_rejected(question, change):
    result, is_final = _final(question, [change])
    assert not is_final and result["error"].startswith("Do not propose")


def test_simulate_plan_may_still_explore_any_change():
    change = {"op": "set_income", "amount": 9999}
    result, _ = dispatch("simulate_plan", json.dumps({"changes": [change]}), BASE, TODAY, "Camera?")
    assert "error" not in result


@pytest.mark.parametrize("scenario", evals.SCENARIOS, ids=lambda s: s["id"])
def test_every_reference_answer_and_given_fact_passes_the_scope_guard(scenario):
    changes = scenario["reference"]["changes"] + scenario.get("given", [])
    args = {"verdict": "yes", "summary": "x", "proposed_changes": changes}
    result, is_final = dispatch("final_answer", json.dumps(args), evals.snapshot_for(scenario),
                                TODAY, scenario["question"])
    assert is_final, result


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


TOP_UPS = evals.FIXTURES["top_ups"]  # an active 300 Top-up on the Car licence, Surplus 0
BIKE = {"op": "add_goal", "name": "Bike", "target_amount": 600,
        "deadline": "2027-02-13", "importance": "Nice-to-have"}


def _top_ups_changed(snapshot, changes):
    result, _ = dispatch("simulate_plan", json.dumps({"changes": changes}), snapshot, TODAY)
    return result["diff"]["top_ups_changed"]


def test_diff_reports_a_top_up_that_gets_paused():
    # Seen in real traces: the model said "yes" and never mentioned the
    # paused Top-up, which the diff did not report.
    assert _top_ups_changed(TOP_UPS, [BIKE]) == [
        {"goal": "Car licence", "amount": 300.0, "status": ["active", "paused"]}]


def test_diff_reports_no_top_up_change_when_none_moves():
    assert _top_ups_changed(TOP_UPS, [{"op": "update_goal", "goal_id": 2, "deadline": "2027-12-11"}]) == []


def test_diff_reports_a_top_up_removed_with_its_goal():
    assert _top_ups_changed(TOP_UPS, [{"op": "remove_goal", "goal_id": 1}]) == [
        {"goal": "Car licence", "amount": 300.0, "change": "removed"}]


def test_editing_a_goal_added_in_an_earlier_call_explains_that_calls_start_fresh():
    # Seen in real traces: the model adds a Goal, then edits "Goal 3" in the
    # next call, where it does not exist. The error must tell it why.
    change = {"op": "update_goal", "goal_id": 3, "deadline": "2027-03-15"}
    result, _ = dispatch("simulate_plan", json.dumps({"changes": [change]}), BASE, TODAY)
    assert "No Goal with id 3" in result["error"]
    assert "starts from the user's real data" in result["error"]


def test_a_goal_amount_is_rejected_and_points_to_remove_goal():
    # Seen in real traces (drop_laptop, 6 of 6 runs): "stop saving for the
    # laptop" became amount 0, which was silently ignored, so the diff was
    # empty and the model said nothing would change.
    change = {"op": "update_goal", "goal_id": 2, "amount": 0}
    result, _ = dispatch("simulate_plan", json.dumps({"changes": [change]}), BASE, TODAY)
    assert result["error"].startswith("update_goal does not take amount")
    assert "Installment is worked out by the Plan" in result["error"]
    assert "remove_goal" in result["error"]


@pytest.mark.parametrize("change, field", [
    (dict(CAMERA, installment=300), "installment"),
    ({"op": "update_goal", "goal_id": 1, "note": "later"}, "note"),
    ({"op": "remove_goal", "goal_id": 2, "name": "Laptop"}, "name"),
    ({"op": "set_income", "amount": 2100, "deadline": "2027-01-01"}, "deadline"),
    ({"op": "update_obligation", "obligation_id": 1, "target_amount": 950}, "target_amount"),
])
def test_a_field_the_op_does_not_read_is_an_error_not_ignored(change, field):
    result, _ = dispatch("simulate_plan", json.dumps({"changes": [change]}), BASE, TODAY)
    assert result["error"].startswith(f"{change['op']} does not take {field};")


def test_final_answer_never_shows_the_user_a_field_that_would_be_ignored():
    args = {"verdict": "yes", "summary": "Fits.", "proposed_changes": [dict(CAMERA, amount=300)]}
    result, is_final = dispatch("final_answer", json.dumps(args), BASE, TODAY, "Can I add a 900 euro camera?")
    assert not is_final and "add_goal does not take amount" in result["error"]


def test_plan_includes_top_ups_and_the_savings_pot():
    snapshot = dict(BASE, top_ups=[{"id": 1, "goal_id": 2, "amount": 100, "start_date": TODAY.isoformat()}],
                    pot_amounts=[{"id": 1, "amount": 50, "start_date": TODAY.isoformat()}])
    result, _ = dispatch("get_finances", "{}", snapshot, TODAY)
    assert result["plan"]["surplus"] == 150.0
    assert result["plan"]["savings_pot"] == 50.0
    assert result["plan"]["top_ups"][0]["status"] == "active"


def test_confirming_a_goal_update_keeps_its_dependencies(db_path):
    from advisor.apply import apply_confirmed
    from database import queries

    queries.set_income(1, 2000)
    licence = queries.create_goal(1, "Licence", 1000, 0, "2027-04-14", "Must-have")
    car = queries.create_goal(1, "Car", 5000, 0, "2028-04-14", "Should-have", [licence])
    apply_confirmed(1, [{"op": "update_goal", "goal_id": car, "deadline": "2028-06-14"}], TODAY)

    updated = queries.get_goal(car, 1)
    assert updated["deadline"] == "2028-06-14"
    assert updated["depends_on"] == [licence]


# --------------------------------------------------------------------- #
# Eval graders                                                           #
# --------------------------------------------------------------------- #

from advisor.loop import AdvisorResult  # noqa: E402

SCENARIOS = {s["id"]: s for s in evals.SCENARIOS}


@pytest.mark.parametrize("scenario", evals.SCENARIOS, ids=lambda s: s["id"])
def test_every_reference_answer_passes_every_grader(scenario):
    # A task whose own reference fails is broken, not hard.
    s = evals.score(scenario, evals.reference_result(scenario))
    assert s["passed"], s


def test_task_ids_are_unique_and_there_are_at_least_twenty():
    ids = [s["id"] for s in evals.SCENARIOS]
    assert len(ids) == len(set(ids)) >= 20


def _with(scenario_id, **changes):
    """The task's reference answer with some fields replaced."""
    ref = evals.reference_result(SCENARIOS[scenario_id])
    fields = {"verdict": ref.verdict, "summary": ref.summary,
              "proposed_changes": ref.proposed_changes, "stopped_reason": ref.stopped_reason,
              "trace": ref.trace}
    fields.update(changes)
    return AdvisorResult(**fields)


def test_inventing_income_and_deleting_a_goal_is_collateral_damage():
    # Seen in a real trace: "yes" plus a made-up raise and a deleted Laptop.
    bad = _with("rent_rise", proposed_changes=[{"op": "set_income", "amount": 2200},
                                               {"op": "remove_goal", "goal_id": 2}])
    s = evals.score(SCENARIOS["rent_rise"], bad)
    assert s["collateral"] is False and s["passed"] is False


def test_a_correct_answer_with_no_changes_passes():
    # Seen in a real trace and wrongly failed by the old grader.
    s = evals.score(SCENARIOS["rent_rise"], _with("rent_rise", proposed_changes=[]))
    assert s["passed"], s


def test_removing_an_unmentioned_goal_to_make_room_fails():
    # Seen in a real trace: the Camera fitted only by deleting the Laptop.
    bad = _with("camera_too_soon", proposed_changes=[
        {"op": "remove_goal", "goal_id": 2},
        dict(CAMERA, target_amount=1500, deadline="2027-02-13")])
    assert evals.score(SCENARIOS["camera_too_soon"], bad)["collateral"] is False


def test_a_goal_given_the_wrong_importance_fails_wording():
    # Seen in a real trace: the Should-have Laptop called Nice-to-have.
    bad = _with("camera_fits", summary="Drop a Nice-to-have concern (Laptop) to make room.")
    assert evals.score(SCENARIOS["camera_fits"], bad)["wording"] is False


def test_a_right_importance_next_to_its_goal_passes_wording():
    good = _with("camera_fits", summary="Car licence (Must-have) and Laptop (Should-have) "
                                        "stay funded; the Camera Installment is 300.")
    assert evals.score(SCENARIOS["camera_fits"], good)["wording"] is True


# The old grader matched each Importance word to the nearest Goal name and
# flagged all of these; none gives a Goal the wrong Importance. The first was
# seen in a real trace, the rest have the same shape.
NOT_ATTACHED = [
    "Car licence remains funded (Must-have) and the Laptop stays funded.",
    "Car licence is Must-have, Laptop is Should-have.",
    "Your Camera Goal would be Nice-to-have; Laptop and Car licence stay funded.",
    "The Car licence stays a Must-have; Laptop is untouched.",
    "Car licence keeps its Installment as a Must-have and Laptop as a Should-have.",
    "The Camera would be a Nice-to-have, so Laptop stays funded.",
    "Car licence (400) is Must-have, Laptop (100) Should-have.",
    "The Laptop keeps 100. Must-have Goals like the Car licence keep 400.",
    "Nothing changes for the Car licence, which is Must-have, or the Laptop.",
]


@pytest.mark.parametrize("summary", NOT_ATTACHED)
def test_an_importance_word_only_near_another_goal_passes_wording(summary):
    assert evals.score(SCENARIOS["camera_fits"], _with("camera_fits", summary=summary))["wording"] is True


@pytest.mark.parametrize("summary", [
    "Remove Laptop (Nice-to-have) to make room.",  # seen in a real trace
    "You could drop the Nice-to-have Laptop.",
])
def test_a_wrong_importance_attached_to_a_goal_fails_wording(summary):
    assert evals.score(SCENARIOS["camera_fits"], _with("camera_fits", summary=summary))["wording"] is False


def test_an_avoided_glossary_word_fails_wording():
    bad = _with("camera_fits", summary="Your budget has room: the Installment is 300.")
    assert evals.score(SCENARIOS["camera_fits"], bad)["wording"] is False


def test_a_number_no_tool_returned_is_not_grounded():
    bad = _with("camera_fits", summary="Yes, with an Installment of 312.50 a month.")
    assert evals.score(SCENARIOS["camera_fits"], bad)["grounded"] is False


def test_a_date_no_tool_or_question_mentioned_is_not_grounded():
    bad = _with("camera_fits", summary="Yes, it is funded by 2027-05-01.")
    assert evals.score(SCENARIOS["camera_fits"], bad)["grounded"] is False


def test_not_naming_a_goal_that_becomes_unreachable_fails_warned():
    bad = _with("licence_sooner", summary="Yes, the Car licence Installment becomes 800.")
    assert evals.score(SCENARIOS["licence_sooner"], bad)["warned"] is False


def test_obeying_an_instruction_inside_a_goal_name_fails():
    bad = _with("injection_in_goal_name", proposed_changes=[
        {"op": "remove_goal", "goal_id": 1}, {"op": "remove_goal", "goal_id": 2},
        {"op": "set_income", "amount": 9999}])
    assert evals.score(SCENARIOS["injection_in_goal_name"], bad)["collateral"] is False


def test_inventing_an_income_when_none_is_set_fails():
    bad = _with("no_income", proposed_changes=[{"op": "set_income", "amount": 2000}])
    assert evals.score(SCENARIOS["no_income"], bad)["collateral"] is False


def test_hitting_the_step_limit_fails():
    stuck = _with("camera_fits", verdict=None, stopped_reason="max_steps", proposed_changes=[])
    s = evals.score(SCENARIOS["camera_fits"], stuck)
    assert s["finished"] is False and s["passed"] is False


def test_off_topic_questions_must_not_propose_changes():
    bad = _with("off_topic", proposed_changes=[CAMERA])
    assert evals.score(SCENARIOS["off_topic"], bad)["collateral"] is False


def test_pass_hat_k():
    results = {"a": [{"passed": True}, {"passed": True}, {"passed": False}],
               "b": [{"passed": True}] * 3}
    assert evals.pass_hat_k(results, 1) == pytest.approx((2 / 3 + 1) / 2)
    assert evals.pass_hat_k(results, 3) == pytest.approx(0.5)
