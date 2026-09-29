import calendar
import functools
import sqlite3
from datetime import date, datetime

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from database import queries
from database.db import get_db, init_db, seed_db

app = Flask(__name__)
app.secret_key = "dev-secret-key-change-in-production"

with app.app_context():
    init_db()
    seed_db()


def login_required(view_func):
    @functools.wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapper


def _parse_date_range(args):
    """Read `date_from` / `date_to` (YYYY-MM-DD) from the query string.

    Returns (date_from, date_to, error) with dates as zero-padded ISO
    strings. The filter needs both dates; if either is missing or malformed
    both come back None (all-time view, no error). If date_from is after
    date_to both come back None with an error message to flash.
    """
    parsed = []
    for key in ("date_from", "date_to"):
        raw = args.get(key, "").strip()
        try:
            # strptime accepts unpadded input like "2026-9-5"; keeping the
            # parsed date lets us compare real dates and emit padded ISO text.
            parsed.append(datetime.strptime(raw, "%Y-%m-%d").date())
        except ValueError:
            return None, None, None

    date_from, date_to = parsed
    if date_from > date_to:
        return None, None, "Start date must be before end date."
    return date_from.isoformat(), date_to.isoformat(), None


def _months_ago(day, months):
    """Return `day` moved back by `months`, clamping to the month's length."""
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def _build_presets(today, date_from, date_to):
    """Return (presets, custom_active) for the filter bar.

    `presets` are the quick-select links with the active one flagged.
    `custom_active` is True when a range is applied that matches no preset,
    i.e. the user picked it with the custom date form.
    """
    last_day = calendar.monthrange(today.year, today.month)[1]
    # "Last N Months" is a rolling window ending today, not calendar months.
    ranges = [
        ("This Month", today.replace(day=1), today.replace(day=last_day)),
        ("Last 3 Months", _months_ago(today, 3), today),
        ("Last 6 Months", _months_ago(today, 6), today),
    ]

    presets = []
    for label, start, end in ranges:
        start_iso, end_iso = start.isoformat(), end.isoformat()
        presets.append({
            "label": label,
            "url": url_for("profile", date_from=start_iso, date_to=end_iso),
            "active": (date_from, date_to) == (start_iso, end_iso),
        })
    presets.append({
        "label": "All Time",
        "url": url_for("profile"),
        "active": date_from is None,
    })
    custom_active = date_from is not None and not any(
        p["active"] for p in presets
    )
    return presets, custom_active


# ------------------------------------------------------------------ #
# Routes                                                              #
# ------------------------------------------------------------------ #

@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name or not email or not password or not confirm_password:
            return render_template("register.html", error="All fields are required.")

        if password != confirm_password:
            return render_template("register.html", error="Passwords do not match.")

        password_hash = generate_password_hash(password)

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                (name, email, password_hash),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return render_template(
                "register.html",
                error="An account with this email already exists.",
            )
        finally:
            conn.close()

        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        try:
            user = conn.execute(
                "SELECT id, name, email, password_hash FROM users WHERE email = ?",
                (email,),
            ).fetchone()
        finally:
            conn.close()

        valid = user is not None and check_password_hash(user["password_hash"], password)

        if not valid:
            return render_template("login.html", error="Invalid email or password.")

        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        session["user_email"] = user["email"]
        return redirect(url_for("profile"))

    return render_template("login.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# ------------------------------------------------------------------ #
# Placeholder routes — students will implement these                  #
# ------------------------------------------------------------------ #

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/profile")
@login_required
def profile():
    user_id = session["user_id"]

    # --- date filter ---
    date_from, date_to, filter_error = _parse_date_range(request.args)
    if filter_error:
        flash(filter_error, "error")
    presets, custom_active = _build_presets(date.today(), date_from, date_to)
    # --- end date filter ---

    # --- user card + summary stats (Subagent: summary) ---
    user = queries.get_user_by_id(user_id)
    initials = "".join(part[0].upper() for part in user["name"].split()[:2])
    stats = queries.get_summary_stats(user_id, date_from, date_to)
    total_spent = stats["total_spent"]
    transaction_count = stats["transaction_count"]
    top_category = stats["top_category"]
    # --- end user card + summary stats ---

    # --- transaction history (Subagent: transaction-history) ---
    transactions = queries.get_recent_transactions(
        user_id, date_from=date_from, date_to=date_to
    )
    # --- end transaction history ---

    # --- category breakdown (Subagent: category-breakdown) ---
    category_breakdown = [
        {"category": c["name"], "total": c["amount"], "percent": c["pct"]}
        for c in queries.get_category_breakdown(user_id, date_from, date_to)
    ]
    # --- end category breakdown ---

    return render_template(
        "profile.html",
        user=user,
        initials=initials,
        total_spent=total_spent,
        transaction_count=transaction_count,
        top_category=top_category,
        transactions=transactions,
        category_breakdown=category_breakdown,
        presets=presets,
        date_from=date_from,
        date_to=date_to,
        custom_active=custom_active,
    )


@app.route("/expenses/add")
def add_expense():
    return "Add expense — coming in Step 7"


@app.route("/expenses/<int:id>/edit")
def edit_expense(id):
    return "Edit expense — coming in Step 8"


@app.route("/expenses/<int:id>/delete")
def delete_expense(id):
    return "Delete expense — coming in Step 9"


if __name__ == "__main__":
    app.run(debug=True, port=5001)
