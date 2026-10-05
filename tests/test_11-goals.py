"""Tests for Step 11: Goals.

Derived from .claude/specs/11-goals.md. All tests use the temp DB from
conftest.py (db_path/app fixtures).
"""

import re
import sqlite3
from datetime import date, timedelta

import pytest
from werkzeug.security import generate_password_hash

import database.db as db

DEMO_EMAIL = "demo@spendly.com"
DEMO_PASSWORD = "demo123"
OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "otherpass1"
MAX_AMOUNT = 10_000_000
MAX_NAME_LENGTH = 60
MAX_GOALS = 100
LEVELS = ["Must-have", "Should-have", "Nice-to-have"]


def _future(days=30):
    return (date.today() + timedelta(days=days)).isoformat()


def _form(**overrides):
    data = {
        "name": "Laptop",
        "target_amount": "1200",
        "saved_amount": "200",
        "deadline": _future(60),
        "importance": "Should-have",
    }
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


ALL_GET_URLS = [
    "/goals",
    "/goals/add",
    "/goals/1/edit",
    "/goals/1/delete",
]
ALL_POST_URLS = [
    "/goals/add",
    "/goals/1/edit",
    "/goals/1/delete",
]


# --------------------------------------------------------------------- #
# Helpers / fixtures                                                     #
# --------------------------------------------------------------------- #

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


def _insert_goal(
    user_id,
    name="Laptop",
    target=1200.0,
    saved=200.0,
    deadline=None,
    importance="Should-have",
):
    conn = db.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO goals (user_id, name, target_amount, saved_amount,"
            " deadline, importance) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, target, saved, deadline or _future(60), importance),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _goal(gid):
    conn = db.get_db()
    try:
        row = conn.execute("SELECT * FROM goals WHERE id = ?", (gid,)).fetchone()
        return tuple(row) if row else None
    finally:
        conn.close()


def _all_goals():
    conn = db.get_db()
    try:
        return [
            tuple(r) for r in conn.execute("SELECT * FROM goals ORDER BY id")
        ]
    finally:
        conn.close()


def _goals_of(user_id):
    conn = db.get_db()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM goals WHERE user_id = ? ORDER BY id", (user_id,)
            )
        ]
    finally:
        conn.close()


def _count(table):
    conn = db.get_db()
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
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


@pytest.fixture
def gid(demo_id):
    return _insert_goal(demo_id, "Laptop", 1200.0, 200.0, _future(60), "Should-have")


@pytest.fixture
def foreign_gid(other_user):
    return _insert_goal(other_user, "ForeignMarker", 321.0, 21.0, _future(90), "Must-have")


# --------------------------------------------------------------------- #
# Database schema                                                        #
# --------------------------------------------------------------------- #

def test_init_db_creates_goals_table_with_columns(db_path):
    conn = db.get_db()
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(goals)")}
    finally:
        conn.close()
    assert "goals" in names
    assert {
        "id", "user_id", "name", "target_amount", "saved_amount",
        "deadline", "importance", "created_at",
    } <= cols


def test_importance_levels_constant_lists_the_three_levels():
    assert list(db.IMPORTANCE_LEVELS) == LEVELS


def test_init_db_is_idempotent_and_keeps_existing_goals(db_path, demo_id):
    _insert_goal(demo_id)
    users, expenses = _count("users"), _count("expenses")
    db.init_db()
    db.init_db()
    assert _count("goals") == 1
    assert _count("users") == users
    assert _count("expenses") == expenses


def test_init_db_adds_goals_table_to_existing_database(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT"
        " NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,"
        " created_at TEXT DEFAULT (datetime('now')))"
    )
    conn.execute(
        "INSERT INTO users (name, email, password_hash) VALUES ('Old','o@x.com','h')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    assert _count("users") == 1
    assert _count("goals") == 0


def test_seed_db_creates_no_goals(db_path):
    assert _count("goals") == 0


def test_goals_saved_amount_defaults_to_zero_in_schema(db_path, demo_id):
    conn = db.get_db()
    try:
        conn.execute(
            "INSERT INTO goals (user_id, name, target_amount, deadline, importance)"
            " VALUES (?, 'X', 10, ?, 'Must-have')",
            (demo_id, _future()),
        )
        conn.commit()
        saved = conn.execute("SELECT saved_amount FROM goals").fetchone()[0]
    finally:
        conn.close()
    assert saved == 0


# --------------------------------------------------------------------- #
# Auth guards                                                            #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("url", ALL_GET_URLS)
def test_get_redirects_to_login_when_logged_out(client, gid, url):
    resp = client.get(url.replace("/1/", f"/{gid}/"))
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


@pytest.mark.parametrize("url", ALL_POST_URLS)
def test_post_redirects_to_login_when_logged_out_and_changes_nothing(
    client, gid, url
):
    before = _all_goals()
    resp = client.post(url.replace("/1/", f"/{gid}/"), data=_form(name="X"))
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert _all_goals() == before


def test_logged_out_missing_id_still_redirects_to_login(client):
    resp = client.get("/goals/999999/edit")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


# --------------------------------------------------------------------- #
# Navbar                                                                 #
# --------------------------------------------------------------------- #

def test_navbar_has_goals_link_when_logged_in(logged_in):
    body = logged_in.get("/profile").get_data(as_text=True)
    assert 'href="/goals"' in body


def test_navbar_goals_link_comes_after_obligations_link(logged_in):
    body = logged_in.get("/profile").get_data(as_text=True)
    assert body.index('href="/obligations"') < body.index('href="/goals"')


def test_navbar_goals_link_absent_when_logged_out(client):
    body = client.get("/").get_data(as_text=True)
    assert 'href="/goals"' not in body


def test_goals_link_opens_goals_page(logged_in):
    assert logged_in.get("/goals").status_code == 200


def _active_goals_link(body):
    """Return the <a> tag in the navbar pointing at /goals."""
    match = re.search(r'<a[^>]*href="/goals"[^>]*>', body)
    assert match, "no navbar link to /goals"
    return match.group(0)


@pytest.mark.parametrize("path", ["/goals", "/goals/add", "/goals/{gid}/edit",
                                  "/goals/{gid}/delete"])
def test_navbar_goals_link_highlighted_on_every_goal_page(logged_in, gid, path):
    body = logged_in.get(path.format(gid=gid)).get_data(as_text=True)
    assert "active" in _active_goals_link(body)


def test_navbar_goals_link_not_highlighted_on_other_pages(logged_in):
    body = logged_in.get("/profile").get_data(as_text=True)
    assert "active" not in _active_goals_link(body)


# --------------------------------------------------------------------- #
# /goals list and empty state                                            #
# --------------------------------------------------------------------- #

def test_goals_empty_state_prompts_to_add_a_goal(logged_in):
    resp = logged_in.get("/goals")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'href="/goals/add"' in body
    assert "Goal" in body


def test_goals_list_shows_all_fields_and_links(logged_in, demo_id):
    deadline = _future(45)
    gid = _insert_goal(demo_id, "Laptop", 1200.0, 200.0, deadline, "Must-have")
    body = logged_in.get("/goals").get_data(as_text=True)
    assert "Laptop" in body
    assert "€1,200.00" in body
    assert "€200.00" in body
    assert deadline in body
    assert "Must-have" in body
    assert f'href="/goals/{gid}/edit"' in body
    assert f'href="/goals/{gid}/delete"' in body
    assert 'href="/goals/add"' in body


def test_goals_amounts_shown_with_two_decimals(logged_in, demo_id):
    _insert_goal(demo_id, "Phone", 20, 5)
    body = logged_in.get("/goals").get_data(as_text=True)
    assert "€20.00" in body
    assert "€5.00" in body


def test_goals_listed_in_deadline_order_earliest_first(logged_in, demo_id):
    _insert_goal(demo_id, "LateGoalZZ", 100, 0, _future(300))
    _insert_goal(demo_id, "EarlyGoalZZ", 100, 0, _future(10))
    _insert_goal(demo_id, "MidGoalZZ", 100, 0, _future(100))
    body = logged_in.get("/goals").get_data(as_text=True)
    assert (
        body.index("EarlyGoalZZ") < body.index("MidGoalZZ") < body.index("LateGoalZZ")
    )


def test_goals_deadline_order_independent_of_creation_order_via_form(logged_in):
    logged_in.post("/goals/add", data=_form(name="SecondZZ", deadline=_future(200)))
    logged_in.post("/goals/add", data=_form(name="FirstZZ", deadline=_future(5)))
    body = logged_in.get("/goals").get_data(as_text=True)
    assert body.index("FirstZZ") < body.index("SecondZZ")


def test_duplicate_goal_names_are_allowed(logged_in):
    for _ in range(2):
        assert logged_in.post("/goals/add", data=_form()).status_code == 302
    assert len(_all_goals()) == 2


def test_post_to_goals_list_not_allowed(logged_in):
    assert logged_in.post("/goals").status_code == 405


# --------------------------------------------------------------------- #
# GET /goals/add                                                         #
# --------------------------------------------------------------------- #

def test_get_add_goal_form_renders_all_fields(logged_in):
    resp = logged_in.get("/goals/add")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    for field in ("name", "target_amount", "saved_amount", "deadline", "importance"):
        assert f'name="{field}"' in body
    assert 'method="post"' in body.lower()
    for level in LEVELS:
        assert level in body


def test_get_add_goal_preselects_should_have(logged_in):
    body = logged_in.get("/goals/add").get_data(as_text=True)
    match = re.search(r'<option[^>]*value="Should-have"[^>]*>', body)
    assert match and "selected" in match.group(0)
    for other in ("Must-have", "Nice-to-have"):
        m = re.search(rf'<option[^>]*value="{other}"[^>]*>', body)
        assert m and "selected" not in m.group(0)


def test_get_add_goal_does_not_change_data(logged_in):
    logged_in.get("/goals/add")
    assert _all_goals() == []


# --------------------------------------------------------------------- #
# POST /goals/add: happy path                                            #
# --------------------------------------------------------------------- #

def test_post_add_goal_creates_row_and_redirects(logged_in, demo_id):
    deadline = _future(60)
    resp = logged_in.post("/goals/add", data=_form(deadline=deadline))
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/goals")
    rows = _goals_of(demo_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Laptop"
    assert row["target_amount"] == 1200.0
    assert row["saved_amount"] == 200.0
    assert row["deadline"] == deadline
    assert row["importance"] == "Should-have"


def test_post_add_goal_shows_success_message_and_amounts(logged_in):
    resp = logged_in.post("/goals/add", data=_form(), follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Laptop" in body
    assert "€1,200.00" in body
    assert "€200.00" in body
    assert "goal" in body.lower() and "added" in body.lower()


@pytest.mark.parametrize("level", LEVELS)
def test_post_add_goal_accepts_each_importance_level(logged_in, demo_id, level):
    resp = logged_in.post("/goals/add", data=_form(importance=level))
    assert resp.status_code == 302
    assert _goals_of(demo_id)[0]["importance"] == level


def test_post_add_goal_trims_name(logged_in, demo_id):
    logged_in.post("/goals/add", data=_form(name="  Laptop  "))
    assert _goals_of(demo_id)[0]["name"] == "Laptop"


def test_post_add_goal_rounds_amounts_to_two_decimals(logged_in, demo_id):
    logged_in.post(
        "/goals/add", data=_form(target_amount="10.456", saved_amount="1.234")
    )
    row = _goals_of(demo_id)[0]
    assert row["target_amount"] == 10.46
    assert row["saved_amount"] == 1.23


def test_post_add_goal_blank_saved_amount_saves_zero(logged_in, demo_id):
    resp = logged_in.post("/goals/add", data=_form(saved_amount=""))
    assert resp.status_code == 302
    assert _goals_of(demo_id)[0]["saved_amount"] == 0


def test_post_add_goal_missing_saved_amount_field_saves_zero(logged_in, demo_id):
    resp = logged_in.post("/goals/add", data=_form(saved_amount=None))
    assert resp.status_code == 302
    assert _goals_of(demo_id)[0]["saved_amount"] == 0


def test_post_add_goal_whitespace_saved_amount_saves_zero(logged_in, demo_id):
    resp = logged_in.post("/goals/add", data=_form(saved_amount="   "))
    assert resp.status_code == 302
    assert _goals_of(demo_id)[0]["saved_amount"] == 0


def test_post_add_goal_explicit_zero_saved_amount_accepted(logged_in, demo_id):
    resp = logged_in.post("/goals/add", data=_form(saved_amount="0"))
    assert resp.status_code == 302
    assert _goals_of(demo_id)[0]["saved_amount"] == 0


def test_post_add_goal_saved_amount_equal_to_target_accepted(logged_in, demo_id):
    resp = logged_in.post(
        "/goals/add", data=_form(target_amount="500", saved_amount="500")
    )
    assert resp.status_code == 302
    assert _goals_of(demo_id)[0]["saved_amount"] == 500.0


def test_post_add_goal_target_at_max_amount_accepted(logged_in, demo_id):
    resp = logged_in.post(
        "/goals/add", data=_form(target_amount=str(MAX_AMOUNT), saved_amount="")
    )
    assert resp.status_code == 302
    assert _goals_of(demo_id)[0]["target_amount"] == MAX_AMOUNT


def test_post_add_goal_smallest_target_accepted(logged_in, demo_id):
    resp = logged_in.post(
        "/goals/add", data=_form(target_amount="0.01", saved_amount="")
    )
    assert resp.status_code == 302
    assert _goals_of(demo_id)[0]["target_amount"] == 0.01


def test_post_add_goal_name_at_max_length_accepted(logged_in, demo_id):
    name = "a" * MAX_NAME_LENGTH
    resp = logged_in.post("/goals/add", data=_form(name=name))
    assert resp.status_code == 302
    assert _goals_of(demo_id)[0]["name"] == name


def test_post_add_goal_deadline_tomorrow_accepted(logged_in, demo_id):
    resp = logged_in.post("/goals/add", data=_form(deadline=_future(1)))
    assert resp.status_code == 302
    assert len(_goals_of(demo_id)) == 1


def test_new_goal_belongs_to_logged_in_user_only(logged_in, demo_id, other_user):
    logged_in.post("/goals/add", data=_form())
    assert len(_goals_of(demo_id)) == 1
    assert _goals_of(other_user) == []


# --------------------------------------------------------------------- #
# POST /goals/add: validation                                            #
# --------------------------------------------------------------------- #

def _assert_rejected(resp):
    assert resp.status_code == 200
    assert _all_goals() == []
    assert 'name="target_amount"' in resp.get_data(as_text=True)


@pytest.mark.parametrize("name", ["", "   ", "\t", None])
def test_post_add_goal_blank_or_missing_name_rejected(logged_in, name):
    _assert_rejected(logged_in.post("/goals/add", data=_form(name=name)))


def test_post_add_goal_name_over_max_length_rejected(logged_in):
    name = "a" * (MAX_NAME_LENGTH + 1)
    _assert_rejected(logged_in.post("/goals/add", data=_form(name=name)))


@pytest.mark.parametrize(
    "bad",
    ["0", "0.00", "-5", "-0.01", "abc", "nan", "NaN", "inf", "-inf",
     "Infinity", "", "   ", "1e999", str(MAX_AMOUNT + 1), "12abc", None],
)
def test_post_add_goal_invalid_target_rejected(logged_in, bad):
    resp = logged_in.post(
        "/goals/add", data=_form(target_amount=bad, saved_amount="")
    )
    _assert_rejected(resp)


@pytest.mark.parametrize(
    "bad", ["-1", "-0.01", "1200.01", "abc", "nan", "inf", "-inf", "1e999"]
)
def test_post_add_goal_invalid_saved_amount_rejected(logged_in, bad):
    resp = logged_in.post(
        "/goals/add", data=_form(target_amount="1200", saved_amount=bad)
    )
    _assert_rejected(resp)


def test_post_add_goal_saved_amount_greater_than_target_shows_error(logged_in):
    resp = logged_in.post(
        "/goals/add", data=_form(target_amount="100", saved_amount="150")
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Saved Amount" in body


def test_post_add_goal_missing_deadline_rejected(logged_in):
    _assert_rejected(logged_in.post("/goals/add", data=_form(deadline=None)))


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "soon", "2025-13-01", "2025-02-30", "01/02/2030", "2030/01/02",
     "20300102", "2030-1-2x"],
)
def test_post_add_goal_malformed_deadline_rejected(logged_in, bad):
    _assert_rejected(logged_in.post("/goals/add", data=_form(deadline=bad)))


def test_post_add_goal_deadline_today_rejected(logged_in):
    resp = logged_in.post("/goals/add", data=_form(deadline=date.today().isoformat()))
    _assert_rejected(resp)


@pytest.mark.parametrize("days", [-1, -365])
def test_post_add_goal_past_deadline_rejected(logged_in, days):
    _assert_rejected(logged_in.post("/goals/add", data=_form(deadline=_future(days))))


@pytest.mark.parametrize(
    "bad", ["", "Critical", "must-have", "MUST-HAVE", "High", "Should have", None]
)
def test_post_add_goal_invalid_importance_rejected(logged_in, bad):
    _assert_rejected(logged_in.post("/goals/add", data=_form(importance=bad)))


def test_post_add_goal_error_message_is_displayed(logged_in):
    body = logged_in.post(
        "/goals/add", data=_form(name="")
    ).get_data(as_text=True)
    assert "name" in body.lower()
    assert "required" in body.lower() or "must" in body.lower() or "enter" in body.lower()


def test_post_add_goal_error_keeps_entered_values(logged_in):
    deadline = _future(77)
    body = logged_in.post(
        "/goals/add",
        data=_form(name="KeepMeZZ", target_amount="abc", deadline=deadline),
    ).get_data(as_text=True)
    assert "KeepMeZZ" in body
    assert deadline in body


# --------------------------------------------------------------------- #
# 100-Goal cap                                                           #
# --------------------------------------------------------------------- #

def _fill_goals(user_id, n):
    conn = db.get_db()
    try:
        conn.executemany(
            "INSERT INTO goals (user_id, name, target_amount, saved_amount,"
            " deadline, importance) VALUES (?, ?, 10, 0, ?, 'Should-have')",
            [(user_id, f"G{i}", _future(10 + i)) for i in range(n)],
        )
        conn.commit()
    finally:
        conn.close()


def test_adding_100th_goal_is_allowed(logged_in, demo_id):
    _fill_goals(demo_id, MAX_GOALS - 1)
    resp = logged_in.post("/goals/add", data=_form(name="Hundredth"))
    assert resp.status_code == 302
    assert len(_goals_of(demo_id)) == MAX_GOALS


def test_adding_101st_goal_shows_error_and_is_not_saved(logged_in, demo_id):
    _fill_goals(demo_id, MAX_GOALS)
    resp = logged_in.post("/goals/add", data=_form(name="OneTooMany"))
    assert resp.status_code == 200
    assert "100" in resp.get_data(as_text=True)
    rows = _goals_of(demo_id)
    assert len(rows) == MAX_GOALS
    assert all(r["name"] != "OneTooMany" for r in rows)


def test_goal_cap_is_per_user(logged_in, demo_id, other_user):
    _fill_goals(other_user, MAX_GOALS)
    resp = logged_in.post("/goals/add", data=_form())
    assert resp.status_code == 302
    assert len(_goals_of(demo_id)) == 1


def test_deleting_a_goal_frees_a_slot_under_the_cap(logged_in, demo_id):
    _fill_goals(demo_id, MAX_GOALS)
    first = _goals_of(demo_id)[0]["id"]
    assert logged_in.post(f"/goals/{first}/delete").status_code == 302
    assert logged_in.post("/goals/add", data=_form()).status_code == 302
    assert len(_goals_of(demo_id)) == MAX_GOALS


# --------------------------------------------------------------------- #
# Edit                                                                   #
# --------------------------------------------------------------------- #

def test_get_edit_form_is_prefilled(logged_in, demo_id):
    deadline = _future(33)
    gid = _insert_goal(demo_id, "PrefillZZ", 1234.5, 12.5, deadline, "Nice-to-have")
    resp = logged_in.get(f"/goals/{gid}/edit")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "PrefillZZ" in body
    assert "1234.5" in body
    assert "12.5" in body
    assert deadline in body
    m = re.search(r'<option[^>]*value="Nice-to-have"[^>]*>', body)
    assert m and "selected" in m.group(0)


def test_get_edit_does_not_change_data(logged_in, gid):
    before = _goal(gid)
    logged_in.get(f"/goals/{gid}/edit")
    assert _goal(gid) == before


def test_post_edit_updates_all_fields_and_redirects(logged_in, gid, demo_id):
    new_deadline = _future(120)
    resp = logged_in.post(
        f"/goals/{gid}/edit",
        data=_form(
            name="Desktop",
            target_amount="2000",
            saved_amount="350.5",
            deadline=new_deadline,
            importance="Must-have",
        ),
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/goals")
    row = _goals_of(demo_id)[0]
    assert row["id"] == gid
    assert row["name"] == "Desktop"
    assert row["target_amount"] == 2000.0
    assert row["saved_amount"] == 350.5
    assert row["deadline"] == new_deadline
    assert row["importance"] == "Must-have"


def test_post_edit_flashes_success_and_shows_new_values(logged_in, gid):
    resp = logged_in.post(
        f"/goals/{gid}/edit",
        data=_form(name="Desktop", target_amount="2000", saved_amount="350"),
        follow_redirects=True,
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Desktop" in body
    assert "€2,000.00" in body
    assert "updated" in body.lower()


def test_post_edit_blank_saved_amount_saves_zero(logged_in, gid, demo_id):
    logged_in.post(f"/goals/{gid}/edit", data=_form(saved_amount=""))
    assert _goals_of(demo_id)[0]["saved_amount"] == 0


def test_post_edit_does_not_change_owner(logged_in, gid, demo_id):
    logged_in.post(f"/goals/{gid}/edit", data=_form())
    assert _goal(gid)[1] == demo_id


def test_post_edit_only_changes_target_row(logged_in, gid, demo_id):
    other = _insert_goal(demo_id, "Phone", 20.0, 0.0)
    before = _goal(other)
    logged_in.post(f"/goals/{gid}/edit", data=_form(name="Renamed"))
    assert _goal(other) == before


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "a" * (MAX_NAME_LENGTH + 1)},
        {"target_amount": "0"},
        {"target_amount": "-1"},
        {"target_amount": "abc"},
        {"target_amount": "nan"},
        {"target_amount": "inf"},
        {"target_amount": str(MAX_AMOUNT + 1)},
        {"saved_amount": "-1"},
        {"saved_amount": "99999"},
        {"saved_amount": "nan"},
        {"deadline": ""},
        {"deadline": "garbage"},
        {"deadline": date.today().isoformat()},
        {"deadline": (date.today() - timedelta(days=3)).isoformat()},
        {"importance": "Urgent"},
        {"importance": ""},
    ],
)
def test_post_edit_invalid_input_rejected_and_row_unchanged(
    logged_in, gid, overrides
):
    before = _goal(gid)
    resp = logged_in.post(f"/goals/{gid}/edit", data=_form(**overrides))
    assert resp.status_code == 200
    assert _goal(gid) == before


# --------------------------------------------------------------------- #
# Delete                                                                 #
# --------------------------------------------------------------------- #

def test_get_delete_confirmation_summarises_goal(logged_in, demo_id):
    gid = _insert_goal(demo_id, "ConfirmMarker", 123.45, 10.0, _future(50), "Must-have")
    resp = logged_in.get(f"/goals/{gid}/delete")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "ConfirmMarker" in body
    assert "123.45" in body


def test_delete_confirmation_has_post_form_and_cancel_link(logged_in, gid):
    body = logged_in.get(f"/goals/{gid}/delete").get_data(as_text=True)
    assert 'method="post"' in body.lower()
    assert f'action="/goals/{gid}/delete"' in body
    assert "<button" in body.lower()
    assert 'href="/goals"' in body
    assert "Cancel" in body


def test_get_delete_does_not_delete(logged_in, gid):
    before = _all_goals()
    logged_in.get(f"/goals/{gid}/delete")
    logged_in.get(f"/goals/{gid}/delete")
    assert _all_goals() == before


def test_cancel_keeps_goal_listed(logged_in, gid):
    logged_in.get(f"/goals/{gid}/delete")
    body = logged_in.get("/goals").get_data(as_text=True)
    assert "Laptop" in body
    assert _goal(gid) is not None


def test_post_delete_removes_row_and_redirects(logged_in, gid):
    resp = logged_in.post(f"/goals/{gid}/delete")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/goals")
    assert _goal(gid) is None


def test_post_delete_flashes_and_removes_from_list(logged_in, gid, demo_id):
    _insert_goal(demo_id, "StaysZZ", 50.0, 0.0)
    resp = logged_in.post(f"/goals/{gid}/delete", follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "deleted" in body.lower()
    assert "StaysZZ" in body
    assert "Laptop" not in body


def test_post_delete_only_removes_target(logged_in, gid, demo_id):
    other = _insert_goal(demo_id, "Phone", 20.0, 0.0)
    before_other = _goal(other)
    logged_in.post(f"/goals/{gid}/delete")
    assert _goal(other) == before_other


def test_delete_same_goal_twice_second_is_404(logged_in, gid):
    assert logged_in.post(f"/goals/{gid}/delete").status_code == 302
    assert logged_in.post(f"/goals/{gid}/delete").status_code == 404


def test_get_delete_after_delete_is_404(logged_in, gid):
    logged_in.post(f"/goals/{gid}/delete")
    assert logged_in.get(f"/goals/{gid}/delete").status_code == 404


def test_deleting_goal_does_not_touch_expenses_or_users(logged_in, gid):
    expenses, users = _count("expenses"), _count("users")
    logged_in.post(f"/goals/{gid}/delete")
    assert _count("expenses") == expenses
    assert _count("users") == users


# --------------------------------------------------------------------- #
# 404s: missing, malformed and foreign ids                               #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("suffix", ["edit", "delete"])
def test_get_missing_goal_returns_404(logged_in, suffix):
    assert logged_in.get(f"/goals/999999/{suffix}").status_code == 404


@pytest.mark.parametrize("suffix", ["edit", "delete"])
def test_post_missing_goal_returns_404_and_creates_nothing(logged_in, suffix):
    resp = logged_in.post(f"/goals/999999/{suffix}", data=_form())
    assert resp.status_code == 404
    assert _all_goals() == []


@pytest.mark.parametrize("suffix", ["edit", "delete"])
def test_non_integer_id_returns_404(logged_in, suffix):
    assert logged_in.get(f"/goals/abc/{suffix}").status_code == 404


@pytest.mark.parametrize("suffix", ["edit", "delete"])
def test_get_foreign_goal_returns_404_without_leaking(
    logged_in, foreign_gid, suffix
):
    resp = logged_in.get(f"/goals/{foreign_gid}/{suffix}")
    assert resp.status_code == 404
    assert "ForeignMarker" not in resp.get_data(as_text=True)


def test_post_edit_foreign_goal_404_and_unchanged(logged_in, foreign_gid):
    before = _goal(foreign_gid)
    resp = logged_in.post(f"/goals/{foreign_gid}/edit", data=_form(name="Hacked"))
    assert resp.status_code == 404
    assert _goal(foreign_gid) == before


def test_post_delete_foreign_goal_404_and_not_deleted(logged_in, foreign_gid):
    before = _goal(foreign_gid)
    resp = logged_in.post(f"/goals/{foreign_gid}/delete")
    assert resp.status_code == 404
    assert _goal(foreign_gid) == before


def test_post_edit_foreign_goal_with_invalid_data_still_404(logged_in, foreign_gid):
    resp = logged_in.post(f"/goals/{foreign_gid}/edit", data=_form(name=""))
    assert resp.status_code == 404


@pytest.mark.parametrize("suffix", ["edit", "delete"])
def test_foreign_and_missing_ids_respond_identically(logged_in, foreign_gid, suffix):
    foreign = logged_in.get(f"/goals/{foreign_gid}/{suffix}")
    missing = logged_in.get(f"/goals/999999/{suffix}")
    assert foreign.status_code == missing.status_code == 404


def test_other_user_cannot_touch_demo_goal(app, gid, other_user):
    c = app.test_client()
    _login(c, OTHER_EMAIL, OTHER_PASSWORD)
    before = _goal(gid)
    assert c.get(f"/goals/{gid}/edit").status_code == 404
    assert c.post(f"/goals/{gid}/edit", data=_form(name="Hacked")).status_code == 404
    assert c.get(f"/goals/{gid}/delete").status_code == 404
    assert c.post(f"/goals/{gid}/delete").status_code == 404
    assert _goal(gid) == before


# --------------------------------------------------------------------- #
# Data isolation                                                         #
# --------------------------------------------------------------------- #

def test_list_shows_only_own_goals(logged_in, gid, foreign_gid):
    body = logged_in.get("/goals").get_data(as_text=True)
    assert "Laptop" in body
    assert "ForeignMarker" not in body
    assert "€321.00" not in body


def test_other_user_sees_only_their_goals(app, gid, foreign_gid):
    c = app.test_client()
    _login(c, OTHER_EMAIL, OTHER_PASSWORD)
    body = c.get("/goals").get_data(as_text=True)
    assert "ForeignMarker" in body
    assert "€321.00" in body
    assert "Laptop" not in body


def test_new_goal_by_one_user_not_visible_to_other(app, other_user):
    a = app.test_client()
    _login(a)
    a.post("/goals/add", data=_form(name="PrivateZZ"))
    b = app.test_client()
    _login(b, OTHER_EMAIL, OTHER_PASSWORD)
    assert "PrivateZZ" not in b.get("/goals").get_data(as_text=True)


def test_goals_empty_state_for_user_without_goals_even_if_others_have(
    app, foreign_gid
):
    c = app.test_client()
    _login(c)
    body = c.get("/goals").get_data(as_text=True)
    assert "ForeignMarker" not in body
    assert 'href="/goals/add"' in body


# --------------------------------------------------------------------- #
# Method handling                                                        #
# --------------------------------------------------------------------- #

def test_put_not_allowed_on_add(logged_in):
    assert logged_in.put("/goals/add").status_code == 405


@pytest.mark.parametrize("suffix", ["edit", "delete"])
def test_put_not_allowed_on_goal_routes(logged_in, gid, suffix):
    assert logged_in.put(f"/goals/{gid}/{suffix}").status_code == 405


# --------------------------------------------------------------------- #
# Existing flows unaffected                                              #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "url", ["/profile", "/expenses/add", "/obligations", "/income",
            "/obligations/add"]
)
def test_existing_pages_still_work(logged_in, url):
    assert logged_in.get(url).status_code == 200


def test_expense_add_still_works(logged_in):
    before = _count("expenses")
    resp = logged_in.post(
        "/expenses/add",
        data={"amount": "12.50", "category": "Food", "date": "2024-03-01",
              "description": "step11 regression"},
    )
    assert resp.status_code == 302
    assert _count("expenses") == before + 1


def test_obligation_add_still_works(logged_in):
    resp = logged_in.post("/obligations/add", data={"name": "Rent", "amount": "900"})
    assert resp.status_code == 302
    assert _count("obligations") == 1


# --------------------------------------------------------------------- #
# CSRF protection                                                        #
# --------------------------------------------------------------------- #

@pytest.fixture
def csrf_on(app):
    app.config["CSRF_ENABLED"] = True
    yield
    app.config["CSRF_ENABLED"] = False


def _page_token(client, url):
    html = client.get(url).get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html)
    assert match, "form is missing the csrf_token hidden input"
    return match.group(1)


def test_all_goal_forms_carry_csrf_token(csrf_on, logged_in, gid):
    for url in ("/goals/add", f"/goals/{gid}/edit", f"/goals/{gid}/delete"):
        assert _page_token(logged_in, url)


def test_add_goal_post_without_token_rejected(csrf_on, logged_in):
    assert logged_in.post("/goals/add", data=_form()).status_code == 400
    assert _all_goals() == []


def test_add_goal_post_with_page_token_succeeds(csrf_on, logged_in):
    token = _page_token(logged_in, "/goals/add")
    resp = logged_in.post("/goals/add", data=_form(csrf_token=token))
    assert resp.status_code == 302
    assert len(_all_goals()) == 1


def test_edit_goal_post_with_wrong_token_rejected(csrf_on, logged_in, gid):
    before = _goal(gid)
    resp = logged_in.post(
        f"/goals/{gid}/edit", data=_form(name="X", csrf_token="bad")
    )
    assert resp.status_code == 400
    assert _goal(gid) == before


def test_delete_goal_post_without_token_rejected(csrf_on, logged_in, gid):
    assert logged_in.post(f"/goals/{gid}/delete").status_code == 400
    assert _goal(gid) is not None


def test_delete_goal_post_with_page_token_succeeds(csrf_on, logged_in, gid):
    token = _page_token(logged_in, f"/goals/{gid}/delete")
    resp = logged_in.post(f"/goals/{gid}/delete", data={"csrf_token": token})
    assert resp.status_code == 302
    assert _goal(gid) is None


def test_logged_out_goal_post_redirects_before_csrf_check(csrf_on, client):
    resp = client.post("/goals/add", data=_form())
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
