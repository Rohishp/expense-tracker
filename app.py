import functools
import sqlite3

from flask import Flask, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

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
    user = {
        "name": session["user_name"],
        "email": session["user_email"],
        "member_since": "March 2024",
    }
    initials = "".join(part[0].upper() for part in user["name"].split()[:2])

    transactions = [
        {"date": "2026-09-24", "description": "Grocery run", "category": "Food", "amount": 42.30},
        {"date": "2026-09-22", "description": "Metro card top-up", "category": "Transport", "amount": 15.00},
        {"date": "2026-09-20", "description": "Electricity bill", "category": "Bills", "amount": 68.50},
        {"date": "2026-09-18", "description": "Pharmacy", "category": "Health", "amount": 24.75},
        {"date": "2026-09-15", "description": "Movie night", "category": "Entertainment", "amount": 18.00},
    ]

    total_spent = sum(t["amount"] for t in transactions)
    transaction_count = len(transactions)

    totals_by_category = {}
    for t in transactions:
        totals_by_category[t["category"]] = totals_by_category.get(t["category"], 0) + t["amount"]
    top_category = max(totals_by_category, key=totals_by_category.get)

    category_breakdown = [
        {
            "category": category,
            "total": amount,
            "percent": round(amount / total_spent * 100) if total_spent else 0,
        }
        for category, amount in sorted(totals_by_category.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return render_template(
        "profile.html",
        user=user,
        initials=initials,
        total_spent=total_spent,
        transaction_count=transaction_count,
        top_category=top_category,
        transactions=transactions,
        category_breakdown=category_breakdown,
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
