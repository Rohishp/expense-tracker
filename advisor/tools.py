"""Tools the advisor agent may call.

None of these writes to the database. The agent works on a snapshot of the
user's Income, Obligations and Goals; every number it sees comes from
planner.plan(), so the LLM never does money arithmetic (docs/adr/0001).
"""

import copy
import json
from datetime import date

import planner

IMPORTANCE_LEVELS = list(planner.IMPORTANCE_ORDER)
CHANGE_OPS = [
    "add_goal",
    "update_goal",
    "remove_goal",
    "set_income",
    "add_obligation",
    "update_obligation",
    "remove_obligation",
]


class ToolError(Exception):
    """A bad tool call. The message goes back to the model so it can retry."""


# ---------------------------------------------------------------- schemas

CHANGE_SCHEMA = {
    "type": "object",
    "description": "One hypothetical change. Only the fields its op needs.",
    "properties": {
        "op": {"type": "string", "enum": CHANGE_OPS},
        "goal_id": {"type": "integer"},
        "obligation_id": {"type": "integer"},
        "name": {"type": "string"},
        "amount": {
            "type": "number",
            "description": "Income or Obligation amount in euros per month",
        },
        "target_amount": {"type": "number"},
        "saved_amount": {"type": "number"},
        "deadline": {"type": "string", "description": "YYYY-MM-DD"},
        "importance": {"type": "string", "enum": IMPORTANCE_LEVELS},
    },
    "required": ["op"],
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_finances",
            "description": (
                "Read the user's Income, Obligations, Goals and their current "
                "Plan. Call this first."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_plan",
            "description": (
                "Run the Plan on a hypothetical copy with these changes applied. "
                "Changes nothing. Returns the new Plan and what changed versus "
                "the current Plan. Use it for every what-if; never calculate "
                "Installments or dates yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {"changes": {"type": "array", "items": CHANGE_SCHEMA}},
                "required": ["changes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "final_answer",
            "description": (
                "Finish. verdict: 'yes' if the request fits as asked, "
                "'trade_off' if it only fits when something else changes, "
                "'no' if no reasonable change makes it fit. proposed_changes "
                "are shown to the user, who must confirm them before anything "
                "is saved."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["yes", "trade_off", "no"]},
                    "summary": {"type": "string"},
                    "proposed_changes": {"type": "array", "items": CHANGE_SCHEMA},
                },
                "required": ["verdict", "summary", "proposed_changes"],
            },
        },
    },
]


# ---------------------------------------------------------------- snapshot


def load_snapshot(user_id):
    """Read the user's data once. The agent only ever sees this copy."""
    from database import queries  # lazy: keeps the tools testable without a DB

    return {
        "income": queries.get_income(user_id),
        "obligations": queries.list_obligations(user_id),
        "goals": queries.list_goals(user_id),
    }


def _find(items, item_id, label):
    for item in items:
        if item["id"] == item_id:
            return item
    raise ToolError(f"No {label} with id {item_id}.")


def _positive(value, field, allow_zero=False):
    if not isinstance(value, (int, float)) or value < 0 or (value == 0 and not allow_zero):
        raise ToolError(f"{field} must be a {'non-negative' if allow_zero else 'positive'} number.")
    return float(value)


def _check_goal(goal, today):
    _positive(goal["target_amount"], "target_amount")
    _positive(goal["saved_amount"], "saved_amount", allow_zero=True)
    if goal["saved_amount"] > goal["target_amount"]:
        raise ToolError("saved_amount cannot be more than target_amount.")
    try:
        deadline = date.fromisoformat(goal["deadline"])
    except (TypeError, ValueError):
        raise ToolError("deadline must be YYYY-MM-DD.")
    if deadline <= today:
        raise ToolError("deadline must be after today.")
    if goal["importance"] not in IMPORTANCE_LEVELS:
        raise ToolError(f"importance must be one of {IMPORTANCE_LEVELS}.")
    if not goal.get("name"):
        raise ToolError("name is required.")


def apply_changes(snapshot, changes, today):
    """Return a new snapshot with `changes` applied; the input is untouched."""
    state = copy.deepcopy(snapshot)
    next_id = 1 + max([g["id"] for g in state["goals"]] + [0])
    for change in changes:
        op = change.get("op")
        if op == "add_goal":
            goal = {
                "id": next_id,
                "name": change.get("name"),
                "target_amount": change.get("target_amount"),
                "saved_amount": change.get("saved_amount", 0),
                "deadline": change.get("deadline"),
                "importance": change.get("importance"),
            }
            _check_goal(goal, today)
            state["goals"].append(goal)
            next_id += 1
        elif op == "update_goal":
            goal = _find(state["goals"], change.get("goal_id"), "Goal")
            fields = ("name", "target_amount", "saved_amount", "deadline", "importance")
            goal.update({k: change[k] for k in fields if k in change})
            _check_goal(goal, today)
        elif op == "remove_goal":
            goal = _find(state["goals"], change.get("goal_id"), "Goal")
            state["goals"].remove(goal)
        elif op == "set_income":
            state["income"] = _positive(change.get("amount"), "amount")
        elif op == "add_obligation":
            if not change.get("name"):
                raise ToolError("name is required.")
            new_id = 1 + max([o["id"] for o in state["obligations"]] + [0])
            state["obligations"].append(
                {"id": new_id, "name": change["name"], "amount": _positive(change.get("amount"), "amount")}
            )
        elif op == "update_obligation":
            obligation = _find(state["obligations"], change.get("obligation_id"), "Obligation")
            if "name" in change:
                obligation["name"] = change["name"]
            if "amount" in change:
                obligation["amount"] = _positive(change["amount"], "amount")
        elif op == "remove_obligation":
            obligation = _find(state["obligations"], change.get("obligation_id"), "Obligation")
            state["obligations"].remove(obligation)
        else:
            raise ToolError(f"Unknown op {op!r}. Use one of {CHANGE_OPS}.")
    return state


# ---------------------------------------------------------------- plan views


def run_plan(snapshot, today):
    if snapshot["income"] is None:
        raise ToolError("Income is not set. Add a set_income change to simulate.")
    return planner.plan(snapshot["income"], snapshot["obligations"], snapshot["goals"], today)


def _compact(plan):
    """The Plan without fields the model does not need (fewer tokens)."""
    return {
        "income": plan["income"],
        "obligations_total": plan["obligations_total"],
        "available": plan["available"],
        "surplus": plan["surplus"],
        "overcommitted": plan["overcommitted"],
        "goals": [
            {
                k: item[k]
                for k in ("id", "name", "importance", "deadline", "target_amount",
                          "saved_amount", "installment", "status", "earliest_date")
            }
            for item in plan["items"]
        ],
    }


def diff_plans(before, after):
    """What a change did: every Goal whose status or Installment moved."""
    old = {i["id"]: i for i in before["items"]}
    changed = []
    for item in after["items"]:
        prev = old.pop(item["id"], None)
        if prev is None:
            changed.append({"goal": item["name"], "change": "added", "status": item["status"],
                            "installment": item["installment"]})
        elif (prev["status"], prev["installment"]) != (item["status"], item["installment"]):
            changed.append({"goal": item["name"], "status": [prev["status"], item["status"]],
                            "installment": [prev["installment"], item["installment"]],
                            "earliest_date": item["earliest_date"]})
    for prev in old.values():
        changed.append({"goal": prev["name"], "change": "removed"})
    return {"goals_changed": changed, "surplus": [before["surplus"], after["surplus"]]}


# ---------------------------------------------------------------- dispatch


def dispatch(name, raw_arguments, snapshot, today):
    """Run one tool call. Returns (result_dict, is_final)."""
    try:
        args = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return {"error": "Arguments were not valid JSON."}, False
    try:
        if name == "get_finances":
            result = {"today": today.isoformat(), "obligations": snapshot["obligations"]}
            result["plan"] = _compact(run_plan(snapshot, today)) if snapshot["income"] is not None else None
            return result, False
        if name == "simulate_plan":
            before = run_plan(snapshot, today) if snapshot["income"] is not None else None
            after = run_plan(apply_changes(snapshot, args.get("changes", []), today), today)
            result = {"plan": _compact(after)}
            if before is not None:
                result["diff"] = diff_plans(before, after)
            return result, False
        if name == "final_answer":
            # Reject proposals that would not even validate, so the user
            # is never shown a change the app would refuse to save.
            apply_changes(snapshot, args.get("proposed_changes", []), today)
            if args.get("verdict") not in ("yes", "trade_off", "no"):
                raise ToolError("verdict must be yes, trade_off or no.")
            return args, True
        return {"error": f"Unknown tool {name!r}."}, False
    except ToolError as exc:
        return {"error": str(exc)}, False
