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


def get_expense(expense_id, user_id):
    """Return the expense as a dict if it belongs to `user_id`, else None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, amount, category, date, description FROM expenses "
            "WHERE id = ? AND user_id = ?",
            (expense_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "amount": float(row["amount"]),
            "category": row["category"],
            "date": row["date"],
            "description": row["description"],
        }
    finally:
        conn.close()


def update_expense(
    expense_id, user_id, amount, category, expense_date, description
):
    """Update an expense owned by `user_id`. Return True if a row changed."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE expenses SET amount = ?, category = ?, date = ?, "
            "description = ? WHERE id = ? AND user_id = ?",
            (amount, category, expense_date, description, expense_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_expense(expense_id, user_id):
    """Delete an expense owned by `user_id`. Return True if a row was deleted."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "DELETE FROM expenses WHERE id = ? AND user_id = ?",
            (expense_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0
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
            "SELECT id, date, description, category, amount FROM expenses "
            "WHERE user_id = ?" + date_sql +
            " ORDER BY date DESC, id DESC LIMIT ?",
            (user_id, *date_params, limit),
        ).fetchall()
        return [
            {
                "id": row["id"],
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


def get_income(user_id):
    """Return the user's monthly Income as a float, or None if not set."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT monthly_amount FROM incomes WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return None if row is None else float(row["monthly_amount"])
    finally:
        conn.close()


def set_income(user_id, amount):
    """Create or replace the user's monthly Income."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO incomes (user_id, monthly_amount) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "monthly_amount = excluded.monthly_amount, "
            "updated_at = datetime('now')",
            (user_id, amount),
        )
        conn.commit()
    finally:
        conn.close()


def list_obligations(user_id):
    """Return the user's Obligations as dicts, oldest first."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, amount FROM obligations "
            "WHERE user_id = ? ORDER BY created_at, id",
            (user_id,),
        ).fetchall()
        return [
            {"id": r["id"], "name": r["name"], "amount": float(r["amount"])}
            for r in rows
        ]
    finally:
        conn.close()


def get_obligation(obligation_id, user_id):
    """Return the Obligation as a dict if it belongs to `user_id`, else None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, name, amount FROM obligations "
            "WHERE id = ? AND user_id = ?",
            (obligation_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "amount": float(row["amount"]),
        }
    finally:
        conn.close()


def create_obligation(user_id, name, amount):
    """Insert an Obligation for `user_id` and return its new id."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO obligations (user_id, name, amount) VALUES (?, ?, ?)",
            (user_id, name, amount),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_obligation(obligation_id, user_id, name, amount):
    """Update an Obligation owned by `user_id`. Return True if a row changed."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE obligations SET name = ?, amount = ? "
            "WHERE id = ? AND user_id = ?",
            (name, amount, obligation_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_obligation(obligation_id, user_id):
    """Delete an Obligation owned by `user_id`. Return True if a row was deleted."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "DELETE FROM obligations WHERE id = ? AND user_id = ?",
            (obligation_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


_GOAL_COLUMNS = "id, name, target_amount, saved_amount, deadline, importance"


def _goal_to_dict(row, depends_on=()):
    return {
        "id": row["id"],
        "name": row["name"],
        "target_amount": float(row["target_amount"]),
        "saved_amount": float(row["saved_amount"]),
        "deadline": row["deadline"],
        "importance": row["importance"],
        "depends_on": list(depends_on),
    }


def _replace_dependencies(conn, goal_id, user_id, depends_on):
    """Set the Goals that `goal_id` depends on (no commit).

    Ownership is enforced in SQL: only Goals of `user_id` can be inserted,
    and a Goal can never depend on itself.
    """
    conn.execute("DELETE FROM goal_dependencies WHERE goal_id = ?", (goal_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO goal_dependencies (goal_id, depends_on_goal_id) "
        "SELECT ?, id FROM goals WHERE id = ? AND user_id = ? AND id != ?",
        [(goal_id, d, user_id, goal_id) for d in depends_on],
    )


def _dependency_edges(conn, user_id):
    rows = conn.execute(
        "SELECT d.goal_id, d.depends_on_goal_id FROM goal_dependencies d "
        "JOIN goals g ON g.id = d.goal_id WHERE g.user_id = ?",
        (user_id,),
    ).fetchall()
    return [(r["goal_id"], r["depends_on_goal_id"]) for r in rows]


def get_dependency_edges(user_id):
    """Return every (goal_id, depends_on_goal_id) pair for the user's Goals."""
    conn = get_db()
    try:
        return _dependency_edges(conn, user_id)
    finally:
        conn.close()


def list_goals(user_id):
    """Return the Goals of `user_id` as dicts, earliest Deadline first."""
    conn = get_db()
    try:
        rows = conn.execute(
            f"SELECT {_GOAL_COLUMNS} FROM goals "
            "WHERE user_id = ? ORDER BY deadline, id",
            (user_id,),
        ).fetchall()
        dependencies = {}
        for goal_id, depends_on_id in _dependency_edges(conn, user_id):
            dependencies.setdefault(goal_id, []).append(depends_on_id)
        return [
            _goal_to_dict(r, sorted(dependencies.get(r["id"], ()))) for r in rows
        ]
    finally:
        conn.close()


def count_goals(user_id):
    """Return how many Goals `user_id` has."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM goals WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row["count"]
    finally:
        conn.close()


def get_goal(goal_id, user_id):
    """Return the Goal as a dict if it belongs to `user_id`, else None."""
    conn = get_db()
    try:
        row = conn.execute(
            f"SELECT {_GOAL_COLUMNS} FROM goals WHERE id = ? AND user_id = ?",
            (goal_id, user_id),
        ).fetchone()
        if row is None:
            return None
        dependencies = conn.execute(
            "SELECT depends_on_goal_id FROM goal_dependencies "
            "WHERE goal_id = ? ORDER BY depends_on_goal_id",
            (goal_id,),
        ).fetchall()
        return _goal_to_dict(row, [d["depends_on_goal_id"] for d in dependencies])
    finally:
        conn.close()


def create_goal(
    user_id,
    name,
    target_amount,
    saved_amount,
    deadline,
    importance,
    depends_on=(),
):
    """Insert a Goal for `user_id` and return its new id."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO goals "
            "(user_id, name, target_amount, saved_amount, deadline, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, target_amount, saved_amount, deadline, importance),
        )
        _replace_dependencies(conn, cursor.lastrowid, user_id, depends_on)
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_goal(
    goal_id,
    user_id,
    name,
    target_amount,
    saved_amount,
    deadline,
    importance,
    depends_on=(),
):
    """Update a Goal owned by `user_id`. Return True if a row changed."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE goals SET name = ?, target_amount = ?, saved_amount = ?, "
            "deadline = ?, importance = ? WHERE id = ? AND user_id = ?",
            (
                name,
                target_amount,
                saved_amount,
                deadline,
                importance,
                goal_id,
                user_id,
            ),
        )
        if cursor.rowcount > 0:
            _replace_dependencies(conn, goal_id, user_id, depends_on)
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_goal(goal_id, user_id):
    """Delete a Goal owned by `user_id`. Return True if a row was deleted.

    Rows in goal_dependencies that mention it go too (ON DELETE CASCADE).
    """
    conn = get_db()
    try:
        cursor = conn.execute(
            "DELETE FROM goals WHERE id = ? AND user_id = ?",
            (goal_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
