"""The Plan: a hypothetical split of Income across Obligations and Goals.

Pure functions only: no Flask, no database, and `today` is always passed in,
so the same inputs always give the same Plan (see docs/adr/0001).
Money is handled in integer cents so an Installment is never a cent short.
"""

from datetime import date, timedelta

# Must match IMPORTANCE_LEVELS in database/db.py (a test checks this).
IMPORTANCE_ORDER = ("Must-have", "Should-have", "Nice-to-have")

DAYS_PER_PERIOD = 30

# Statuses that count as a Goal being funded, for its dependents.
_MET = ("funded", "fully_funded")


def _cents(amount):
    return round(amount * 100)


def _euros(cents):
    return cents / 100


def _ceil_div(numerator, denominator):
    """Integer ceiling division, so money never goes through floats."""
    return -(-numerator // denominator)


def _months_left(deadline, today):
    """Whole pay periods until the Deadline; a past or near Deadline is 1."""
    try:
        days = (date.fromisoformat(deadline) - today).days
    except ValueError:
        # Writes are validated, so this only guards a hand-edited row.
        return 1
    return max(1, _ceil_div(days, DAYS_PER_PERIOD))


def _sort_key(goal):
    return (
        IMPORTANCE_ORDER.index(goal["importance"]),
        goal["deadline"],
        goal["id"],
    )


def _dependency_order(goals):
    """Importance order, but every Dependency comes before the Goal needing it."""
    known = {g["id"] for g in goals}
    waiting = sorted(goals, key=_sort_key)
    placed = set()
    ordered = []
    while waiting:
        # Take the first waiting Goal whose Dependencies are all placed.
        for goal in waiting:
            needed = [d for d in goal.get("depends_on", ()) if d in known]
            if all(d in placed for d in needed):
                break
        else:
            # Only a cycle (rejected on save) gets here; keep the base order.
            ordered.extend(waiting)
            break
        ordered.append(goal)
        placed.add(goal["id"])
        waiting.remove(goal)
    return ordered


def _first_unmet_dependency(goal, statuses):
    """Id of the first Dependency that is not funded, or None."""
    for dependency_id in goal.get("depends_on", ()):
        if dependency_id in statuses and statuses[dependency_id] not in _MET:
            return dependency_id
    return None


def plan(income, obligations, goals, today):
    """Return the Plan for `income` (a float) as a dict.

    Goals are funded in order of Importance, then earliest Deadline, then id,
    except that a Goal always comes after the Goals it depends on and is
    unreachable (`blocked_by` set) while any of them is unfunded.
    A Goal is funded in full or not at all; an Unreachable Goal takes nothing,
    and lower-ranked Goals are still tried with what is left.
    """
    obligations_cents = sum(_cents(o["amount"]) for o in obligations)
    available = _cents(income) - obligations_cents
    capacity = max(available, 0)

    names = {g["id"]: g["name"] for g in goals}
    statuses = {}
    items = []
    for goal in _dependency_order(goals):
        remaining = _cents(goal["target_amount"]) - _cents(goal["saved_amount"])
        months_left = _months_left(goal["deadline"], today)
        earliest_date = None
        blocked_by = None

        if remaining <= 0:
            status = "fully_funded"
            installment = 0
        elif (blocker := _first_unmet_dependency(goal, statuses)) is not None:
            # A Goal is useless until its Dependencies are funded.
            status = "unreachable"
            installment = 0
            blocked_by = names[blocker]
        else:
            # Round the Installment up to the cent.
            installment = _ceil_div(remaining, months_left)
            if installment <= capacity:
                status = "funded"
                capacity -= installment
            else:
                status = "unreachable"
                installment = 0
                if capacity > 0:
                    periods = _ceil_div(remaining, capacity)
                    earliest_date = (
                        today + timedelta(days=DAYS_PER_PERIOD * periods)
                    ).isoformat()

        statuses[goal["id"]] = status
        items.append(
            {
                **goal,
                "installment": _euros(installment),
                "months_left": months_left,
                "status": status,
                "earliest_date": earliest_date,
                "blocked_by": blocked_by,
            }
        )

    return {
        "income": _euros(_cents(income)),
        "obligations_total": _euros(obligations_cents),
        "available": _euros(available),
        "items": items,
        "surplus": _euros(capacity),
        "overcommitted": available < 0,
    }
