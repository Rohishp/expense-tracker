"""Tests for Step 14: Redirect Surplus.

Derived from .claude/specs/14-redirect-surplus.md, GLOSSARY.md and ADR 0002.
Expected values are computed by hand from the spec's algorithm section.
Unit tests call planner.plan() with a fixed `today`; route tests use the temp
DB from conftest.py and Deadlines relative to date.today().

The step 12 example used throughout: Income 2000, Obligations 1200 (Available
800), Car licence 2400 and Laptop 1200. Car licence: Installment 400, last
Installment in period 5, so it is freed from period 6 (today + 180 days).
Laptop: Installment 100, freed from period 12 (today + 360 days). Surplus 300.
"""

import ast
import html
import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from werkzeug.security import generate_password_hash

import database.db as db
import database.queries as queries
import planner

DEMO_EMAIL = "demo@sawings.com"
DEMO_PASSWORD = "demo123"
OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "otherpass1"

TODAY = date(2026, 1, 1)
PLANNER_PATH = Path(planner.__file__)

CAR = "Car licence"
LAPTOP = "Laptop"


# --------------------------------------------------------------------- #
# Unit helpers                                                           #
# --------------------------------------------------------------------- #

def _dl(days, today=TODAY):
    return (today + timedelta(days=days)).isoformat()


def _g(gid, target, saved=0, days=30, importance="Should-have", name=None):
    return {
        "id": gid,
        "name": name or f"G{gid}",
        "target_amount": target,
        "saved_amount": saved,
        "deadline": _dl(days),
        "importance": importance,
    }


def _tu(tid, goal_id, amount, start_days=0):
    return {
        "id": tid,
        "goal_id": goal_id,
        "amount": amount,
        "start_date": _dl(start_days),
    }


def _pot(pid, amount, start_days=0):
    return {"id": pid, "amount": amount, "start_date": _dl(start_days)}


def _run(income, goals, obligations=(), top_ups=(), pots=(), today=TODAY):
    return planner.plan(
        income,
        [{"amount": a} for a in obligations],
        goals,
        today,
        top_ups=top_ups,
        pot_amounts=pots,
    )


def _example_goals():
    return [
        _g(1, 2400, 0, 180, name=CAR),
        _g(2, 1200, 0, 360, name=LAPTOP),
    ]


def _example(top_ups=(), pots=()):
    return _run(2000, _example_goals(), [1200], top_ups, pots)


def _item(result, gid):
    return next(i for i in result["items"] if i["id"] == gid)


def _freed(result, gid):
    return next(f for f in result["freed"] if f["goal_id"] == gid)


def _top_up(result, tid):
    return next(t for t in result["top_ups"] if t["id"] == tid)


# --------------------------------------------------------------------- #
# Unit: no Top-ups and no Savings Pot behave as in step 12               #
# --------------------------------------------------------------------- #

def test_plan_without_top_ups_or_pot_keeps_step_12_values():
    r = _example()
    assert r["available"] == 800
    assert r["overcommitted"] is False
    assert _item(r, 1)["installment"] == 400
    assert _item(r, 1)["status"] == "funded"
    assert _item(r, 1)["months_left"] == 6
    assert _item(r, 2)["installment"] == 100
    assert _item(r, 2)["status"] == "funded"
    assert _item(r, 2)["months_left"] == 12
    assert r["surplus"] == 300


def test_plan_with_empty_top_ups_and_pot_equals_default_call():
    explicit = _example(top_ups=[], pots=[])
    default = planner.plan(2000, [{"amount": 1200}], _example_goals(), TODAY)
    assert explicit == default


def test_plan_result_has_top_ups_savings_pot_freed_and_surplus_limit_keys():
    r = _example()
    assert {"top_ups", "savings_pot", "freed", "surplus_limit"} <= set(r)
    assert {"income", "obligations_total", "available", "items", "surplus",
            "overcommitted"} <= set(r)


def test_plan_without_top_ups_or_pot_has_empty_top_ups_and_pot():
    r = _example()
    assert r["top_ups"] == []
    assert r["savings_pot"]["set"] == 0
    assert r["savings_pot"]["funded"] == 0
    assert r["savings_pot"]["amounts"] == []


def test_plan_with_no_goals_surplus_limit_equals_surplus():
    r = _run(1000, [])
    assert r["surplus"] == 1000
    assert r["surplus_limit"] == 1000


# --------------------------------------------------------------------- #
# Unit: Freed Installment forecast                                       #
# --------------------------------------------------------------------- #

def test_freed_forecast_for_step_12_example():
    freed = _example()["freed"]
    assert [f["goal_id"] for f in freed] == [1, 2]
    assert freed[0]["freed_date"] == _dl(180)
    assert freed[0]["amount"] == 400
    assert freed[1]["freed_date"] == _dl(360)
    assert freed[1]["amount"] == 100


def test_freed_entries_carry_period_and_limit():
    r = _example()
    assert _freed(r, 1)["freed_period"] == 6
    assert _freed(r, 2)["freed_period"] == 12
    assert _freed(r, 1)["limit"] == 400
    assert _freed(r, 2)["limit"] == 100


def test_funded_items_gain_freed_period_date_and_amount():
    r = _example()
    car = _item(r, 1)
    assert car["freed_period"] == 6
    assert car["freed_date"] == _dl(180)
    assert car["freed_amount"] == 400
    laptop = _item(r, 2)
    assert laptop["freed_period"] == 12
    assert laptop["freed_date"] == _dl(360)
    assert laptop["freed_amount"] == 100


def test_freed_forecast_is_sorted_by_freed_date_not_by_funding_order():
    goals = [
        _g(1, 1200, 0, 360, "Must-have", name=LAPTOP),
        _g(2, 2400, 0, 180, "Nice-to-have", name=CAR),
    ]
    freed = _run(2000, goals, [1200])["freed"]
    assert [f["goal_id"] for f in freed] == [2, 1]


def test_freed_forecast_excludes_fully_funded_and_unreachable_goals():
    goals = [
        _g(1, 500, 500, 60),
        _g(2, 99999, 0, 30),
        _g(3, 100, 0, 60),
    ]
    r = _run(1000, goals)
    assert _item(r, 1)["status"] == "fully_funded"
    assert _item(r, 2)["status"] == "unreachable"
    assert [f["goal_id"] for f in r["freed"]] == [3]


def test_freed_forecast_is_empty_without_funded_goals():
    assert _run(1000, [])["freed"] == []


def test_freed_after_one_period_for_goal_past_its_deadline():
    r = _run(1000, [_g(1, 300, 0, -5)])
    assert _freed(r, 1)["freed_date"] == _dl(30)
    assert _freed(r, 1)["amount"] == 300


def test_freed_installment_rounded_up_to_the_cent_is_the_freed_amount():
    # 100 over 3 periods = 33.34 a period (rounded up), done after period 2.
    r = _run(1000, [_g(1, 100, 0, 90)])
    assert _freed(r, 1)["freed_date"] == _dl(90)
    assert _freed(r, 1)["amount"] == 33.34


def test_top_up_moves_freed_date_earlier_and_raises_freed_amount():
    # Installment 400 + Top-up 300 = 700 a period: 700 * 4 = 2800 >= 2400,
    # so the last period is 3 and the Freed date is today + 120 days.
    r = _example([_tu(1, 1, 300)])
    car = _freed(r, 1)
    assert car["freed_date"] == _dl(120)
    assert car["amount"] == 700
    assert _item(r, 1)["freed_date"] == _dl(120)


def test_top_up_does_not_change_other_goals_freed_forecast():
    r = _example([_tu(1, 1, 300)])
    assert _freed(r, 2)["freed_date"] == _dl(360)
    assert _freed(r, 2)["amount"] == 100


def test_forecast_stops_when_running_total_exactly_reaches_remaining():
    # Available 1000: Installment 400 + Top-up 400 = 800 a period; 3 periods
    # make exactly 2400, so the last period is 2.
    r = _run(2200, [_g(1, 2400, 0, 180, name=CAR)], [1200], [_tu(1, 1, 400)])
    assert _freed(r, 1)["freed_date"] == _dl(90)
    assert _freed(r, 1)["amount"] == 800


def test_later_top_up_start_only_counts_from_its_start_period():
    # Top-up starts in period 3 (today + 90 days).
    # Periods 0-2 pay 400 (1200), period 3 pays 800 (2000), period 4 pays 800
    # (2800 >= 2400): the last period is 4, freed from today + 150 days.
    r = _run(2200, [_g(1, 2400, 0, 180, name=CAR)], [1200], [_tu(1, 1, 400, 90)])
    assert _freed(r, 1)["freed_date"] == _dl(150)
    assert _freed(r, 1)["amount"] == 800


def test_top_up_starting_after_the_goal_finishes_is_not_in_freed_amount():
    # The Goal's last period is 5 (today + 200 days is period 7).
    r = _run(2200, [_g(1, 2400, 0, 180, name=CAR)], [1200], [_tu(1, 1, 400, 200)])
    assert _freed(r, 1)["freed_date"] == _dl(180)
    assert _freed(r, 1)["amount"] == 400


# --------------------------------------------------------------------- #
# Unit: Top-up statuses                                                  #
# --------------------------------------------------------------------- #

def test_top_up_that_fits_is_active_and_takes_surplus():
    r = _example([_tu(1, 1, 300)])
    assert _top_up(r, 1)["status"] == "active"
    assert r["surplus"] == 0


def test_top_up_result_keeps_its_input_fields():
    r = _example([_tu(7, 1, 120, 0)])
    t = _top_up(r, 7)
    assert t["goal_id"] == 1
    assert t["amount"] == 120
    assert t["start_date"] == _dl(0)


@pytest.mark.parametrize(
    "start_days, status",
    [(-40, "active"), (-1, "active"), (0, "active"), (1, "scheduled"),
     (29, "scheduled"), (30, "scheduled"), (31, "scheduled"), (300, "scheduled")],
)
def test_top_up_status_depends_on_start_period(start_days, status):
    r = _run(5000, [_g(1, 1200, 0, 180)], [], [_tu(1, 1, 50, start_days)])
    assert _top_up(r, 1)["status"] == status


def test_scheduled_top_up_takes_nothing_from_todays_surplus():
    r = _example([_tu(1, 2, 400, 180)])
    assert _top_up(r, 1)["status"] == "scheduled"
    assert r["surplus"] == 300


def test_top_up_on_fully_funded_goal_is_finished():
    r = _run(1000, [_g(1, 500, 500, 60)], [], [_tu(1, 1, 50)])
    assert _top_up(r, 1)["status"] == "finished"


def test_top_up_on_fully_funded_goal_is_finished_even_when_it_starts_later():
    r = _run(1000, [_g(1, 500, 500, 60)], [], [_tu(1, 1, 50, 90)])
    assert _top_up(r, 1)["status"] == "finished"


def test_top_up_on_unreachable_goal_is_paused():
    r = _run(1000, [_g(1, 99999, 0, 30)], [], [_tu(1, 1, 50)])
    assert _item(r, 1)["status"] == "unreachable"
    assert _top_up(r, 1)["status"] == "paused"


def test_scheduled_top_up_on_unreachable_goal_is_paused_not_scheduled():
    r = _run(1000, [_g(1, 99999, 0, 30)], [], [_tu(1, 1, 50, 90)])
    assert _top_up(r, 1)["status"] == "paused"


def test_top_up_that_does_not_fit_is_paused_and_later_ones_are_still_tried():
    # Available 800, Installments 400 + 100, capacity 300.
    r = _example([_tu(1, 1, 500), _tu(2, 2, 300)])
    assert _top_up(r, 1)["status"] == "paused"
    assert _top_up(r, 2)["status"] == "active"
    assert r["surplus"] == 0


def test_top_ups_are_funded_in_goal_walk_order_before_id():
    # Capacity 400 after two Installments of 300; room for only one Top-up.
    # The Must-have Goal (id 2) is first in the walk, so its Top-up wins even
    # though the other Top-up has the lower id.
    goals = [
        _g(1, 300, 0, 30, "Nice-to-have"),
        _g(2, 300, 0, 30, "Must-have"),
    ]
    r = _run(1000, goals, [], [_tu(1, 1, 300), _tu(2, 2, 300)])
    assert _top_up(r, 2)["status"] == "active"
    assert _top_up(r, 1)["status"] == "paused"
    assert r["surplus"] == 100


def test_top_ups_on_the_same_goal_are_funded_by_lowest_id_first():
    # Capacity 200, two Top-ups of 150 on one Goal: only the lower id fits.
    r = _run(500, [_g(1, 300, 0, 30)], [], [_tu(5, 1, 150), _tu(3, 1, 150)])
    assert _top_up(r, 3)["status"] == "active"
    assert _top_up(r, 5)["status"] == "paused"


def test_finished_top_up_takes_nothing_so_later_top_ups_still_fit():
    # Capacity 700. If the finished Top-up (500) took money, the second
    # Top-up (500) would no longer fit.
    goals = [_g(1, 100, 100, 10), _g(2, 300, 0, 30)]
    r = _run(1000, goals, [], [_tu(1, 1, 500), _tu(2, 2, 500)])
    assert _top_up(r, 1)["status"] == "finished"
    assert _top_up(r, 2)["status"] == "active"
    assert r["surplus"] == 200


def test_paused_top_up_takes_nothing_so_later_top_ups_still_fit():
    goals = [
        _g(1, 5000, 0, 30, "Must-have"),
        _g(2, 300, 0, 30, "Should-have"),
    ]
    r = _run(1000, goals, [], [_tu(1, 1, 600), _tu(2, 2, 600)])
    assert _top_up(r, 1)["status"] == "paused"
    assert _top_up(r, 2)["status"] == "active"
    assert r["surplus"] == 100


# --------------------------------------------------------------------- #
# Unit: Savings Pot                                                      #
# --------------------------------------------------------------------- #

def test_savings_pot_amount_starting_now_lowers_surplus():
    r = _example(pots=[_pot(1, 100)])
    assert r["savings_pot"]["set"] == 100
    assert r["savings_pot"]["funded"] == 100
    assert r["surplus"] == 200


def test_savings_pot_lists_its_amounts():
    r = _example(pots=[_pot(4, 100), _pot(9, 50, 90)])
    ids = sorted(a["id"] for a in r["savings_pot"]["amounts"])
    assert ids == [4, 9]


def test_savings_pot_set_is_the_sum_of_amounts_starting_now():
    r = _example(pots=[_pot(1, 100), _pot(2, 50)])
    assert r["savings_pot"]["set"] == 150
    assert r["savings_pot"]["funded"] == 150
    assert r["surplus"] == 150


def test_scheduled_savings_pot_amount_is_not_in_set_or_surplus():
    r = _example(pots=[_pot(1, 400, 180)])
    assert r["savings_pot"]["set"] == 0
    assert r["savings_pot"]["funded"] == 0
    assert r["surplus"] == 300


def test_savings_pot_set_only_counts_amounts_whose_start_period_is_zero():
    r = _example(pots=[_pot(1, 100), _pot(2, 50, 1), _pot(3, 70, 90)])
    assert r["savings_pot"]["set"] == 100
    assert r["surplus"] == 200


def test_savings_pot_amount_with_start_date_in_the_past_counts_as_now():
    r = _example(pots=[_pot(1, 100, -40)])
    assert r["savings_pot"]["set"] == 100
    assert r["surplus"] == 200


def test_savings_pot_is_cut_to_the_capacity_left_after_top_ups():
    # Available 750: Installment 400, Top-up 300 (active), 50 left for a Pot
    # amount of 100.
    r = _run(1950, [_g(1, 2400, 0, 180)], [1200], [_tu(1, 1, 300)], [_pot(1, 100)])
    assert _top_up(r, 1)["status"] == "active"
    assert r["savings_pot"]["set"] == 100
    assert r["savings_pot"]["funded"] == 50
    assert r["surplus"] == 0


def test_top_up_is_paused_and_pot_cut_while_the_goal_stays_funded():
    # Available 450: Installment 400 leaves 50, too little for the Top-up of
    # 300 and half of the Pot amount of 100.
    r = _run(1650, [_g(1, 2400, 0, 180)], [1200], [_tu(1, 1, 300)], [_pot(1, 100)])
    assert _item(r, 1)["status"] == "funded"
    assert _top_up(r, 1)["status"] == "paused"
    assert r["savings_pot"]["set"] == 100
    assert r["savings_pot"]["funded"] == 50
    assert r["surplus"] == 0


def test_top_up_never_makes_a_goal_unreachable():
    # Available 400 is exactly the Installment: the Goal is still funded and
    # the Top-up is paused.
    r = _run(1600, [_g(1, 2400, 0, 180)], [1200], [_tu(1, 1, 300)])
    assert _item(r, 1)["status"] == "funded"
    assert _top_up(r, 1)["status"] == "paused"


def test_savings_pot_never_makes_a_goal_unreachable():
    r = _run(1600, [_g(1, 2400, 0, 180)], [1200], pots=[_pot(1, 300)])
    assert _item(r, 1)["status"] == "funded"
    assert r["savings_pot"]["set"] == 300
    assert r["savings_pot"]["funded"] == 0


def test_goal_becomes_unreachable_only_when_its_installment_does_not_fit():
    # Available 399 < Installment 400. Its Top-up is paused.
    r = _run(1599, [_g(1, 2400, 0, 180)], [1200], [_tu(1, 1, 300)])
    assert _item(r, 1)["status"] == "unreachable"
    assert _top_up(r, 1)["status"] == "paused"


def test_overcommitted_plan_pauses_top_ups_funds_no_pot_and_has_zero_surplus():
    r = _run(500, [_g(1, 2400, 0, 180)], [800], [_tu(1, 1, 300)], [_pot(1, 100)])
    assert r["overcommitted"] is True
    assert _top_up(r, 1)["status"] == "paused"
    assert r["savings_pot"]["set"] == 100
    assert r["savings_pot"]["funded"] == 0
    assert r["surplus"] == 0


def test_surplus_is_capacity_left_after_installments_top_ups_and_pot():
    r = _example([_tu(1, 1, 100)], [_pot(1, 50)])
    assert r["surplus"] == 150


def test_money_is_computed_in_cents_without_float_noise():
    r = _run(
        1000,
        [_g(1, 100, 0, 30)],
        [],
        [_tu(1, 1, 0.10), _tu(2, 1, 0.20)],
        [_pot(1, 0.10), _pot(2, 0.20)],
    )
    assert r["savings_pot"]["set"] == 0.30
    assert r["savings_pot"]["funded"] == 0.30
    assert r["surplus"] == 899.40


# --------------------------------------------------------------------- #
# Unit: redirect limit                                                   #
# --------------------------------------------------------------------- #

def test_surplus_limit_is_the_surplus_when_nothing_is_scheduled():
    r = _example()
    assert r["surplus"] == 300
    assert r["surplus_limit"] == 300


def test_surplus_limit_is_zero_when_the_surplus_is_taken_by_a_top_up():
    r = _example([_tu(1, 1, 300)])
    assert r["surplus"] == 0
    assert r["surplus_limit"] == 0


def test_surplus_limit_drops_with_a_pot_amount_starting_now():
    r = _example(pots=[_pot(1, 100)])
    assert r["surplus_limit"] == 200


def test_surplus_limit_protects_a_later_scheduled_top_up():
    # A Top-up of 450 on the Laptop from today + 180 days leaves only
    # 800 - 100 - 450 = 250 free from period 6 on, so today's Surplus of 300
    # cannot be redirected in full.
    r = _example([_tu(1, 2, 450, 180)])
    assert r["surplus"] == 300
    assert r["surplus_limit"] == 250


def test_goal_limit_is_capped_by_its_freed_amount_not_boosted_by_the_surplus():
    # The Surplus (300) is free earlier, so it must not add to the Car
    # licence's limit of 400.
    r = _example()
    assert _freed(r, 1)["limit"] == 400


def test_goal_limit_not_changed_by_redirecting_surplus_to_the_pot():
    r = _example(pots=[_pot(1, 100)])
    assert _freed(r, 1)["limit"] == 400
    assert _freed(r, 2)["limit"] == 100


def test_goal_limit_is_zero_once_its_freed_installment_is_a_scheduled_top_up():
    r = _example([_tu(1, 2, 400, 180)])
    assert _freed(r, 1)["limit"] == 0


def test_goal_limit_is_zero_once_its_freed_installment_is_a_scheduled_pot_amount():
    r = _example(pots=[_pot(1, 400, 180)])
    assert _freed(r, 1)["limit"] == 0
    assert r["surplus"] == 300


def test_top_up_funded_by_a_freed_installment_shortens_the_other_goal():
    # Laptop gets 400 more from period 6: periods 0-5 pay 100 (600), period 6
    # pays 500 (1100), period 7 pays 500 (1600 >= 1200): last period is 7.
    r = _example([_tu(1, 2, 400, 180)])
    assert _top_up(r, 1)["status"] == "scheduled"
    assert r["surplus"] == 300
    assert _freed(r, 2)["freed_date"] == _dl(240)
    assert _freed(r, 2)["amount"] == 500


def test_lump_sum_lowers_the_installment():
    # Remaining 1200 over 6 periods is 200; after a Lump Sum of 600 it is 100.
    before = _run(5000, [_g(1, 1200, 0, 180)])
    after = _run(5000, [_g(1, 1200, 600, 180)])
    assert _item(before, 1)["installment"] == 200
    assert _item(after, 1)["installment"] == 100


# --------------------------------------------------------------------- #
# Unit: determinism and module constraints                               #
# --------------------------------------------------------------------- #

def test_plan_with_top_ups_and_pot_is_deterministic():
    args = ([_tu(1, 1, 120), _tu(2, 2, 40, 90)], [_pot(1, 60), _pot(2, 30, 60)])
    first = _example(*args)
    for _ in range(5):
        assert _example(*args) == first


def test_plan_forecast_depends_on_the_today_argument():
    other = date(2027, 6, 15)
    goals = [
        {**_g(1, 2400, 0, 180, name=CAR), "deadline": _dl(180, other)},
    ]
    r = planner.plan(2000, [{"amount": 1200}], goals, other)
    assert r["freed"][0]["freed_date"] == _dl(180, other)


def _planner_tree():
    return ast.parse(PLANNER_PATH.read_text(encoding="utf-8"))


def test_planner_still_imports_nothing_from_flask_or_database():
    modules = []
    for node in ast.walk(_planner_tree()):
        if isinstance(node, ast.Import):
            modules += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    for mod in modules:
        assert mod.split(".")[0] not in ("flask", "database"), mod


def test_planner_never_reads_the_clock():
    for node in ast.walk(_planner_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("today", "now", "utcnow"):
                pytest.fail("planner.py must take `today` as an argument")


# --------------------------------------------------------------------- #
# Route helpers / fixtures                                               #
# --------------------------------------------------------------------- #

def _future(days):
    return (date.today() + timedelta(days=days)).isoformat()


def _login(client, email=DEMO_EMAIL, password=DEMO_PASSWORD):
    return client.post("/login", data={"email": email, "password": password})


def _user_id(email):
    conn = db.get_db()
    try:
        return conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()["id"]
    finally:
        conn.close()


def _setup_example(user_id, car_days=170, laptop_days=350):
    """Income 2000, Obligation 1200, Car licence and Laptop.

    The default Deadlines (+170 and +350) give the same Installments as the
    spec's +180 and +360 (6 and 12 periods) but make the Freed dates (+180 and
    +360) differ from the Deadlines shown in the Plan table.
    """
    queries.set_income(user_id, 2000)
    queries.create_obligation(user_id, "Rent", 1200)
    car = queries.create_goal(
        user_id, CAR, 2400, 0, _future(car_days), "Should-have"
    )
    laptop = queries.create_goal(
        user_id, LAPTOP, 1200, 0, _future(laptop_days), "Should-have"
    )
    return car, laptop


SNAPSHOT_TABLES = (
    "users",
    "expenses",
    "incomes",
    "obligations",
    "goals",
    "goal_dependencies",
    "top_ups",
    "savings_pot_amounts",
)


def _snapshot():
    conn = db.get_db()
    try:
        return {
            t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")]
            for t in SNAPSHOT_TABLES
        }
    finally:
        conn.close()


def _tables():
    conn = db.get_db()
    try:
        return {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


def _columns(table):
    conn = db.get_db()
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _saved_amount(goal_id):
    conn = db.get_db()
    try:
        return conn.execute(
            "SELECT saved_amount FROM goals WHERE id = ?", (goal_id,)
        ).fetchone()["saved_amount"]
    finally:
        conn.close()


def _text(body):
    """Visible text of a page: tags stripped, entities decoded, spaces collapsed."""
    stripped = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", html.unescape(stripped))


def _top_up_status_shown(body, status):
    return re.search(rf"Top-up.{{0,80}}{status}", _text(body), re.I) is not None


def _redirect_hrefs(body):
    return [
        html.unescape(h)
        for h in re.findall(r'href="([^"]*/plan/redirect[^"]*)"', body)
    ]


def _surplus_links(body):
    return [h for h in _redirect_hrefs(body) if "source=surplus" in h]


def _goal_links(body, goal_id):
    return [
        h
        for h in _redirect_hrefs(body)
        if "source=goal" in h and re.search(rf"goal_id={goal_id}(?!\d)", h)
    ]


def _is_plan_redirect(resp):
    return resp.status_code == 302 and urlparse(resp.headers["Location"]).path == "/plan"


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in(client):
    _login(client)
    return client


@pytest.fixture
def demo_id(db_path):
    return _user_id(DEMO_EMAIL)


@pytest.fixture
def other_user(db_path):
    conn = db.get_db()
    try:
        conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("Other", OTHER_EMAIL, generate_password_hash(OTHER_PASSWORD)),
        )
        conn.commit()
    finally:
        conn.close()
    return _user_id(OTHER_EMAIL)


@pytest.fixture
def example(demo_id):
    car, laptop = _setup_example(demo_id)
    return SimpleNamespace(car=car, laptop=laptop)


@pytest.fixture
def scenario(example, demo_id):
    """The example plus one Top-up on the Car licence and one Pot amount."""
    queries.create_redirect(demo_id, _future(0), [(example.car, 100)], 50)
    return SimpleNamespace(
        car=example.car,
        laptop=example.laptop,
        top_up=queries.list_top_ups(demo_id)[0]["id"],
        pot=queries.list_pot_amounts(demo_id)[0]["id"],
    )


@pytest.fixture
def theirs(other_user):
    """Another user with Income, a funded Goal, one Top-up and one Pot amount."""
    queries.set_income(other_user, 5000)
    goal = queries.create_goal(
        other_user, "TheirsZZ", 600, 0, _future(60), "Should-have"
    )
    queries.create_redirect(other_user, _future(0), [(goal, 123)], 77)
    return SimpleNamespace(
        user=other_user,
        goal=goal,
        top_up=queries.list_top_ups(other_user)[0]["id"],
        pot=queries.list_pot_amounts(other_user)[0]["id"],
    )


def _plan_body(client):
    resp = client.get("/plan")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _post_redirect(client, **fields):
    return client.post("/plan/redirect", data=fields)


# --------------------------------------------------------------------- #
# Auth: every new route redirects to /login when logged out              #
# --------------------------------------------------------------------- #

LOGGED_OUT_REQUESTS = [
    ("get", "/plan/redirect?source=surplus"),
    ("get", "/plan/redirect?source=goal&goal_id=1"),
    ("post", "/plan/redirect"),
    ("get", "/top-ups/1/delete"),
    ("post", "/top-ups/1/delete"),
    ("get", "/savings-pot/1/delete"),
    ("post", "/savings-pot/1/delete"),
    ("get", "/goals/1/lump-sum"),
    ("post", "/goals/1/lump-sum"),
]


@pytest.mark.parametrize("method, url", LOGGED_OUT_REQUESTS)
def test_new_routes_redirect_to_login_when_logged_out(client, method, url):
    resp = getattr(client, method)(url)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_logged_out_posts_to_real_records_change_nothing(client, scenario):
    before = _snapshot()
    requests = [
        ("/plan/redirect", {"source": "surplus", "pot_amount": "10"}),
        (f"/top-ups/{scenario.top_up}/delete", {}),
        (f"/savings-pot/{scenario.pot}/delete", {}),
        (f"/goals/{scenario.car}/lump-sum", {"amount": "10"}),
    ]
    for url, data in requests:
        resp = client.post(url, data=data)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
    assert _snapshot() == before


# --------------------------------------------------------------------- #
# Database                                                               #
# --------------------------------------------------------------------- #

def test_new_tables_exist(db_path):
    assert {"top_ups", "savings_pot_amounts"} <= _tables()


def test_top_ups_table_has_the_specified_columns(db_path):
    assert {"id", "user_id", "goal_id", "amount", "start_date", "created_at"} <= (
        _columns("top_ups")
    )


def test_savings_pot_amounts_table_has_the_specified_columns(db_path):
    assert {"id", "user_id", "amount", "start_date", "created_at"} <= _columns(
        "savings_pot_amounts"
    )


def test_init_db_is_idempotent_with_the_new_tables(scenario):
    before = _snapshot()
    db.init_db()
    db.init_db()
    assert _snapshot() == before


def test_init_db_adds_new_tables_to_an_existing_database_and_keeps_data(
    example, demo_id
):
    conn = db.get_db()
    try:
        conn.execute("DROP TABLE top_ups")
        conn.execute("DROP TABLE savings_pot_amounts")
        conn.commit()
    finally:
        conn.close()
    assert "top_ups" not in _tables()

    db.init_db()

    assert {"top_ups", "savings_pot_amounts"} <= _tables()
    assert queries.get_goal(example.car, demo_id)["name"] == CAR
    assert queries.get_goal(example.laptop, demo_id)["name"] == LAPTOP
    assert queries.get_income(demo_id) == 2000
    assert _user_id(DEMO_EMAIL) == demo_id


# --------------------------------------------------------------------- #
# GET /plan: no Top-ups, no Savings Pot                                  #
# --------------------------------------------------------------------- #

def test_plan_without_top_ups_or_pot_shows_step_12_figures(logged_in, example):
    body = _plan_body(logged_in)
    assert "€400.00" in body  # Car licence Installment
    assert "€100.00" in body  # Laptop Installment
    assert "€300.00" in body  # Surplus
    assert "unreachable goal" not in body.lower()


def test_plan_without_top_ups_shows_no_top_up_under_goals(logged_in, example):
    assert not _top_up_status_shown(_plan_body(logged_in), "active")
    assert not _top_up_status_shown(_plan_body(logged_in), "scheduled")
    assert not _top_up_status_shown(_plan_body(logged_in), "paused")


def test_plan_still_flags_unreachable_goals(logged_in, demo_id):
    queries.set_income(demo_id, 1000)
    queries.create_goal(demo_id, "NoRoomZZ", 5000, 0, _future(30), "Must-have")
    assert "unreachable goal" in _plan_body(logged_in).lower()


def test_plan_get_writes_nothing_to_database(logged_in, scenario):
    before = _snapshot()
    _plan_body(logged_in)
    _plan_body(logged_in)
    assert _snapshot() == before


# --------------------------------------------------------------------- #
# GET /plan: Redirect links                                              #
# --------------------------------------------------------------------- #

def test_plan_links_to_redirect_the_surplus_when_it_is_above_zero(
    logged_in, example
):
    assert _surplus_links(_plan_body(logged_in))


def test_plan_has_no_surplus_redirect_link_when_surplus_is_zero(
    logged_in, demo_id
):
    queries.set_income(demo_id, 1000)
    queries.create_goal(demo_id, "ExactZZ", 1000, 0, _future(30), "Should-have")
    body = _plan_body(logged_in)
    assert "€0.00" in body
    assert _surplus_links(body) == []


def test_plan_has_no_surplus_redirect_link_after_surplus_is_fully_redirected(
    logged_in, example
):
    _post_redirect(logged_in, source="surplus", **{f"topup_{example.car}": "300"})
    body = _plan_body(logged_in)
    assert "€0.00" in body
    assert _surplus_links(body) == []


def test_plan_without_goals_still_lets_the_user_redirect_the_surplus(
    logged_in, demo_id
):
    queries.set_income(demo_id, 1000)
    assert _surplus_links(_plan_body(logged_in))


# --------------------------------------------------------------------- #
# GET /plan: Coming up (Freed Installment forecast)                      #
# --------------------------------------------------------------------- #

def test_plan_coming_up_lists_freed_installments_with_their_dates(
    logged_in, example
):
    body = _plan_body(logged_in)
    assert "Coming up" in body
    # Freed from the period after the last Installment, not at the Deadline.
    assert _future(180) in body
    assert _future(360) in body


def test_plan_coming_up_for_exact_step_12_example(logged_in, demo_id):
    _setup_example(demo_id, car_days=180, laptop_days=360)
    body = _plan_body(logged_in)
    assert "Coming up" in body
    assert CAR in body and LAPTOP in body
    assert "€400.00" in body
    assert "€100.00" in body
    assert _future(180) in body
    assert _future(360) in body


def test_plan_has_a_redirect_link_for_each_freed_installment(logged_in, example):
    body = _plan_body(logged_in)
    assert _goal_links(body, example.car)
    assert _goal_links(body, example.laptop)


def test_plan_has_no_coming_up_without_funded_goals(logged_in, demo_id):
    queries.set_income(demo_id, 1000)
    assert "Coming up" not in _plan_body(logged_in)


# --------------------------------------------------------------------- #
# GET /plan/redirect: the form                                           #
# --------------------------------------------------------------------- #

def test_redirect_form_for_surplus_shows_amount_and_fields(logged_in, example):
    resp = logged_in.get("/plan/redirect?source=surplus")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "€300.00" in body
    assert f"topup_{example.car}" in body
    assert f"topup_{example.laptop}" in body
    assert "pot_amount" in body


def test_redirect_form_has_links_to_add_a_goal_and_back_to_plan(logged_in, example):
    body = logged_in.get("/plan/redirect?source=surplus").get_data(as_text=True)
    assert 'href="/goals/add"' in body
    assert 'href="/plan"' in body


def test_redirect_form_for_a_goal_shows_freed_amount_and_date(logged_in, example):
    resp = logged_in.get(f"/plan/redirect?source=goal&goal_id={example.car}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "€400.00" in body
    assert _future(180) in body


def test_redirect_form_for_a_goal_has_no_field_for_the_source_goal(
    logged_in, example
):
    body = logged_in.get(
        f"/plan/redirect?source=goal&goal_id={example.car}"
    ).get_data(as_text=True)
    assert f"topup_{example.laptop}" in body
    assert f"topup_{example.car}" not in body
    assert "pot_amount" in body


def test_redirect_form_has_no_field_for_an_unreachable_goal(logged_in, demo_id, example):
    huge = queries.create_goal(
        demo_id, "HugeZZ", 99999, 0, _future(30), "Nice-to-have"
    )
    body = logged_in.get("/plan/redirect?source=surplus").get_data(as_text=True)
    assert f"topup_{example.car}" in body
    assert f"topup_{huge}" not in body


def test_redirect_form_has_no_field_for_a_fully_funded_goal(logged_in, demo_id, example):
    done = queries.create_goal(demo_id, "DoneZZ", 100, 100, _future(60), "Should-have")
    body = logged_in.get("/plan/redirect?source=surplus").get_data(as_text=True)
    assert f"topup_{example.car}" in body
    assert f"topup_{done}" not in body


def test_redirect_form_has_no_field_for_a_goal_finished_before_the_start_date(
    logged_in, demo_id, example
):
    # Last Installment in period 1: not still paying from today + 180 days.
    short = queries.create_goal(
        demo_id, "ShortZZ", 300, 0, _future(60), "Should-have"
    )
    freed_car = logged_in.get(
        f"/plan/redirect?source=goal&goal_id={example.car}"
    ).get_data(as_text=True)
    assert f"topup_{short}" not in freed_car
    assert f"topup_{example.laptop}" in freed_car
    # It is still paying today, so it is offered for the Surplus.
    surplus = logged_in.get("/plan/redirect?source=surplus").get_data(as_text=True)
    assert f"topup_{short}" in surplus


def test_redirect_form_has_no_field_for_a_goal_that_finishes_on_the_start_date(
    logged_in, demo_id
):
    # Both Goals pay through period 5 and are freed from period 6.
    queries.set_income(demo_id, 3000)
    queries.create_obligation(demo_id, "Rent", 1200)
    car = queries.create_goal(demo_id, CAR, 2400, 0, _future(170), "Should-have")
    twin = queries.create_goal(demo_id, "TwinZZ", 2400, 0, _future(170), "Should-have")
    body = logged_in.get(f"/plan/redirect?source=goal&goal_id={car}").get_data(
        as_text=True
    )
    assert f"topup_{twin}" not in body


def test_redirect_form_get_writes_nothing(logged_in, scenario):
    before = _snapshot()
    logged_in.get("/plan/redirect?source=surplus")
    logged_in.get(f"/plan/redirect?source=goal&goal_id={scenario.car}")
    assert _snapshot() == before


# --------------------------------------------------------------------- #
# /plan/redirect: unknown source or Goal gives 404                       #
# --------------------------------------------------------------------- #

BAD_SOURCES = [
    {},
    {"source": "bogus"},
    {"source": "goal"},
    {"source": "goal", "goal_id": "99999"},
    {"source": "goal", "goal_id": "abc"},
]


@pytest.mark.parametrize("params", BAD_SOURCES)
def test_redirect_get_with_bad_source_returns_404(logged_in, example, params):
    resp = logged_in.get("/plan/redirect", query_string=params)
    assert resp.status_code == 404


@pytest.mark.parametrize("params", BAD_SOURCES)
def test_redirect_post_with_bad_source_returns_404_and_saves_nothing(
    logged_in, example, params
):
    before = _snapshot()
    resp = logged_in.post("/plan/redirect", data={**params, "pot_amount": "10"})
    assert resp.status_code == 404
    assert _snapshot() == before


def test_redirect_for_an_unreachable_goal_returns_404(logged_in, demo_id, example):
    huge = queries.create_goal(
        demo_id, "HugeZZ", 99999, 0, _future(30), "Nice-to-have"
    )
    assert (
        logged_in.get(f"/plan/redirect?source=goal&goal_id={huge}").status_code == 404
    )
    before = _snapshot()
    resp = _post_redirect(logged_in, source="goal", goal_id=str(huge), pot_amount="5")
    assert resp.status_code == 404
    assert _snapshot() == before


def test_redirect_for_another_users_goal_returns_404(logged_in, example, theirs):
    url = f"/plan/redirect?source=goal&goal_id={theirs.goal}"
    assert logged_in.get(url).status_code == 404
    before = _snapshot()
    resp = _post_redirect(
        logged_in, source="goal", goal_id=str(theirs.goal), pot_amount="5"
    )
    assert resp.status_code == 404
    assert _snapshot() == before


# --------------------------------------------------------------------- #
# POST /plan/redirect: happy paths                                       #
# --------------------------------------------------------------------- #

def test_redirecting_surplus_as_top_up_redirects_to_plan(logged_in, example):
    resp = _post_redirect(logged_in, source="surplus", **{f"topup_{example.car}": "300"})
    assert _is_plan_redirect(resp)


def test_redirecting_surplus_as_top_up_saves_a_top_up_starting_today(
    logged_in, demo_id, example
):
    _post_redirect(logged_in, source="surplus", **{f"topup_{example.car}": "300"})
    top_ups = queries.list_top_ups(demo_id)
    assert len(top_ups) == 1
    assert top_ups[0]["goal_id"] == example.car
    assert top_ups[0]["amount"] == 300
    assert top_ups[0]["start_date"] == date.today().isoformat()
    assert queries.list_pot_amounts(demo_id) == []


def test_redirecting_surplus_as_top_up_shows_active_top_up_and_zero_surplus(
    logged_in, example
):
    before = _plan_body(logged_in)
    assert "€0.00" not in before
    _post_redirect(logged_in, source="surplus", **{f"topup_{example.car}": "300"})
    body = _plan_body(logged_in)
    assert _top_up_status_shown(body, "active")
    assert "€0.00" in body


def test_redirecting_surplus_as_top_up_moves_the_freed_date_earlier(
    logged_in, example
):
    before = _plan_body(logged_in)
    assert _future(180) in before and _future(120) not in before
    _post_redirect(logged_in, source="surplus", **{f"topup_{example.car}": "300"})
    body = _plan_body(logged_in)
    assert _future(120) in body
    assert _future(180) not in body


def test_redirecting_freed_installment_as_top_up_is_scheduled_from_freed_date(
    logged_in, demo_id, example
):
    resp = _post_redirect(
        logged_in,
        source="goal",
        goal_id=str(example.car),
        **{f"topup_{example.laptop}": "400"},
    )
    assert _is_plan_redirect(resp)
    top_ups = queries.list_top_ups(demo_id)
    assert len(top_ups) == 1
    assert top_ups[0]["goal_id"] == example.laptop
    assert top_ups[0]["amount"] == 400
    assert top_ups[0]["start_date"] == _future(180)


def test_redirecting_freed_installment_does_not_change_todays_surplus(
    logged_in, example
):
    _post_redirect(
        logged_in,
        source="goal",
        goal_id=str(example.car),
        **{f"topup_{example.laptop}": "400"},
    )
    body = _plan_body(logged_in)
    assert "€300.00" in body
    assert "€0.00" not in body
    assert _surplus_links(body)


def test_redirecting_freed_installment_as_top_up_shortens_the_other_goal(
    logged_in, example
):
    _post_redirect(
        logged_in,
        source="goal",
        goal_id=str(example.car),
        **{f"topup_{example.laptop}": "400"},
    )
    # The Laptop's last period moves from 11 to 7: freed at today + 240 days.
    body = _plan_body(logged_in)
    assert _future(240) in body
    assert _future(360) not in body


def test_redirecting_surplus_to_pot_shows_it_and_lowers_the_surplus(
    logged_in, demo_id, example
):
    resp = _post_redirect(logged_in, source="surplus", pot_amount="100")
    assert _is_plan_redirect(resp)
    pots = queries.list_pot_amounts(demo_id)
    assert len(pots) == 1
    assert pots[0]["amount"] == 100
    assert pots[0]["start_date"] == date.today().isoformat()
    assert queries.list_top_ups(demo_id) == []

    body = _plan_body(logged_in)
    assert "Savings Pot" in body
    assert f"/savings-pot/{pots[0]['id']}/delete" in body
    assert "€200.00" in body
    assert "€300.00" not in body


def test_deleting_the_pot_amount_restores_the_surplus(logged_in, demo_id, example):
    _post_redirect(logged_in, source="surplus", pot_amount="100")
    pot_id = queries.list_pot_amounts(demo_id)[0]["id"]

    confirm = logged_in.get(f"/savings-pot/{pot_id}/delete")
    assert confirm.status_code == 200
    assert len(queries.list_pot_amounts(demo_id)) == 1  # GET does not delete

    resp = logged_in.post(f"/savings-pot/{pot_id}/delete")
    assert _is_plan_redirect(resp)
    assert queries.list_pot_amounts(demo_id) == []
    body = _plan_body(logged_in)
    assert "€300.00" in body
    assert "€200.00" not in body


def test_redirecting_freed_installment_to_pot_is_scheduled_from_freed_date(
    logged_in, demo_id, example
):
    resp = _post_redirect(
        logged_in, source="goal", goal_id=str(example.car), pot_amount="400"
    )
    assert _is_plan_redirect(resp)
    pots = queries.list_pot_amounts(demo_id)
    assert len(pots) == 1
    assert pots[0]["amount"] == 400
    assert pots[0]["start_date"] == _future(180)
    body = _plan_body(logged_in)
    assert "€300.00" in body
    assert "€0.00" not in body


def test_redirect_with_several_top_ups_and_a_pot_amount_saves_all_of_them(
    logged_in, demo_id, example
):
    resp = _post_redirect(
        logged_in,
        source="surplus",
        pot_amount="100",
        **{f"topup_{example.car}": "100", f"topup_{example.laptop}": "100"},
    )
    assert _is_plan_redirect(resp)
    top_ups = {t["goal_id"]: t for t in queries.list_top_ups(demo_id)}
    assert set(top_ups) == {example.car, example.laptop}
    assert top_ups[example.car]["amount"] == 100
    assert top_ups[example.laptop]["amount"] == 100
    pots = queries.list_pot_amounts(demo_id)
    assert [p["amount"] for p in pots] == [100]
    for row in list(top_ups.values()) + pots:
        assert row["start_date"] == date.today().isoformat()
    assert "€0.00" in _plan_body(logged_in)


def test_redirect_skips_empty_fields(logged_in, demo_id, example):
    resp = _post_redirect(
        logged_in,
        source="surplus",
        pot_amount="",
        **{f"topup_{example.car}": "", f"topup_{example.laptop}": "100"},
    )
    assert _is_plan_redirect(resp)
    top_ups = queries.list_top_ups(demo_id)
    assert [(t["goal_id"], t["amount"]) for t in top_ups] == [(example.laptop, 100)]
    assert queries.list_pot_amounts(demo_id) == []


def test_redirect_with_decimal_amount_is_stored(logged_in, demo_id, example):
    _post_redirect(logged_in, source="surplus", pot_amount="12.34")
    assert queries.list_pot_amounts(demo_id)[0]["amount"] == pytest.approx(12.34)


def test_redirect_for_user_without_goals_can_go_to_the_pot(logged_in, demo_id):
    queries.set_income(demo_id, 1000)
    resp = _post_redirect(logged_in, source="surplus", pot_amount="500")
    assert _is_plan_redirect(resp)
    assert queries.list_pot_amounts(demo_id)[0]["amount"] == 500


# --------------------------------------------------------------------- #
# POST /plan/redirect: the limit                                         #
# --------------------------------------------------------------------- #

def test_redirect_up_to_exactly_the_surplus_limit_is_allowed(
    logged_in, demo_id, example
):
    resp = _post_redirect(
        logged_in,
        source="surplus",
        pot_amount="100",
        **{f"topup_{example.car}": "200"},
    )
    assert _is_plan_redirect(resp)
    assert len(queries.list_top_ups(demo_id)) == 1
    assert len(queries.list_pot_amounts(demo_id)) == 1


def test_redirect_one_cent_over_the_surplus_limit_is_refused(logged_in, example):
    before = _snapshot()
    resp = _post_redirect(logged_in, source="surplus", pot_amount="300.01")
    assert resp.status_code == 200
    assert _snapshot() == before


def test_redirect_top_ups_plus_pot_over_the_amount_saves_nothing(logged_in, example):
    before = _snapshot()
    resp = _post_redirect(
        logged_in,
        source="surplus",
        pot_amount="150",
        **{f"topup_{example.car}": "200"},
    )
    assert resp.status_code == 200
    assert _snapshot() == before


def test_redirect_over_limit_is_refused_even_if_form_amount_was_edited(
    logged_in, example
):
    before = _snapshot()
    resp = _post_redirect(
        logged_in,
        source="surplus",
        pot_amount="150",
        amount="99999",
        limit="99999",
        redirect_amount="99999",
        **{f"topup_{example.car}": "200"},
    )
    assert resp.status_code == 200
    assert _snapshot() == before


def test_redirect_of_a_freed_installment_up_to_its_limit_is_allowed(
    logged_in, demo_id, example
):
    resp = _post_redirect(
        logged_in,
        source="goal",
        goal_id=str(example.car),
        **{f"topup_{example.laptop}": "400"},
    )
    assert _is_plan_redirect(resp)
    assert len(queries.list_top_ups(demo_id)) == 1


@pytest.mark.parametrize("amount", ["400.01", "450", "700"])
def test_redirect_of_a_freed_installment_over_its_limit_is_refused(
    logged_in, example, amount
):
    # The Surplus (300) is free earlier and cannot be redirected through the
    # Car licence's link.
    before = _snapshot()
    resp = _post_redirect(
        logged_in,
        source="goal",
        goal_id=str(example.car),
        **{f"topup_{example.laptop}": amount},
    )
    assert resp.status_code == 200
    assert _snapshot() == before


def test_a_freed_installment_cannot_be_redirected_twice(logged_in, demo_id, example):
    first = _post_redirect(
        logged_in,
        source="goal",
        goal_id=str(example.car),
        **{f"topup_{example.laptop}": "400"},
    )
    assert _is_plan_redirect(first)
    before = _snapshot()

    again = _post_redirect(
        logged_in,
        source="goal",
        goal_id=str(example.car),
        **{f"topup_{example.laptop}": "1"},
    )
    assert again.status_code == 200
    assert _snapshot() == before
    assert len(queries.list_top_ups(demo_id)) == 1


def test_a_fully_redirected_freed_installment_shows_as_already_redirected(
    logged_in, example
):
    _post_redirect(
        logged_in,
        source="goal",
        goal_id=str(example.car),
        **{f"topup_{example.laptop}": "400"},
    )
    assert "already redirected" in _plan_body(logged_in).lower()


def test_a_freed_installment_cannot_be_redirected_again_via_the_pot_either(
    logged_in, demo_id, example
):
    _post_redirect(logged_in, source="goal", goal_id=str(example.car), pot_amount="400")
    before = _snapshot()
    resp = _post_redirect(
        logged_in, source="goal", goal_id=str(example.car), pot_amount="1"
    )
    assert resp.status_code == 200
    assert _snapshot() == before
    assert "already redirected" in _plan_body(logged_in).lower()


def test_redirect_cannot_use_surplus_money_twice(logged_in, demo_id, example):
    _post_redirect(logged_in, source="surplus", pot_amount="300")
    before = _snapshot()
    resp = _post_redirect(logged_in, source="surplus", pot_amount="1")
    assert resp.status_code == 200
    assert _snapshot() == before


def test_new_surplus_redirect_cannot_break_a_later_scheduled_top_up(
    logged_in, demo_id, example
):
    # 450 a month on the Laptop from the Car licence's Freed date leaves only
    # 250 free from then on, although today's Surplus is 300.
    queries.create_redirect(demo_id, _future(180), [(example.laptop, 450)])
    assert "€300.00" in _plan_body(logged_in)

    before = _snapshot()
    refused = _post_redirect(logged_in, source="surplus", pot_amount="250.01")
    assert refused.status_code == 200
    assert _snapshot() == before

    accepted = _post_redirect(logged_in, source="surplus", pot_amount="250")
    assert _is_plan_redirect(accepted)
    assert len(queries.list_pot_amounts(demo_id)) == 1


# --------------------------------------------------------------------- #
# POST /plan/redirect: validation                                        #
# --------------------------------------------------------------------- #

INVALID_AMOUNTS = ["abc", "-5", "0", "0.00", "0.001", "nan", "inf"]


@pytest.mark.parametrize("amount", INVALID_AMOUNTS)
def test_redirect_invalid_top_up_amount_shows_error_and_saves_nothing(
    logged_in, example, amount
):
    before = _snapshot()
    resp = _post_redirect(
        logged_in, source="surplus", **{f"topup_{example.car}": amount}
    )
    assert resp.status_code == 200
    assert "pot_amount" in resp.get_data(as_text=True)
    assert _snapshot() == before


@pytest.mark.parametrize("amount", INVALID_AMOUNTS)
def test_redirect_invalid_pot_amount_shows_error_and_saves_nothing(
    logged_in, example, amount
):
    before = _snapshot()
    resp = _post_redirect(logged_in, source="surplus", pot_amount=amount)
    assert resp.status_code == 200
    assert "pot_amount" in resp.get_data(as_text=True)
    assert _snapshot() == before


def test_redirect_with_one_invalid_field_saves_none_of_the_valid_ones(
    logged_in, example
):
    before = _snapshot()
    resp = _post_redirect(
        logged_in,
        source="surplus",
        pot_amount="abc",
        **{f"topup_{example.car}": "100"},
    )
    assert resp.status_code == 200
    assert _snapshot() == before


def test_redirect_with_every_field_empty_shows_error_and_saves_nothing(
    logged_in, example
):
    before = _snapshot()
    resp = _post_redirect(
        logged_in,
        source="surplus",
        pot_amount="",
        **{f"topup_{example.car}": "", f"topup_{example.laptop}": ""},
    )
    assert resp.status_code == 200
    assert _snapshot() == before


def test_redirect_with_no_amount_fields_at_all_saves_nothing(logged_in, example):
    before = _snapshot()
    resp = _post_redirect(logged_in, source="surplus")
    assert resp.status_code == 200
    assert _snapshot() == before


def test_redirect_cannot_top_up_the_source_goal_itself(logged_in, example):
    before = _snapshot()
    _post_redirect(
        logged_in,
        source="goal",
        goal_id=str(example.car),
        **{f"topup_{example.car}": "100"},
    )
    assert _snapshot() == before


def test_redirect_cannot_top_up_an_unreachable_goal(logged_in, demo_id, example):
    huge = queries.create_goal(
        demo_id, "HugeZZ", 99999, 0, _future(30), "Nice-to-have"
    )
    before = _snapshot()
    _post_redirect(logged_in, source="surplus", **{f"topup_{huge}": "100"})
    assert _snapshot() == before


def test_redirect_cannot_top_up_another_users_goal(
    logged_in, demo_id, example, theirs
):
    before = _snapshot()
    _post_redirect(logged_in, source="surplus", **{f"topup_{theirs.goal}": "100"})
    assert _snapshot() == before
    assert len(queries.list_top_ups(theirs.user)) == 1


# --------------------------------------------------------------------- #
# Savings Pot and Top-ups when Income cannot cover everything            #
# --------------------------------------------------------------------- #

def test_plan_shows_set_and_funded_amounts_when_the_pot_is_cut(logged_in, demo_id):
    queries.set_income(demo_id, 1950)
    queries.create_obligation(demo_id, "Rent", 1200)
    car = queries.create_goal(demo_id, CAR, 2400, 0, _future(170), "Should-have")
    queries.create_redirect(demo_id, _future(0), [(car, 300)], 100)
    body = _plan_body(logged_in)
    assert "Savings Pot" in body
    assert "€100.00" in body  # set
    assert "€50.00" in body  # funded
    assert _top_up_status_shown(body, "active")


def test_plan_pauses_a_top_up_with_a_warning_before_any_goal_is_unreachable(
    logged_in, demo_id
):
    queries.set_income(demo_id, 1650)
    queries.create_obligation(demo_id, "Rent", 1200)
    car = queries.create_goal(demo_id, CAR, 2400, 0, _future(170), "Should-have")
    queries.create_redirect(demo_id, _future(0), [(car, 300)])
    body = _plan_body(logged_in)
    assert _top_up_status_shown(body, "paused")
    assert "unreachable goal" not in body.lower()


# --------------------------------------------------------------------- #
# Fully funded banner                                                    #
# --------------------------------------------------------------------- #

def test_plan_shows_banner_for_a_fully_funded_goal_with_redirect_link(
    logged_in, demo_id
):
    queries.set_income(demo_id, 5000)
    queries.create_goal(demo_id, "DoneZZ", 500, 500, _future(90), "Should-have")
    body = _plan_body(logged_in)
    assert "DoneZZ is fully funded" in body
    assert _surplus_links(body)


def test_plan_shows_a_banner_for_each_fully_funded_goal(logged_in, demo_id):
    queries.set_income(demo_id, 5000)
    queries.create_goal(demo_id, "DoneOneZZ", 500, 500, _future(90), "Should-have")
    queries.create_goal(demo_id, "DoneTwoZZ", 300, 400, _future(90), "Should-have")
    body = _plan_body(logged_in)
    assert "DoneOneZZ is fully funded" in body
    assert "DoneTwoZZ is fully funded" in body


def test_plan_has_no_fully_funded_banner_when_no_goal_is_fully_funded(
    logged_in, example
):
    assert "is fully funded" not in _plan_body(logged_in)


def test_plan_shows_banner_after_a_lump_sum_completes_a_goal(logged_in, demo_id):
    queries.set_income(demo_id, 5000)
    goal = queries.create_goal(demo_id, "BikeZZ", 1000, 0, _future(90), "Should-have")
    assert "BikeZZ is fully funded" not in _plan_body(logged_in)
    logged_in.post(f"/goals/{goal}/lump-sum", data={"amount": "1000"})
    assert "BikeZZ is fully funded" in _plan_body(logged_in)


# --------------------------------------------------------------------- #
# Lump Sum                                                               #
# --------------------------------------------------------------------- #

@pytest.fixture
def bike(demo_id):
    """Target 1450, Saved 250: 1200 remaining over 6 periods (Installment 200)."""
    queries.set_income(demo_id, 3000)
    return queries.create_goal(demo_id, "BikeZZ", 1450, 250, _future(180), "Should-have")


def test_lump_sum_form_shows_remaining_amount_and_amount_field(logged_in, bike):
    resp = logged_in.get(f"/goals/{bike}/lump-sum")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "€1,200.00" in body
    assert 'name="amount"' in body


def test_lump_sum_form_shows_the_goals_current_installment(logged_in, bike):
    # Spec (Templates, lump_sum.html): "It shows the Goal's remaining amount,
    # its current Installment and the Installment it would have after the
    # Lump Sum." The current Installment (1200 over 6 periods) does not depend
    # on the typed amount, so it can be in the served HTML.
    body = logged_in.get(f"/goals/{bike}/lump-sum").get_data(as_text=True)
    assert "€200.00" in body


def test_lump_sum_get_writes_nothing(logged_in, bike):
    before = _snapshot()
    logged_in.get(f"/goals/{bike}/lump-sum")
    assert _snapshot() == before


def test_lump_sum_of_600_raises_saved_amount_by_600(logged_in, bike):
    resp = logged_in.post(f"/goals/{bike}/lump-sum", data={"amount": "600"})
    assert _is_plan_redirect(resp)
    assert _saved_amount(bike) == pytest.approx(850)


def test_lump_sum_of_600_lowers_the_installment_from_200_to_100(logged_in, bike):
    assert "€200.00" in _plan_body(logged_in)
    logged_in.post(f"/goals/{bike}/lump-sum", data={"amount": "600"})
    body = _plan_body(logged_in)
    assert "€100.00" in body
    assert "€200.00" not in body


def test_lump_sum_keeps_the_deadline(logged_in, demo_id, bike):
    logged_in.post(f"/goals/{bike}/lump-sum", data={"amount": "600"})
    assert queries.get_goal(bike, demo_id)["deadline"] == _future(180)


def test_lump_sum_equal_to_the_remaining_amount_is_allowed(logged_in, bike):
    resp = logged_in.post(f"/goals/{bike}/lump-sum", data={"amount": "1200"})
    assert _is_plan_redirect(resp)
    assert _saved_amount(bike) == pytest.approx(1450)


def test_lump_sum_with_cents_is_added(logged_in, bike):
    logged_in.post(f"/goals/{bike}/lump-sum", data={"amount": "10.50"})
    assert _saved_amount(bike) == pytest.approx(260.50)


def test_two_lump_sums_add_up(logged_in, bike):
    logged_in.post(f"/goals/{bike}/lump-sum", data={"amount": "100"})
    logged_in.post(f"/goals/{bike}/lump-sum", data={"amount": "200"})
    assert _saved_amount(bike) == pytest.approx(550)


@pytest.mark.parametrize(
    "amount",
    ["1200.01", "5000", "", "abc", "0", "0.00", "-5", "0.001", "nan", "inf"],
)
def test_invalid_lump_sum_shows_error_and_saves_nothing(logged_in, bike, amount):
    before = _snapshot()
    resp = logged_in.post(f"/goals/{bike}/lump-sum", data={"amount": amount})
    assert resp.status_code == 200
    assert 'name="amount"' in resp.get_data(as_text=True)
    assert _snapshot() == before


def test_lump_sum_after_a_goal_is_fully_funded_is_refused(logged_in, demo_id):
    queries.set_income(demo_id, 3000)
    goal = queries.create_goal(demo_id, "DoneZZ", 500, 500, _future(90), "Should-have")
    before = _snapshot()
    resp = logged_in.post(f"/goals/{goal}/lump-sum", data={"amount": "1"})
    assert resp.status_code == 200
    assert _snapshot() == before


def test_lump_sum_for_missing_goal_returns_404(logged_in, demo_id):
    assert logged_in.get("/goals/99999/lump-sum").status_code == 404
    before = _snapshot()
    assert (
        logged_in.post("/goals/99999/lump-sum", data={"amount": "10"}).status_code
        == 404
    )
    assert _snapshot() == before


def test_lump_sum_for_another_users_goal_returns_404_and_changes_nothing(
    logged_in, demo_id, theirs
):
    assert logged_in.get(f"/goals/{theirs.goal}/lump-sum").status_code == 404
    before = _snapshot()
    resp = logged_in.post(f"/goals/{theirs.goal}/lump-sum", data={"amount": "10"})
    assert resp.status_code == 404
    assert _snapshot() == before


def test_goals_page_links_to_add_lump_sum_for_goals_not_fully_funded(
    logged_in, demo_id
):
    queries.set_income(demo_id, 3000)
    open_goal = queries.create_goal(demo_id, "OpenZZ", 500, 100, _future(90), "Should-have")
    done_goal = queries.create_goal(demo_id, "DoneZZ", 500, 500, _future(90), "Should-have")
    body = logged_in.get("/goals").get_data(as_text=True)
    assert f"/goals/{open_goal}/lump-sum" in body
    assert f"/goals/{done_goal}/lump-sum" not in body


# --------------------------------------------------------------------- #
# Delete a Top-up                                                        #
# --------------------------------------------------------------------- #

def test_delete_top_up_confirm_page_does_not_delete(logged_in, demo_id, scenario):
    resp = logged_in.get(f"/top-ups/{scenario.top_up}/delete")
    assert resp.status_code == 200
    assert len(queries.list_top_ups(demo_id)) == 1


def test_delete_top_up_post_removes_it_and_redirects_to_plan(
    logged_in, demo_id, scenario
):
    resp = logged_in.post(f"/top-ups/{scenario.top_up}/delete")
    assert _is_plan_redirect(resp)
    assert queries.list_top_ups(demo_id) == []


def test_delete_top_up_leaves_other_top_ups_and_the_pot(logged_in, demo_id, scenario):
    queries.create_redirect(demo_id, _future(0), [(scenario.laptop, 40)])
    logged_in.post(f"/top-ups/{scenario.top_up}/delete")
    remaining = queries.list_top_ups(demo_id)
    assert [t["goal_id"] for t in remaining] == [scenario.laptop]
    assert len(queries.list_pot_amounts(demo_id)) == 1


def test_deleting_a_top_up_restores_surplus_and_freed_date(logged_in, demo_id, example):
    _post_redirect(logged_in, source="surplus", **{f"topup_{example.car}": "300"})
    body = _plan_body(logged_in)
    assert "€0.00" in body and _future(120) in body
    top_up_id = queries.list_top_ups(demo_id)[0]["id"]

    logged_in.post(f"/top-ups/{top_up_id}/delete")

    body = _plan_body(logged_in)
    assert "€300.00" in body
    assert "€0.00" not in body
    assert _future(120) not in body
    assert _future(180) in body


def test_plan_shows_a_delete_link_for_each_top_up(logged_in, scenario):
    body = _plan_body(logged_in)
    assert f"/top-ups/{scenario.top_up}/delete" in body


def test_delete_top_up_missing_id_returns_404(logged_in, demo_id):
    assert logged_in.get("/top-ups/99999/delete").status_code == 404
    assert logged_in.post("/top-ups/99999/delete").status_code == 404


def test_delete_top_up_of_another_user_returns_404_and_keeps_it(
    logged_in, demo_id, theirs
):
    assert logged_in.get(f"/top-ups/{theirs.top_up}/delete").status_code == 404
    assert logged_in.post(f"/top-ups/{theirs.top_up}/delete").status_code == 404
    assert len(queries.list_top_ups(theirs.user)) == 1


# --------------------------------------------------------------------- #
# Delete a Savings Pot amount                                            #
# --------------------------------------------------------------------- #

def test_delete_pot_amount_confirm_page_does_not_delete(logged_in, demo_id, scenario):
    resp = logged_in.get(f"/savings-pot/{scenario.pot}/delete")
    assert resp.status_code == 200
    assert len(queries.list_pot_amounts(demo_id)) == 1


def test_delete_pot_amount_post_removes_only_that_amount(logged_in, demo_id, scenario):
    queries.create_redirect(demo_id, _future(0), [], 25)
    other_amount = queries.list_pot_amounts(demo_id)[1]["id"]
    resp = logged_in.post(f"/savings-pot/{scenario.pot}/delete")
    assert _is_plan_redirect(resp)
    assert [p["id"] for p in queries.list_pot_amounts(demo_id)] == [other_amount]
    assert len(queries.list_top_ups(demo_id)) == 1


def test_delete_pot_amount_missing_id_returns_404(logged_in, demo_id):
    assert logged_in.get("/savings-pot/99999/delete").status_code == 404
    assert logged_in.post("/savings-pot/99999/delete").status_code == 404


def test_delete_pot_amount_of_another_user_returns_404_and_keeps_it(
    logged_in, demo_id, theirs
):
    assert logged_in.get(f"/savings-pot/{theirs.pot}/delete").status_code == 404
    assert logged_in.post(f"/savings-pot/{theirs.pot}/delete").status_code == 404
    assert len(queries.list_pot_amounts(theirs.user)) == 1


def test_top_up_id_is_not_a_pot_amount_id_for_other_users(logged_in, demo_id, theirs):
    # The Pot amount route must not delete a Top-up row of the same id.
    logged_in.post(f"/savings-pot/{theirs.top_up}/delete")
    assert len(queries.list_top_ups(theirs.user)) == 1


# --------------------------------------------------------------------- #
# Deleting a Goal removes its Top-ups                                    #
# --------------------------------------------------------------------- #

def test_deleting_a_goal_removes_its_top_ups(logged_in, demo_id, scenario):
    queries.create_redirect(demo_id, _future(0), [(scenario.laptop, 40)])
    resp = logged_in.post(f"/goals/{scenario.car}/delete")
    assert _is_plan_redirect(resp) or resp.status_code == 302

    remaining = queries.list_top_ups(demo_id)
    assert [t["goal_id"] for t in remaining] == [scenario.laptop]
    conn = db.get_db()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM top_ups WHERE goal_id = ?", (scenario.car,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_plan_still_renders_after_deleting_a_goal_with_top_ups(
    logged_in, demo_id, scenario
):
    logged_in.post(f"/goals/{scenario.car}/delete")
    body = _plan_body(logged_in)
    assert CAR not in body


# --------------------------------------------------------------------- #
# Data isolation                                                         #
# --------------------------------------------------------------------- #

def test_plan_does_not_show_another_users_top_ups_or_pot(logged_in, example, theirs):
    body = _plan_body(logged_in)
    assert "TheirsZZ" not in body
    assert "€123.00" not in body
    assert "€77.00" not in body
    assert "€300.00" in body  # own Surplus is untouched


def test_each_user_sees_only_their_own_top_ups_and_pot(app, demo_id, scenario, theirs):
    a = app.test_client()
    _login(a)
    b = app.test_client()
    _login(b, OTHER_EMAIL, OTHER_PASSWORD)

    body_a = _plan_body(a)
    body_b = _plan_body(b)
    assert f"/top-ups/{scenario.top_up}/delete" in body_a
    assert f"/top-ups/{theirs.top_up}/delete" not in body_a
    assert f"/top-ups/{theirs.top_up}/delete" in body_b
    assert f"/top-ups/{scenario.top_up}/delete" not in body_b
    assert f"/savings-pot/{theirs.pot}/delete" in body_b
    assert f"/savings-pot/{scenario.pot}/delete" not in body_b


def test_redirect_creates_rows_only_for_the_logged_in_user(
    logged_in, demo_id, example, theirs
):
    _post_redirect(logged_in, source="surplus", pot_amount="10")
    assert len(queries.list_pot_amounts(demo_id)) == 1
    assert len(queries.list_pot_amounts(theirs.user)) == 1
    assert len(queries.list_top_ups(theirs.user)) == 1


# --------------------------------------------------------------------- #
# CSRF                                                                   #
# --------------------------------------------------------------------- #

def _csrf_cases(s):
    return {
        "redirect": (
            "/plan/redirect?source=surplus",
            "/plan/redirect",
            {"source": "surplus", "pot_amount": "10"},
        ),
        "delete_top_up": (
            f"/top-ups/{s.top_up}/delete",
            f"/top-ups/{s.top_up}/delete",
            {},
        ),
        "delete_pot": (
            f"/savings-pot/{s.pot}/delete",
            f"/savings-pot/{s.pot}/delete",
            {},
        ),
        "lump_sum": (
            f"/goals/{s.car}/lump-sum",
            f"/goals/{s.car}/lump-sum",
            {"amount": "10"},
        ),
    }


CSRF_ROUTES = ["redirect", "delete_top_up", "delete_pot", "lump_sum"]


@pytest.mark.parametrize("route", CSRF_ROUTES)
def test_post_without_csrf_token_returns_400_and_changes_nothing(
    app, logged_in, scenario, route
):
    app.config["CSRF_ENABLED"] = True
    _, post_url, data = _csrf_cases(scenario)[route]
    before = _snapshot()
    resp = logged_in.post(post_url, data=data)
    assert resp.status_code == 400
    assert _snapshot() == before


@pytest.mark.parametrize("route", CSRF_ROUTES)
def test_post_with_wrong_csrf_token_returns_400_and_changes_nothing(
    app, logged_in, scenario, route
):
    form_url, post_url, data = _csrf_cases(scenario)[route]
    app.config["CSRF_ENABLED"] = True
    logged_in.get(form_url)  # the session now has a real token
    before = _snapshot()
    resp = logged_in.post(post_url, data={**data, "csrf_token": "not-the-token"})
    assert resp.status_code == 400
    assert _snapshot() == before


@pytest.mark.parametrize("route", CSRF_ROUTES)
def test_form_includes_the_csrf_token_and_post_with_it_succeeds(
    app, logged_in, scenario, route
):
    form_url, post_url, data = _csrf_cases(scenario)[route]
    app.config["CSRF_ENABLED"] = True
    page = logged_in.get(form_url)
    assert page.status_code == 200
    with logged_in.session_transaction() as sess:
        token = sess["csrf_token"]
    assert token in page.get_data(as_text=True)
    before = _snapshot()
    resp = logged_in.post(post_url, data={**data, "csrf_token": token})
    assert _is_plan_redirect(resp)
    assert _snapshot() != before


# --------------------------------------------------------------------- #
# Existing pages unaffected                                              #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "url",
    [
        "/profile", "/expenses/add", "/income", "/obligations",
        "/obligations/add", "/goals", "/goals/add", "/analytics", "/plan",
    ],
)
def test_existing_pages_still_render(logged_in, scenario, url):
    assert logged_in.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/", "/login", "/register", "/terms", "/privacy"])
def test_public_pages_still_render(client, url):
    assert client.get(url).status_code == 200


# --------------------------------------------------------------------- #
# Code review fixes                                                      #
# --------------------------------------------------------------------- #


def test_top_ups_are_capped(logged_in, example, demo_id):
    from app import MAX_TOP_UPS

    for _ in range(MAX_TOP_UPS):
        queries.create_redirect(demo_id, _future(0), [(example.car, 0.01)])
    resp = logged_in.post(
        "/plan/redirect", data={"source": "surplus", f"topup_{example.laptop}": "1"}
    )
    assert resp.status_code == 200
    assert f"at most {MAX_TOP_UPS} Top-ups" in resp.get_data(as_text=True)
    assert queries.count_top_ups(demo_id) == MAX_TOP_UPS


def test_savings_pot_amounts_are_capped(logged_in, example, demo_id):
    from app import MAX_POT_AMOUNTS

    for _ in range(MAX_POT_AMOUNTS):
        queries.create_redirect(demo_id, _future(0), [], 0.01)
    resp = logged_in.post("/plan/redirect", data={"source": "surplus", "pot_amount": "1"})
    assert resp.status_code == 200
    assert f"at most {MAX_POT_AMOUNTS} Savings Pot amounts" in resp.get_data(as_text=True)
    assert queries.count_pot_amounts(demo_id) == MAX_POT_AMOUNTS


def test_create_redirect_saves_nothing_when_the_recheck_fails(example, demo_id):
    saved = queries.create_redirect(
        demo_id, _future(0), [(example.car, 100)], 50, still_fits=lambda: False
    )
    assert saved is None
    assert queries.list_top_ups(demo_id) == []
    assert queries.list_pot_amounts(demo_id) == []


def test_create_redirect_with_a_goal_that_is_gone_saves_nothing(theirs, example, demo_id):
    # theirs.goal belongs to another user, so it is "gone" for the demo user;
    # the Savings Pot amount in the same redirect must not be saved either.
    saved = queries.create_redirect(demo_id, _future(0), [(theirs.goal, 100)], 50)
    assert saved is None
    assert queries.list_top_ups(demo_id) == []
    assert queries.list_pot_amounts(demo_id) == []


def test_the_plan_stays_fast_with_many_goals_and_top_ups():
    import time

    goals = [
        _g(i, 1000 + i, days=30 * (i % 600 + 1), name=f"G{i}") for i in range(1, 101)
    ]
    top_ups = [
        {"id": j, "goal_id": j % 100 + 1, "amount": 1,
         "start_date": (TODAY + timedelta(days=30 * (j % 50))).isoformat()}
        for j in range(2000)
    ]
    started = time.perf_counter()
    planner.plan(100000, [], goals, TODAY, top_ups)
    assert time.perf_counter() - started < 1.0


def test_deleting_a_scheduled_top_up_says_its_money_is_free_from_its_start(
    logged_in, example, demo_id
):
    queries.create_redirect(demo_id, _future(180), [(example.laptop, 50)])
    top_up = queries.list_top_ups(demo_id)[0]
    body = logged_in.get(f"/top-ups/{top_up['id']}/delete").get_data(as_text=True)
    assert "has not started yet" in body
    assert "goes back to your Surplus" not in body
