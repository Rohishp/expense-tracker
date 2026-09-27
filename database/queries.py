"""Pure query helpers for Spendly's profile page.

No Flask imports here — these are plain functions over sqlite3 rows so they
can be unit-tested in isolation from the web layer. Each function opens its
own connection via get_db() and closes it before returning.
"""

from datetime import datetime

from database.db import get_db


def get_user_by_id(user_id):
    """Return {"name", "email", "member_since"} for the user, or None.

    Subagent: summary
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT name, email, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        member_since = datetime.strptime(
            row["created_at"], "%Y-%m-%d %H:%M:%S"
        ).strftime("%B %Y")
        return {
            "name": row["name"],
            "email": row["email"],
            "member_since": member_since,
        }
    finally:
        conn.close()


def get_summary_stats(user_id):
    """Return {"total_spent", "transaction_count", "top_category"}.

    Zero-expense users get {"total_spent": 0, "transaction_count": 0,
    "top_category": "—"}. top_category is the category with the highest
    summed amount (not count).

    Subagent: summary
    """
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT category, amount FROM expenses WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        if not rows:
            return {
                "total_spent": 0,
                "transaction_count": 0,
                "top_category": "—",
            }
        total_spent = 0.0
        transaction_count = 0
        category_totals = {}
        for row in rows:
            amount = float(row["amount"])
            total_spent += amount
            transaction_count += 1
            category = row["category"]
            category_totals[category] = category_totals.get(category, 0.0) + amount
        top_category = max(category_totals, key=category_totals.get)
        return {
            "total_spent": total_spent,
            "transaction_count": transaction_count,
            "top_category": top_category,
        }
    finally:
        conn.close()


def get_recent_transactions(user_id, limit=10):
    """Return a list of {"date", "description", "category", "amount"},
    newest-first, limited to `limit` rows. Empty list if no expenses.

    Subagent: transaction-history
    """
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT date, description, category, amount FROM expenses "
            "WHERE user_id = ? ORDER BY date DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [
            {
                "date": row["date"],
                "description": row["description"],
                "category": row["category"],
                "amount": float(row["amount"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_category_breakdown(user_id):
    """Return a list of {"name", "amount", "pct"}, ordered by amount desc.

    `pct` values are ints that sum to exactly 100 (rounding remainder goes to
    the largest category). Empty list if no expenses.

    Subagent: category-breakdown
    """
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT category, SUM(amount) AS total FROM expenses "
            "WHERE user_id = ? GROUP BY category",
            (user_id,),
        ).fetchall()
        if not rows:
            return []

        categories = [
            {"name": row["category"], "amount": float(row["total"])}
            for row in rows
        ]
        total_spent_all = sum(c["amount"] for c in categories)

        for c in categories:
            c["pct"] = round(c["amount"] / total_spent_all * 100)

        remainder = 100 - sum(c["pct"] for c in categories)
        largest = max(categories, key=lambda c: c["amount"])
        largest["pct"] += remainder

        categories.sort(key=lambda c: c["amount"], reverse=True)
        return categories
    finally:
        conn.close()
