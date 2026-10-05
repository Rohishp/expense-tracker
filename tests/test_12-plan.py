"""Tests for Step 12: Plan.

Derived from .claude/specs/12-plan.md and GLOSSARY.md. Expected values are
computed by hand from the spec's algorithm section. Unit tests call
planner.plan() with a fixed `today`; route tests use the temp DB from
conftest.py and Deadlines relative to date.today().
"""

import ast
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import database.db as db
import planner

DEMO_EMAIL = "demo@spendly.com"
DEMO_PASSWORD = "demo123"
OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "otherpass1"

TODAY = date(2026, 1, 1)
PLANNER_PATH = Path(planner.__file__)


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


def _run(income, goals, obligations=(), today=TODAY):
    return planner.plan(income, [{"amount": a} for a in obligations], goals, today)


def _item(result, gid):
    return next(i for i in result["items"] if i["id"] == gid)


# --------------------------------------------------------------------- #
# Unit: Available                                                        #
# --------------------------------------------------------------------- #

def test_plan_available_is_income_minus_obligations():
    r = _run(2500, [], [600, 400])
    assert r["income"] == 2500
    assert r["obligations_total"] == 1000
    assert r["available"] == 1500


def test_plan_available_with_no_obligations_equals_income():
    assert _run(2000, [])["available"] == 2000


def test_plan_available_rounded_to_two_decimals():
    assert _run(1000, [], [0.1, 0.2])["available"] == 999.70


def test_plan_no_goals_gives_empty_items_and_surplus_equals_available():
    r = _run(2500, [], [1000])
    assert r["items"] == []
    assert r["surplus"] == 1500
    assert r["overcommitted"] is False


def test_plan_returns_documented_keys():
    r = _run(1000, [_g(1, 100)])
    assert {
        "income", "obligations_total", "available", "items", "surplus",
        "overcommitted",
    } <= set(r)


def test_plan_items_carry_goal_fields_and_computed_fields():
    r = _run(1000, [_g(7, 100, 0, 30, "Must-have", name="Bike")])
    item = r["items"][0]
    assert item["id"] == 7
    assert item["name"] == "Bike"
    assert item["importance"] == "Must-have"
    assert item["deadline"] == _dl(30)
    assert {"installment", "months_left", "status", "earliest_date"} <= set(item)


# --------------------------------------------------------------------- #
# Unit: months left                                                      #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "days, months",
    [
        (180, 6),
        (60, 2),
        (31, 2),
        (30, 1),
        (29, 1),
        (10, 1),
        (1, 1),
        (0, 1),
        (-10, 1),
        (-400, 1),
        (61, 3),
        (90, 3),
        (91, 4),
    ],
)
def test_plan_months_left(days, months):
    r = _run(100000, [_g(1, 1000, 0, days)])
    assert r["items"][0]["months_left"] == months


def test_plan_deadline_under_30_days_uses_one_month_for_installment():
    r = _run(1000, [_g(1, 300, 0, 10)])
    assert r["items"][0]["installment"] == 300.00


def test_plan_past_deadline_uses_one_month_for_installment():
    r = _run(1000, [_g(1, 300, 0, -5)])
    item = r["items"][0]
    assert item["installment"] == 300.00
    assert item["status"] == "funded"


# --------------------------------------------------------------------- #
# Unit: Installment                                                      #
# --------------------------------------------------------------------- #

def test_plan_installment_is_remaining_over_months_left():
    r = _run(5000, [_g(1, 1400, 200, 180)])
    assert r["items"][0]["installment"] == 200.00


def test_plan_installment_rounded_up_to_next_cent():
    r = _run(5000, [_g(1, 100, 0, 90)])
    assert r["items"][0]["installment"] == 33.34


def test_plan_installment_rounds_up_even_for_tiny_fraction():
    # 1000.01 / 2 months = 500.005 -> 500.01
    r = _run(5000, [_g(1, 1000.01, 0, 60)])
    assert r["items"][0]["installment"] == 500.01


def test_plan_installment_exact_division_is_not_bumped():
    r = _run(5000, [_g(1, 90, 0, 90)])
    assert r["items"][0]["installment"] == 30.00


def test_plan_installment_exact_cents_not_bumped_by_float_noise():
    # 0.30 / 3 = 0.10 exactly in cents
    r = _run(5000, [_g(1, 0.30, 0, 90)])
    assert r["items"][0]["installment"] == 0.10


def test_plan_installment_uses_remaining_not_target():
    r = _run(5000, [_g(1, 1000, 400, 60)])
    assert r["items"][0]["installment"] == 300.00


# --------------------------------------------------------------------- #
# Unit: fully funded                                                     #
# --------------------------------------------------------------------- #

def test_plan_goal_with_saved_equal_to_target_is_fully_funded():
    r = _run(1000, [_g(1, 500, 500, 60)])
    item = r["items"][0]
    assert item["status"] == "fully_funded"
    assert item["installment"] == 0.00


def test_plan_goal_with_saved_above_target_is_fully_funded():
    item = _run(1000, [_g(1, 500, 600, 60)])["items"][0]
    assert item["status"] == "fully_funded"
    assert item["installment"] == 0.00


def test_plan_fully_funded_goal_takes_nothing_from_surplus():
    r = _run(1000, [_g(1, 500, 500, 60)])
    assert r["surplus"] == 1000


def test_plan_fully_funded_goal_is_fully_funded_even_with_zero_available():
    r = _run(500, [_g(1, 500, 500, 60)], [500])
    assert r["items"][0]["status"] == "fully_funded"
    assert r["items"][0]["earliest_date"] is None


# --------------------------------------------------------------------- #
# Unit: ordering                                                         #
# --------------------------------------------------------------------- #

def test_plan_orders_by_importance_must_should_nice():
    goals = [
        _g(1, 10, 0, 30, "Nice-to-have"),
        _g(2, 10, 0, 30, "Should-have"),
        _g(3, 10, 0, 30, "Must-have"),
    ]
    ids = [i["id"] for i in _run(1000, goals)["items"]]
    assert ids == [3, 2, 1]


def test_plan_importance_beats_earlier_deadline():
    goals = [
        _g(1, 10, 0, 10, "Should-have"),
        _g(2, 10, 0, 300, "Must-have"),
    ]
    ids = [i["id"] for i in _run(1000, goals)["items"]]
    assert ids == [2, 1]


def test_plan_same_importance_orders_by_earliest_deadline():
    goals = [
        _g(1, 10, 0, 200),
        _g(2, 10, 0, 20),
        _g(3, 10, 0, 100),
    ]
    ids = [i["id"] for i in _run(1000, goals)["items"]]
    assert ids == [2, 3, 1]


def test_plan_same_importance_and_deadline_orders_by_lowest_id():
    goals = [_g(5, 10, 0, 50), _g(2, 10, 0, 50), _g(9, 10, 0, 50)]
    ids = [i["id"] for i in _run(1000, goals)["items"]]
    assert ids == [2, 5, 9]


def test_plan_walk_order_decides_who_is_funded_when_money_is_short():
    goals = [
        _g(1, 600, 0, 30, "Nice-to-have"),
        _g(2, 600, 0, 30, "Must-have"),
    ]
    r = _run(700, goals)
    assert _item(r, 2)["status"] == "funded"
    assert _item(r, 1)["status"] == "unreachable"


def test_plan_earlier_deadline_funded_first_within_importance():
    goals = [_g(1, 600, 0, 100), _g(2, 600, 0, 30)]
    r = _run(700, goals)
    assert _item(r, 2)["status"] == "funded"
    assert _item(r, 1)["status"] == "unreachable"


def test_plan_input_order_does_not_change_result():
    goals = [
        _g(1, 300, 0, 90, "Nice-to-have"),
        _g(2, 200, 0, 30, "Must-have"),
        _g(3, 100, 0, 60, "Should-have"),
    ]
    forward = _run(1000, goals)
    backward = _run(1000, list(reversed(goals)))
    assert forward == backward


# --------------------------------------------------------------------- #
# Unit: funding walk                                                     #
# --------------------------------------------------------------------- #

def test_plan_goal_funded_when_installment_below_capacity():
    r = _run(1000, [_g(1, 300, 0, 30)])
    assert r["items"][0]["status"] == "funded"
    assert r["items"][0]["earliest_date"] is None


def test_plan_goal_funded_when_installment_exactly_equals_capacity():
    r = _run(300, [_g(1, 300, 0, 30)])
    assert r["items"][0]["status"] == "funded"
    assert r["surplus"] == 0


def test_plan_goal_unreachable_when_installment_one_cent_above_capacity():
    r = _run(300, [_g(1, 300.01, 0, 30)])
    assert r["items"][0]["status"] == "unreachable"


def test_plan_funding_is_all_or_nothing_no_partial_funding():
    r = _run(100, [_g(1, 150, 0, 30)])
    # Not partly funded: the whole 100 stays as Surplus.
    assert r["items"][0]["status"] == "unreachable"
    assert r["surplus"] == 100


def test_plan_funded_goals_consume_capacity_in_order():
    goals = [
        _g(1, 400, 0, 30, "Must-have"),
        _g(2, 400, 0, 30, "Should-have"),
        _g(3, 400, 0, 30, "Nice-to-have"),
    ]
    r = _run(900, goals)
    assert [i["status"] for i in r["items"]] == ["funded", "funded", "unreachable"]
    assert r["surplus"] == 100


def test_plan_unreachable_goal_takes_nothing_and_later_goal_still_funded():
    goals = [
        _g(1, 5000, 0, 30, "Must-have"),
        _g(2, 400, 0, 30, "Should-have"),
    ]
    r = _run(1000, goals)
    assert _item(r, 1)["status"] == "unreachable"
    assert _item(r, 2)["status"] == "funded"
    assert r["surplus"] == 600


def test_plan_unreachable_goal_does_not_block_several_later_goals():
    goals = [
        _g(1, 9000, 0, 30, "Must-have"),
        _g(2, 300, 0, 30, "Should-have"),
        _g(3, 300, 0, 30, "Nice-to-have"),
    ]
    r = _run(700, goals)
    assert [i["status"] for i in r["items"]] == ["unreachable", "funded", "funded"]
    assert r["surplus"] == 100


def test_plan_lower_ranked_goal_unreachable_after_funded_one_uses_remaining_capacity():
    goals = [
        _g(1, 600, 0, 30, "Must-have"),
        _g(2, 600, 0, 30, "Should-have"),
    ]
    r = _run(1000, goals)
    assert _item(r, 1)["status"] == "funded"
    assert _item(r, 2)["status"] == "unreachable"


# --------------------------------------------------------------------- #
# Unit: earliest date                                                    #
# --------------------------------------------------------------------- #

def test_plan_earliest_date_is_today_plus_n_periods_of_30_days():
    # remaining 150, capacity 100 -> n = ceil(1.5) = 2 -> +60 days
    r = _run(100, [_g(1, 150, 0, 30)])
    assert r["items"][0]["earliest_date"] == _dl(60)


def test_plan_earliest_date_exact_division_does_not_round_up_further():
    # remaining 200, capacity 100 -> n = 2
    r = _run(100, [_g(1, 200, 0, 30)])
    assert r["items"][0]["earliest_date"] == _dl(60)


def test_plan_earliest_date_for_large_ratio():
    # remaining 1000, capacity 300 -> n = ceil(3.33) = 4 -> +120 days
    r = _run(300, [_g(1, 1000, 0, 30)])
    assert r["items"][0]["earliest_date"] == _dl(120)


def test_plan_earliest_date_uses_remaining_not_target():
    # target 1000, saved 700 -> remaining 300; installment 300 over 1 month
    # capacity 100 -> n = 3 -> +90 days
    r = _run(100, [_g(1, 1000, 700, 30)])
    assert r["items"][0]["earliest_date"] == _dl(90)


def test_plan_earliest_date_uses_capacity_left_at_that_point_in_the_walk():
    # Available 500; Must funded with 300 -> capacity 200.
    # Should remaining 1000 -> n = ceil(1000/200) = 5 -> +150 days.
    goals = [
        _g(1, 300, 0, 30, "Must-have"),
        _g(2, 1000, 0, 30, "Should-have"),
    ]
    r = _run(500, goals)
    assert _item(r, 2)["status"] == "unreachable"
    assert _item(r, 2)["earliest_date"] == _dl(150)


def test_plan_earliest_date_is_iso_string():
    r = _run(100, [_g(1, 150, 0, 30)])
    assert isinstance(r["items"][0]["earliest_date"], str)
    assert date.fromisoformat(r["items"][0]["earliest_date"])


def test_plan_earliest_date_depends_on_today_argument():
    other = date(2027, 6, 15)
    goals = [{**_g(1, 150, 0, 30), "deadline": _dl(30, other)}]
    r = planner.plan(100, [], goals, other)
    assert r["items"][0]["earliest_date"] == (other + timedelta(days=60)).isoformat()


def test_plan_capacity_zero_gives_no_date():
    r = _run(1000, [_g(1, 100, 0, 30)], [1000])
    item = r["items"][0]
    assert item["status"] == "unreachable"
    assert item["earliest_date"] is None


def test_plan_capacity_exhausted_by_earlier_goals_gives_no_date():
    goals = [
        _g(1, 500, 0, 30, "Must-have"),
        _g(2, 100, 0, 30, "Should-have"),
    ]
    r = _run(500, goals)
    assert _item(r, 1)["status"] == "funded"
    assert _item(r, 2)["status"] == "unreachable"
    assert _item(r, 2)["earliest_date"] is None


def test_plan_funded_goal_has_no_earliest_date():
    assert _run(1000, [_g(1, 100, 0, 30)])["items"][0]["earliest_date"] is None


# --------------------------------------------------------------------- #
# Unit: over-committed and Surplus                                       #
# --------------------------------------------------------------------- #

def test_plan_overcommitted_when_obligations_exceed_income():
    r = _run(500, [], [800])
    assert r["overcommitted"] is True
    assert r["available"] == -300
    assert r["surplus"] == 0


def test_plan_not_overcommitted_when_obligations_equal_income():
    r = _run(800, [], [800])
    assert r["overcommitted"] is False
    assert r["available"] == 0
    assert r["surplus"] == 0


def test_plan_overcommitted_makes_every_unfunded_goal_unreachable_without_date():
    goals = [
        _g(1, 100, 0, 30, "Must-have"),
        _g(2, 50, 0, 30, "Nice-to-have"),
    ]
    r = _run(500, goals, [800])
    for item in r["items"]:
        assert item["status"] == "unreachable"
        assert item["earliest_date"] is None
    assert r["surplus"] == 0


def test_plan_overcommitted_still_reports_fully_funded_goals():
    goals = [_g(1, 100, 100, 30), _g(2, 100, 0, 30)]
    r = _run(500, goals, [800])
    assert _item(r, 1)["status"] == "fully_funded"
    assert _item(r, 2)["status"] == "unreachable"


def test_plan_surplus_is_available_minus_funded_installments():
    r = _run(2500, [_g(1, 1200, 0, 180)], [1000])
    assert r["available"] == 1500
    assert r["surplus"] == 1300


def test_plan_surplus_excludes_unreachable_goals():
    goals = [_g(1, 5000, 0, 30), _g(2, 200, 0, 30)]
    assert _run(1000, goals)["surplus"] == 800


def test_plan_surplus_never_negative():
    goals = [_g(1, 500, 0, 30)]
    assert _run(100, goals)["surplus"] >= 0
    assert _run(100, goals, [500])["surplus"] == 0


def test_plan_surplus_zero_when_everything_exactly_funded():
    r = _run(500, [_g(1, 200, 0, 30), _g(2, 300, 0, 30)])
    assert r["surplus"] == 0


# --------------------------------------------------------------------- #
# Unit: determinism and module constraints                               #
# --------------------------------------------------------------------- #

def test_plan_is_deterministic_for_same_inputs():
    goals = [
        _g(1, 300, 10, 77, "Nice-to-have"),
        _g(2, 1200, 0, 180, "Must-have"),
        _g(3, 999.99, 0, 45, "Should-have"),
    ]
    first = _run(1500, goals, [400, 100.5])
    for _ in range(5):
        assert _run(1500, goals, [400, 100.5]) == first


def test_importance_order_matches_importance_levels():
    assert list(planner.IMPORTANCE_ORDER) == list(db.IMPORTANCE_LEVELS)
    assert list(planner.IMPORTANCE_ORDER) == [
        "Must-have", "Should-have", "Nice-to-have",
    ]


def _planner_tree():
    return ast.parse(PLANNER_PATH.read_text(encoding="utf-8"))


def test_planner_does_not_import_flask_or_database():
    modules = []
    for node in ast.walk(_planner_tree()):
        if isinstance(node, ast.Import):
            modules += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    for mod in modules:
        root = mod.split(".")[0]
        assert root not in ("flask", "database"), f"planner imports {mod}"


def test_planner_never_calls_date_today():
    for node in ast.walk(_planner_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("today", "now", "utcnow"):
                pytest.fail("planner.py must take `today` as an argument")


def test_plan_result_does_not_depend_on_real_today(monkeypatch):
    before = _run(100, [_g(1, 150, 0, 30)])

    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(1999, 1, 1)

    monkeypatch.setattr(planner, "date", FakeDate, raising=False)
    assert _run(100, [_g(1, 150, 0, 30)]) == before


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


def _set_income(user_id, amount):
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM incomes WHERE user_id = ?", (user_id,))
        if amount is not None:
            conn.execute(
                "INSERT INTO incomes (user_id, monthly_amount) VALUES (?, ?)",
                (user_id, amount),
            )
        conn.commit()
    finally:
        conn.close()


def _add_obligation(user_id, amount, name="Rent"):
    conn = db.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO obligations (user_id, name, amount) VALUES (?, ?, ?)",
            (user_id, name, amount),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _add_goal(user_id, name, target, saved, days, importance="Should-have"):
    conn = db.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO goals (user_id, name, target_amount, saved_amount,"
            " deadline, importance) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, target, saved, _future(days), importance),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _snapshot():
    conn = db.get_db()
    try:
        return {
            t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")]
            for t in ("users", "expenses", "incomes", "obligations", "goals")
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


def _plan_body(client):
    resp = client.get("/plan")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


# --------------------------------------------------------------------- #
# Route: auth, method, navbar                                            #
# --------------------------------------------------------------------- #

def test_plan_redirects_to_login_when_logged_out(client):
    resp = client.get("/plan")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_plan_page_loads_when_logged_in(logged_in):
    assert logged_in.get("/plan").status_code == 200


def test_post_plan_returns_405(logged_in):
    assert logged_in.post("/plan").status_code == 405


def test_post_plan_returns_405_even_with_data_and_changes_nothing(logged_in, demo_id):
    _set_income(demo_id, 2000)
    before = _snapshot()
    assert logged_in.post("/plan", data={"amount": "1"}).status_code == 405
    assert _snapshot() == before


def _plan_nav_tag(body):
    match = re.search(r'<a[^>]*href="/plan"[^>]*>', body)
    assert match, "no navbar link to /plan"
    return match.group(0)


def test_navbar_has_plan_link_when_logged_in(logged_in):
    body = logged_in.get("/profile").get_data(as_text=True)
    assert 'href="/plan"' in body


def test_navbar_plan_link_comes_after_goals_link(logged_in):
    body = logged_in.get("/profile").get_data(as_text=True)
    assert body.index('href="/goals"') < body.index('href="/plan"')


def test_navbar_plan_link_absent_when_logged_out(client):
    assert 'href="/plan"' not in client.get("/").get_data(as_text=True)


def test_navbar_plan_link_highlighted_on_plan_page(logged_in):
    body = logged_in.get("/plan").get_data(as_text=True)
    assert "active" in _plan_nav_tag(body)


def test_navbar_plan_link_not_highlighted_on_other_pages(logged_in):
    body = logged_in.get("/profile").get_data(as_text=True)
    assert "active" not in _plan_nav_tag(body)


def test_goals_link_not_highlighted_on_plan_page(logged_in):
    body = logged_in.get("/plan").get_data(as_text=True)
    match = re.search(r'<a[^>]*href="/goals"[^>]*>', body)
    assert match and "active" not in match.group(0)


# --------------------------------------------------------------------- #
# Route: empty states                                                    #
# --------------------------------------------------------------------- #

def test_plan_without_income_prompts_to_set_income(logged_in, demo_id):
    _set_income(demo_id, None)
    body = _plan_body(logged_in)
    assert 'href="/income"' in body
    assert "Income" in body


def test_plan_without_income_shows_no_goal_table(logged_in, demo_id):
    _set_income(demo_id, None)
    _add_goal(demo_id, "HiddenGoalZZ", 500, 0, 60)
    body = _plan_body(logged_in)
    assert "<table" not in body
    assert "HiddenGoalZZ" not in body


def test_plan_without_income_shows_no_surplus_or_available_amounts(logged_in, demo_id):
    _set_income(demo_id, None)
    _add_obligation(demo_id, 123)
    body = _plan_body(logged_in)
    assert "Surplus" not in body
    assert "Unreachable" not in body


def test_plan_with_income_and_no_goals_shows_summary_and_goal_prompt(
    logged_in, demo_id
):
    _set_income(demo_id, 2500)
    _add_obligation(demo_id, 1000)
    body = _plan_body(logged_in)
    assert "€2,500.00" in body
    assert "€1,000.00" in body
    assert "€1,500.00" in body
    assert 'href="/goals/add"' in body


def test_plan_with_income_and_no_goals_surplus_equals_available(logged_in, demo_id):
    _set_income(demo_id, 2500)
    _add_obligation(demo_id, 1000)
    body = _plan_body(logged_in)
    assert "Surplus" in body
    # Available and Surplus are both €1,500.00
    assert body.count("€1,500.00") >= 2


def test_plan_with_income_and_no_obligations_available_equals_income(
    logged_in, demo_id
):
    _set_income(demo_id, 2000)
    body = _plan_body(logged_in)
    assert "€2,000.00" in body
    assert "Available" in body


# --------------------------------------------------------------------- #
# Route: worked examples from the Definition of done                     #
# --------------------------------------------------------------------- #

def test_plan_income_2500_obligations_1000_shows_available_1500(logged_in, demo_id):
    _set_income(demo_id, 2500)
    _add_obligation(demo_id, 600, "Rent")
    _add_obligation(demo_id, 400, "Groceries")
    body = _plan_body(logged_in)
    assert "€1,500.00" in body
    assert "€1,000.00" in body


def test_plan_goal_1200_remaining_over_180_days_shows_installment_200(
    logged_in, demo_id
):
    _set_income(demo_id, 5000)
    _add_goal(demo_id, "LaptopZZ", 1450, 250, 180)
    body = _plan_body(logged_in)
    assert "LaptopZZ" in body
    assert "€200.00" in body


def test_plan_goal_installment_rounded_up_to_next_cent(logged_in, demo_id):
    _set_income(demo_id, 5000)
    _add_goal(demo_id, "RoundZZ", 100, 0, 90)
    assert "€33.34" in _plan_body(logged_in)


def test_plan_goal_fully_saved_shows_fully_funded_and_zero_installment(
    logged_in, demo_id
):
    _set_income(demo_id, 5000)
    _add_goal(demo_id, "DoneZZ", 500, 500, 90)
    body = _plan_body(logged_in)
    assert "DoneZZ" in body
    assert "fully funded" in body.lower()
    assert "€0.00" in body


def test_plan_goal_due_in_under_30_days_uses_one_month(logged_in, demo_id):
    _set_income(demo_id, 5000)
    _add_goal(demo_id, "SoonZZ", 333, 3, 10)
    assert "€330.00" in _plan_body(logged_in)


def test_plan_shows_goal_importance_and_deadline(logged_in, demo_id):
    _set_income(demo_id, 5000)
    _add_goal(demo_id, "ColumnsZZ", 500, 0, 77, "Must-have")
    body = _plan_body(logged_in)
    assert "Must-have" in body
    assert _future(77) in body


def test_plan_surplus_line_equals_available_minus_funded_installments(
    logged_in, demo_id
):
    _set_income(demo_id, 2500)
    _add_obligation(demo_id, 1000)
    _add_goal(demo_id, "SurplusZZ", 1450, 250, 180)
    body = _plan_body(logged_in)
    assert "Surplus" in body
    assert "€1,300.00" in body


def test_plan_goals_listed_in_importance_then_deadline_order(logged_in, demo_id):
    _set_income(demo_id, 50000)
    _add_goal(demo_id, "NiceEarlyZZ", 100, 0, 10, "Nice-to-have")
    _add_goal(demo_id, "ShouldZZ", 100, 0, 50, "Should-have")
    _add_goal(demo_id, "MustLateZZ", 100, 0, 300, "Must-have")
    _add_goal(demo_id, "MustEarlyZZ", 100, 0, 20, "Must-have")
    body = _plan_body(logged_in)
    order = [
        body.index(n)
        for n in ("MustEarlyZZ", "MustLateZZ", "ShouldZZ", "NiceEarlyZZ")
    ]
    assert order == sorted(order)


# --------------------------------------------------------------------- #
# Route: Unreachable Goals                                               #
# --------------------------------------------------------------------- #

def test_plan_lower_importance_goal_is_unreachable_when_money_is_short(
    logged_in, demo_id
):
    _set_income(demo_id, 1000)
    _add_goal(demo_id, "MustGoalZZ", 600, 0, 10, "Must-have")
    _add_goal(demo_id, "NiceGoalZZ", 600, 0, 10, "Nice-to-have")
    body = _plan_body(logged_in)
    assert "unreachable goal" in body.lower()
    assert "NiceGoalZZ" in body
    assert body.index("MustGoalZZ") < body.index("NiceGoalZZ")


def test_plan_unreachable_goal_shows_earliest_date(logged_in, demo_id):
    # Available 1000; Must takes 600 -> capacity 400; Nice remaining 600
    # -> n = ceil(600 / 400) = 2 -> today + 60 days.
    _set_income(demo_id, 1000)
    _add_goal(demo_id, "MustGoalZZ", 600, 0, 10, "Must-have")
    _add_goal(demo_id, "NiceGoalZZ", 600, 0, 10, "Nice-to-have")
    assert _future(60) in _plan_body(logged_in)


def test_plan_no_unreachable_warning_when_everything_is_funded(logged_in, demo_id):
    _set_income(demo_id, 5000)
    _add_goal(demo_id, "EasyZZ", 300, 0, 60)
    body = _plan_body(logged_in).lower()
    assert "unreachable goal" not in body
    assert "not reachable with your current income" not in body


def test_plan_unreachable_goal_with_no_capacity_says_not_reachable(
    logged_in, demo_id
):
    _set_income(demo_id, 1000)
    _add_obligation(demo_id, 1000)
    _add_goal(demo_id, "NoRoomZZ", 400, 0, 30)
    body = _plan_body(logged_in)
    assert "not reachable with your current income" in body.lower()


def test_plan_unreachable_goal_with_no_capacity_shows_no_date(logged_in, demo_id):
    _set_income(demo_id, 1000)
    _add_obligation(demo_id, 1000)
    _add_goal(demo_id, "NoRoomZZ", 400, 0, 30)
    body = _plan_body(logged_in)
    for days in range(1, 400):
        assert _future(days * 30) not in body or days * 30 == 30


def test_plan_unreachable_goal_takes_nothing_and_later_goal_is_funded(
    logged_in, demo_id
):
    _set_income(demo_id, 1000)
    _add_goal(demo_id, "BigMustZZ", 5000, 0, 30, "Must-have")
    _add_goal(demo_id, "SmallShouldZZ", 455, 55, 30, "Should-have")
    body = _plan_body(logged_in)
    assert "€400.00" in body  # Installment of the funded Goal
    assert "€600.00" in body  # Surplus = 1000 - 400


# --------------------------------------------------------------------- #
# Route: over-committed                                                  #
# --------------------------------------------------------------------- #

def test_plan_overcommitted_banner_shown_when_obligations_exceed_income(
    logged_in, demo_id
):
    _set_income(demo_id, 500)
    _add_obligation(demo_id, 800)
    body = _plan_body(logged_in).lower()
    # The spec names the banner but not its wording, so check the element.
    assert "plan-banner" in body


def test_plan_no_overcommitted_banner_when_income_covers_obligations(
    logged_in, demo_id
):
    _set_income(demo_id, 2000)
    _add_obligation(demo_id, 800)
    body = _plan_body(logged_in).lower()
    assert "plan-banner" not in body


def test_plan_overcommitted_shows_zero_surplus_and_unreachable_goals_without_date(
    logged_in, demo_id
):
    _set_income(demo_id, 500)
    _add_obligation(demo_id, 800)
    _add_goal(demo_id, "OverGoalZZ", 100, 0, 30)
    body = _plan_body(logged_in)
    assert "€0.00" in body
    assert "unreachable goal" in body.lower()
    assert "not reachable with your current income" in body.lower()


# --------------------------------------------------------------------- #
# Route: live update, read-only, isolation                               #
# --------------------------------------------------------------------- #

def test_plan_updates_when_income_changes(logged_in, demo_id):
    _set_income(demo_id, 2500)
    assert "€2,500.00" in _plan_body(logged_in)
    logged_in.post("/income", data={"amount": "3100"})
    body = _plan_body(logged_in)
    assert "€3,100.00" in body
    assert "€2,500.00" not in body


def test_plan_updates_when_obligation_is_added(logged_in, demo_id):
    _set_income(demo_id, 2500)
    assert "€2,500.00" in _plan_body(logged_in)
    logged_in.post("/obligations/add", data={"name": "Rent", "amount": "700"})
    body = _plan_body(logged_in)
    assert "€700.00" in body
    assert "€1,800.00" in body


def test_plan_updates_when_goal_is_added_and_deleted(logged_in, demo_id):
    _set_income(demo_id, 2500)
    gid = _add_goal(demo_id, "LiveGoalZZ", 500, 0, 60)
    assert "LiveGoalZZ" in _plan_body(logged_in)
    logged_in.post(f"/goals/{gid}/delete")
    assert "LiveGoalZZ" not in _plan_body(logged_in)


def test_plan_updates_when_goal_saved_amount_changes(logged_in, demo_id):
    _set_income(demo_id, 5000)
    gid = _add_goal(demo_id, "EditGoalZZ", 1450, 250, 180)
    assert "€200.00" in _plan_body(logged_in)
    logged_in.post(
        f"/goals/{gid}/edit",
        data={
            "name": "EditGoalZZ",
            "target_amount": "1450",
            "saved_amount": "1450",
            "deadline": _future(180),
            "importance": "Should-have",
        },
    )
    assert "fully funded" in _plan_body(logged_in).lower()


def test_plan_get_writes_nothing_to_database(logged_in, demo_id):
    _set_income(demo_id, 1000)
    _add_obligation(demo_id, 200)
    _add_goal(demo_id, "ReadOnlyZZ", 5000, 0, 30)
    before_tables = _tables()
    before = _snapshot()
    _plan_body(logged_in)
    _plan_body(logged_in)
    assert _snapshot() == before
    assert _tables() == before_tables


def test_plan_does_not_alter_recorded_expenses(logged_in, demo_id):
    _set_income(demo_id, 1000)
    _add_goal(demo_id, "ExpGoalZZ", 500, 0, 30)
    before = _snapshot()["expenses"]
    _plan_body(logged_in)
    assert _snapshot()["expenses"] == before


def test_plan_shows_only_own_data(logged_in, demo_id, other_user):
    _set_income(demo_id, 2500)
    _add_goal(demo_id, "MineZZ", 500, 0, 60)
    _set_income(other_user, 7777)
    _add_obligation(other_user, 4321, "TheirRentZZ")
    _add_goal(other_user, "TheirsZZ", 888, 0, 60)
    body = _plan_body(logged_in)
    assert "MineZZ" in body
    assert "€2,500.00" in body
    assert "TheirsZZ" not in body
    assert "€7,777.00" not in body
    assert "€4,321.00" not in body


def test_each_user_sees_their_own_plan(app, demo_id, other_user):
    _set_income(demo_id, 2500)
    _add_goal(demo_id, "MineZZ", 500, 0, 60)
    _set_income(other_user, 7777)
    _add_goal(other_user, "TheirsZZ", 888, 0, 60)

    a = app.test_client()
    _login(a)
    b = app.test_client()
    _login(b, OTHER_EMAIL, OTHER_PASSWORD)

    body_a = _plan_body(a)
    body_b = _plan_body(b)
    assert "MineZZ" in body_a and "TheirsZZ" not in body_a
    assert "TheirsZZ" in body_b and "MineZZ" not in body_b
    assert "€7,777.00" in body_b and "€7,777.00" not in body_a


def test_plan_for_user_without_income_not_affected_by_other_users_income(
    logged_in, demo_id, other_user
):
    _set_income(demo_id, None)
    _set_income(other_user, 7777)
    body = _plan_body(logged_in)
    assert "€7,777.00" not in body
    assert 'href="/income"' in body


# --------------------------------------------------------------------- #
# Existing pages unaffected                                              #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "url",
    [
        "/profile", "/expenses/add", "/income", "/obligations",
        "/obligations/add", "/goals", "/goals/add", "/analytics",
    ],
)
def test_existing_pages_still_render(logged_in, url):
    assert logged_in.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/", "/login", "/register", "/terms", "/privacy"])
def test_public_pages_still_render(client, url):
    assert client.get(url).status_code == 200


def test_init_db_is_idempotent_after_plan_step(db_path):
    db.init_db()
    db.init_db()
    assert {"users", "expenses", "incomes", "obligations", "goals"} <= _tables()
