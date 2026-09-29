"""Pure query helpers for Spendly's profile page.

No Flask imports here — these are plain functions over sqlite3 rows so they
can be unit-tested in isolation from the web layer. Each function opens its
own connection via get_db() and closes it before returning.
"""

from datetime import datetime

from database.db import get_db


def _date_clause(date_from, date_to):
    """Return (sql_fragment, params) for an inclusive date range.

    The range applies only when both bounds are given. The fragment is a
    fixed string; the dates travel as bound parameters.
    """
    if date_from and date_to:
        return " AND date BETWEEN ? AND ?", (date_from, date_to)
    return "", ()


def create_expense(user_id, amount, category, expense_date, description):
    """Insert an expense for `user_id` and return its new id."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO expenses (user_id, amount, category, date, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, category, expense_date, description),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


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


def get_summary_stats(user_id, date_from=None, date_to=None):
    """Return {"total_spent", "transaction_count", "top_category"}.

    Zero-expense users get {"total_spent": 0, "transaction_count": 0,
    "top_category": "—"}. top_category is the category with the highest
    summed amount (not count). `date_from`/`date_to` (YYYY-MM-DD, inclusive)
    limit the expenses considered when both are given.

    Subagent: summary
    """
    date_sql, date_params = _date_clause(date_from, date_to)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT category, amount FROM expenses WHERE user_id = ?" + date_sql,
            (user_id, *date_params),
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


def get_recent_transactions(user_id, limit=10, date_from=None, date_to=None):
    """Return a list of {"date", "description", "category", "amount"},
    newest-first, limited to `limit` rows. Empty list if no expenses.
    `date_from`/`date_to` (YYYY-MM-DD, inclusive) narrow the rows before
    `limit` is applied, when both are given.

    Subagent: transaction-history
    """
    date_sql, date_params = _date_clause(date_from, date_to)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT date, description, category, amount FROM expenses "
            "WHERE user_id = ?" + date_sql +
            " ORDER BY date DESC, id DESC LIMIT ?",
            (user_id, *date_params, limit),
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


def get_category_breakdown(user_id, date_from=None, date_to=None):
    """Return a list of {"name", "amount", "pct"}, ordered by amount desc.

    `pct` values are ints that sum to exactly 100 (rounding remainder goes to
    the largest category). Empty list if no expenses. `date_from`/`date_to`
    (YYYY-MM-DD, inclusive) limit the expenses considered when both are given.

    Subagent: category-breakdown
    """
    date_sql, date_params = _date_clause(date_from, date_to)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT category, SUM(amount) AS total FROM expenses "
            "WHERE user_id = ?" + date_sql + " GROUP BY category",
            (user_id, *date_params),
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
