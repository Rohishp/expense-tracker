import calendar
import functools
import math
import os
import secrets
import sqlite3
from datetime import date, datetime

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from database import queries
from database.db import CATEGORIES, get_db, init_db, seed_db

app = Flask(__name__)
# Set SECRET_KEY in the environment for any real deployment; the fallback is
# only for local development.
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
# Lax stops browsers sending the session cookie on cross-site POSTs.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Tests turn this off so they can post forms without fetching a token first.
app.config["CSRF_ENABLED"] = True

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


def _csrf_token():
    """Return the session's CSRF token, creating it on first use."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = _csrf_token


def csrf_protect(view_func):
    """Reject POSTs whose form token does not match the session token."""
    @functools.wraps(view_func)
    def wrapper(*args, **kwargs):
        if request.method == "POST" and app.config["CSRF_ENABLED"]:
            sent = request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(
                sent.encode(), expected.encode()
            ):
                abort(400)
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


@app.route("/analytics")
@login_required
def analytics():
    return render_template("analytics.html")


MAX_DESCRIPTION_LENGTH = 200
MAX_AMOUNT = 10_000_000
MAX_NAME_LENGTH = 60
MAX_OBLIGATIONS = 100


def _validate_amount(raw):
    """Return (amount, error) for a monetary form value."""
    try:
        amount = float(raw.strip())
    except ValueError:
        return None, "Amount must be a number."
    # float() accepts "nan" and "inf", so reject non-finite values explicitly.
    if not math.isfinite(amount) or amount <= 0:
        return None, "Amount must be greater than zero."
    if amount > MAX_AMOUNT:
        return None, f"Amount must be at most {MAX_AMOUNT:,}."
    amount = round(amount, 2)
    if amount <= 0:
        return None, "Amount must be greater than zero."
    return amount, None


def _validate_expense_form(form):
    """Return (clean_values, error). `error` is None when the form is valid.

    The keys of `clean_values` match the keyword arguments of
    `queries.create_expense`.
    """
    raw_amount = form.get("amount", "").strip()
    category = form.get("category", "")
    date_str = form.get("date", "").strip()
    description = form.get("description", "").strip()

    amount, error = _validate_amount(raw_amount)
    if error:
        return None, error

    if category not in CATEGORIES:
        return None, "Please choose a valid category."

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None, "Please enter a valid date."

    if len(description) > MAX_DESCRIPTION_LENGTH:
        return None, (
            f"Description must be {MAX_DESCRIPTION_LENGTH} characters or fewer."
        )

    return {
        "amount": round(amount, 2),
        "category": category,
        "expense_date": date_str,
        "description": description or None,
    }, None


def _expense_to_form(expense):
    """Return the form values used to pre-fill the edit form."""
    return {
        "amount": expense["amount"],
        "category": expense["category"],
        "date": expense["date"],
        "description": expense["description"] or "",
    }


def _render_expense_form(form, error=None, expense_id=None):
    return render_template(
        "add_expense.html",
        error=error,
        categories=CATEGORIES,
        form=form,
        expense_id=expense_id,
        max_description_length=MAX_DESCRIPTION_LENGTH,
    )


@app.route("/expenses/add", methods=["GET", "POST"])
@login_required
@csrf_protect
def add_expense():
    form = {"date": date.today().isoformat()}
    error = None

    if request.method == "POST":
        form = request.form
        clean, error = _validate_expense_form(form)
        if not error:
            queries.create_expense(session["user_id"], **clean)
            return redirect(url_for("profile"))

    return _render_expense_form(form, error)


@app.route("/expenses/<int:id>/edit", methods=["GET", "POST"])
@login_required
@csrf_protect
def edit_expense(id):
    # Ownership is part of the query, so missing and foreign ids both 404.
    expense = queries.get_expense(id, session["user_id"])
    if expense is None:
        abort(404)

    form = _expense_to_form(expense)
    error = None

    if request.method == "POST":
        form = request.form
        clean, error = _validate_expense_form(form)
        if not error:
            # False means the row vanished since the read above.
            if not queries.update_expense(id, session["user_id"], **clean):
                abort(404)
            return redirect(url_for("profile"))

    return _render_expense_form(form, error, expense_id=id)


@app.route("/expenses/<int:id>/delete", methods=["GET", "POST"])
@login_required
@csrf_protect
def delete_expense(id):
    # Ownership is part of the query, so missing and foreign ids both 404.
    expense = queries.get_expense(id, session["user_id"])
    if expense is None:
        abort(404)

    if request.method == "POST":
        # False means the row vanished since the read above.
        if not queries.delete_expense(id, session["user_id"]):
            abort(404)
        flash("Expense deleted.", "success")
        return redirect(url_for("profile"))

    return render_template("delete_expense.html", expense=expense)


# ------------------------------------------------------------------ #
# Income and Obligations                                              #
# ------------------------------------------------------------------ #

@app.template_filter("eur")
def eur(value):
    return f"{value:,.2f}"


def _validate_income_form(form):
    """Return (clean_values, error); keys match `queries.set_income`."""
    amount, error = _validate_amount(form.get("amount", ""))
    if error:
        return None, error
    return {"amount": amount}, None


def _validate_obligation_form(form):
    """Return (clean_values, error); keys match `queries.create_obligation`."""
    name = form.get("name", "").strip()
    if not name:
        return None, "Name is required."
    if len(name) > MAX_NAME_LENGTH:
        return None, f"Name must be {MAX_NAME_LENGTH} characters or fewer."
    amount, error = _validate_amount(form.get("amount", ""))
    if error:
        return None, error
    return {"name": name, "amount": amount}, None


def _render_obligation_form(form, error=None, obligation_id=None):
    return render_template(
        "obligation_form.html",
        error=error,
        form=form,
        obligation_id=obligation_id,
        max_name_length=MAX_NAME_LENGTH,
    )


@app.route("/obligations")
@login_required
def obligations():
    user_id = session["user_id"]
    items = queries.list_obligations(user_id)
    return render_template(
        "obligations.html",
        income=queries.get_income(user_id),
        obligations=items,
        total=sum(o["amount"] for o in items),
    )


@app.route("/income", methods=["GET", "POST"])
@login_required
@csrf_protect
def income():
    user_id = session["user_id"]
    current = queries.get_income(user_id)
    form = {"amount": current if current is not None else ""}
    error = None

    if request.method == "POST":
        form = request.form
        clean, error = _validate_income_form(form)
        if not error:
            queries.set_income(user_id, clean["amount"])
            flash("Income saved.", "success")
            return redirect(url_for("obligations"))

    return render_template("income.html", error=error, form=form)


@app.route("/obligations/add", methods=["GET", "POST"])
@login_required
@csrf_protect
def add_obligation():
    form = {}
    error = None

    if request.method == "POST":
        form = request.form
        clean, error = _validate_obligation_form(form)
        if not error and (
            len(queries.list_obligations(session["user_id"])) >= MAX_OBLIGATIONS
        ):
            error = f"You can have at most {MAX_OBLIGATIONS} obligations."
        if not error:
            queries.create_obligation(session["user_id"], **clean)
            flash("Obligation added.", "success")
            return redirect(url_for("obligations"))

    return _render_obligation_form(form, error)


@app.route("/obligations/<int:id>/edit", methods=["GET", "POST"])
@login_required
@csrf_protect
def edit_obligation(id):
    # Ownership is part of the query, so missing and foreign ids both 404.
    obligation = queries.get_obligation(id, session["user_id"])
    if obligation is None:
        abort(404)

    form = obligation
    error = None

    if request.method == "POST":
        form = request.form
        clean, error = _validate_obligation_form(form)
        if not error:
            # False means the row vanished since the read above.
            if not queries.update_obligation(id, session["user_id"], **clean):
                abort(404)
            flash("Obligation updated.", "success")
            return redirect(url_for("obligations"))

    return _render_obligation_form(form, error, obligation_id=id)


@app.route("/obligations/<int:id>/delete", methods=["GET", "POST"])
@login_required
@csrf_protect
def delete_obligation(id):
    obligation = queries.get_obligation(id, session["user_id"])
    if obligation is None:
        abort(404)

    if request.method == "POST":
        if not queries.delete_obligation(id, session["user_id"]):
            abort(404)
        flash("Obligation deleted.", "success")
        return redirect(url_for("obligations"))

    return render_template("delete_obligation.html", obligation=obligation)


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", port=5001)
