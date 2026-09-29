"""Tests for Step 7: GET/POST /expenses/add.

Derived from .claude/specs/07-add-expense.md. All tests use the temp DB from
conftest.py (db_path/app fixtures).
"""

from datetime import date

import pytest
from werkzeug.security import generate_password_hash

import database.db as db

DEMO_EMAIL = "demo@spendly.com"
DEMO_PASSWORD = "demo123"
CATEGORIES = ["Food", "Transport", "Bills", "Health",
              "Entertainment", "Shopping", "Other"]
URL = "/expenses/add"


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


def _count_expenses():
    conn = db.get_db()
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM expenses").fetchone()["c"]
    finally:
        conn.close()


def _rows(where="1=1", params=()):
    conn = db.get_db()
    try:
        return conn.execute(
            f"SELECT * FROM expenses WHERE {where}", params
        ).fetchall()
    finally:
        conn.close()


def _valid(**overrides):
    data = {
        "amount": "12.50",
        "category": "Food",
        "date": "2024-03-15",
        "description": "Unique lunch marker",
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
def other_user(db_path):
    conn = db.get_db()
    try:
        conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("Other", "other@example.com", generate_password_hash("otherpass1")),
        )
        conn.commit()
    finally:
        conn.close()
    return "other@example.com", "otherpass1"


# --------------------------------------------------------------------- #
# Auth guards                                                            #
# --------------------------------------------------------------------- #

def test_get_redirects_to_login_when_logged_out(client):
    resp = client.get(URL)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_post_redirects_to_login_when_logged_out(client):
    before = _count_expenses()
    resp = client.post(URL, data=_valid())
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert _count_expenses() == before


# --------------------------------------------------------------------- #
# GET form                                                               #
# --------------------------------------------------------------------- #

def test_get_form_renders(logged_in):
    resp = logged_in.get(URL)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for field in ("amount", "category", "date", "description"):
        assert f'name="{field}"' in body


def test_get_form_lists_all_seven_categories(logged_in):
    body = logged_in.get(URL).get_data(as_text=True)
    for cat in CATEGORIES:
        assert cat in body


def test_get_form_prefills_today(logged_in):
    body = logged_in.get(URL).get_data(as_text=True)
    assert date.today().isoformat() in body


def test_get_form_has_cancel_link_to_profile(logged_in):
    body = logged_in.get(URL).get_data(as_text=True)
    assert 'href="/profile"' in body


def test_profile_has_add_expense_link(logged_in):
    body = logged_in.get("/profile").get_data(as_text=True)
    assert f'href="{URL}"' in body
    assert "Add expense" in body


# --------------------------------------------------------------------- #
# Happy path                                                             #
# --------------------------------------------------------------------- #

def test_valid_post_redirects_to_profile(logged_in):
    resp = logged_in.post(URL, data=_valid())
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/profile")


def test_valid_post_inserts_row_under_session_user(logged_in):
    before = _count_expenses()
    logged_in.post(URL, data=_valid())
    assert _count_expenses() == before + 1
    rows = _rows("description = ?", ("Unique lunch marker",))
    assert len(rows) == 1
    row = rows[0]
    assert row["user_id"] == _user_id(DEMO_EMAIL)
    assert row["amount"] == 12.5
    assert row["category"] == "Food"
    assert row["date"] == "2024-03-15"


def test_new_expense_appears_on_profile(logged_in):
    logged_in.post(URL, data=_valid(description="Visible marker xyz"))
    body = logged_in.get("/profile").get_data(as_text=True)
    assert "Visible marker xyz" in body


def test_profile_total_and_count_reflect_new_expense(logged_in):
    # Seed: total 205.74 over 8 transactions (per the step 6 tests).
    logged_in.post(URL, data=_valid(amount="100.00", date=date.today().isoformat()))
    body = logged_in.get("/profile").get_data(as_text=True)
    assert "305.74" in body


def test_amount_rounded_to_two_decimals(logged_in):
    logged_in.post(URL, data=_valid(amount="10.129"))
    row = _rows("description = ?", ("Unique lunch marker",))[0]
    assert row["amount"] == 10.13


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_category_accepted(logged_in, category):
    resp = logged_in.post(URL, data=_valid(category=category))
    assert resp.status_code == 302
    assert _rows("description = ?", ("Unique lunch marker",))[0]["category"] == category


def test_empty_description_accepted_and_stored_null(logged_in):
    resp = logged_in.post(URL, data=_valid(description=""))
    assert resp.status_code == 302
    rows = _rows("date = ? AND amount = ?", ("2024-03-15", 12.5))
    assert len(rows) == 1
    assert rows[0]["description"] is None


def test_whitespace_only_description_stored_null(logged_in):
    logged_in.post(URL, data=_valid(description="    "))
    rows = _rows("date = ? AND amount = ?", ("2024-03-15", 12.5))
    assert len(rows) == 1
    assert rows[0]["description"] is None


def test_description_is_stripped(logged_in):
    logged_in.post(URL, data=_valid(description="   padded note   "))
    rows = _rows("date = ? AND amount = ?", ("2024-03-15", 12.5))
    assert rows[0]["description"] == "padded note"


def test_missing_description_field_accepted(logged_in):
    data = _valid()
    del data["description"]
    resp = logged_in.post(URL, data=data)
    assert resp.status_code == 302


def test_user_id_from_session_not_form(logged_in, other_user):
    other_id = _user_id(other_user[0])
    logged_in.post(URL, data=_valid(user_id=str(other_id)))
    row = _rows("description = ?", ("Unique lunch marker",))[0]
    assert row["user_id"] == _user_id(DEMO_EMAIL)


def test_other_user_does_not_see_expense(app, other_user):
    c1 = app.test_client()
    _login(c1)
    c1.post(URL, data=_valid(description="Private marker qqq"))

    c2 = app.test_client()
    _login(c2, *other_user)
    body = c2.get("/profile").get_data(as_text=True)
    assert "Private marker qqq" not in body


# --------------------------------------------------------------------- #
# Validation errors                                                      #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "amount",
    ["", "   ", "0", "0.00", "-5", "-0.01", "abc", "12abc", "nan", "NaN",
     "inf", "-inf", "Infinity"],
)
def test_invalid_amount_shows_error_and_inserts_nothing(logged_in, amount):
    before = _count_expenses()
    resp = logged_in.post(URL, data=_valid(amount=amount))
    assert resp.status_code == 200
    assert _count_expenses() == before
    assert "error" in resp.get_data(as_text=True).lower()


def test_missing_amount_field_rejected(logged_in):
    before = _count_expenses()
    data = _valid()
    del data["amount"]
    resp = logged_in.post(URL, data=data)
    assert resp.status_code == 200
    assert _count_expenses() == before


@pytest.mark.parametrize("category", ["", "Groceries", "food", "FOOD", "'; DROP TABLE expenses;--"])
def test_invalid_category_rejected(logged_in, category):
    before = _count_expenses()
    resp = logged_in.post(URL, data=_valid(category=category))
    assert resp.status_code == 200
    assert _count_expenses() == before


def test_missing_category_field_rejected(logged_in):
    before = _count_expenses()
    data = _valid()
    del data["category"]
    resp = logged_in.post(URL, data=data)
    assert resp.status_code == 200
    assert _count_expenses() == before


@pytest.mark.parametrize(
    "bad_date",
    ["", "   ", "not-a-date", "15/03/2024", "2024-13-01", "2024-02-30",
     "2024-3-5x", "2024/03/15"],
)
def test_invalid_date_rejected(logged_in, bad_date):
    before = _count_expenses()
    resp = logged_in.post(URL, data=_valid(date=bad_date))
    assert resp.status_code == 200
    assert _count_expenses() == before


def test_missing_date_field_rejected(logged_in):
    before = _count_expenses()
    data = _valid()
    del data["date"]
    resp = logged_in.post(URL, data=data)
    assert resp.status_code == 200
    assert _count_expenses() == before


def test_description_over_cap_is_rejected_and_not_stored(logged_in):
    before = _count_expenses()
    resp = logged_in.post(URL, data=_valid(description="x" * 500))
    assert resp.status_code == 200
    assert b"200 characters" in resp.data
    assert _count_expenses() == before


def test_amount_above_maximum_is_rejected(logged_in):
    before = _count_expenses()
    resp = logged_in.post(URL, data=_valid(amount="1e300"))
    assert resp.status_code == 200
    assert b"auth-error" in resp.data
    assert _count_expenses() == before


def test_description_at_200_chars_accepted(logged_in):
    desc = "y" * 200
    resp = logged_in.post(URL, data=_valid(description=desc))
    assert resp.status_code == 302
    assert _rows("description = ?", (desc,))


# --------------------------------------------------------------------- #
# Values preserved on error                                              #
# --------------------------------------------------------------------- #

def test_values_preserved_after_validation_error(logged_in):
    resp = logged_in.post(
        URL,
        data=_valid(amount="-3", category="Transport", date="2024-05-06",
                    description="keep this note"),
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "-3" in body
    assert "2024-05-06" in body
    assert "keep this note" in body
    # Selected category is preserved
    assert "selected" in body
    idx = body.index("Transport")
    assert "selected" in body[max(0, idx - 80): idx + 40]


def test_values_preserved_when_date_invalid(logged_in):
    resp = logged_in.post(
        URL, data=_valid(date="garbage", amount="7.25", description="note abc")
    )
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "7.25" in body
    assert "note abc" in body


def test_error_response_still_lists_categories(logged_in):
    body = logged_in.post(URL, data=_valid(amount="0")).get_data(as_text=True)
    for cat in CATEGORIES:
        assert cat in body
