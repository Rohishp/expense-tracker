"""Tests for Step 9: GET/POST /expenses/<id>/delete.

Derived from .claude/specs/09-delete-expense.md. All tests use the temp DB from
conftest.py (db_path/app fixtures).
"""

import re
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

import database.db as db

DEMO_EMAIL = "demo@sawings.com"
DEMO_PASSWORD = "demo123"
OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "otherpass1"
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


def _insert_expense(user_id, amount=1234.56, category="Shopping", exp_date=None,
                    description="Delete target marker"):
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


def _url(expense_id):
    return f"/expenses/{expense_id}/delete"


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


# --------------------------------------------------------------------- #
# Auth guards                                                            #
# --------------------------------------------------------------------- #

def test_get_redirects_to_login_when_logged_out(client, eid):
    resp = client.get(_url(eid))
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_post_redirects_to_login_when_logged_out(client, eid):
    before = _snapshot()
    resp = client.post(_url(eid))
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert _snapshot() == before


def test_logged_out_nonexistent_id_still_redirects_to_login(client):
    resp = client.get(_url(999999))
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


# --------------------------------------------------------------------- #
# Profile Delete link                                                    #
# --------------------------------------------------------------------- #

def test_profile_row_has_delete_link(logged_in, eid):
    body = logged_in.get("/profile").get_data(as_text=True)
    assert f'href="{_url(eid)}"' in body
    assert "Delete" in body


def test_profile_delete_link_next_to_edit_link(logged_in, eid):
    body = logged_in.get("/profile").get_data(as_text=True)
    assert f'href="/expenses/{eid}/edit"' in body
    assert f'href="{_url(eid)}"' in body


# --------------------------------------------------------------------- #
# GET confirmation page                                                  #
# --------------------------------------------------------------------- #

def test_get_confirmation_page_renders(logged_in, eid):
    resp = logged_in.get(_url(eid))
    assert resp.status_code == 200


def test_confirmation_page_shows_expense_details(logged_in, db_path):
    eid = _insert_expense(_user_id(DEMO_EMAIL), amount=321.75,
                          category="Health", exp_date="2024-01-09",
                          description="Confirm details zzz")
    body = logged_in.get(_url(eid)).get_data(as_text=True)
    assert "321.75" in body
    assert "Health" in body
    assert "2024-01-09" in body
    assert "Confirm details zzz" in body


def test_confirmation_page_has_post_form_to_delete_url(logged_in, eid):
    body = logged_in.get(_url(eid)).get_data(as_text=True)
    assert 'method="post"' in body.lower()
    assert f'action="{_url(eid)}"' in body


def test_confirmation_page_has_cancel_link_to_profile(logged_in, eid):
    body = logged_in.get(_url(eid)).get_data(as_text=True)
    assert 'href="/profile"' in body
    assert "Cancel" in body


def test_confirmation_page_has_confirm_button(logged_in, eid):
    body = logged_in.get(_url(eid)).get_data(as_text=True)
    assert "delete" in body.lower()
    assert "<button" in body.lower()


def test_get_does_not_delete(logged_in, eid):
    before = _snapshot()
    logged_in.get(_url(eid))
    logged_in.get(_url(eid))
    assert _snapshot() == before
    assert _row(eid) is not None


def test_cancel_leaves_expense_listed_on_profile(logged_in, eid):
    logged_in.get(_url(eid))
    resp = logged_in.get("/profile")
    assert resp.status_code == 200
    assert "Delete target marker" in resp.get_data(as_text=True)
    assert _row(eid) is not None


# --------------------------------------------------------------------- #
# POST happy path                                                        #
# --------------------------------------------------------------------- #

def test_post_redirects_to_profile(logged_in, eid):
    resp = logged_in.post(_url(eid))
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/profile")


def test_post_deletes_row(logged_in, eid):
    logged_in.post(_url(eid))
    assert _row(eid) is None


def test_post_shows_success_flash_on_profile(logged_in, eid):
    resp = logged_in.post(_url(eid), follow_redirects=True)
    assert resp.status_code == 200
    assert "deleted" in resp.get_data(as_text=True).lower()


def test_flash_not_shown_when_nothing_deleted(logged_in, eid):
    body = logged_in.get("/profile").get_data(as_text=True)
    assert "expense deleted" not in body.lower()


def test_deleted_expense_gone_from_profile_list(logged_in, eid):
    logged_in.post(_url(eid))
    body = logged_in.get("/profile").get_data(as_text=True)
    assert "Delete target marker" not in body
    assert f'href="{_url(eid)}"' not in body


def test_deleted_expense_removed_from_totals(logged_in, eid):
    # Seeded total is 205.74 (see step 7/8 tests); the extra 1234.56 expense
    # must vanish from the amounts shown once deleted.
    before = logged_in.get("/profile").get_data(as_text=True)
    assert "1234.56" in before or "1,234.56" in before
    logged_in.post(_url(eid))
    after = logged_in.get("/profile").get_data(as_text=True)
    assert "1234.56" not in after and "1,234.56" not in after
    assert "205.74" in after


def test_deleted_expense_removed_from_category_breakdown(logged_in, db_path):
    # Use a category-specific amount so its disappearance is observable.
    eid = _insert_expense(_user_id(DEMO_EMAIL), amount=888.88,
                          category="Entertainment", description="Breakdown row")
    assert "888.88" in logged_in.get("/profile").get_data(as_text=True)
    logged_in.post(_url(eid))
    assert "888.88" not in logged_in.get("/profile").get_data(as_text=True)


def test_only_target_expense_removed(logged_in, eid):
    bystander = _insert_expense(_user_id(DEMO_EMAIL), amount=5.0,
                                description="Bystander")
    before = _snapshot()
    logged_in.post(_url(eid))
    after = _snapshot()
    assert len(after) == len(before) - 1
    assert [r for r in before if r[0] != eid] == after
    assert _row(bystander)["description"] == "Bystander"


def test_seeded_expenses_untouched_after_delete(logged_in, eid):
    seeded = [r for r in _snapshot() if r[0] != eid]
    logged_in.post(_url(eid))
    assert _snapshot() == seeded


# --------------------------------------------------------------------- #
# 404s and ownership                                                     #
# --------------------------------------------------------------------- #

def test_get_nonexistent_id_returns_404(logged_in):
    assert logged_in.get(_url(999999)).status_code == 404


def test_post_nonexistent_id_returns_404(logged_in):
    before = _snapshot()
    assert logged_in.post(_url(999999)).status_code == 404
    assert _snapshot() == before


def test_get_other_users_expense_returns_404(logged_in, foreign_eid):
    resp = logged_in.get(_url(foreign_eid))
    assert resp.status_code == 404
    assert "Foreign marker" not in resp.get_data(as_text=True)


def test_post_other_users_expense_returns_404_and_not_deleted(logged_in, foreign_eid):
    before = _row(foreign_eid)
    resp = logged_in.post(_url(foreign_eid))
    assert resp.status_code == 404
    assert tuple(_row(foreign_eid)) == tuple(before)


def test_foreign_and_missing_ids_look_identical(logged_in, foreign_eid):
    assert (logged_in.get(_url(foreign_eid)).status_code
            == logged_in.get(_url(999999)).status_code == 404)
    assert (logged_in.post(_url(foreign_eid)).status_code
            == logged_in.post(_url(999999)).status_code == 404)


def test_other_user_cannot_delete_demo_expense(app, eid, other_user):
    c = app.test_client()
    _login(c, OTHER_EMAIL, OTHER_PASSWORD)
    before = _row(eid)
    assert c.get(_url(eid)).status_code == 404
    assert c.post(_url(eid)).status_code == 404
    assert tuple(_row(eid)) == tuple(before)


def test_owner_delete_leaves_other_users_expenses(logged_in, eid, foreign_eid):
    before = _row(foreign_eid)
    assert logged_in.post(_url(eid)).status_code == 302
    assert tuple(_row(foreign_eid)) == tuple(before)


def test_double_delete_second_attempt_returns_404(logged_in, eid):
    assert logged_in.post(_url(eid)).status_code == 302
    assert logged_in.post(_url(eid)).status_code == 404


def test_get_after_delete_returns_404(logged_in, eid):
    logged_in.post(_url(eid))
    assert logged_in.get(_url(eid)).status_code == 404


def test_non_integer_id_returns_404(logged_in):
    assert logged_in.get("/expenses/abc/delete").status_code == 404


def test_put_method_not_allowed(logged_in, eid):
    assert logged_in.put(_url(eid)).status_code == 405
    assert _row(eid) is not None


# --------------------------------------------------------------------- #
# Add / Edit flows still work                                            #
# --------------------------------------------------------------------- #

def test_add_page_still_works(logged_in):
    resp = logged_in.get("/expenses/add")
    assert resp.status_code == 200
    assert 'name="amount"' in resp.get_data(as_text=True)


def test_edit_page_still_works(logged_in, eid):
    resp = logged_in.get(f"/expenses/{eid}/edit")
    assert resp.status_code == 200
    assert 'name="amount"' in resp.get_data(as_text=True)


# --------------------------------------------------------------------- #
# CSRF protection (review follow-up)                                     #
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


def test_post_without_token_is_rejected(csrf_on, logged_in, eid):
    assert logged_in.post(_url(eid)).status_code == 400
    assert _row(eid) is not None


def test_post_with_wrong_token_is_rejected(csrf_on, logged_in, eid):
    resp = logged_in.post(_url(eid), data={"csrf_token": "bad"})
    assert resp.status_code == 400
    assert _row(eid) is not None


def test_post_with_page_token_deletes(csrf_on, logged_in, eid):
    token = _page_token(logged_in, _url(eid))
    resp = logged_in.post(_url(eid), data={"csrf_token": token})
    assert resp.status_code == 302
    assert _row(eid) is None


def test_add_and_edit_forms_carry_token(csrf_on, logged_in, eid):
    assert _page_token(logged_in, "/expenses/add")
    assert _page_token(logged_in, f"/expenses/{eid}/edit")


def test_logged_out_post_redirects_before_csrf_check(csrf_on, client, eid):
    resp = client.post(_url(eid))
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
