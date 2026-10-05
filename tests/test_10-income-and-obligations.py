"""Tests for Step 10: Income and Obligations.

Derived from .claude/specs/10-income-and-obligations.md. All tests use the temp
DB from conftest.py (db_path/app fixtures).
"""

import re
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import database.db as db

DEMO_EMAIL = "demo@spendly.com"
DEMO_PASSWORD = "demo123"
OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "otherpass1"
MAX_AMOUNT = 10_000_000
MAX_NAME_LENGTH = 60

ALL_GET_URLS = [
    "/obligations",
    "/income",
    "/obligations/add",
    "/obligations/1/edit",
    "/obligations/1/delete",
]
ALL_POST_URLS = [
    "/income",
    "/obligations/add",
    "/obligations/1/edit",
    "/obligations/1/delete",
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


def _insert_obligation(user_id, name="Rent", amount=900.0):
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


def _insert_income(user_id, amount):
    conn = db.get_db()
    try:
        conn.execute(
            "INSERT INTO incomes (user_id, monthly_amount) VALUES (?, ?)",
            (user_id, amount),
        )
        conn.commit()
    finally:
        conn.close()


def _obligation(oid):
    conn = db.get_db()
    try:
        row = conn.execute(
            "SELECT * FROM obligations WHERE id = ?", (oid,)
        ).fetchone()
        return tuple(row) if row else None
    finally:
        conn.close()


def _all_obligations():
    conn = db.get_db()
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT * FROM obligations ORDER BY id").fetchall()]
    finally:
        conn.close()


def _incomes(user_id=None):
    conn = db.get_db()
    try:
        if user_id is None:
            rows = conn.execute("SELECT * FROM incomes ORDER BY user_id")
        else:
            rows = conn.execute(
                "SELECT * FROM incomes WHERE user_id = ?", (user_id,))
        return [tuple(r) for r in rows.fetchall()]
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
def oid(demo_id):
    return _insert_obligation(demo_id, "Rent", 900.0)


@pytest.fixture
def foreign_oid(other_user):
    return _insert_obligation(other_user, "ForeignMarker", 321.0)


# --------------------------------------------------------------------- #
# Database schema                                                        #
# --------------------------------------------------------------------- #

def test_init_db_creates_incomes_and_obligations_tables(db_path):
    conn = db.get_db()
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        inc_cols = {r[1] for r in conn.execute("PRAGMA table_info(incomes)")}
        obl_cols = {r[1] for r in conn.execute("PRAGMA table_info(obligations)")}
    finally:
        conn.close()
    assert {"incomes", "obligations"} <= names
    assert {"user_id", "monthly_amount", "updated_at"} <= inc_cols
    assert {"id", "user_id", "name", "amount", "created_at"} <= obl_cols


def test_init_db_is_idempotent_and_keeps_existing_data(db_path):
    users_before = _count("users")
    expenses_before = _count("expenses")
    db.init_db()
    db.init_db()
    assert _count("users") == users_before
    assert _count("expenses") == expenses_before


def test_init_db_adds_tables_to_existing_database(tmp_path, monkeypatch):
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
    assert _count("obligations") == 0
    assert _count("incomes") == 0


def test_seed_db_creates_no_income_or_obligations(db_path):
    assert _count("incomes") == 0
    assert _count("obligations") == 0


def test_incomes_allows_only_one_row_per_user(db_path, demo_id):
    _insert_income(demo_id, 100.0)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_income(demo_id, 200.0)


# --------------------------------------------------------------------- #
# Auth guards                                                            #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("url", ALL_GET_URLS)
def test_get_redirects_to_login_when_logged_out(client, oid, url):
    resp = client.get(url.replace("/1/", f"/{oid}/"))
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


@pytest.mark.parametrize("url", ALL_POST_URLS)
def test_post_redirects_to_login_when_logged_out_and_changes_nothing(
    client, oid, url
):
    before = _all_obligations()
    resp = client.post(
        url.replace("/1/", f"/{oid}/"), data={"name": "X", "amount": "5"}
    )
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert _all_obligations() == before
    assert _incomes() == []


def test_logged_out_missing_id_still_redirects_to_login(client):
    resp = client.get("/obligations/999999/edit")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


# --------------------------------------------------------------------- #
# Navbar                                                                 #
# --------------------------------------------------------------------- #

def test_navbar_has_obligations_link_when_logged_in(logged_in):
    body = logged_in.get("/profile").get_data(as_text=True)
    assert 'href="/obligations"' in body
    assert "Obligations" in body


def test_navbar_obligations_link_absent_when_logged_out(client):
    body = client.get("/").get_data(as_text=True)
    assert 'href="/obligations"' not in body


def test_obligations_link_opens_obligations_page(logged_in):
    assert logged_in.get("/obligations").status_code == 200


# --------------------------------------------------------------------- #
# /obligations list and empty state                                      #
# --------------------------------------------------------------------- #

def test_obligations_empty_state_prompts_for_income_and_obligation(logged_in):
    resp = logged_in.get("/obligations")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'href="/income"' in body
    assert 'href="/obligations/add"' in body


def test_obligations_list_shows_name_amount_and_links(logged_in, oid):
    body = logged_in.get("/obligations").get_data(as_text=True)
    assert "Rent" in body
    assert "€900.00" in body
    assert f'href="/obligations/{oid}/edit"' in body
    assert f'href="/obligations/{oid}/delete"' in body
    assert 'href="/obligations/add"' in body


def test_obligations_total_is_sum_of_amounts(logged_in, demo_id):
    _insert_obligation(demo_id, "Rent", 900.0)
    _insert_obligation(demo_id, "Groceries", 350.5)
    body = logged_in.get("/obligations").get_data(as_text=True)
    assert "€1,250.50" in body


def test_obligations_amounts_shown_with_two_decimals(logged_in, demo_id):
    _insert_obligation(demo_id, "Phone", 20)
    body = logged_in.get("/obligations").get_data(as_text=True)
    assert "€20.00" in body


def test_obligations_page_shows_current_income(logged_in, demo_id):
    _insert_income(demo_id, 2500.0)
    body = logged_in.get("/obligations").get_data(as_text=True)
    assert "€2,500.00" in body


def test_duplicate_obligation_names_are_allowed(logged_in):
    for _ in range(2):
        resp = logged_in.post(
            "/obligations/add", data={"name": "Rent", "amount": "100"}
        )
        assert resp.status_code == 302
    assert len(_all_obligations()) == 2


# --------------------------------------------------------------------- #
# Income: GET /income                                                    #
# --------------------------------------------------------------------- #

def test_get_income_form_renders_empty_when_no_income(logged_in):
    resp = logged_in.get("/income")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'name="amount"' in body
    assert 'method="post"' in body.lower()


def test_get_income_form_prefilled_when_income_exists(logged_in, demo_id):
    _insert_income(demo_id, 1234.5)
    body = logged_in.get("/income").get_data(as_text=True)
    assert "1234.5" in body


def test_get_income_does_not_change_data(logged_in):
    logged_in.get("/income")
    assert _incomes() == []


# --------------------------------------------------------------------- #
# Income: POST /income                                                   #
# --------------------------------------------------------------------- #

def test_post_income_saves_and_redirects_to_obligations(logged_in, demo_id):
    resp = logged_in.post("/income", data={"amount": "2500"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/obligations")
    rows = _incomes(demo_id)
    assert len(rows) == 1
    assert rows[0][1] == 2500.0


def test_post_income_flashes_success_and_shows_formatted_amount(logged_in):
    resp = logged_in.post("/income", data={"amount": "2500"},
                          follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "€2,500.00" in body
    assert "saved" in body.lower()


def test_post_income_again_replaces_previous_value(logged_in, demo_id):
    logged_in.post("/income", data={"amount": "2500"})
    logged_in.post("/income", data={"amount": "3100.25"})
    rows = _incomes(demo_id)
    assert len(rows) == 1
    assert rows[0][1] == 3100.25
    body = logged_in.get("/obligations").get_data(as_text=True)
    assert "€3,100.25" in body
    assert "€2,500.00" not in body


def test_post_income_rounds_to_two_decimals(logged_in, demo_id):
    logged_in.post("/income", data={"amount": "100.126"})
    assert _incomes(demo_id)[0][1] == 100.13


def test_post_income_trims_whitespace_around_amount(logged_in, demo_id):
    logged_in.post("/income", data={"amount": "  42  "})
    assert _incomes(demo_id)[0][1] == 42.0


def test_post_income_at_max_amount_is_accepted(logged_in, demo_id):
    resp = logged_in.post("/income", data={"amount": str(MAX_AMOUNT)})
    assert resp.status_code == 302
    assert _incomes(demo_id)[0][1] == MAX_AMOUNT


def test_post_income_smallest_positive_amount_is_accepted(logged_in, demo_id):
    resp = logged_in.post("/income", data={"amount": "0.01"})
    assert resp.status_code == 302
    assert _incomes(demo_id)[0][1] == 0.01


@pytest.mark.parametrize(
    "bad",
    ["0", "0.00", "-5", "-0.01", "abc", "nan", "NaN", "inf", "-inf",
     "Infinity", "", "   ", "1e999", str(MAX_AMOUNT + 1), "12abc"],
)
def test_post_income_invalid_amount_shows_error_and_saves_nothing(
    logged_in, bad
):
    resp = logged_in.post("/income", data={"amount": bad})
    assert resp.status_code == 200
    assert _incomes() == []
    assert 'name="amount"' in resp.get_data(as_text=True)


def test_post_income_missing_amount_field_saves_nothing(logged_in):
    resp = logged_in.post("/income", data={})
    assert resp.status_code == 200
    assert _incomes() == []


def test_post_income_invalid_does_not_overwrite_existing(logged_in, demo_id):
    _insert_income(demo_id, 2500.0)
    logged_in.post("/income", data={"amount": "-1"})
    logged_in.post("/income", data={"amount": "nan"})
    assert _incomes(demo_id)[0][1] == 2500.0


def test_post_income_error_message_is_displayed(logged_in):
    body = logged_in.post("/income", data={"amount": "0"}).get_data(as_text=True)
    assert "amount" in body.lower()
    assert "must" in body.lower() or "greater" in body.lower()


# --------------------------------------------------------------------- #
# Obligations: GET /obligations/add                                      #
# --------------------------------------------------------------------- #

def test_get_add_obligation_form_renders_empty(logged_in):
    resp = logged_in.get("/obligations/add")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'name="name"' in body
    assert 'name="amount"' in body
    assert 'method="post"' in body.lower()


def test_get_add_obligation_does_not_change_data(logged_in):
    logged_in.get("/obligations/add")
    assert _all_obligations() == []


# --------------------------------------------------------------------- #
# Obligations: POST /obligations/add                                     #
# --------------------------------------------------------------------- #

def test_post_add_obligation_creates_row_and_redirects(logged_in, demo_id):
    resp = logged_in.post(
        "/obligations/add", data={"name": "Rent", "amount": "900"}
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/obligations")
    rows = _all_obligations()
    assert len(rows) == 1
    assert rows[0][1] == demo_id
    assert rows[0][2] == "Rent"
    assert rows[0][3] == 900.0


def test_post_add_obligation_appears_in_list_and_total(logged_in):
    logged_in.post("/obligations/add", data={"name": "Rent", "amount": "900"})
    body = logged_in.get("/obligations").get_data(as_text=True)
    assert "Rent" in body
    assert "€900.00" in body
    logged_in.post("/obligations/add",
                   data={"name": "Groceries", "amount": "300"})
    body = logged_in.get("/obligations").get_data(as_text=True)
    assert "Groceries" in body
    assert "€1,200.00" in body


def test_post_add_obligation_trims_name(logged_in):
    logged_in.post("/obligations/add",
                   data={"name": "  Rent  ", "amount": "900"})
    assert _all_obligations()[0][2] == "Rent"


def test_post_add_obligation_rounds_amount_to_two_decimals(logged_in):
    logged_in.post("/obligations/add",
                   data={"name": "Rent", "amount": "10.456"})
    assert _all_obligations()[0][3] == 10.46


def test_post_add_obligation_name_at_max_length_accepted(logged_in):
    name = "a" * MAX_NAME_LENGTH
    resp = logged_in.post("/obligations/add",
                          data={"name": name, "amount": "5"})
    assert resp.status_code == 302
    assert _all_obligations()[0][2] == name


def test_post_add_obligation_name_over_max_length_rejected(logged_in):
    name = "a" * (MAX_NAME_LENGTH + 1)
    resp = logged_in.post("/obligations/add",
                          data={"name": name, "amount": "5"})
    assert resp.status_code == 200
    assert _all_obligations() == []


@pytest.mark.parametrize("name", ["", "   ", "\t"])
def test_post_add_obligation_blank_name_rejected(logged_in, name):
    resp = logged_in.post("/obligations/add",
                          data={"name": name, "amount": "50"})
    assert resp.status_code == 200
    assert _all_obligations() == []


def test_post_add_obligation_missing_name_field_rejected(logged_in):
    resp = logged_in.post("/obligations/add", data={"amount": "50"})
    assert resp.status_code == 200
    assert _all_obligations() == []


@pytest.mark.parametrize(
    "bad",
    ["0", "-10", "abc", "nan", "inf", "-inf", "", "1e999",
     str(MAX_AMOUNT + 1)],
)
def test_post_add_obligation_invalid_amount_rejected(logged_in, bad):
    resp = logged_in.post("/obligations/add",
                          data={"name": "Rent", "amount": bad})
    assert resp.status_code == 200
    assert _all_obligations() == []


def test_post_add_obligation_amount_at_max_accepted(logged_in):
    resp = logged_in.post("/obligations/add",
                          data={"name": "Big", "amount": str(MAX_AMOUNT)})
    assert resp.status_code == 302
    assert _all_obligations()[0][3] == MAX_AMOUNT


def test_post_add_obligation_error_keeps_entered_name(logged_in):
    body = logged_in.post(
        "/obligations/add", data={"name": "KeepMeZZ", "amount": "abc"}
    ).get_data(as_text=True)
    assert "KeepMeZZ" in body


# --------------------------------------------------------------------- #
# Obligations: edit                                                      #
# --------------------------------------------------------------------- #

def test_get_edit_form_is_prefilled(logged_in, oid):
    resp = logged_in.get(f"/obligations/{oid}/edit")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Rent" in body
    assert "900" in body


def test_get_edit_does_not_change_data(logged_in, oid):
    before = _obligation(oid)
    logged_in.get(f"/obligations/{oid}/edit")
    assert _obligation(oid) == before


def test_post_edit_updates_name_and_amount_and_redirects(logged_in, oid):
    resp = logged_in.post(
        f"/obligations/{oid}/edit", data={"name": "Flat rent", "amount": "950"}
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/obligations")
    row = _obligation(oid)
    assert row[2] == "Flat rent"
    assert row[3] == 950.0


def test_post_edit_updates_total_on_list(logged_in, oid, demo_id):
    _insert_obligation(demo_id, "Groceries", 100.0)
    logged_in.post(f"/obligations/{oid}/edit",
                   data={"name": "Rent", "amount": "1000"})
    body = logged_in.get("/obligations").get_data(as_text=True)
    assert "€1,100.00" in body


def test_post_edit_does_not_change_owner(logged_in, oid, demo_id):
    logged_in.post(f"/obligations/{oid}/edit",
                   data={"name": "Rent", "amount": "1"})
    assert _obligation(oid)[1] == demo_id


def test_post_edit_only_changes_target_row(logged_in, oid, demo_id):
    other = _insert_obligation(demo_id, "Phone", 20.0)
    before = _obligation(other)
    logged_in.post(f"/obligations/{oid}/edit",
                   data={"name": "Rent2", "amount": "5"})
    assert _obligation(other) == before


@pytest.mark.parametrize(
    "data",
    [
        {"name": "", "amount": "10"},
        {"name": "   ", "amount": "10"},
        {"name": "a" * (MAX_NAME_LENGTH + 1), "amount": "10"},
        {"name": "Rent", "amount": "0"},
        {"name": "Rent", "amount": "-1"},
        {"name": "Rent", "amount": "abc"},
        {"name": "Rent", "amount": "nan"},
        {"name": "Rent", "amount": "inf"},
        {"name": "Rent", "amount": str(MAX_AMOUNT + 1)},
    ],
)
def test_post_edit_invalid_input_rejected_and_row_unchanged(
    logged_in, oid, data
):
    before = _obligation(oid)
    resp = logged_in.post(f"/obligations/{oid}/edit", data=data)
    assert resp.status_code == 200
    assert _obligation(oid) == before


# --------------------------------------------------------------------- #
# Obligations: delete                                                    #
# --------------------------------------------------------------------- #

def test_get_delete_confirmation_summarises_obligation(logged_in, demo_id):
    oid = _insert_obligation(demo_id, "ConfirmMarker", 123.45)
    resp = logged_in.get(f"/obligations/{oid}/delete")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "ConfirmMarker" in body
    assert "123.45" in body


def test_delete_confirmation_has_post_form_and_cancel_link(logged_in, oid):
    body = logged_in.get(f"/obligations/{oid}/delete").get_data(as_text=True)
    assert 'method="post"' in body.lower()
    assert f'action="/obligations/{oid}/delete"' in body
    assert "<button" in body.lower()
    assert 'href="/obligations"' in body
    assert "Cancel" in body


def test_get_delete_does_not_delete(logged_in, oid):
    before = _all_obligations()
    logged_in.get(f"/obligations/{oid}/delete")
    logged_in.get(f"/obligations/{oid}/delete")
    assert _all_obligations() == before


def test_cancel_keeps_obligation_listed(logged_in, oid):
    logged_in.get(f"/obligations/{oid}/delete")
    body = logged_in.get("/obligations").get_data(as_text=True)
    assert "Rent" in body
    assert _obligation(oid) is not None


def test_post_delete_removes_row_and_redirects(logged_in, oid):
    resp = logged_in.post(f"/obligations/{oid}/delete")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/obligations")
    assert _obligation(oid) is None


def test_post_delete_flashes_and_updates_total(logged_in, oid, demo_id):
    _insert_obligation(demo_id, "Groceries", 100.0)
    resp = logged_in.post(f"/obligations/{oid}/delete", follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "deleted" in body.lower()
    assert "€100.00" in body
    assert "€1,000.00" not in body
    assert "Rent" not in body.replace("Obligations", "")


def test_flash_not_shown_when_nothing_deleted(logged_in, oid):
    body = logged_in.get("/obligations").get_data(as_text=True)
    assert "obligation deleted" not in body.lower()


def test_post_delete_only_removes_target(logged_in, oid, demo_id):
    other = _insert_obligation(demo_id, "Phone", 20.0)
    before_other = _obligation(other)
    logged_in.post(f"/obligations/{oid}/delete")
    assert _obligation(other) == before_other


def test_delete_same_obligation_twice_second_is_404(logged_in, oid):
    assert logged_in.post(f"/obligations/{oid}/delete").status_code == 302
    assert logged_in.post(f"/obligations/{oid}/delete").status_code == 404


def test_get_delete_after_delete_is_404(logged_in, oid):
    logged_in.post(f"/obligations/{oid}/delete")
    assert logged_in.get(f"/obligations/{oid}/delete").status_code == 404


def test_deleting_obligation_does_not_touch_income_or_expenses(
    logged_in, oid, demo_id
):
    _insert_income(demo_id, 2500.0)
    expenses_before = _count("expenses")
    logged_in.post(f"/obligations/{oid}/delete")
    assert _incomes(demo_id)[0][1] == 2500.0
    assert _count("expenses") == expenses_before


# --------------------------------------------------------------------- #
# 404s: missing ids, foreign ids                                         #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("suffix", ["edit", "delete"])
def test_get_missing_obligation_returns_404(logged_in, suffix):
    assert logged_in.get(f"/obligations/999999/{suffix}").status_code == 404


@pytest.mark.parametrize("suffix", ["edit", "delete"])
def test_post_missing_obligation_returns_404(logged_in, suffix):
    resp = logged_in.post(f"/obligations/999999/{suffix}",
                          data={"name": "X", "amount": "5"})
    assert resp.status_code == 404
    assert _all_obligations() == []


@pytest.mark.parametrize("suffix", ["edit", "delete"])
def test_non_integer_id_returns_404(logged_in, suffix):
    assert logged_in.get(f"/obligations/abc/{suffix}").status_code == 404


@pytest.mark.parametrize("suffix", ["edit", "delete"])
def test_get_foreign_obligation_returns_404_without_leaking(
    logged_in, foreign_oid, suffix
):
    resp = logged_in.get(f"/obligations/{foreign_oid}/{suffix}")
    assert resp.status_code == 404
    assert "ForeignMarker" not in resp.get_data(as_text=True)


def test_post_edit_foreign_obligation_404_and_unchanged(logged_in, foreign_oid):
    before = _obligation(foreign_oid)
    resp = logged_in.post(f"/obligations/{foreign_oid}/edit",
                          data={"name": "Hacked", "amount": "1"})
    assert resp.status_code == 404
    assert _obligation(foreign_oid) == before


def test_post_delete_foreign_obligation_404_and_not_deleted(
    logged_in, foreign_oid
):
    before = _obligation(foreign_oid)
    resp = logged_in.post(f"/obligations/{foreign_oid}/delete")
    assert resp.status_code == 404
    assert _obligation(foreign_oid) == before


def test_foreign_and_missing_ids_respond_identically(logged_in, foreign_oid):
    for suffix in ("edit", "delete"):
        assert (logged_in.get(f"/obligations/{foreign_oid}/{suffix}").status_code
                == logged_in.get(f"/obligations/999999/{suffix}").status_code
                == 404)


def test_other_user_cannot_touch_demo_obligation(app, oid, other_user):
    c = app.test_client()
    _login(c, OTHER_EMAIL, OTHER_PASSWORD)
    before = _obligation(oid)
    assert c.get(f"/obligations/{oid}/edit").status_code == 404
    assert c.post(f"/obligations/{oid}/edit",
                  data={"name": "Hacked", "amount": "1"}).status_code == 404
    assert c.get(f"/obligations/{oid}/delete").status_code == 404
    assert c.post(f"/obligations/{oid}/delete").status_code == 404
    assert _obligation(oid) == before


# --------------------------------------------------------------------- #
# Data isolation                                                         #
# --------------------------------------------------------------------- #

def test_list_shows_only_own_obligations_and_total(
    logged_in, oid, foreign_oid
):
    body = logged_in.get("/obligations").get_data(as_text=True)
    assert "Rent" in body
    assert "ForeignMarker" not in body
    assert "€900.00" in body
    assert "€1,221.00" not in body
    assert "€321.00" not in body


def test_other_user_sees_only_their_obligations(app, oid, foreign_oid):
    c = app.test_client()
    _login(c, OTHER_EMAIL, OTHER_PASSWORD)
    body = c.get("/obligations").get_data(as_text=True)
    assert "ForeignMarker" in body
    assert "€321.00" in body
    assert "Rent" not in body.replace("Obligations", "")


def test_income_is_per_user(app, demo_id, other_user):
    _insert_income(other_user, 7777.0)
    c = app.test_client()
    _login(c)
    assert "7,777.00" not in c.get("/obligations").get_data(as_text=True)
    assert "7777" not in c.get("/income").get_data(as_text=True)


def test_setting_income_does_not_affect_other_user(
    logged_in, demo_id, other_user
):
    _insert_income(other_user, 7777.0)
    logged_in.post("/income", data={"amount": "1000"})
    assert _incomes(other_user)[0][1] == 7777.0
    assert _incomes(demo_id)[0][1] == 1000.0
    assert len(_incomes()) == 2


def test_new_obligation_belongs_to_logged_in_user_only(
    logged_in, other_user
):
    logged_in.post("/obligations/add", data={"name": "Rent", "amount": "9"})
    conn = db.get_db()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM obligations WHERE user_id = ?", (other_user,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 0


# --------------------------------------------------------------------- #
# Method handling                                                        #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("url", ["/income", "/obligations/add"])
def test_put_not_allowed(logged_in, url):
    assert logged_in.put(url).status_code == 405


def test_post_to_obligations_list_not_allowed(logged_in):
    assert logged_in.post("/obligations").status_code == 405


# --------------------------------------------------------------------- #
# Existing flows unaffected                                              #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("url", ["/profile", "/expenses/add"])
def test_existing_pages_still_work(logged_in, url):
    assert logged_in.get(url).status_code == 200


def test_expense_add_still_works(logged_in):
    before = _count("expenses")
    resp = logged_in.post(
        "/expenses/add",
        data={"amount": "12.50", "category": "Food", "date": "2024-03-01",
              "description": "step10 regression"},
    )
    assert resp.status_code == 302
    assert _count("expenses") == before + 1


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


def test_all_forms_carry_csrf_token(csrf_on, logged_in, oid):
    for url in ("/income", "/obligations/add", f"/obligations/{oid}/edit",
                f"/obligations/{oid}/delete"):
        assert _page_token(logged_in, url)


def test_income_post_without_token_rejected(csrf_on, logged_in):
    assert logged_in.post("/income", data={"amount": "5"}).status_code == 400
    assert _incomes() == []


def test_add_obligation_post_without_token_rejected(csrf_on, logged_in):
    resp = logged_in.post("/obligations/add",
                          data={"name": "Rent", "amount": "5"})
    assert resp.status_code == 400
    assert _all_obligations() == []


def test_edit_obligation_post_with_wrong_token_rejected(
    csrf_on, logged_in, oid
):
    before = _obligation(oid)
    resp = logged_in.post(
        f"/obligations/{oid}/edit",
        data={"name": "X", "amount": "5", "csrf_token": "bad"},
    )
    assert resp.status_code == 400
    assert _obligation(oid) == before


def test_delete_obligation_post_without_token_rejected(csrf_on, logged_in, oid):
    assert logged_in.post(f"/obligations/{oid}/delete").status_code == 400
    assert _obligation(oid) is not None


def test_delete_obligation_post_with_page_token_succeeds(
    csrf_on, logged_in, oid
):
    token = _page_token(logged_in, f"/obligations/{oid}/delete")
    resp = logged_in.post(f"/obligations/{oid}/delete",
                          data={"csrf_token": token})
    assert resp.status_code == 302
    assert _obligation(oid) is None


def test_logged_out_post_redirects_before_csrf_check(csrf_on, client):
    resp = client.post("/income", data={"amount": "5"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
