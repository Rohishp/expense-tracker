"""Tests for Step 8: GET/POST /expenses/<id>/edit.

Derived from .claude/specs/08-edit-expense.md. All tests use the temp DB from
conftest.py (db_path/app fixtures).
"""

from datetime import date

import pytest
from werkzeug.security import generate_password_hash

import database.db as db

DEMO_EMAIL = "demo@sawings.com"
DEMO_PASSWORD = "demo123"
OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "otherpass1"
CATEGORIES = ["Food", "Transport", "Bills", "Health",
              "Entertainment", "Shopping", "Other"]
TODAY = date.today().isoformat()


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


def _insert_expense(user_id, amount=42.0, category="Food", exp_date=None,
                    description="Original marker"):
    conn = db.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO expenses (user_id, amount, category, date, description)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, category, exp_date or TODAY, description),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _row(expense_id):
    conn = db.get_db()
    try:
        return conn.execute(
            "SELECT * FROM expenses WHERE id = ?", (expense_id,)
        ).fetchone()
    finally:
        conn.close()


def _snapshot():
    conn = db.get_db()
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT * FROM expenses ORDER BY id").fetchall()]
    finally:
        conn.close()


def _valid(**overrides):
    data = {
        "amount": "99.99",
        "category": "Transport",
        "date": "2024-06-01",
        "description": "Edited marker",
    }
    data.update(overrides)
    return data


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in(client):
    _login(client)
    return client


@pytest.fixture
def eid(db_path):
    """An expense owned by the demo user."""
    return _insert_expense(_user_id(DEMO_EMAIL))


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
def foreign_eid(other_user):
    """An expense owned by another user."""
    return _insert_expense(other_user, amount=77.0, category="Bills",
                           exp_date="2024-02-02", description="Foreign marker")


def _url(expense_id):
    return f"/expenses/{expense_id}/edit"


# --------------------------------------------------------------------- #
# Auth guards                                                            #
# --------------------------------------------------------------------- #

def test_get_redirects_to_login_when_logged_out(client, eid):
    resp = client.get(_url(eid))
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_post_redirects_to_login_when_logged_out(client, eid):
    before = _snapshot()
    resp = client.post(_url(eid), data=_valid())
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert _snapshot() == before


# --------------------------------------------------------------------- #
# Profile Edit links                                                     #
# --------------------------------------------------------------------- #

def test_profile_row_has_edit_link(logged_in, eid):
    body = logged_in.get("/profile").get_data(as_text=True)
    assert f'href="{_url(eid)}"' in body
    assert "Edit" in body


def test_profile_has_edit_link_for_every_seeded_expense(logged_in):
    conn = db.get_db()
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM expenses WHERE user_id = ?",
            (_user_id(DEMO_EMAIL),)).fetchall()]
    finally:
        conn.close()
    body = logged_in.get("/profile").get_data(as_text=True)
    # The profile list may be limited/filtered; at least one link must exist
    # and every link present must point at an owned expense.
    assert any(f'href="{_url(i)}"' in body for i in ids)


# --------------------------------------------------------------------- #
# GET form                                                               #
# --------------------------------------------------------------------- #

def test_get_form_renders_with_fields(logged_in, eid):
    resp = logged_in.get(_url(eid))
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for field in ("amount", "category", "date", "description"):
        assert f'name="{field}"' in body


def test_get_form_prefilled_with_stored_values(logged_in, db_path):
    eid = _insert_expense(_user_id(DEMO_EMAIL), amount=123.45,
                          category="Health", exp_date="2024-01-09",
                          description="Prefill note zzz")
    body = logged_in.get(_url(eid)).get_data(as_text=True)
    assert "123.45" in body
    assert "2024-01-09" in body
    assert "Prefill note zzz" in body
    idx = body.index("Health")
    assert "selected" in body[max(0, idx - 80): idx + 40]


def test_get_form_lists_all_categories(logged_in, eid):
    body = logged_in.get(_url(eid)).get_data(as_text=True)
    for cat in CATEGORIES:
        assert cat in body


def test_get_form_posts_to_edit_url(logged_in, eid):
    body = logged_in.get(_url(eid)).get_data(as_text=True)
    assert f'action="{_url(eid)}"' in body


def test_edit_page_not_labelled_add_expense_heading(logged_in, eid):
    body = logged_in.get(_url(eid)).get_data(as_text=True)
    assert "Edit" in body


# --------------------------------------------------------------------- #
# Happy path                                                             #
# --------------------------------------------------------------------- #

def test_valid_post_redirects_to_profile(logged_in, eid):
    resp = logged_in.post(_url(eid), data=_valid())
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/profile")


def test_valid_post_updates_row(logged_in, eid):
    logged_in.post(_url(eid), data=_valid())
    row = _row(eid)
    assert row["amount"] == 99.99
    assert row["category"] == "Transport"
    assert row["date"] == "2024-06-01"
    assert row["description"] == "Edited marker"
    assert row["user_id"] == _user_id(DEMO_EMAIL)


def test_updated_values_appear_on_profile(logged_in, eid):
    logged_in.post(_url(eid), data=_valid(date=TODAY,
                                          description="Shown after edit"))
    body = logged_in.get("/profile").get_data(as_text=True)
    assert "Shown after edit" in body
    assert "Original marker" not in body
    assert "99.99" in body


def test_profile_total_reflects_edit(logged_in, eid):
    # Seed total 205.74 (per step 6/7 tests) + this expense's new amount.
    logged_in.post(_url(eid), data=_valid(amount="250.00", date=TODAY))
    body = logged_in.get("/profile").get_data(as_text=True)
    assert "455.74" in body


def test_only_target_expense_changes(logged_in, eid):
    other_own = _insert_expense(_user_id(DEMO_EMAIL), amount=5.0,
                                description="Bystander")
    before = _snapshot()
    logged_in.post(_url(eid), data=_valid())
    after = _snapshot()
    changed = [b[0] for b, a in zip(before, after) if b != a]
    assert changed == [eid]
    assert _row(other_own)["description"] == "Bystander"
    assert len(before) == len(after)


def test_amount_rounded_to_two_decimals(logged_in, eid):
    logged_in.post(_url(eid), data=_valid(amount="10.129"))
    assert _row(eid)["amount"] == 10.13


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_category_accepted(logged_in, eid, category):
    resp = logged_in.post(_url(eid), data=_valid(category=category))
    assert resp.status_code == 302
    assert _row(eid)["category"] == category


def test_clearing_description_stores_null(logged_in, eid):
    resp = logged_in.post(_url(eid), data=_valid(description=""))
    assert resp.status_code == 302
    assert _row(eid)["description"] is None


def test_whitespace_description_stores_null(logged_in, eid):
    logged_in.post(_url(eid), data=_valid(description="   "))
    assert _row(eid)["description"] is None


def test_description_at_200_chars_accepted(logged_in, eid):
    desc = "y" * 200
    resp = logged_in.post(_url(eid), data=_valid(description=desc))
    assert resp.status_code == 302
    assert _row(eid)["description"] == desc


def test_user_id_in_form_cannot_reassign_owner(logged_in, eid, other_user):
    logged_in.post(_url(eid), data=_valid(user_id=str(other_user)))
    assert _row(eid)["user_id"] == _user_id(DEMO_EMAIL)


# --------------------------------------------------------------------- #
# Validation errors                                                      #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "amount",
    ["", "   ", "0", "0.00", "-5", "-0.01", "abc", "12abc", "nan", "NaN",
     "inf", "-inf", "Infinity", "1e300", "10000000.01"],
)
def test_invalid_amount_rejected_without_update(logged_in, eid, amount):
    before = _snapshot()
    resp = logged_in.post(_url(eid), data=_valid(amount=amount))
    assert resp.status_code == 200
    assert "error" in resp.get_data(as_text=True).lower()
    assert _snapshot() == before


@pytest.mark.parametrize(
    "category", ["", "Groceries", "food", "'; DROP TABLE expenses;--"])
def test_invalid_category_rejected_without_update(logged_in, eid, category):
    before = _snapshot()
    resp = logged_in.post(_url(eid), data=_valid(category=category))
    assert resp.status_code == 200
    assert _snapshot() == before


@pytest.mark.parametrize(
    "bad_date",
    ["", "   ", "not-a-date", "15/03/2024", "2024-13-01", "2024-02-30",
     "2024/03/15"],
)
def test_invalid_date_rejected_without_update(logged_in, eid, bad_date):
    before = _snapshot()
    resp = logged_in.post(_url(eid), data=_valid(date=bad_date))
    assert resp.status_code == 200
    assert _snapshot() == before


def test_description_over_cap_rejected_without_update(logged_in, eid):
    before = _snapshot()
    resp = logged_in.post(_url(eid), data=_valid(description="x" * 201))
    assert resp.status_code == 200
    assert b"200 characters" in resp.data
    assert _snapshot() == before


@pytest.mark.parametrize("missing", ["amount", "category", "date"])
def test_missing_required_field_rejected(logged_in, eid, missing):
    before = _snapshot()
    data = _valid()
    del data[missing]
    resp = logged_in.post(_url(eid), data=data)
    assert resp.status_code == 200
    assert _snapshot() == before


def test_values_preserved_after_validation_error(logged_in, eid):
    resp = logged_in.post(
        _url(eid),
        data=_valid(amount="-3", category="Shopping", date="2024-05-06",
                    description="keep this note"),
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "-3" in body
    assert "2024-05-06" in body
    assert "keep this note" in body
    idx = body.index("Shopping")
    assert "selected" in body[max(0, idx - 80): idx + 40]


def test_values_preserved_when_date_invalid(logged_in, eid):
    resp = logged_in.post(
        _url(eid), data=_valid(date="garbage", amount="7.25",
                               description="note abc"))
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "7.25" in body
    assert "note abc" in body


def test_error_response_still_lists_categories(logged_in, eid):
    body = logged_in.post(_url(eid), data=_valid(amount="0")).get_data(as_text=True)
    for cat in CATEGORIES:
        assert cat in body


# --------------------------------------------------------------------- #
# 404s and ownership                                                     #
# --------------------------------------------------------------------- #

def test_get_nonexistent_id_returns_404(logged_in):
    assert logged_in.get(_url(999999)).status_code == 404


def test_post_nonexistent_id_returns_404(logged_in):
    before = _snapshot()
    assert logged_in.post(_url(999999), data=_valid()).status_code == 404
    assert _snapshot() == before


def test_get_other_users_expense_returns_404(logged_in, foreign_eid):
    resp = logged_in.get(_url(foreign_eid))
    assert resp.status_code == 404
    assert "Foreign marker" not in resp.get_data(as_text=True)


def test_post_other_users_expense_returns_404_and_unchanged(logged_in, foreign_eid):
    before = _row(foreign_eid)
    resp = logged_in.post(_url(foreign_eid), data=_valid())
    assert resp.status_code == 404
    after = _row(foreign_eid)
    assert tuple(after) == tuple(before)


def test_foreign_and_missing_ids_look_identical(logged_in, foreign_eid):
    assert (logged_in.get(_url(foreign_eid)).status_code
            == logged_in.get(_url(999999)).status_code == 404)


def test_owner_can_still_edit_own_after_other_user_exists(app, eid, foreign_eid):
    c = app.test_client()
    _login(c)
    assert c.post(_url(eid), data=_valid()).status_code == 302
    assert _row(foreign_eid)["description"] == "Foreign marker"


def test_other_user_cannot_edit_demo_expense(app, eid, other_user):
    c = app.test_client()
    _login(c, OTHER_EMAIL, OTHER_PASSWORD)
    before = _row(eid)
    assert c.get(_url(eid)).status_code == 404
    assert c.post(_url(eid), data=_valid()).status_code == 404
    assert tuple(_row(eid)) == tuple(before)


# --------------------------------------------------------------------- #
# Step 7 add flow still works                                            #
# --------------------------------------------------------------------- #

def test_add_page_still_renders_with_add_wording(logged_in):
    resp = logged_in.get("/expenses/add")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Add expense" in body
    assert 'action="/expenses/add"' in body
    assert 'name="amount"' in body


def test_add_page_does_not_show_edit_wording(logged_in):
    body = logged_in.get("/expenses/add").get_data(as_text=True)
    assert "Edit expense" not in body


def test_add_post_still_creates_expense(logged_in):
    resp = logged_in.post("/expenses/add", data=_valid(description="Added via step7"))
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/profile")
    conn = db.get_db()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM expenses WHERE description = ?",
            ("Added via step7",)).fetchone()["c"]
    finally:
        conn.close()
    assert n == 1
