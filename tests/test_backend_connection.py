from datetime import datetime

import database.db as db
from database import queries
from werkzeug.security import generate_password_hash

DEMO_EMAIL = "demo@sawings.com"
DEMO_PASSWORD = "demo123"
DEMO_TOTAL_SPENT = 205.74  # sum of the 8 seeded expense amounts
DEMO_TRANSACTION_COUNT = 8
DEMO_TOP_CATEGORY = "Bills"
DEMO_CATEGORIES = {"Food", "Transport", "Bills", "Health", "Entertainment", "Shopping", "Other"}


def _user_id_by_email(email):
    conn = db.get_db()
    try:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def _create_user_with_no_expenses(email="nobody@example.com", name="Nobody"):
    conn = db.get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            (name, email, generate_password_hash("whatever123")),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


# --------------------------------------------------------------------- #
# get_user_by_id                                                         #
# --------------------------------------------------------------------- #

def test_get_user_by_id_valid(db_path):
    user_id = _user_id_by_email(DEMO_EMAIL)
    user = queries.get_user_by_id(user_id)
    assert user["name"] == "Demo User"
    assert user["email"] == DEMO_EMAIL
    assert user["member_since"] == datetime.now().strftime("%B %Y")


def test_get_user_by_id_nonexistent(db_path):
    assert queries.get_user_by_id(999999) is None


# --------------------------------------------------------------------- #
# get_summary_stats                                                      #
# --------------------------------------------------------------------- #

def test_get_summary_stats_with_expenses(db_path):
    user_id = _user_id_by_email(DEMO_EMAIL)
    stats = queries.get_summary_stats(user_id)
    assert round(stats["total_spent"], 2) == DEMO_TOTAL_SPENT
    assert stats["transaction_count"] == DEMO_TRANSACTION_COUNT
    assert stats["top_category"] == DEMO_TOP_CATEGORY


def test_get_summary_stats_no_expenses(db_path):
    user_id = _create_user_with_no_expenses()
    stats = queries.get_summary_stats(user_id)
    assert stats == {"total_spent": 0, "transaction_count": 0, "top_category": "—"}


# --------------------------------------------------------------------- #
# get_recent_transactions                                                #
# --------------------------------------------------------------------- #

def test_get_recent_transactions_with_expenses(db_path):
    user_id = _user_id_by_email(DEMO_EMAIL)
    transactions = queries.get_recent_transactions(user_id)
    assert len(transactions) == DEMO_TRANSACTION_COUNT
    for t in transactions:
        assert set(t.keys()) == {"id", "date", "description", "category", "amount"}
    dates = [t["date"] for t in transactions]
    assert dates == sorted(dates, reverse=True)


def test_get_recent_transactions_no_expenses(db_path):
    user_id = _create_user_with_no_expenses()
    assert queries.get_recent_transactions(user_id) == []


# --------------------------------------------------------------------- #
# get_category_breakdown                                                 #
# --------------------------------------------------------------------- #

def test_get_category_breakdown_with_expenses(db_path):
    user_id = _user_id_by_email(DEMO_EMAIL)
    breakdown = queries.get_category_breakdown(user_id)

    amounts = [c["amount"] for c in breakdown]
    assert amounts == sorted(amounts, reverse=True)

    pcts = [c["pct"] for c in breakdown]
    assert all(isinstance(p, int) for p in pcts)
    assert sum(pcts) == 100

    assert breakdown[0]["name"] == DEMO_TOP_CATEGORY


def test_get_category_breakdown_no_expenses(db_path):
    user_id = _create_user_with_no_expenses()
    assert queries.get_category_breakdown(user_id) == []


# --------------------------------------------------------------------- #
# GET /profile route                                                     #
# --------------------------------------------------------------------- #

def test_profile_redirects_when_logged_out(client):
    response = client.get("/profile")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_profile_shows_seed_user_data(client, db_path):
    login_response = client.post(
        "/login", data={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
    )
    assert login_response.status_code == 302

    response = client.get("/profile")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Demo User" in html
    assert DEMO_EMAIL in html
    assert "€" in html
    assert f"{DEMO_TOTAL_SPENT:.2f}" in html
    assert DEMO_TOP_CATEGORY in html
    for category in DEMO_CATEGORIES:
        assert category in html

    user_id = _user_id_by_email(DEMO_EMAIL)
    dates_newest_first = [t["date"] for t in queries.get_recent_transactions(user_id)]
    # Search inside the history table only: the date filter bar also contains dates.
    table_html = html[html.index('class="history-table"'):]
    positions = [table_html.index(d) for d in dates_newest_first]
    assert positions == sorted(positions)


def test_profile_shows_empty_state_for_new_user(client):
    client.post(
        "/register",
        data={
            "name": "Fresh User",
            "email": "fresh@example.com",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    client.post("/login", data={"email": "fresh@example.com", "password": "password123"})

    response = client.get("/profile")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "€0.00" in html
    assert "Fresh User" in html
