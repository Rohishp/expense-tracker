import calendar
import functools
import math
import os
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta

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

import planner
import suggestions
from database import queries
from database.db import (
    CATEGORIES,
    IMPORTANCE_LEVELS,
    get_db,
    init_db,
    seed_db,
)

app = Flask(__name__)
# Set SECRET_KEY in the environment for any real deployment; the fallback is
# only for local development.
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
# Lax stops browsers sending the session cookie on cross-site POSTs.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Tests turn this off so they can post forms without fetching a token first.
app.config["CSRF_ENABLED"] = True
# Forms are small; this stops huge bodies reaching the paid LLM call.
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
# Minimum seconds between Suggest clicks per session (tests set it to 0).
app.config["SUGGEST_COOLDOWN_SECONDS"] = 3

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
MAX_GOALS = 100
MAX_DEADLINE_YEARS = 50
# Every Plan loops over these, so they are capped like Goals (step 14 review).
MAX_TOP_UPS = 100
MAX_POT_AMOUNTS = 20


def _validate_amount(raw, allow_zero=False):
    """Return (amount, error) for a monetary form value.

    Zero is rejected unless `allow_zero` is set (used for a Saved Amount).
    """
    too_small = (
        "Amount must not be negative."
        if allow_zero
        else "Amount must be greater than zero."
    )
    try:
        amount = float(raw.strip())
    except ValueError:
        return None, "Amount must be a number."
    # float() accepts "nan" and "inf", so reject non-finite values explicitly.
    if not math.isfinite(amount):
        return None, "Amount must be a number."
    if amount < 0 or (amount == 0 and not allow_zero):
        return None, too_small
    if amount > MAX_AMOUNT:
        return None, f"Amount must be at most {MAX_AMOUNT:,}."
    amount = round(amount, 2)
    # Only a tiny positive value (e.g. 0.001) can round down to zero here.
    if amount == 0 and not allow_zero:
        return None, too_small
    return amount, None


def _validate_name(raw):
    """Return (name, error) for a required, length-limited name."""
    name = raw.strip()
    if not name:
        return None, "Name is required."
    if len(name) > MAX_NAME_LENGTH:
        return None, f"Name must be {MAX_NAME_LENGTH} characters or fewer."
    return name, None


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
    name, error = _validate_name(form.get("name", ""))
    if error:
        return None, error
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


# ------------------------------------------------------------------ #
# Goals                                                               #
# ------------------------------------------------------------------ #


def _validate_deadline(raw):
    """Return (deadline, error); a valid YYYY-MM-DD after today, within range."""
    deadline = raw.strip()
    try:
        deadline_date = datetime.strptime(deadline, "%Y-%m-%d").date()
    except ValueError:
        return None, "Please enter a valid Deadline."
    today = date.today()
    if deadline_date <= today:
        return None, "Deadline must be after today."
    if deadline_date > today + timedelta(days=365 * MAX_DEADLINE_YEARS):
        return None, f"Deadline must be within {MAX_DEADLINE_YEARS} years."
    return deadline, None


def _int_list(values):
    """Keep the values that are whole numbers, as ints.

    Used only to re-tick the Dependency boxes after a failed or suggested
    submit; saving goes through the strict `_validate_dependencies`.
    """
    result = []
    for value in values:
        try:
            result.append(int(value))
        except ValueError:
            pass
    return result


def _other_goals(user_id, goal_id=None):
    """The user's Goals, minus the one being edited (it cannot depend on itself)."""
    return [g for g in queries.list_goals(user_id) if g["id"] != goal_id]


def _creates_cycle(goal_id, new_dependencies, edges):
    """True if `goal_id` depending on `new_dependencies` would make a loop."""
    needs = {}
    for dependent, prerequisite in edges:
        if dependent != goal_id:
            needs.setdefault(dependent, []).append(prerequisite)
    seen = set()
    stack = list(new_dependencies)
    while stack:
        current = stack.pop()
        if current == goal_id:
            return True
        if current not in seen:
            seen.add(current)
            stack.extend(needs.get(current, ()))
    return False


def _validate_dependencies(raw_ids, user_id, goal_id=None):
    """Return (ids, error) for the Dependencies chosen on the Goal form."""
    try:
        ids = list(dict.fromkeys(int(raw) for raw in raw_ids))
    except ValueError:
        return None, "Please choose valid Dependencies."
    if goal_id is not None and goal_id in ids:
        return None, "A Goal cannot depend on itself."
    own_ids = {g["id"] for g in queries.list_goals(user_id)}
    if any(i not in own_ids for i in ids):
        return None, "Please choose valid Dependencies."
    if goal_id is not None and _creates_cycle(
        goal_id, ids, queries.get_dependency_edges(user_id)
    ):
        return None, "Those Dependencies would make a loop."
    return ids, None


def _validate_goal_form(form, user_id, goal_id=None):
    """Return (clean_values, error); keys match `queries.create_goal`."""
    name, error = _validate_name(form.get("name", ""))
    if error:
        return None, error

    target, error = _validate_amount(form.get("target_amount", ""))
    if error:
        return None, f"Target: {error}"

    raw_saved = form.get("saved_amount", "").strip()
    if raw_saved:
        saved, error = _validate_amount(raw_saved, allow_zero=True)
        if error:
            return None, f"Saved Amount: {error}"
        if saved > target:
            return None, "Saved Amount cannot be more than the target."
    else:
        saved = 0.0

    deadline, error = _validate_deadline(form.get("deadline", ""))
    if error:
        return None, error

    importance = form.get("importance", "")
    if importance not in IMPORTANCE_LEVELS:
        return None, "Please choose a valid Importance."

    depends_on, error = _validate_dependencies(
        form.getlist("depends_on"), user_id, goal_id
    )
    if error:
        return None, error

    return {
        "name": name,
        "target_amount": target,
        "saved_amount": saved,
        "deadline": deadline,
        "importance": importance,
        "depends_on": depends_on,
    }, None


def _render_goal_form(
    form, error=None, goal_id=None, selected=(), notice=None, others=None
):
    # `form` is a dict (add, or edit prefill) or request.form; the template
    # only calls .get() on it, so either works.
    if others is None:
        others = _other_goals(session["user_id"], goal_id)
    return render_template(
        "goal_form.html",
        error=error,
        notice=notice,
        form=form,
        goal_id=goal_id,
        editing=goal_id is not None,
        other_goals=others,
        selected_dependencies=list(selected),
        importance_levels=IMPORTANCE_LEVELS,
        max_name_length=MAX_NAME_LENGTH,
        min_deadline=(date.today() + timedelta(days=1)).isoformat(),
    )


@app.route("/goals")
@login_required
def goals():
    items = queries.list_goals(session["user_id"])
    names = {g["id"]: g["name"] for g in items}
    for g in items:
        g["depends_on_names"] = [names[d] for d in g["depends_on"] if d in names]
    return render_template("goals.html", goals=items)


@app.route("/goals/add", methods=["GET", "POST"])
@login_required
@csrf_protect
def add_goal():
    form = {"importance": "Should-have"}
    selected = []
    error = None

    if request.method == "POST":
        form = request.form
        selected = _int_list(form.getlist("depends_on"))
        clean, error = _validate_goal_form(form, session["user_id"])
        if not error and queries.count_goals(session["user_id"]) >= MAX_GOALS:
            error = f"You can have at most {MAX_GOALS} Goals."
        if not error:
            queries.create_goal(session["user_id"], **clean)
            flash("Goal added.", "success")
            return redirect(url_for("goals"))

    return _render_goal_form(form, error, selected=selected)


@app.route("/goals/<int:id>/edit", methods=["GET", "POST"])
@login_required
@csrf_protect
def edit_goal(id):
    # Ownership is part of the query, so missing and foreign ids both 404.
    goal = queries.get_goal(id, session["user_id"])
    if goal is None:
        abort(404)

    form = goal
    selected = goal["depends_on"]
    error = None

    if request.method == "POST":
        form = request.form
        selected = _int_list(form.getlist("depends_on"))
        clean, error = _validate_goal_form(form, session["user_id"], goal_id=id)
        if not error:
            # False means the row vanished since the read above.
            if not queries.update_goal(id, session["user_id"], **clean):
                abort(404)
            flash("Goal updated.", "success")
            return redirect(url_for("goals"))

    return _render_goal_form(form, error, goal_id=id, selected=selected)


def _goal_id_from_form(user_id):
    """The optional hidden `goal_id` (edit form); 404 if it is not the user's."""
    raw = request.form.get("goal_id", "")
    if not raw:
        return None
    try:
        goal_id = int(raw)
    except ValueError:
        abort(404)
    # SQLite integers are 64-bit; anything larger cannot be a real id.
    if not 0 < goal_id < 2**63 or queries.get_goal(goal_id, user_id) is None:
        abort(404)
    return goal_id


def _suggestion_inputs(form):
    """Validate what is sent to the paid LLM; return (name, target, deadline, error).

    The name must be valid. Target and Deadline are optional hints, so a
    value that does not validate is simply left out.
    """
    name, error = _validate_name(form.get("name", ""))
    if error:
        return None, "", "", error
    target, target_error = _validate_amount(form.get("target_amount", ""))
    deadline = form.get("deadline", "").strip()
    try:
        datetime.strptime(deadline, "%Y-%m-%d")
    except ValueError:
        deadline = ""
    return name, "" if target_error else target, deadline, None


def _suggest_cooldown_active():
    """True if this session asked for a suggestion too recently."""
    now = time.time()
    last = session.get("last_suggest", 0)
    if now - last < app.config["SUGGEST_COOLDOWN_SECONDS"]:
        return True
    session["last_suggest"] = now
    return False


@app.route("/goals/suggest", methods=["POST"])
@login_required
@csrf_protect
def suggest_goal():
    """Fill the Goal form with a suggested Importance and Dependencies.

    Nothing is saved: the user reviews the form and saves it (ADR 0001).
    """
    user_id = session["user_id"]
    goal_id = _goal_id_from_form(user_id)
    form = request.form.to_dict()
    selected = _int_list(request.form.getlist("depends_on"))
    others = _other_goals(user_id, goal_id)

    name, target, deadline, error = _suggestion_inputs(form)
    if error:
        notice = error
    elif _suggest_cooldown_active():
        notice = "Please wait a moment before asking again."
    else:
        result = suggestions.suggest(name, target, deadline, others)
        if result is None:
            notice = "Suggestions are unavailable right now. Choose manually."
        else:
            form["importance"] = result["importance"]
            selected = result["depends_on"]
            notice = "Suggested by AI. Review before saving."

    return _render_goal_form(
        form, goal_id=goal_id, selected=selected, notice=notice, others=others
    )


@app.route("/goals/<int:id>/delete", methods=["GET", "POST"])
@login_required
@csrf_protect
def delete_goal(id):
    goal = queries.get_goal(id, session["user_id"])
    if goal is None:
        abort(404)

    if request.method == "POST":
        if not queries.delete_goal(id, session["user_id"]):
            abort(404)
        flash("Goal deleted.", "success")
        return redirect(url_for("goals"))

    return render_template("delete_goal.html", goal=goal)


# ------------------------------------------------------------------ #
# Plan                                                                #
# ------------------------------------------------------------------ #


def _current_plan(user_id, today):
    """The user's Plan with their Top-ups and Savings Pot, or None without Income."""
    income = queries.get_income(user_id)
    if income is None:
        return None
    return planner.plan(
        income,
        queries.list_obligations(user_id),
        queries.list_goals(user_id),
        today,
        queries.list_top_ups(user_id),
        queries.list_pot_amounts(user_id),
    )


@app.route("/plan")
@login_required
def plan():
    return render_template(
        "plan.html", plan=_current_plan(session["user_id"], date.today())
    )


# ------------------------------------------------------------------ #
# Redirect Surplus                                                    #
# ------------------------------------------------------------------ #


def _to_cents(amount):
    return round(amount * 100)


def _find_redirect_source(values, result, today):
    """Where the money comes from, recomputed from the Plan; None if unknown.

    Only the user's own funded Goals are in `result["freed"]`, so a missing,
    foreign or unfunded `goal_id` is unknown in the same way.
    """
    if result is None:
        return None
    kind = values.get("source", "")
    if kind == "surplus":
        return {
            "kind": "surplus",
            "goal_id": None,
            "name": None,
            "start_date": today.isoformat(),
            "start_period": 0,
            "limit": result["surplus_limit"],
        }
    if kind == "goal":
        try:
            goal_id = int(values.get("goal_id", ""))
        except ValueError:
            return None
        for freed in result["freed"]:
            if freed["goal_id"] == goal_id:
                return {
                    "kind": "goal",
                    "goal_id": goal_id,
                    "name": freed["name"],
                    "start_date": freed["freed_date"],
                    "start_period": freed["freed_period"],
                    "limit": freed["limit"],
                }
    return None


def _redirect_context(user_id, values, today):
    """(source, targets) from the Plan as it is now; (None, None) if unknown."""
    result = _current_plan(user_id, today)
    source = _find_redirect_source(values, result, today)
    if source is None:
        return None, None
    targets = planner.redirect_targets(
        result, source["start_period"], source["goal_id"]
    )
    return source, targets


def _validate_redirect(form, targets, limit):
    """Return (clean, error); `clean` has `top_ups`, a list of (goal_id, amount),
    and `pot_amount` (or None). Empty fields are skipped."""
    top_ups = []
    for goal in targets:
        raw = form.get(f"topup_{goal['id']}", "").strip()
        if not raw:
            continue
        amount, error = _validate_amount(raw)
        if error:
            return None, f"Top-up for {goal['name']}: {error}"
        top_ups.append((goal["id"], amount))

    pot_amount = None
    raw_pot = form.get("pot_amount", "").strip()
    if raw_pot:
        pot_amount, error = _validate_amount(raw_pot)
        if error:
            return None, f"Savings Pot: {error}"

    if not top_ups and pot_amount is None:
        return None, "Enter at least one amount to redirect."
    total = sum(_to_cents(a) for _, a in top_ups) + _to_cents(pot_amount or 0)
    if total > _to_cents(limit):
        return None, f"You can redirect at most €{eur(limit)}."
    return {"top_ups": top_ups, "pot_amount": pot_amount}, None


def _check_redirect(user_id, form, source, targets):
    """Return (clean, error) for a redirect: valid, within the limit, under the caps."""
    clean, error = _validate_redirect(form, targets, source["limit"])
    if error:
        return None, error
    if queries.count_top_ups(user_id) + len(clean["top_ups"]) > MAX_TOP_UPS:
        return None, f"You can have at most {MAX_TOP_UPS} Top-ups. Delete some first."
    if clean["pot_amount"] and queries.count_pot_amounts(user_id) >= MAX_POT_AMOUNTS:
        return None, (
            f"You can have at most {MAX_POT_AMOUNTS} Savings Pot amounts. "
            "Delete some first."
        )
    return clean, None


@app.route("/plan/redirect", methods=["GET", "POST"])
@login_required
@csrf_protect
def redirect_money():
    user_id = session["user_id"]
    today = date.today()
    form = {}
    values = request.args
    if request.method == "POST":
        form = values = request.form
    source, targets = _redirect_context(user_id, values, today)
    if source is None:
        abort(404)
    error = None

    if request.method == "POST":
        clean, error = _check_redirect(user_id, form, source, targets)
        if not error:

            def still_fits():
                # Run again under the write lock: a redirect saved since the
                # check above may have used this money.
                fresh_source, fresh_targets = _redirect_context(user_id, form, today)
                return fresh_source is not None and not _check_redirect(
                    user_id, form, fresh_source, fresh_targets
                )[1]

            saved = queries.create_redirect(
                user_id,
                source["start_date"],
                clean["top_ups"],
                clean["pot_amount"],
                still_fits=still_fits,
            )
            if saved is None:
                error = "Your Plan changed meanwhile. Please try again."
            else:
                flash("Your money has been redirected.", "success")
                return redirect(url_for("plan"))

    return render_template(
        "redirect.html", source=source, targets=targets, form=form, error=error
    )


@app.route("/top-ups/<int:id>/delete", methods=["GET", "POST"])
@login_required
@csrf_protect
def delete_top_up(id):
    top_up = queries.get_top_up(id, session["user_id"])
    if top_up is None:
        abort(404)

    if request.method == "POST":
        if not queries.delete_top_up(id, session["user_id"]):
            abort(404)
        flash("Top-up deleted.", "success")
        return redirect(url_for("plan"))

    return render_template(
        "delete_top_up.html",
        top_up=top_up,
        starts_later=top_up["start_date"] > date.today().isoformat(),
    )


@app.route("/savings-pot/<int:id>/delete", methods=["GET", "POST"])
@login_required
@csrf_protect
def delete_pot_amount(id):
    pot_amount = queries.get_pot_amount(id, session["user_id"])
    if pot_amount is None:
        abort(404)

    if request.method == "POST":
        if not queries.delete_pot_amount(id, session["user_id"]):
            abort(404)
        flash("Savings Pot amount deleted.", "success")
        return redirect(url_for("plan"))

    return render_template(
        "delete_pot_amount.html",
        pot_amount=pot_amount,
        starts_later=pot_amount["start_date"] > date.today().isoformat(),
    )


def _validate_lump_sum(raw, remaining):
    """Return (amount, error) for a Lump Sum on a Goal with `remaining` to save."""
    amount, error = _validate_amount(raw)
    if not error and amount > remaining:
        return None, f"A Lump Sum cannot be more than the €{eur(remaining)} left to save."
    return amount, error


@app.route("/goals/<int:id>/lump-sum", methods=["GET", "POST"])
@login_required
@csrf_protect
def lump_sum(id):
    user_id = session["user_id"]
    goal = queries.get_goal(id, user_id)
    if goal is None:
        abort(404)

    remaining = round(goal["target_amount"] - goal["saved_amount"], 2)
    error = None

    if request.method == "POST":
        amount, error = _validate_lump_sum(request.form.get("amount", ""), remaining)
        if not error and not queries.add_lump_sum(id, user_id, amount):
            error = "This Goal changed meanwhile. Please try again."
        if not error:
            flash("Lump Sum added.", "success")
            return redirect(url_for("plan"))

    periods = planner.periods_until(goal["deadline"], date.today())
    return render_template(
        "lump_sum.html",
        goal=goal,
        remaining=remaining,
        periods=periods,
        installment=planner.installment_for(remaining, periods) if remaining > 0 else 0,
        form=request.form,
        error=error,
    )


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", port=5001)
