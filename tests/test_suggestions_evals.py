"""The suggestions eval set is well formed and its scoring is right.

No network: suggestions._call_llm is replaced, as in test_13.
"""

import json

import pytest

import suggestions
import suggestions_evals as ev
from database.db import IMPORTANCE_LEVELS

CASES = {c["id"]: c for c in ev.CASES}


def test_case_ids_are_unique_and_there_are_at_least_thirty():
    ids = [c["id"] for c in ev.CASES]
    assert len(ids) == len(set(ids)) >= 30


@pytest.mark.parametrize("case", ev.CASES, ids=lambda c: c["id"])
def test_every_case_is_answerable(case):
    other_ids = {g["id"] for g in case["others"]}
    assert len({g["id"] for g in case["others"]}) == len(case["others"])
    assert case["goal"]["id"] not in other_ids
    assert case["depends_on"], "at least one acceptable Dependency set"
    for accepted in case["depends_on"]:
        assert accepted <= other_ids, "a Dependency must be one of the other Goals"
    assert case["importance"] and case["importance"] <= set(IMPORTANCE_LEVELS)


def test_the_set_tests_both_linking_and_not_linking():
    # Balanced: a model that never links, or always links, cannot score well.
    linked = [c for c in ev.CASES if any(c["depends_on"])]
    unlinked = [c for c in ev.CASES if c["depends_on"] == [set()] and c["others"]]
    assert len(linked) >= 8 and len(unlinked) >= 8


def _answer(case):
    """A reply that every grader should accept."""
    return {"importance": sorted(case["importance"])[0],
            "depends_on": sorted(case["depends_on"][0])}


@pytest.mark.parametrize("case", ev.CASES, ids=lambda c: c["id"])
def test_a_right_answer_through_the_real_parser_passes(case, monkeypatch):
    monkeypatch.setattr(suggestions, "_call_llm", lambda prompt: json.dumps(_answer(case)))
    goal = case["goal"]
    got = suggestions.suggest(goal["name"], goal["target_amount"], goal["deadline"], case["others"])
    assert ev.score(case, got)["passed"]


def test_a_false_dependency_is_counted_and_fails():
    case = CASES["licence_not_on_car"]
    s = ev.score(case, {"importance": "Must-have", "depends_on": [2]})
    assert s["passed"] is False and s["false_dependencies"] == 1


def test_a_missed_dependency_is_counted_and_fails():
    case = CASES["car_needs_licence"]
    s = ev.score(case, {"importance": "Must-have", "depends_on": []})
    assert s["passed"] is False and s["missed_dependencies"] == 1


def test_any_acceptable_dependency_set_passes():
    case = CASES["road_trip_needs_car"]
    for deps in ([2], [1, 2]):
        assert ev.score(case, {"importance": "Nice-to-have", "depends_on": deps})["passed"]


def test_an_unacceptable_importance_fails():
    case = CASES["emergency_fund_alone"]
    assert ev.score(case, {"importance": "Nice-to-have", "depends_on": []})["passed"] is False


def test_unavailable_counts_as_a_failure():
    s = ev.score(CASES["car_needs_licence"], None)
    assert s["available"] is False and s["passed"] is False


def test_summary_metrics():
    results = {
        "a": [ev.score(CASES["car_needs_licence"], {"importance": "Must-have", "depends_on": [1]})],
        "b": [ev.score(CASES["licence_not_on_car"], {"importance": "Must-have", "depends_on": [2]})],
        "c": [ev.score(CASES["car_needs_licence"], None)],
    }
    m = ev.summarise(results)
    assert m["available"] == pytest.approx(2 / 3)
    assert m["dependency_precision"] == pytest.approx(0.5)
    assert m["dependency_recall"] == pytest.approx(1.0)
    assert m["runs_with_a_false_dependency"] == 1
    assert ev.pass_hat_k(results, 1) == pytest.approx(1 / 3)
