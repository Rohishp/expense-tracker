"""Tests for Step 6: date filter on GET /profile.

Dates are always built relative to date.today() so the tests never go stale.
Most route tests use a fresh user whose expenses are inserted by the test, so
the seed data does not interfere.
"""

import calendar
import html as html_lib
import re
from datetime import date, timedelta

import pytest
from werkzeug.security import generate_password_hash

import database.db as db
from database import queries

DEMO_EMAIL = "demo@sawings.com"
DEMO_PASSWORD = "demo123"
DEMO_TOTAL_SPENT = 205.74
DEMO_TRANSACTION_COUNT = 8
FLASH_MESSAGE = "Start date must be before end date."

USER_EMAIL = "filter@example.com"
USER_PASSWORD = "filterpass123"


# --------------------------------------------------------------------- #
# Helpers                                                                #
# --------------------------------------------------------------------- #

def _iso(d):
    return d.isoformat()


def _month_bounds(today=None):
    today = today or date.today()
    first = today.replace(day=1)
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    return first, last


def _create_user(email=USER_EMAIL, name="Filter User", password=USER_PASSWORD):
    conn = db.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            (name, email, generate_password_hash(password)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _add_expense(user_id, amount, category, on_date, description):
    conn = db.get_db()
    try:
        conn.execute(
            "INSERT INTO expenses (user_id, amount, category, date, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, category, _iso(on_date), description),
        )
        conn.commit()
    finally:
        conn.close()


def _demo_id():
    conn = db.get_db()
    try:
        return conn.execute(
            "SELECT id FROM users WHERE email = ?", (DEMO_EMAIL,)
        ).fetchone()["id"]
    finally:
        conn.close()


def _login(client, email=USER_EMAIL, password=USER_PASSWORD):
    resp = client.post("/login", data={"email": email, "password": password})
    assert resp.status_code == 302


def _get(client, query=""):
    resp = client.get("/profile" + query)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _preset_href(page_html, label):
    """Return the href of the anchor whose visible text is `label`."""
    for match in re.finditer(r"<a\b([^>]*)>(.*?)</a>", page_html, re.S):
        text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        if text == label:
            href = re.search(r'href="([^"]*)"', match.group(1))
            if href:
                return html_lib.unescape(href.group(1))
    raise AssertionError(f"No preset link labelled {label!r} found")


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def user(db_path):
    """A fresh user (no expenses) and its id."""
    return _create_user()


@pytest.fixture
def logged_in(client, user):
    _login(client)
    return client


@pytest.fixture
def spread(user):
    """Expenses at known offsets from today, one per distinct description."""
    today = date.today()
    first, last = _month_bounds(today)
    rows = {
        "today": (10.00, "Food", today, "ZZ-today-item"),
        "prev_month_last": (20.00, "Transport", first - timedelta(days=1), "ZZ-prevmonthend-item"),
        "d30": (30.00, "Bills", today - timedelta(days=30), "ZZ-30days-item"),
        "d120": (40.00, "Health", today - timedelta(days=120), "ZZ-120days-item"),
        "d150": (50.00, "Shopping", today - timedelta(days=150), "ZZ-150days-item"),
        "d240": (60.00, "Other", today - timedelta(days=240), "ZZ-240days-item"),
        "next_month_first": (70.00, "Entertainment", last + timedelta(days=1), "ZZ-nextmonth-item"),
    }
    for amount, cat, d, desc in rows.values():
        _add_expense(user, amount, cat, d, desc)
    return rows


# --------------------------------------------------------------------- #
# Query helpers: defaults and filtering                                  #
# --------------------------------------------------------------------- #

def test_summary_stats_defaults_unfiltered(db_path):
    stats = queries.get_summary_stats(_demo_id())
    assert round(stats["total_spent"], 2) == DEMO_TOTAL_SPENT
    assert stats["transaction_count"] == DEMO_TRANSACTION_COUNT


def test_transactions_defaults_unfiltered_and_limit_kept(db_path):
    uid = _demo_id()
    assert len(queries.get_recent_transactions(uid)) == DEMO_TRANSACTION_COUNT
    assert len(queries.get_recent_transactions(uid, limit=3)) == 3


def test_breakdown_defaults_unfiltered(db_path):
    breakdown = queries.get_category_breakdown(_demo_id())
    assert round(sum(c["amount"] for c in breakdown), 2) == DEMO_TOTAL_SPENT
    assert sum(c["pct"] for c in breakdown) == 100


def test_summary_stats_inclusive_range(user):
    d1, d2, d3 = date(2024, 1, 10), date(2024, 1, 15), date(2024, 1, 20)
    _add_expense(user, 5.00, "Food", d1, "a")
    _add_expense(user, 7.00, "Bills", d2, "b")
    _add_expense(user, 9.00, "Bills", d3, "c")
    stats = queries.get_summary_stats(user, _iso(d1), _iso(d2))
    assert stats["transaction_count"] == 2
    assert round(stats["total_spent"], 2) == 12.00
    assert stats["top_category"] == "Bills"


def test_summary_stats_empty_range(user):
    _add_expense(user, 5.00, "Food", date(2024, 1, 10), "a")
    stats = queries.get_summary_stats(user, "2023-01-01", "2023-12-31")
    assert stats == {"total_spent": 0, "transaction_count": 0, "top_category": "—"}


def test_transactions_inclusive_range_newest_first(user):
    for i, d in enumerate([date(2024, 3, 1), date(2024, 3, 5), date(2024, 3, 9), date(2024, 3, 12)]):
        _add_expense(user, 1.00 + i, "Food", d, f"t{i}")
    rows = queries.get_recent_transactions(user, date_from="2024-03-05", date_to="2024-03-09")
    assert [r["date"] for r in rows] == ["2024-03-09", "2024-03-05"]


def test_transactions_limit_applies_within_range(user):
    for day in range(1, 8):
        _add_expense(user, 1.00, "Food", date(2024, 4, day), f"t{day}")
    _add_expense(user, 1.00, "Food", date(2024, 5, 1), "outside")
    rows = queries.get_recent_transactions(user, limit=3, date_from="2024-04-01", date_to="2024-04-07")
    assert [r["date"] for r in rows] == ["2024-04-07", "2024-04-06", "2024-04-05"]


def test_breakdown_range_recalculates_percentages(user):
    _add_expense(user, 30.00, "Food", date(2024, 6, 1), "in1")
    _add_expense(user, 10.00, "Bills", date(2024, 6, 2), "in2")
    _add_expense(user, 500.00, "Shopping", date(2024, 7, 1), "out")
    breakdown = queries.get_category_breakdown(user, "2024-06-01", "2024-06-30")
    assert [c["name"] for c in breakdown] == ["Food", "Bills"]
    assert [c["pct"] for c in breakdown] == [75, 25]
    assert sum(c["pct"] for c in breakdown) == 100


def test_breakdown_pct_sums_to_100_with_awkward_split(user):
    for cat in ("Food", "Bills", "Health"):
        _add_expense(user, 10.00, cat, date(2024, 8, 1), cat)
    breakdown = queries.get_category_breakdown(user, "2024-08-01", "2024-08-31")
    assert sum(c["pct"] for c in breakdown) == 100


def test_breakdown_empty_range(user):
    _add_expense(user, 10.00, "Food", date(2024, 8, 1), "x")
    assert queries.get_category_breakdown(user, "2020-01-01", "2020-01-31") == []


def test_transactions_empty_range(user):
    _add_expense(user, 10.00, "Food", date(2024, 8, 1), "x")
    assert queries.get_recent_transactions(user, date_from="2020-01-01", date_to="2020-01-31") == []


def test_query_helpers_isolate_users_in_range(db_path, user):
    _add_expense(user, 99.00, "Food", date.today(), "mine")
    today = _iso(date.today())
    demo = _demo_id()
    assert queries.get_summary_stats(demo, today, today)["total_spent"] != 99.00 + 0
    assert all(t["description"] != "mine"
               for t in queries.get_recent_transactions(demo, date_from=today, date_to=today))
    stats = queries.get_summary_stats(user, today, today)
    assert stats["transaction_count"] == 1


@pytest.mark.parametrize("func", [
    queries.get_summary_stats,
    queries.get_category_breakdown,
])
def test_range_boundaries_inclusive_single_day(user, func):
    _add_expense(user, 10.00, "Food", date(2024, 9, 10), "before")
    _add_expense(user, 20.00, "Food", date(2024, 9, 11), "on")
    _add_expense(user, 30.00, "Food", date(2024, 9, 12), "after")
    result = func(user, "2024-09-11", "2024-09-11")
    if isinstance(result, dict):
        assert round(result["total_spent"], 2) == 20.00
    else:
        assert round(result[0]["amount"], 2) == 20.00


# --------------------------------------------------------------------- #
# Route: auth                                                            #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("query", [
    "?date_from=2024-01-01&date_to=2024-12-31",
    "?date_from=not-a-date",
    "",
])
def test_profile_with_params_redirects_when_logged_out(client, db_path, query):
    resp = client.get("/profile" + query)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


# --------------------------------------------------------------------- #
# Route: unfiltered                                                      #
# --------------------------------------------------------------------- #

def test_no_params_shows_all_expenses(logged_in, spread):
    page = _get(logged_in)
    for _, _, _, desc in spread.values():
        assert desc in page
    assert "€280.00" in page


def test_seed_user_no_params_shows_seed_total(client, db_path):
    _login(client, DEMO_EMAIL, DEMO_PASSWORD)
    page = _get(client)
    assert f"€{DEMO_TOTAL_SPENT:.2f}" in page


def test_all_time_preset_has_clean_url(logged_in):
    page = _get(logged_in)
    href = _preset_href(page, "All Time")
    assert href.rstrip("?") == "/profile"
    assert "date_from" not in href and "date_to" not in href


# --------------------------------------------------------------------- #
# Route: presets                                                         #
# --------------------------------------------------------------------- #

def test_this_month_explicit_range_filters_all_sections(logged_in, spread):
    first, last = _month_bounds()
    page = _get(logged_in, f"?date_from={_iso(first)}&date_to={_iso(last)}")
    assert "ZZ-today-item" in page
    assert "ZZ-prevmonthend-item" not in page
    assert "ZZ-nextmonth-item" not in page
    assert "ZZ-120days-item" not in page
    assert "€10.00" in page


def test_this_month_preset_link_dates(logged_in):
    first, last = _month_bounds()
    href = _preset_href(_get(logged_in), "This Month")
    assert f"date_from={_iso(first)}" in href
    assert f"date_to={_iso(last)}" in href


def test_this_month_preset_link_filters_data(logged_in, spread):
    href = _preset_href(_get(logged_in), "This Month")
    page = logged_in.get(href).get_data(as_text=True)
    assert "ZZ-today-item" in page
    assert "ZZ-prevmonthend-item" not in page
    assert "ZZ-nextmonth-item" not in page
    assert "ZZ-30days-item" not in page or (date.today() - timedelta(days=30)).month == date.today().month


def test_last_3_months_preset_link_window_ends_today(logged_in):
    href = _preset_href(_get(logged_in), "Last 3 Months")
    assert f"date_to={_iso(date.today())}" in href
    assert "date_from=" in href


def test_last_3_months_preset_filters_data(logged_in, spread):
    href = _preset_href(_get(logged_in), "Last 3 Months")
    page = logged_in.get(href).get_data(as_text=True)
    assert "ZZ-today-item" in page
    assert "ZZ-30days-item" in page
    assert "ZZ-120days-item" not in page
    assert "ZZ-240days-item" not in page
    assert "ZZ-nextmonth-item" not in page  # window ends today


def test_last_6_months_preset_link_window_ends_today(logged_in):
    href = _preset_href(_get(logged_in), "Last 6 Months")
    assert f"date_to={_iso(date.today())}" in href
    assert "date_from=" in href


def test_last_6_months_preset_filters_data(logged_in, spread):
    href = _preset_href(_get(logged_in), "Last 6 Months")
    page = logged_in.get(href).get_data(as_text=True)
    assert "ZZ-today-item" in page
    assert "ZZ-120days-item" in page
    assert "ZZ-150days-item" in page
    assert "ZZ-240days-item" not in page
    assert "ZZ-nextmonth-item" not in page


def test_all_time_preset_follows_to_unfiltered_view(logged_in, spread):
    # Apply a filter first, then click All Time.
    _get(logged_in, "?date_from=2000-01-01&date_to=2000-01-02")
    href = _preset_href(_get(logged_in), "All Time")
    page = logged_in.get(href).get_data(as_text=True)
    for _, _, _, desc in spread.values():
        assert desc in page


# --------------------------------------------------------------------- #
# Route: custom range                                                    #
# --------------------------------------------------------------------- #

def test_custom_range_filters_transactions(logged_in, spread):
    today = date.today()
    frm, to = today - timedelta(days=130), today - timedelta(days=100)
    page = _get(logged_in, f"?date_from={_iso(frm)}&date_to={_iso(to)}")
    assert "ZZ-120days-item" in page
    for key in ("today", "d30", "d150", "d240", "prev_month_last", "next_month_first"):
        assert spread[key][3] not in page


def test_custom_range_filters_summary_stats(logged_in, spread):
    today = date.today()
    frm, to = today - timedelta(days=160), today - timedelta(days=100)
    page = _get(logged_in, f"?date_from={_iso(frm)}&date_to={_iso(to)}")
    assert "€90.00" in page  # 40 + 50
    assert "€280.00" not in page


def test_custom_range_filters_category_breakdown(logged_in, spread):
    today = date.today()
    frm, to = today - timedelta(days=160), today - timedelta(days=100)
    page = _get(logged_in, f"?date_from={_iso(frm)}&date_to={_iso(to)}")
    assert "Health" in page and "Shopping" in page
    # Categories whose only expenses fall outside the range are gone.
    # (Category names may also appear in an add-expense form; check the amounts.)
    assert "€10.00" not in page and "€60.00" not in page and "€70.00" not in page


def test_custom_range_boundaries_are_inclusive(logged_in, user):
    _add_expense(user, 11.11, "Food", date(2024, 2, 9), "ZZ-before")
    _add_expense(user, 22.22, "Food", date(2024, 2, 10), "ZZ-start")
    _add_expense(user, 33.33, "Food", date(2024, 2, 20), "ZZ-end")
    _add_expense(user, 44.44, "Food", date(2024, 2, 21), "ZZ-after")
    page = _get(logged_in, "?date_from=2024-02-10&date_to=2024-02-20")
    assert "ZZ-start" in page and "ZZ-end" in page
    assert "ZZ-before" not in page and "ZZ-after" not in page
    assert "€55.55" in page


def test_custom_range_same_day_is_valid_and_no_flash(logged_in, user):
    _add_expense(user, 5.00, "Food", date(2024, 2, 10), "ZZ-sameday")
    _add_expense(user, 6.00, "Food", date(2024, 2, 11), "ZZ-otherday")
    page = _get(logged_in, "?date_from=2024-02-10&date_to=2024-02-10")
    assert "ZZ-sameday" in page
    assert "ZZ-otherday" not in page
    assert FLASH_MESSAGE not in page


def test_valid_range_shows_no_flash(logged_in, spread):
    page = _get(logged_in, "?date_from=2000-01-01&date_to=2099-01-01")
    assert FLASH_MESSAGE not in page


def test_filter_bar_reflects_custom_range_values(logged_in, spread):
    page = _get(logged_in, "?date_from=2024-02-10&date_to=2024-02-20")
    assert 'type="date"' in page
    assert "2024-02-10" in page and "2024-02-20" in page
    assert "Apply" in page


def test_filter_bar_has_all_preset_labels(logged_in):
    page = _get(logged_in)
    for label in ("This Month", "Last 3 Months", "Last 6 Months", "All Time"):
        assert label in page


def test_filter_bar_has_named_date_inputs(logged_in):
    page = _get(logged_in)
    assert 'name="date_from"' in page
    assert 'name="date_to"' in page


# --------------------------------------------------------------------- #
# Route: empty range                                                     #
# --------------------------------------------------------------------- #

def test_empty_range_shows_zero_state_without_error(logged_in, spread):
    resp = logged_in.get("/profile?date_from=1990-01-01&date_to=1990-01-31")
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "€0.00" in page
    for _, _, _, desc in spread.values():
        assert desc not in page


def test_empty_range_hides_all_category_rows(logged_in, spread):
    page = _get(logged_in, "?date_from=1990-01-01&date_to=1990-01-31")
    for amount in ("€10.00", "€20.00", "€30.00", "€40.00", "€50.00", "€60.00", "€70.00"):
        assert amount not in page


def test_user_with_no_expenses_and_filter_shows_zero(logged_in):
    page = _get(logged_in, "?date_from=2024-01-01&date_to=2024-12-31")
    assert "€0.00" in page


# --------------------------------------------------------------------- #
# Route: currency                                                        #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("query", [
    "",
    "?date_from=2000-01-01&date_to=2099-01-01",
    "?date_from=1990-01-01&date_to=1990-01-31",
])
def test_euro_symbol_shown_regardless_of_filter(logged_in, spread, query):
    page = _get(logged_in, query)
    assert "€" in page
    assert "₹" not in page


# --------------------------------------------------------------------- #
# Route: validation                                                      #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("query", [
    "?date_from=not-a-date&date_to=2099-01-01",
    "?date_from=2000-01-01&date_to=not-a-date",
    "?date_from=not-a-date&date_to=also-bad",
    "?date_from=2024-13-45&date_to=2099-01-01",
    "?date_from=01/02/2024&date_to=2099-01-01",
    "?date_from=&date_to=",
])
def test_malformed_dates_fall_back_to_unfiltered(logged_in, spread, query):
    resp = logged_in.get("/profile" + query)
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200
    for _, _, _, desc in spread.values():
        assert desc in page
    assert "€280.00" in page


def test_malformed_date_does_not_flash_range_error(logged_in, spread):
    page = _get(logged_in, "?date_from=not-a-date")
    assert FLASH_MESSAGE not in page


@pytest.mark.parametrize("query_key", ["date_from", "date_to"])
def test_only_one_valid_date_shows_unfiltered(logged_in, spread, query_key):
    value = _iso(date.today() - timedelta(days=100))
    page = _get(logged_in, f"?{query_key}={value}")
    for _, _, _, desc in spread.values():
        assert desc in page
    assert "€280.00" in page


def test_reversed_range_falls_back_and_flashes(logged_in, spread):
    page = _get(logged_in, "?date_from=2099-01-01&date_to=2000-01-01")
    assert FLASH_MESSAGE in page
    for _, _, _, desc in spread.values():
        assert desc in page
    assert "€280.00" in page


def test_reversed_range_flash_appears_only_once_then_clears(logged_in, spread):
    _get(logged_in, "?date_from=2099-01-01&date_to=2000-01-01")
    page = _get(logged_in)
    assert FLASH_MESSAGE not in page


def test_unpadded_dates_are_treated_as_real_dates(logged_in, user):
    # "2024-2-9" is the same day as "2024-02-09"; comparing it as text against
    # "2024-02-20" would wrongly call the range reversed.
    _add_expense(user, 11.11, "Food", date(2024, 2, 8), "ZZ-before")
    _add_expense(user, 22.22, "Food", date(2024, 2, 9), "ZZ-start")
    _add_expense(user, 33.33, "Food", date(2024, 2, 20), "ZZ-end")
    page = _get(logged_in, "?date_from=2024-2-9&date_to=2024-02-20")
    assert FLASH_MESSAGE not in page
    assert "ZZ-start" in page and "ZZ-end" in page
    assert "ZZ-before" not in page
    assert "€55.55" in page


def test_unpadded_reversed_dates_still_flash(logged_in, spread):
    page = _get(logged_in, "?date_from=2024-10-5&date_to=2024-9-30")
    assert FLASH_MESSAGE in page


def test_sql_injection_in_dates_is_harmless(logged_in, spread):
    resp = logged_in.get("/profile?date_from=2000-01-01'%20OR%20'1'='1&date_to=2099-01-01")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert "€280.00" in page


# --------------------------------------------------------------------- #
# Route: percentages                                                     #
# --------------------------------------------------------------------- #

def test_filtered_breakdown_pcts_sum_to_100(user):
    _add_expense(user, 33.33, "Food", date(2024, 5, 1), "a")
    _add_expense(user, 33.33, "Bills", date(2024, 5, 2), "b")
    _add_expense(user, 33.34, "Health", date(2024, 5, 3), "c")
    _add_expense(user, 900.00, "Other", date(2024, 6, 3), "outside")
    breakdown = queries.get_category_breakdown(user, "2024-05-01", "2024-05-31")
    assert len(breakdown) == 3
    assert sum(c["pct"] for c in breakdown) == 100


# --------------------------------------------------------------------- #
# Route: per-user isolation                                              #
# --------------------------------------------------------------------- #

def test_other_users_expenses_never_leak_into_filtered_view(client, db_path):
    me = _create_user()
    other = _create_user(email="other@example.com", name="Other Person")
    today = date.today()
    _add_expense(me, 10.00, "Food", today, "ZZ-mine")
    _add_expense(other, 777.77, "Shopping", today, "ZZ-theirs")
    _login(client)
    first, last = _month_bounds()
    page = _get(client, f"?date_from={_iso(first)}&date_to={_iso(last)}")
    assert "ZZ-mine" in page
    assert "ZZ-theirs" not in page
    assert "777.77" not in page
    assert "€10.00" in page


def test_seed_user_data_not_visible_to_new_user_in_range(logged_in):
    first, last = _month_bounds()
    page = _get(logged_in, f"?date_from={_iso(first)}&date_to={_iso(last)}")
    assert "Groceries at the supermarket" not in page
    assert "€0.00" in page
