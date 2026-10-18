"""Tests for Step 13: AI Suggestions (Importance and Dependencies).

Derived from .claude/specs/13-ai-suggestions.md, GLOSSARY.md and ADR 0001.
Expected values are computed by hand from the spec. The LLM is never reached:
an autouse fixture replaces suggestions._call_llm with a function that fails
the test, and tests that need a reply patch it explicitly.
"""

import re
from datetime import date, timedelta

import pytest
from werkzeug.security import generate_password_hash

import database.db as db
import planner
import suggestions

DEMO_EMAIL = "demo@sawings.com"
DEMO_PASSWORD = "demo123"
OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "otherpass1"

UNAVAILABLE = "Suggestions are unavailable right now. Choose manually."

# Captured at import time, before the autouse guard replaces it.
REAL_CALL_LLM = suggestions._call_llm


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    def _boom(prompt):
        pytest.fail("suggestions._call_llm was reached without being faked")

    monkeypatch.setattr(suggestions, "_call_llm", _boom)


def _fake(monkeypatch, reply):
    """Replace _call_llm with a fake returning `reply`; return the prompt log."""
    prompts = []

    def _fake_call(prompt):
        prompts.append(prompt)
        return reply

    monkeypatch.setattr(suggestions, "_call_llm", _fake_call)
    return prompts


def _fake_raises(monkeypatch, exc):
    def _raise(prompt):
        raise exc

    monkeypatch.setattr(suggestions, "_call_llm", _raise)


OTHERS = [
    {"id": 1, "name": "Car", "target_amount": 5000, "deadline": "2030-01-01"},
    {"id": 2, "name": "Licence", "target_amount": 300, "deadline": "2029-01-01"},
    {"id": 3, "name": "Trip", "target_amount": 900, "deadline": "2029-06-01"},
]


def _suggest(**kw):
    args = dict(
        name="Insurance",
        target_amount=400,
        deadline="2029-12-01",
        other_goals=OTHERS,
    )
    args.update(kw)
    return suggestions.suggest(**args)


# --------------------------------------------------------------------- #
# Unit: suggestions.suggest                                              #
# --------------------------------------------------------------------- #

def test_suggest_valid_reply_returns_importance_and_depends_on(monkeypatch):
    _fake(monkeypatch, '{"importance": "Must-have", "depends_on": [2]}')
    assert _suggest() == {"importance": "Must-have", "depends_on": [2]}


@pytest.mark.parametrize("level", ["Must-have", "Should-have", "Nice-to-have"])
def test_suggest_accepts_every_importance_level(monkeypatch, level):
    _fake(monkeypatch, '{"importance": "%s", "depends_on": []}' % level)
    assert _suggest() == {"importance": level, "depends_on": []}


def test_suggest_accepts_code_fenced_json(monkeypatch):
    _fake(
        monkeypatch,
        '```json\n{"importance": "Should-have", "depends_on": [1]}\n```',
    )
    assert _suggest() == {"importance": "Should-have", "depends_on": [1]}


def test_suggest_accepts_plain_code_fence(monkeypatch):
    _fake(monkeypatch, '```\n{"importance": "Nice-to-have", "depends_on": []}\n```')
    assert _suggest() == {"importance": "Nice-to-have", "depends_on": []}


@pytest.mark.parametrize("bad", ["Critical", "must-have", "", "Priority", None, 3])
def test_suggest_invalid_importance_gives_none(monkeypatch, bad):
    import json

    _fake(monkeypatch, json.dumps({"importance": bad, "depends_on": [1]}))
    assert _suggest() is None


def test_suggest_missing_importance_gives_none(monkeypatch):
    _fake(monkeypatch, '{"depends_on": [1]}')
    assert _suggest() is None


def test_suggest_drops_ids_not_in_other_goals(monkeypatch):
    _fake(monkeypatch, '{"importance": "Must-have", "depends_on": [2, 99, 7]}')
    assert _suggest() == {"importance": "Must-have", "depends_on": [2]}


def test_suggest_all_ids_invalid_gives_empty_list_not_none(monkeypatch):
    _fake(monkeypatch, '{"importance": "Must-have", "depends_on": [98, 99]}')
    assert _suggest() == {"importance": "Must-have", "depends_on": []}


def test_suggest_with_no_other_goals_drops_every_id(monkeypatch):
    _fake(monkeypatch, '{"importance": "Should-have", "depends_on": [1, 2]}')
    assert _suggest(other_goals=[]) == {
        "importance": "Should-have",
        "depends_on": [],
    }


def test_suggest_drops_non_integer_ids(monkeypatch):
    _fake(
        monkeypatch,
        '{"importance": "Must-have", "depends_on": ["Car", 2.5, null, [1], 3]}',
    )
    assert _suggest() == {"importance": "Must-have", "depends_on": [3]}


def test_suggest_drops_boolean_ids(monkeypatch):
    # True == 1 in Python, but a boolean is not an id.
    _fake(monkeypatch, '{"importance": "Must-have", "depends_on": [true, false, 2]}')
    assert _suggest() == {"importance": "Must-have", "depends_on": [2]}


def test_suggest_removes_duplicate_ids(monkeypatch):
    _fake(monkeypatch, '{"importance": "Must-have", "depends_on": [2, 2, 3, 2]}')
    result = _suggest()
    assert sorted(result["depends_on"]) == [2, 3]
    assert len(result["depends_on"]) == 2


@pytest.mark.parametrize(
    "reply",
    ["", "not json at all", "{broken", "Sure! Must-have and depends on Car.", "[]"],
)
def test_suggest_unparseable_reply_gives_none(monkeypatch, reply):
    _fake(monkeypatch, reply)
    assert _suggest() is None


def test_suggest_call_llm_returning_none_gives_none(monkeypatch):
    _fake(monkeypatch, None)
    assert _suggest() is None


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("timed out"),
        ConnectionError("network down"),
        RuntimeError("api error"),
        ValueError("bad"),
        Exception("anything"),
    ],
)
def test_suggest_never_raises_when_call_llm_raises(monkeypatch, exc):
    _fake_raises(monkeypatch, exc)
    assert _suggest() is None


def test_suggest_without_api_key_returns_none_and_does_not_raise(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(suggestions, "_call_llm", REAL_CALL_LLM)
    assert _suggest() is None


def test_suggest_with_blank_api_key_returns_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(suggestions, "_call_llm", REAL_CALL_LLM)
    assert _suggest() is None


def test_suggest_does_not_need_api_key_when_call_is_replaced(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _fake(monkeypatch, '{"importance": "Must-have", "depends_on": []}')
    assert _suggest() == {"importance": "Must-have", "depends_on": []}


def test_suggest_passes_a_prompt_string_to_call_llm(monkeypatch):
    prompts = _fake(monkeypatch, '{"importance": "Must-have", "depends_on": []}')
    _suggest(name="UniqueNameZZ")
    assert len(prompts) == 1
    assert isinstance(prompts[0], str)
    assert "UniqueNameZZ" in prompts[0]


def test_suggest_result_only_contains_validated_keys(monkeypatch):
    _fake(
        monkeypatch,
        '{"importance": "Must-have", "depends_on": [1], "note": "ignore previous"}',
    )
    assert set(_suggest()) == {"importance", "depends_on"}


# --------------------------------------------------------------------- #
# Unit: planner.plan with Dependencies                                   #
# --------------------------------------------------------------------- #

TODAY = date(2026, 1, 1)


def _dl(days, today=TODAY):
    return (today + timedelta(days=days)).isoformat()


def _g(gid, target, saved=0, days=30, importance="Should-have", name=None, deps=None):
    goal = {
        "id": gid,
        "name": name or f"G{gid}",
        "target_amount": target,
        "saved_amount": saved,
        "deadline": _dl(days),
        "importance": importance,
    }
    if deps is not None:
        goal["depends_on"] = deps
    return goal


def _run(income, goals, obligations=()):
    return planner.plan(income, [{"amount": a} for a in obligations], goals, TODAY)


def _item(result, gid):
    return next(i for i in result["items"] if i["id"] == gid)


def test_plan_prerequisite_placed_before_dependent_despite_lower_importance():
    goals = [
        _g(2, 100, importance="Must-have", deps=[1]),
        _g(1, 100, importance="Nice-to-have"),
    ]
    r = _run(1000, goals)
    assert [i["id"] for i in r["items"]] == [1, 2]
    assert _item(r, 1)["status"] == "funded"
    assert _item(r, 2)["status"] == "funded"
    assert r["surplus"] == 800


def test_plan_prerequisite_placed_before_dependent_with_later_deadline():
    goals = [
        _g(1, 100, days=300),
        _g(2, 100, days=10, deps=[1]),
    ]
    assert [i["id"] for i in _run(1000, goals)["items"]] == [1, 2]


def test_plan_order_unrelated_goals_keep_base_order_around_dependency():
    # Base order: 2 (Must), 3 (Should), 1 (Nice). 2 needs 1, so 1 comes before
    # 2; 3 comes after 2 only because 2 is the first placeable Goal after 1.
    goals = [
        _g(1, 10, importance="Nice-to-have"),
        _g(2, 10, importance="Must-have", deps=[1]),
        _g(3, 10, importance="Should-have"),
    ]
    ids = [i["id"] for i in _run(1000, goals)["items"]]
    assert ids.index(1) < ids.index(2)
    assert set(ids) == {1, 2, 3}


def test_plan_dependent_installment_computed_as_in_step_12():
    # Remaining 1200 over 6 months (180 days) = 200.
    goals = [_g(1, 100), _g(2, 1200, 0, 180, "Must-have", deps=[1])]
    r = _run(5000, goals)
    assert _item(r, 2)["installment"] == 200.00
    assert _item(r, 2)["months_left"] == 6


def test_plan_blocked_dependent_is_unreachable_with_blocked_by_name():
    goals = [
        _g(1, 5000, name="Licence", importance="Nice-to-have"),
        _g(2, 300, name="Car", importance="Must-have", deps=[1]),
    ]
    r = _run(1000, goals)
    car = _item(r, 2)
    assert _item(r, 1)["status"] == "unreachable"
    assert car["status"] == "unreachable"
    assert car["blocked_by"] == "Licence"
    assert car["earliest_date"] is None


def test_plan_blocked_dependent_takes_nothing_from_capacity():
    # Prerequisite 5000 unreachable, dependent 300 blocked, third Goal 200 is
    # funded: Surplus = 1000 - 200 = 800.
    goals = [
        _g(1, 5000, importance="Nice-to-have"),
        _g(2, 300, importance="Must-have", deps=[1]),
        _g(3, 200, importance="Should-have"),
    ]
    r = _run(1000, goals)
    assert _item(r, 3)["status"] == "funded"
    assert r["surplus"] == 800


def test_plan_blocked_dependent_leaves_full_capacity_for_later_goals():
    # Blocked Goal (Must, 900) must not use up capacity of 1000.
    goals = [
        _g(1, 9999, importance="Nice-to-have"),
        _g(2, 900, importance="Must-have", deps=[1]),
        _g(3, 1000, importance="Nice-to-have", days=30),
    ]
    r = _run(1000, goals)
    assert _item(r, 2)["status"] == "unreachable"
    assert _item(r, 3)["status"] == "funded"
    assert r["surplus"] == 0


def test_plan_blocked_by_names_first_unfunded_prerequisite():
    goals = [
        _g(1, 100, name="Alpha", importance="Must-have"),
        _g(2, 9000, name="Beta", importance="Must-have"),
        _g(3, 100, name="Gamma", importance="Must-have", deps=[1, 2]),
    ]
    r = _run(1000, goals)
    assert _item(r, 1)["status"] == "funded"
    assert _item(r, 3)["status"] == "unreachable"
    assert _item(r, 3)["blocked_by"] == "Beta"


def test_plan_chain_blocking_propagates_to_next_dependent():
    goals = [
        _g(1, 9000, name="Root"),
        _g(2, 100, name="Middle", deps=[1]),
        _g(3, 100, name="Leaf", deps=[2]),
    ]
    r = _run(1000, goals)
    assert _item(r, 2)["blocked_by"] == "Root"
    assert _item(r, 3)["status"] == "unreachable"
    assert _item(r, 3)["blocked_by"] == "Middle"
    assert r["surplus"] == 1000


def test_plan_fully_funded_prerequisite_does_not_block():
    goals = [
        _g(1, 500, saved=500, name="Done"),
        _g(2, 300, name="Next", deps=[1]),
    ]
    r = _run(1000, goals)
    assert _item(r, 1)["status"] == "fully_funded"
    assert _item(r, 2)["status"] == "funded"
    assert _item(r, 2)["blocked_by"] is None


def test_plan_fully_funded_prerequisite_blocks_nothing_even_with_zero_available():
    goals = [_g(1, 500, saved=500), _g(2, 300, deps=[1])]
    r = _run(500, goals, [500])
    assert _item(r, 2)["status"] == "unreachable"
    # Unreachable because of money, not because of the Dependency.
    assert _item(r, 2)["blocked_by"] is None


def test_plan_dependent_unreachable_by_money_is_not_blocked():
    # Income 250: prerequisite 200 funded, capacity 50, dependent 100 -> money
    # shortfall; n = ceil(100 / 50) = 2 -> +60 days.
    goals = [
        _g(1, 200, importance="Nice-to-have"),
        _g(2, 100, importance="Must-have", deps=[1]),
    ]
    r = _run(250, goals)
    assert _item(r, 1)["status"] == "funded"
    item = _item(r, 2)
    assert item["status"] == "unreachable"
    assert item["blocked_by"] is None
    assert item["earliest_date"] == _dl(60)


def test_plan_without_dependencies_matches_empty_depends_on():
    base = [
        _g(1, 300, 0, 90, "Nice-to-have"),
        _g(2, 200, 0, 30, "Must-have"),
        _g(3, 100, 0, 60, "Should-have"),
    ]
    with_empty = [dict(g, depends_on=[]) for g in base]

    def _without_depends_on(result):
        # Items echo the input Goal dict, so ignore that passthrough key.
        items = [
            {k: v for k, v in i.items() if k != "depends_on"}
            for i in result["items"]
        ]
        return {**result, "items": items}

    assert _without_depends_on(_run(1000, base)) == _without_depends_on(
        _run(1000, with_empty)
    )


def test_plan_without_dependencies_keeps_step_12_order_and_values():
    goals = [
        _g(1, 600, 0, 30, "Nice-to-have"),
        _g(2, 600, 0, 30, "Must-have"),
    ]
    r = _run(700, goals)
    assert [i["id"] for i in r["items"]] == [2, 1]
    assert _item(r, 2)["status"] == "funded"
    assert _item(r, 1)["status"] == "unreachable"
    assert r["surplus"] == 100


def test_plan_missing_prerequisite_id_is_ignored():
    goals = [_g(1, 300, deps=[999])]
    r = _run(1000, goals)
    assert _item(r, 1)["status"] == "funded"
    assert _item(r, 1)["blocked_by"] is None


def test_plan_cycle_does_not_hang_and_returns_every_goal():
    goals = [_g(1, 100, deps=[2]), _g(2, 100, deps=[1])]
    r = _run(1000, goals)
    assert {i["id"] for i in r["items"]} == {1, 2}


def test_plan_longer_cycle_does_not_hang():
    goals = [_g(1, 100, deps=[3]), _g(2, 100, deps=[1]), _g(3, 100, deps=[2])]
    r = _run(1000, goals)
    assert len(r["items"]) == 3


def test_plan_every_item_has_blocked_by_none_normally():
    goals = [
        _g(1, 100),
        _g(2, 500, saved=500),
        _g(3, 99999),
    ]
    r = _run(1000, goals)
    for item in r["items"]:
        assert "blocked_by" in item
        assert item["blocked_by"] is None


def test_plan_goal_without_depends_on_key_is_treated_as_no_dependencies():
    r = _run(1000, [_g(1, 100)])
    assert r["items"][0]["blocked_by"] is None
    assert r["items"][0]["status"] == "funded"


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


def _add_goal(user_id, name, target=500, saved=0, days=60, importance="Should-have"):
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


def _add_dep(goal_id, prerequisite_id):
    conn = db.get_db()
    try:
        conn.execute(
            "INSERT INTO goal_dependencies (goal_id, depends_on_goal_id)"
            " VALUES (?, ?)",
            (goal_id, prerequisite_id),
        )
        conn.commit()
    finally:
        conn.close()


def _deps():
    conn = db.get_db()
    try:
        return {
            (r[0], r[1])
            for r in conn.execute(
                "SELECT goal_id, depends_on_goal_id FROM goal_dependencies"
            )
        }
    finally:
        conn.close()


def _state():
    conn = db.get_db()
    try:
        return (
            [tuple(r) for r in conn.execute("SELECT * FROM goals ORDER BY id")],
            sorted(
                tuple(r) for r in conn.execute("SELECT * FROM goal_dependencies")
            ),
        )
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


def _form(**over):
    data = {
        "name": "FormGoalZZ",
        "target_amount": "500",
        "saved_amount": "0",
        "deadline": _future(60),
        "importance": "Should-have",
    }
    data.update(over)
    return data


def _checkbox(body, gid):
    return re.search(
        r'<input[^>]*name="depends_on"[^>]*value="%d"[^>]*>' % gid, body
    )


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


# --------------------------------------------------------------------- #
# Route: auth, method, CSRF                                              #
# --------------------------------------------------------------------- #

def test_suggest_redirects_to_login_when_logged_out(client):
    resp = client.post("/goals/suggest", data=_form())
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_get_suggest_returns_405_when_logged_in(logged_in):
    assert logged_in.get("/goals/suggest").status_code == 405


def test_suggest_without_csrf_token_returns_400_and_no_llm_call(
    app, client, monkeypatch
):
    _login(client)
    monkeypatch.setitem(app.config, "CSRF_ENABLED", True)
    # The autouse guard fails the test if the LLM is reached.
    resp = client.post("/goals/suggest", data=_form())
    assert resp.status_code == 400


def test_suggest_with_wrong_csrf_token_returns_400(app, client, monkeypatch):
    _login(client)
    monkeypatch.setitem(app.config, "CSRF_ENABLED", True)
    resp = client.post("/goals/suggest", data=_form(csrf_token="wrong"))
    assert resp.status_code == 400


# --------------------------------------------------------------------- #
# Route: Goal form                                                       #
# --------------------------------------------------------------------- #

def test_add_form_lists_other_goals_as_dependency_checkboxes(logged_in, demo_id):
    a = _add_goal(demo_id, "AlphaZZ")
    b = _add_goal(demo_id, "BetaZZ")
    body = logged_in.get("/goals/add").get_data(as_text=True)
    assert _checkbox(body, a)
    assert _checkbox(body, b)
    assert "AlphaZZ" in body and "BetaZZ" in body
    assert "Depends on" in body


def test_add_form_has_suggest_button_posting_to_suggest_route(logged_in):
    body = logged_in.get("/goals/add").get_data(as_text=True)
    assert "Suggest Importance and Dependencies" in body
    assert 'formaction="/goals/suggest"' in body


def test_add_form_has_no_goal_id_hidden_field(logged_in):
    body = logged_in.get("/goals/add").get_data(as_text=True)
    assert 'name="goal_id"' not in body


def test_form_with_no_other_goals_shows_hint_instead_of_checkboxes(logged_in):
    body = logged_in.get("/goals/add").get_data(as_text=True)
    assert 'name="depends_on"' not in body
    # Wording of the hint is not specified; it must mention other Goals.
    assert "other goals" in body.lower()


def test_edit_form_with_no_other_goals_shows_hint(logged_in, demo_id):
    gid = _add_goal(demo_id, "OnlyOneZZ")
    body = logged_in.get(f"/goals/{gid}/edit").get_data(as_text=True)
    assert 'name="depends_on"' not in body
    assert "other goals" in body.lower()


def test_edit_form_excludes_the_goal_itself_from_dependency_choices(
    logged_in, demo_id
):
    me = _add_goal(demo_id, "MyselfZZ")
    other = _add_goal(demo_id, "SomeoneElseZZ")
    body = logged_in.get(f"/goals/{me}/edit").get_data(as_text=True)
    assert _checkbox(body, me) is None
    assert _checkbox(body, other)


def test_edit_form_has_hidden_goal_id_and_suggest_button(logged_in, demo_id):
    gid = _add_goal(demo_id, "EditMeZZ")
    body = logged_in.get(f"/goals/{gid}/edit").get_data(as_text=True)
    assert re.search(r'name="goal_id"[^>]*value="%d"' % gid, body)
    assert 'formaction="/goals/suggest"' in body


def test_edit_form_checks_existing_dependencies(logged_in, demo_id):
    a = _add_goal(demo_id, "PrereqZZ")
    b = _add_goal(demo_id, "DependentZZ")
    c = _add_goal(demo_id, "UnrelatedZZ")
    _add_dep(b, a)
    body = logged_in.get(f"/goals/{b}/edit").get_data(as_text=True)
    assert "checked" in _checkbox(body, a).group(0)
    assert "checked" not in _checkbox(body, c).group(0)


def test_form_never_lists_another_users_goals(logged_in, demo_id, other_user):
    mine = _add_goal(demo_id, "MineZZ")
    theirs = _add_goal(other_user, "TheirsZZ")
    body = logged_in.get("/goals/add").get_data(as_text=True)
    assert _checkbox(body, mine)
    assert _checkbox(body, theirs) is None
    assert "TheirsZZ" not in body


# --------------------------------------------------------------------- #
# Route: saving Dependencies                                             #
# --------------------------------------------------------------------- #

def test_add_goal_with_dependency_saves_it_and_goals_page_shows_it(
    logged_in, demo_id
):
    prereq = _add_goal(demo_id, "LicenceZZ")
    resp = logged_in.post(
        "/goals/add", data=_form(name="CarZZ", depends_on=[str(prereq)])
    )
    assert resp.status_code == 302
    conn = db.get_db()
    try:
        new_id = conn.execute(
            "SELECT id FROM goals WHERE name = 'CarZZ'"
        ).fetchone()["id"]
    finally:
        conn.close()
    assert _deps() == {(new_id, prereq)}
    body = logged_in.get("/goals").get_data(as_text=True)
    assert "Depends on" in body
    assert "LicenceZZ" in body


def test_goals_page_without_dependencies_has_no_depends_on_text(logged_in, demo_id):
    _add_goal(demo_id, "LonelyZZ")
    assert "Depends on" not in logged_in.get("/goals").get_data(as_text=True)


def test_add_goal_with_several_dependencies_saves_all(logged_in, demo_id):
    a = _add_goal(demo_id, "AZZ")
    b = _add_goal(demo_id, "BZZ")
    logged_in.post(
        "/goals/add", data=_form(name="CZZ", depends_on=[str(a), str(b)])
    )
    conn = db.get_db()
    try:
        cid = conn.execute("SELECT id FROM goals WHERE name='CZZ'").fetchone()["id"]
    finally:
        conn.close()
    assert _deps() == {(cid, a), (cid, b)}


def test_add_goal_without_dependencies_stores_none(logged_in, demo_id):
    logged_in.post("/goals/add", data=_form())
    assert _deps() == set()


def test_add_goal_duplicate_dependency_ids_are_ignored(logged_in, demo_id):
    a = _add_goal(demo_id, "AZZ")
    resp = logged_in.post(
        "/goals/add", data=_form(name="DupZZ", depends_on=[str(a), str(a)])
    )
    assert resp.status_code == 302
    assert len(_deps()) == 1


def test_edit_replaces_old_dependencies(logged_in, demo_id):
    a = _add_goal(demo_id, "AZZ")
    b = _add_goal(demo_id, "BZZ")
    c = _add_goal(demo_id, "CZZ")
    _add_dep(c, a)
    resp = logged_in.post(
        f"/goals/{c}/edit", data=_form(name="CZZ", depends_on=[str(b)])
    )
    assert resp.status_code == 302
    assert _deps() == {(c, b)}


def test_edit_with_nothing_checked_removes_dependencies(logged_in, demo_id):
    a = _add_goal(demo_id, "AZZ")
    c = _add_goal(demo_id, "CZZ")
    _add_dep(c, a)
    resp = logged_in.post(f"/goals/{c}/edit", data=_form(name="CZZ"))
    assert resp.status_code == 302
    assert _deps() == set()


def test_edit_only_replaces_this_goals_dependencies(logged_in, demo_id):
    a = _add_goal(demo_id, "AZZ")
    b = _add_goal(demo_id, "BZZ")
    c = _add_goal(demo_id, "CZZ")
    _add_dep(b, a)
    _add_dep(c, a)
    logged_in.post(f"/goals/{c}/edit", data=_form(name="CZZ"))
    assert _deps() == {(b, a)}


def test_deleting_goal_removes_every_dependency_mentioning_it(logged_in, demo_id):
    a = _add_goal(demo_id, "PrereqZZ")
    b = _add_goal(demo_id, "DependentZZ")
    c = _add_goal(demo_id, "OtherZZ")
    _add_dep(b, a)
    _add_dep(a, c)
    logged_in.post(f"/goals/{a}/delete")
    assert _deps() == set()


def test_deleting_prerequisite_means_dependent_no_longer_shows_it(
    logged_in, demo_id
):
    a = _add_goal(demo_id, "GonePrereqZZ")
    b = _add_goal(demo_id, "StaysZZ")
    _add_dep(b, a)
    assert "GonePrereqZZ" in logged_in.get("/goals").get_data(as_text=True)
    logged_in.post(f"/goals/{a}/delete")
    body = logged_in.get("/goals").get_data(as_text=True)
    assert "StaysZZ" in body
    assert "GonePrereqZZ" not in body
    assert "Depends on" not in body


def test_deleting_dependent_keeps_prerequisite(logged_in, demo_id):
    a = _add_goal(demo_id, "KeepZZ")
    b = _add_goal(demo_id, "DropZZ")
    _add_dep(b, a)
    logged_in.post(f"/goals/{b}/delete")
    assert "KeepZZ" in logged_in.get("/goals").get_data(as_text=True)


# --------------------------------------------------------------------- #
# Route: Dependency validation                                           #
# --------------------------------------------------------------------- #

def test_add_with_foreign_dependency_shows_error_and_saves_nothing(
    logged_in, demo_id, other_user
):
    theirs = _add_goal(other_user, "TheirsZZ")
    before = _state()
    resp = logged_in.post(
        "/goals/add", data=_form(name="SneakyZZ", depends_on=[str(theirs)])
    )
    assert resp.status_code == 200
    assert _state() == before


def test_add_with_missing_dependency_shows_error_and_saves_nothing(
    logged_in, demo_id
):
    before = _state()
    resp = logged_in.post(
        "/goals/add", data=_form(name="GhostZZ", depends_on=["99999"])
    )
    assert resp.status_code == 200
    assert _state() == before


@pytest.mark.parametrize("bad", ["abc", "1.5", "-1", ""])
def test_add_with_malformed_dependency_id_saves_nothing(logged_in, demo_id, bad):
    before = _state()
    resp = logged_in.post(
        "/goals/add", data=_form(name="MalformedZZ", depends_on=[bad])
    )
    assert resp.status_code != 500
    assert _state() == before


def test_edit_with_foreign_dependency_shows_error_and_saves_nothing(
    logged_in, demo_id, other_user
):
    mine = _add_goal(demo_id, "MineZZ")
    theirs = _add_goal(other_user, "TheirsZZ")
    before = _state()
    resp = logged_in.post(
        f"/goals/{mine}/edit",
        data=_form(name="ChangedZZ", depends_on=[str(theirs)]),
    )
    assert resp.status_code == 200
    assert _state() == before


def test_edit_with_missing_dependency_saves_nothing(logged_in, demo_id):
    mine = _add_goal(demo_id, "MineZZ")
    before = _state()
    resp = logged_in.post(
        f"/goals/{mine}/edit", data=_form(name="ChangedZZ", depends_on=["99999"])
    )
    assert resp.status_code == 200
    assert _state() == before


def test_edit_with_self_dependency_shows_error_and_saves_nothing(
    logged_in, demo_id
):
    mine = _add_goal(demo_id, "MineZZ")
    before = _state()
    resp = logged_in.post(
        f"/goals/{mine}/edit", data=_form(name="ChangedZZ", depends_on=[str(mine)])
    )
    assert resp.status_code == 200
    assert _state() == before


def test_edit_that_creates_two_goal_cycle_shows_error_and_saves_nothing(
    logged_in, demo_id
):
    a = _add_goal(demo_id, "AZZ")
    b = _add_goal(demo_id, "BZZ")
    _add_dep(a, b)  # A needs B
    before = _state()
    resp = logged_in.post(
        f"/goals/{b}/edit", data=_form(name="ChangedBZZ", depends_on=[str(a)])
    )  # B needs A would loop
    assert resp.status_code == 200
    assert _state() == before


def test_edit_that_creates_longer_cycle_shows_error_and_saves_nothing(
    logged_in, demo_id
):
    a = _add_goal(demo_id, "AZZ")
    b = _add_goal(demo_id, "BZZ")
    c = _add_goal(demo_id, "CZZ")
    _add_dep(a, b)
    _add_dep(b, c)
    before = _state()
    resp = logged_in.post(
        f"/goals/{c}/edit", data=_form(name="ChangedCZZ", depends_on=[str(a)])
    )
    assert resp.status_code == 200
    assert _state() == before


def test_edit_with_non_cyclic_dependency_in_a_chain_is_accepted(logged_in, demo_id):
    a = _add_goal(demo_id, "AZZ")
    b = _add_goal(demo_id, "BZZ")
    c = _add_goal(demo_id, "CZZ")
    _add_dep(a, b)
    resp = logged_in.post(
        f"/goals/{c}/edit", data=_form(name="CZZ", depends_on=[str(a)])
    )
    assert resp.status_code == 302
    assert (c, a) in _deps()


# --------------------------------------------------------------------- #
# Route: Suggest                                                         #
# --------------------------------------------------------------------- #

def _suggest_post(client, **over):
    return client.post("/goals/suggest", data=_form(**over))


def test_suggest_success_selects_importance_and_checks_dependency(
    logged_in, demo_id, monkeypatch
):
    a = _add_goal(demo_id, "LicenceZZ")
    _fake(monkeypatch, '{"importance": "Must-have", "depends_on": [%d]}' % a)
    resp = _suggest_post(logged_in, name="CarZZ", importance="Nice-to-have",
                         target_amount="777.5", saved_amount="12")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert re.search(r'<option value="Must-have"\s+selected', body)
    assert not re.search(r'<option value="Nice-to-have"\s+selected', body)
    assert "checked" in _checkbox(body, a).group(0)


def test_suggest_success_keeps_other_fields(logged_in, demo_id, monkeypatch):
    a = _add_goal(demo_id, "LicenceZZ")
    _fake(monkeypatch, '{"importance": "Must-have", "depends_on": [%d]}' % a)
    deadline = _future(45)
    body = _suggest_post(
        logged_in, name="CarZZ", target_amount="777.5", saved_amount="12",
        deadline=deadline,
    ).get_data(as_text=True)
    assert "CarZZ" in body
    assert "777.5" in body
    assert 'value="12"' in body
    assert deadline in body


def test_suggest_success_shows_review_notice(logged_in, demo_id, monkeypatch):
    _fake(monkeypatch, '{"importance": "Must-have", "depends_on": []}')
    body = _suggest_post(logged_in).get_data(as_text=True)
    assert "Suggested by AI" in body
    assert "review before saving" in body.lower()
    assert UNAVAILABLE not in body


def test_suggest_saves_nothing(logged_in, demo_id, monkeypatch):
    a = _add_goal(demo_id, "LicenceZZ")
    _fake(monkeypatch, '{"importance": "Must-have", "depends_on": [%d]}' % a)
    before = _state()
    _suggest_post(logged_in, name="NotSavedZZ")
    assert _state() == before


def test_suggest_is_called_only_once_per_click(logged_in, demo_id, monkeypatch):
    prompts = _fake(monkeypatch, '{"importance": "Must-have", "depends_on": []}')
    _suggest_post(logged_in)
    assert len(prompts) == 1


@pytest.mark.parametrize(
    "reply",
    [
        '{"importance": "Critical", "depends_on": []}',
        "this is not json",
        "",
        '{"depends_on": []}',
    ],
)
def test_suggest_bad_reply_shows_unavailable_notice(
    logged_in, demo_id, monkeypatch, reply
):
    _fake(monkeypatch, reply)
    body = _suggest_post(logged_in).get_data(as_text=True)
    assert _suggest_post(logged_in).status_code == 200
    assert UNAVAILABLE in body
    assert "Suggested by AI" not in body


@pytest.mark.parametrize(
    "exc", [TimeoutError("t"), ConnectionError("n"), RuntimeError("api")]
)
def test_suggest_llm_failure_shows_unavailable_notice(
    logged_in, demo_id, monkeypatch, exc
):
    _fake_raises(monkeypatch, exc)
    resp = _suggest_post(logged_in)
    assert resp.status_code == 200
    assert UNAVAILABLE in resp.get_data(as_text=True)


def test_suggest_without_api_key_shows_unavailable_notice(
    logged_in, demo_id, monkeypatch
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(suggestions, "_call_llm", REAL_CALL_LLM)
    resp = _suggest_post(logged_in)
    assert resp.status_code == 200
    assert UNAVAILABLE in resp.get_data(as_text=True)


def test_suggest_failure_keeps_form_fields_and_form_still_saves(
    logged_in, demo_id, monkeypatch
):
    _fake(monkeypatch, "garbage")
    body = _suggest_post(logged_in, name="StillSavesZZ").get_data(as_text=True)
    assert "StillSavesZZ" in body
    resp = logged_in.post("/goals/add", data=_form(name="StillSavesZZ"))
    assert resp.status_code == 302
    assert "StillSavesZZ" in logged_in.get("/goals").get_data(as_text=True)


def test_suggest_keeps_valid_ids_and_drops_invalid_ones(
    logged_in, demo_id, other_user, monkeypatch
):
    mine = _add_goal(demo_id, "MineZZ")
    other_mine = _add_goal(demo_id, "AlsoMineZZ")
    theirs = _add_goal(other_user, "TheirsZZ")
    _fake(
        monkeypatch,
        '{"importance": "Should-have", "depends_on": [%d, %d, 99999]}'
        % (mine, theirs),
    )
    body = _suggest_post(logged_in).get_data(as_text=True)
    assert "checked" in _checkbox(body, mine).group(0)
    assert "checked" not in _checkbox(body, other_mine).group(0)
    assert _checkbox(body, theirs) is None


def test_suggest_never_sends_another_users_goals_to_the_llm(
    logged_in, demo_id, other_user, monkeypatch
):
    _add_goal(demo_id, "MineVisibleZZ")
    _add_goal(other_user, "SecretTheirsZZ")
    prompts = _fake(monkeypatch, '{"importance": "Must-have", "depends_on": []}')
    _suggest_post(logged_in)
    assert "SecretTheirsZZ" not in prompts[0]


def test_suggest_with_blank_name_shows_notice_and_does_not_call_llm(
    logged_in, demo_id
):
    # The autouse guard fails the test if the LLM is reached.
    before = _state()
    resp = _suggest_post(logged_in, name="")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Suggested by AI" not in body
    assert _state() == before


def test_suggest_on_edit_form_excludes_goal_being_edited(
    logged_in, demo_id, monkeypatch
):
    me = _add_goal(demo_id, "StoredNameZZ")
    other = _add_goal(demo_id, "NeighbourZZ")
    prompts = _fake(
        monkeypatch,
        '{"importance": "Must-have", "depends_on": [%d, %d]}' % (me, other),
    )
    resp = _suggest_post(logged_in, name="TypedNameZZ", goal_id=str(me))
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert _checkbox(body, me) is None
    assert "checked" in _checkbox(body, other).group(0)
    assert "StoredNameZZ" not in prompts[0]
    assert "NeighbourZZ" in prompts[0]


def test_suggest_on_edit_form_keeps_hidden_goal_id(logged_in, demo_id, monkeypatch):
    me = _add_goal(demo_id, "MeZZ")
    _fake(monkeypatch, '{"importance": "Must-have", "depends_on": []}')
    body = _suggest_post(logged_in, goal_id=str(me)).get_data(as_text=True)
    assert re.search(r'name="goal_id"[^>]*value="%d"' % me, body)


def test_suggest_with_missing_goal_id_returns_404(logged_in, demo_id):
    assert _suggest_post(logged_in, goal_id="99999").status_code == 404


def test_suggest_with_foreign_goal_id_returns_404(logged_in, demo_id, other_user):
    theirs = _add_goal(other_user, "TheirsZZ")
    assert _suggest_post(logged_in, goal_id=str(theirs)).status_code == 404


# --------------------------------------------------------------------- #
# Route: Plan with Dependencies                                          #
# --------------------------------------------------------------------- #

def _plan_body(client):
    resp = client.get("/plan")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_plan_shows_waiting_for_prerequisite_name(logged_in, demo_id):
    _set_income(demo_id, 1000)
    pre = _add_goal(demo_id, "PrereqZZ", target=5000, days=30)
    dep = _add_goal(demo_id, "DependentZZ", target=300, days=30,
                    importance="Must-have")
    _add_dep(dep, pre)
    body = _plan_body(logged_in)
    assert "Waiting for PrereqZZ" in body


def test_plan_blocked_dependent_takes_nothing_from_surplus(logged_in, demo_id):
    # Prerequisite unreachable (5000), dependent blocked: Surplus stays 1000.
    _set_income(demo_id, 1000)
    pre = _add_goal(demo_id, "PrereqZZ", target=5000, days=30)
    dep = _add_goal(demo_id, "DependentZZ", target=300, days=30)
    _add_dep(dep, pre)
    body = _plan_body(logged_in)
    assert body.count("€1,000.00") >= 2  # Available and Surplus


def test_plan_prerequisite_listed_before_dependent_with_higher_importance(
    logged_in, demo_id
):
    _set_income(demo_id, 5000)
    pre = _add_goal(demo_id, "FirstPrereqZZ", importance="Nice-to-have")
    dep = _add_goal(demo_id, "ThenDependentZZ", importance="Must-have")
    _add_dep(dep, pre)
    body = _plan_body(logged_in)
    assert body.index("FirstPrereqZZ") < body.index("ThenDependentZZ")
    assert "Waiting for" not in body


def test_plan_dependent_installment_computed_normally_after_prerequisite(
    logged_in, demo_id
):
    _set_income(demo_id, 5000)
    pre = _add_goal(demo_id, "PrereqZZ", target=100, days=30)
    dep = _add_goal(demo_id, "DependentZZ", target=1450, saved=250, days=180)
    _add_dep(dep, pre)
    body = _plan_body(logged_in)
    assert "€200.00" in body


def test_plan_fully_funded_prerequisite_does_not_block_dependent(
    logged_in, demo_id
):
    _set_income(demo_id, 5000)
    pre = _add_goal(demo_id, "DonePrereqZZ", target=500, saved=500)
    dep = _add_goal(demo_id, "FreeDependentZZ", target=300)
    _add_dep(dep, pre)
    body = _plan_body(logged_in)
    assert "Waiting for" not in body
    assert "FreeDependentZZ" in body


def test_plan_without_dependencies_has_no_waiting_text(logged_in, demo_id):
    _set_income(demo_id, 1000)
    _add_goal(demo_id, "BigZZ", target=5000, days=30)
    assert "Waiting for" not in _plan_body(logged_in)


def test_plan_page_does_not_call_llm_or_write_dependencies(logged_in, demo_id):
    _set_income(demo_id, 1000)
    pre = _add_goal(demo_id, "PrereqZZ", target=5000)
    dep = _add_goal(demo_id, "DependentZZ", target=300)
    _add_dep(dep, pre)
    before = _state()
    _plan_body(logged_in)
    assert _state() == before


# --------------------------------------------------------------------- #
# Isolation                                                              #
# --------------------------------------------------------------------- #

def test_goals_page_does_not_show_other_users_dependencies(
    logged_in, demo_id, other_user
):
    a = _add_goal(other_user, "TheirPrereqZZ")
    b = _add_goal(other_user, "TheirDependentZZ")
    _add_dep(b, a)
    _add_goal(demo_id, "MineZZ")
    body = logged_in.get("/goals").get_data(as_text=True)
    assert "TheirPrereqZZ" not in body
    assert "TheirDependentZZ" not in body
    assert "Depends on" not in body


def test_plan_page_does_not_show_other_users_waiting_goals(
    logged_in, demo_id, other_user
):
    _set_income(demo_id, 1000)
    _set_income(other_user, 1000)
    a = _add_goal(other_user, "TheirPrereqZZ", target=9000)
    b = _add_goal(other_user, "TheirDependentZZ", target=100)
    _add_dep(b, a)
    body = _plan_body(logged_in)
    assert "TheirPrereqZZ" not in body
    assert "Waiting for" not in body


def test_each_user_saves_dependencies_on_own_goals_only(app, demo_id, other_user):
    mine = _add_goal(demo_id, "MineZZ")
    theirs = _add_goal(other_user, "TheirsZZ")
    a = app.test_client()
    _login(a)
    b = app.test_client()
    _login(b, OTHER_EMAIL, OTHER_PASSWORD)
    assert a.post(
        "/goals/add", data=_form(name="A2ZZ", depends_on=[str(mine)])
    ).status_code == 302
    assert b.post(
        "/goals/add", data=_form(name="B2ZZ", depends_on=[str(theirs)])
    ).status_code == 302
    assert b.post(
        "/goals/add", data=_form(name="B3ZZ", depends_on=[str(mine)])
    ).status_code == 200


# --------------------------------------------------------------------- #
# Database and regression                                                #
# --------------------------------------------------------------------- #

def test_goal_dependencies_table_exists(db_path):
    assert "goal_dependencies" in _tables()


def test_init_db_is_idempotent_and_keeps_goals_and_dependencies(db_path, demo_id):
    a = _add_goal(demo_id, "KeepAZZ")
    b = _add_goal(demo_id, "KeepBZZ")
    _add_dep(b, a)
    db.init_db()
    db.init_db()
    assert _deps() == {(b, a)}
    conn = db.get_db()
    try:
        names = {r["name"] for r in conn.execute("SELECT name FROM goals")}
    finally:
        conn.close()
    assert {"KeepAZZ", "KeepBZZ"} <= names


def test_init_db_adds_table_to_existing_database_and_keeps_goals(db_path, demo_id):
    _add_goal(demo_id, "OldGoalZZ")
    conn = db.get_db()
    try:
        conn.execute("DROP TABLE goal_dependencies")
        conn.commit()
    finally:
        conn.close()
    db.init_db()
    assert "goal_dependencies" in _tables()
    conn = db.get_db()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM goals WHERE name = 'OldGoalZZ'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_dependency_rows_are_unique_per_pair(db_path, demo_id):
    import sqlite3

    a = _add_goal(demo_id, "AZZ")
    b = _add_goal(demo_id, "BZZ")
    _add_dep(b, a)
    with pytest.raises(sqlite3.IntegrityError):
        _add_dep(b, a)


@pytest.mark.parametrize(
    "url",
    [
        "/profile", "/expenses/add", "/income", "/obligations",
        "/obligations/add", "/goals", "/goals/add", "/plan", "/analytics",
    ],
)
def test_existing_pages_still_render(logged_in, url):
    assert logged_in.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/", "/login", "/register", "/terms", "/privacy"])
def test_public_pages_still_render(client, url):
    assert client.get(url).status_code == 200


def test_goal_add_without_dependencies_still_works_without_api_key(
    logged_in, demo_id, monkeypatch
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = logged_in.post("/goals/add", data=_form(name="NoKeyZZ"))
    assert resp.status_code == 302
    assert "NoKeyZZ" in logged_in.get("/goals").get_data(as_text=True)


# --------------------------------------------------------------------- #
# Suggest: input limits, cooldown and goal_id bounds (security review)   #
# --------------------------------------------------------------------- #

def test_suggest_over_long_name_makes_no_llm_call(logged_in, demo_id):
    # The autouse fixture fails the test if the real LLM function is reached.
    body = _suggest_post(logged_in, name="x" * 61).get_data(as_text=True)
    assert "60" in body
    assert "Suggested by AI" not in body


def test_suggest_leaves_out_invalid_target_and_deadline(
    logged_in, demo_id, monkeypatch
):
    prompts = _fake(monkeypatch, '{"importance": "Must-have", "depends_on": []}')
    body = _suggest_post(
        logged_in, name="CarZZ", target_amount="not-a-number", deadline="junk"
    ).get_data(as_text=True)
    assert len(prompts) == 1
    assert "not-a-number" not in prompts[0]
    assert "junk" not in prompts[0]
    assert "Suggested by AI" in body


def test_suggest_cooldown_blocks_a_second_click(
    logged_in, demo_id, app, monkeypatch
):
    monkeypatch.setitem(app.config, "SUGGEST_COOLDOWN_SECONDS", 60)
    prompts = _fake(monkeypatch, '{"importance": "Must-have", "depends_on": []}')
    first = _suggest_post(logged_in).get_data(as_text=True)
    second = _suggest_post(logged_in).get_data(as_text=True)
    assert "Suggested by AI" in first
    assert "wait a moment" in second.lower()
    assert len(prompts) == 1


def test_suggest_huge_goal_id_is_404(logged_in, demo_id):
    resp = logged_in.post(
        "/goals/suggest", data=_form(goal_id=str(2**70))
    )
    assert resp.status_code == 404


def test_privacy_page_mentions_ai_suggestions(client):
    body = client.get("/privacy").get_data(as_text=True)
    assert "Anthropic" in body
