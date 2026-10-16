"""The Plan: a hypothetical split of Income across Obligations and Goals.

Pure functions only: no Flask, no database, and `today` is always passed in,
so the same inputs always give the same Plan (see docs/adr/0001).
Money is handled in integer cents so an Installment is never a cent short.
"""

from datetime import date, timedelta
from itertools import accumulate
from typing import NamedTuple

# Must match IMPORTANCE_LEVELS in database/db.py (a test checks this).
IMPORTANCE_ORDER = ("Must-have", "Should-have", "Nice-to-have")

DAYS_PER_PERIOD = 30

# Statuses that count as a Goal being funded, for its dependents.
_MET = ("funded", "fully_funded")


class Scheduled(NamedTuple):
    """An amount per period from a start period: a Top-up or Savings Pot amount."""

    start_period: int
    cents: int


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


def periods_until(deadline, today):
    """Whole pay periods until a Deadline, counted as the Plan counts them."""
    return _months_left(deadline, today)


def installment_for(remaining, periods):
    """The even Installment for `remaining` euros over `periods`, rounded up."""
    return _euros(_ceil_div(_cents(remaining), periods))


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


def _period_of(start_date, today):
    """The pay period a start date falls in; today or earlier is period 0."""
    try:
        days = (date.fromisoformat(start_date) - today).days
    except ValueError:
        return 0
    return max(0, _ceil_div(days, DAYS_PER_PERIOD))


def _paid_in(scheduled, period):
    """Cents per period that `scheduled` amounts add in `period`."""
    return sum(s.cents for s in scheduled if s.start_period <= period)


def _last_paying_period(remaining_cents, installment_cents, top_ups, months_left):
    """The last period a funded Goal pays into; it is freed from the next one.

    Its Installment alone covers `remaining_cents` within `months_left`
    periods, so the search never has to look further.
    """
    paid = 0
    for period in range(months_left):
        paid += installment_cents + _paid_in(top_ups, period)
        if paid >= remaining_cents:
            return period
    return months_left - 1


def _fund_top_ups(top_ups, items, capacity, today):
    """Fund Top-ups after every Goal, in Goal walk order then id.

    Returns (Top-ups with their status, the Top-ups that use money per Goal
    id as Scheduled lists, the capacity left). Paused and finished Top-ups
    take nothing; a later Top-up is still tried.
    """
    by_id = {item["id"]: (rank, item) for rank, item in enumerate(items)}
    known = [t for t in top_ups if t["goal_id"] in by_id]
    counted = {}
    result = []
    for top_up in sorted(known, key=lambda t: (by_id[t["goal_id"]][0], t.get("id", 0))):
        goal = by_id[top_up["goal_id"]][1]
        scheduled = Scheduled(
            _period_of(top_up["start_date"], today), _cents(top_up["amount"])
        )
        if goal["status"] == "fully_funded":
            status = "finished"
        elif goal["status"] != "funded":
            status = "paused"
        elif scheduled.start_period > 0:
            status = "scheduled"
        elif scheduled.cents <= capacity:
            status = "active"
            capacity -= scheduled.cents
        else:
            status = "paused"
        if status in ("active", "scheduled"):
            counted.setdefault(goal["id"], []).append(scheduled)
        result.append(
            {
                **top_up,
                "goal_name": goal["name"],
                "status": status,
                "start_period": scheduled.start_period,
            }
        )
    return result, counted, capacity


def _forecast_freed(items, installment_cents, remaining_cents, counted, today):
    """Add to each funded item when it stops paying and what it frees.

    Returns {goal_id: freed_period}, the first period the Goal no longer pays.
    """
    freed_periods = {}
    for item in items:
        item["freed_period"] = item["freed_date"] = item["freed_amount"] = None
        goal_id = item["id"]
        if goal_id not in installment_cents:
            continue
        top_ups = counted.get(goal_id, [])
        last = _last_paying_period(
            remaining_cents[goal_id], installment_cents[goal_id], top_ups,
            item["months_left"],
        )
        freed_periods[goal_id] = item["freed_period"] = last + 1
        item["freed_date"] = (
            today + timedelta(days=DAYS_PER_PERIOD * (last + 1))
        ).isoformat()
        item["freed_amount"] = _euros(installment_cents[goal_id] + _paid_in(top_ups, last))
    return freed_periods


def _unused_by_period(capacity, installment_cents, counted, freed_periods, pot, pot_funded):
    """Cents not used in each period, from now until nothing changes any more.

    Period 0 is the Surplus. Later periods lose each Goal once it is funded and
    gain the Top-ups and Savings Pot amounts that start by then.
    """
    starts = [s.start_period for s in pot] + [
        s.start_period for scheduled in counted.values() for s in scheduled
    ]
    horizon = max([0, *freed_periods.values(), *starts])
    unused = []
    for period in range(horizon + 1):
        if period == 0:
            used = pot_funded
        else:
            used = _paid_in(pot, period)
        for goal_id, installment in installment_cents.items():
            if freed_periods[goal_id] > period:
                used += installment + _paid_in(counted.get(goal_id, ()), period)
        unused.append(capacity - used)
    return unused


def plan(income, obligations, goals, today, top_ups=(), pot_amounts=()):
    """Return the Plan for `income` (a float) as a dict.

    Goals are funded in order of Importance, then earliest Deadline, then id,
    except that a Goal always comes after the Goals it depends on and is
    unreachable (`blocked_by` set) while any of them is unfunded.
    A Goal is funded in full or not at all; an Unreachable Goal takes nothing,
    and lower-ranked Goals are still tried with what is left.

    Top-ups and Savings Pot amounts (dicts with `amount` and `start_date`,
    Top-ups also `goal_id`) are funded after every Goal, so the Savings Pot is
    cut first and a Top-up is paused before any Goal becomes Unreachable. The
    Plan never redirects money itself (docs/adr/0002); it only forecasts each
    Freed Installment and how much of it is still free to redirect.
    """
    obligations_cents = sum(_cents(o["amount"]) for o in obligations)
    available = _cents(income) - obligations_cents
    capacity = start_capacity = max(available, 0)

    names = {g["id"]: g["name"] for g in goals}
    statuses = {}
    # Only for funded Goals with something left to save.
    installment_cents = {}
    remaining_cents = {}
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
                installment_cents[goal["id"]] = installment
                remaining_cents[goal["id"]] = remaining
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

    top_up_items, counted, capacity = _fund_top_ups(top_ups, items, capacity, today)

    # The Savings Pot comes last, so it is the first thing cut.
    pot_rows = [
        {**p, "start_period": _period_of(p["start_date"], today)} for p in pot_amounts
    ]
    pot = [Scheduled(p["start_period"], _cents(p["amount"])) for p in pot_rows]
    pot_set = _paid_in(pot, 0)
    pot_funded = min(pot_set, capacity)
    capacity -= pot_funded

    freed_periods = _forecast_freed(
        items, installment_cents, remaining_cents, counted, today
    )
    unused = _unused_by_period(
        start_capacity, installment_cents, counted, freed_periods, pot, pot_funded
    )
    # free_from[p]: cents free in period p and every later one.
    free_from = [max(0, c) for c in accumulate(reversed(unused), min)][::-1]

    rank = {item["id"]: index for index, item in enumerate(items)}
    freed = []
    for item in items:
        period = item["freed_period"]
        if period is None:
            continue
        # Only money that becomes free in this period and stays free can be
        # redirected from it; money free earlier has its own redirect.
        newly_free = max(0, free_from[period] - unused[period - 1])
        freed.append(
            {
                "goal_id": item["id"],
                "name": item["name"],
                "freed_period": period,
                "freed_date": item["freed_date"],
                "amount": item["freed_amount"],
                "limit": _euros(min(_cents(item["freed_amount"]), newly_free)),
            }
        )
    freed.sort(key=lambda f: (f["freed_period"], rank[f["goal_id"]]))

    return {
        "income": _euros(_cents(income)),
        "obligations_total": _euros(obligations_cents),
        "available": _euros(available),
        "items": items,
        "surplus": _euros(capacity),
        "overcommitted": available < 0,
        "top_ups": top_up_items,
        "savings_pot": {
            "set": _euros(pot_set),
            "funded": _euros(pot_funded),
            "amounts": pot_rows,
        },
        "freed": freed,
        "surplus_limit": _euros(free_from[0]),
    }


def redirect_targets(result, start_period, source_goal_id=None):
    """The funded Goals a redirect may top up: not the source Goal, and still
    paying after the money frees (a Top-up on a Goal funded by then would never
    be used)."""
    return [
        item
        for item in result["items"]
        if item["freed_period"] is not None
        and item["freed_period"] > start_period
        and item["id"] != source_goal_id
    ]
