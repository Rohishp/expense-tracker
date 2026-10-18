"""Tools the advisor agent may call.

None of these writes to the database. The agent works on a snapshot of the
user's Income, Obligations and Goals; every number it sees comes from
planner.plan(), so the LLM never does money arithmetic (docs/adr/0001).
"""

import copy
import json
import math
import re
from datetime import date, timedelta

import planner
from advisor.tracing import traceable
from database.db import (
    MAX_AMOUNT,
    MAX_DEADLINE_YEARS,
    MAX_GOALS,
    MAX_NAME_LENGTH,
    MAX_OBLIGATIONS,
)

IMPORTANCE_LEVELS = list(planner.IMPORTANCE_ORDER)
# A final answer is stored and shown to the user; keep it to a readable size.
MAX_SUMMARY_LENGTH = 4000
MAX_PROPOSED_CHANGES = 10
# The fields each op reads. Any other field is rejected, not ignored: in real
# traces the model set a Goal's "amount" to stop saving for it, the planner
# never saw it, and the model told the user nothing would change.
OP_FIELDS = {
    "add_goal": {"name", "target_amount", "saved_amount", "deadline", "importance"},
    "update_goal": {"goal_id", "name", "target_amount", "saved_amount", "deadline", "importance"},
    "remove_goal": {"goal_id"},
    "set_income": {"amount"},
    "add_obligation": {"name", "amount"},
    "update_obligation": {"obligation_id", "name", "amount"},
    "remove_obligation": {"obligation_id"},
}
CHANGE_OPS = list(OP_FIELDS)


class ToolError(Exception):
    """A bad tool call. The message goes back to the model so it can retry."""


# ---------------------------------------------------------------- schemas

CHANGE_SCHEMA = {
    "type": "object",
    "description": "One hypothetical change. Only the fields its op needs; any other field is an error.",
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
                "the current Plan (Goals, Top-ups and the Surplus). Use it for "
                "every what-if; never calculate "
                "Installments or dates yourself. Every call starts again from "
                "the user's real data: changes from earlier calls are NOT kept, "
                "so to try a different version of a Goal you are adding, send "
                "its add_goal again with the new values."
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
                "Finish. verdict: 'yes' if it fits exactly as asked; "
                "'trade_off' if it fits only when something changes (a later "
                "Deadline or smaller amount for what was asked, another Goal, "
                "or the Income) or when it pauses a Top-up; 'no' if it cannot "
                "fit even with the changes "
                "the user allows. proposed_changes: the least disruptive "
                "changes that work, or none. They are shown to the user, who "
                "must confirm them before anything is saved."
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
    """Read the user's data once. The agent only ever sees this copy.

    Top-ups and Savings Pot amounts are included so the agent's Plan matches
    the /plan page (step 14).
    """
    from database import queries  # lazy: keeps the tools testable without a DB

    return {
        "income": queries.get_income(user_id),
        "obligations": queries.list_obligations(user_id),
        "goals": queries.list_goals(user_id),
        "top_ups": queries.list_top_ups(user_id),
        "pot_amounts": queries.list_pot_amounts(user_id),
    }


def _find(items, item_id, label):
    for item in items:
        if item["id"] == item_id:
            return item
    message = f"No {label} with id {item_id}."
    if label == "Goal":
        # The commonest agent mistake: editing a Goal added in an earlier call.
        message += (
            " Each simulate_plan call starts from the user's real data, so a Goal"
            " added in an earlier call does not exist here. Send its add_goal"
            " again in this call, with the values you want."
        )
    raise ToolError(message)


# The checks below accept only what the app's own forms accept: the same
# limits (database/db.py), so a confirmed change can always be saved.


def _positive(value, field, allow_zero=False):
    # bool is an int in Python, and JSON's NaN and Infinity parse as floats.
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (value == 0 and not allow_zero)
    ):
        raise ToolError(f"{field} must be a {'non-negative' if allow_zero else 'positive'} number.")
    if value > MAX_AMOUNT:
        raise ToolError(f"{field} must be at most {MAX_AMOUNT}.")
    return float(value)


def _check_name(name):
    if not isinstance(name, str) or not name.strip():
        raise ToolError("name is required.")
    if len(name) > MAX_NAME_LENGTH:
        raise ToolError(f"name must be {MAX_NAME_LENGTH} characters or fewer.")


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
    if deadline > today + timedelta(days=365 * MAX_DEADLINE_YEARS):
        raise ToolError(f"deadline must be within {MAX_DEADLINE_YEARS} years.")
    if goal["importance"] not in IMPORTANCE_LEVELS:
        raise ToolError(f"importance must be one of {IMPORTANCE_LEVELS}.")
    _check_name(goal.get("name"))


def _check_fields(op, change):
    if op not in OP_FIELDS:
        raise ToolError(f"Unknown op {op!r}. Use one of {CHANGE_OPS}.")
    unused = sorted(set(change) - OP_FIELDS[op] - {"op"})
    if not unused:
        return
    message = f"{op} does not take {', '.join(unused)}; it takes {', '.join(sorted(OP_FIELDS[op]))}."
    if op in ("add_goal", "update_goal") and {"amount", "installment"} & set(unused):
        message += (
            " A Goal's Installment is worked out by the Plan from its target_amount,"
            " saved_amount and deadline, so it cannot be set. To stop saving for a"
            " Goal, use remove_goal."
        )
    raise ToolError(message)


def apply_changes(snapshot, changes, today):
    """Return a new snapshot with `changes` applied; the input is untouched."""
    state = copy.deepcopy(snapshot)
    next_id = 1 + max([g["id"] for g in state["goals"]] + [0])
    for change in changes:
        if not isinstance(change, dict):
            raise ToolError("Each change must be an object with an op.")
        op = change.get("op")
        _check_fields(op, change)
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
            if len(state["goals"]) > MAX_GOALS:
                raise ToolError(f"The user can have at most {MAX_GOALS} Goals.")
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
            _check_name(change.get("name"))
            new_id = 1 + max([o["id"] for o in state["obligations"]] + [0])
            state["obligations"].append(
                {"id": new_id, "name": change["name"], "amount": _positive(change.get("amount"), "amount")}
            )
            if len(state["obligations"]) > MAX_OBLIGATIONS:
                raise ToolError(f"The user can have at most {MAX_OBLIGATIONS} Obligations.")
        elif op == "update_obligation":
            obligation = _find(state["obligations"], change.get("obligation_id"), "Obligation")
            if "name" in change:
                _check_name(change["name"])
                obligation["name"] = change["name"]
            if "amount" in change:
                obligation["amount"] = _positive(change["amount"], "amount")
        elif op == "remove_obligation":
            obligation = _find(state["obligations"], change.get("obligation_id"), "Obligation")
            state["obligations"].remove(obligation)
    return state


# ---------------------------------------------------------------- plan views


@traceable(name="planner.plan")
def run_plan(snapshot, today):
    if snapshot["income"] is None:
        raise ToolError("Income is not set. Add a set_income change to simulate.")
    return planner.plan(
        snapshot["income"],
        snapshot["obligations"],
        snapshot["goals"],
        today,
        snapshot.get("top_ups", ()),
        snapshot.get("pot_amounts", ()),
    )


def _compact(plan):
    """The Plan without fields the model does not need (fewer tokens)."""
    return {
        "income": plan["income"],
        "obligations_total": plan["obligations_total"],
        "available": plan["available"],
        "surplus": plan["surplus"],
        "overcommitted": plan["overcommitted"],
        "top_ups": [
            {k: t[k] for k in ("goal_name", "amount", "start_date", "status")}
            for t in plan["top_ups"]
        ],
        "savings_pot": plan["savings_pot"]["funded"],
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
    """What a change did: every Goal whose status or Installment moved, and
    every Top-up whose status moved (a Top-up on a removed Goal is removed)."""
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

    new_top_ups = {t["id"]: t for t in after["top_ups"]}
    top_ups_changed = []
    for prev in before["top_ups"]:
        top_up = new_top_ups.get(prev["id"])
        entry = {"goal": prev["goal_name"], "amount": prev["amount"]}
        if top_up is None:
            top_ups_changed.append(dict(entry, change="removed"))
        elif top_up["status"] != prev["status"]:
            top_ups_changed.append(dict(entry, status=[prev["status"], top_up["status"]]))
    return {"goals_changed": changed, "top_ups_changed": top_ups_changed,
            "surplus": [before["surplus"], after["surplus"]]}


# ---------------------------------------------------------------- scope guard

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_AMOUNT = re.compile(r"\d[\d,]*(?:\.\d+)?")


def question_amounts(question):
    """Every amount in the question, in cents. Dates are not amounts."""
    amounts = set()
    for raw in _AMOUNT.findall(_ISO_DATE.sub(" ", question or "")):
        try:
            amounts.add(round(float(raw.replace(",", "")) * 100))
        except ValueError:
            pass
    return amounts


def _named(question, name):
    pattern = r"(?<!\w)" + re.escape(name.lower()) + r"(?!\w)"
    return re.search(pattern, (question or "").lower()) is not None


def check_scope(proposed, snapshot, question):
    """Reject proposed changes the question did not open up.

    An Income or Obligation amount must come from the question, either as
    the new amount or as the change from the old one ("rent goes up by
    200"), and an Obligation or Goal may only be removed if the question
    names it. A model can ignore a prompt rule; it cannot get past this.
    """
    amounts = question_amounts(question)

    def given(new, old=None):
        cents = round(new * 100)
        return cents in amounts or (old is not None and abs(cents - round(old * 100)) in amounts)

    for change in proposed:
        op = change.get("op")
        if op == "set_income" and not given(change["amount"], snapshot["income"]):
            raise ToolError(
                "Do not propose set_income: the user did not give that Income amount. "
                "Explain the option in the summary instead."
            )
        if op == "add_obligation" and not given(change["amount"]):
            raise ToolError(
                "Do not propose add_obligation: the user did not give that amount. "
                "Explain the option in the summary instead."
            )
        if op in ("update_obligation", "remove_obligation"):
            obligation = _find(snapshot["obligations"], change.get("obligation_id"), "Obligation")
            if op == "update_obligation" and "amount" in change:
                allowed = given(change["amount"], obligation["amount"])
            else:
                allowed = _named(question, obligation["name"])
            if not allowed:
                raise ToolError(
                    f"Do not propose {op} on the {obligation['name']} Obligation: the user "
                    "did not ask for that change. Explain the option in the summary instead."
                )
        if op == "remove_goal":
            goal = _find(snapshot["goals"], change.get("goal_id"), "Goal")
            if not _named(question, goal["name"]):
                raise ToolError(
                    f"Do not propose removing the {goal['name']} Goal: the user did not "
                    "name it. Mention that option in the summary instead."
                )


# ---------------------------------------------------------------- dispatch


def dispatch(name, raw_arguments, snapshot, today, question=""):
    """Run one tool call. Returns (result_dict, is_final).

    `question` is the user's question; final_answer checks proposals
    against it (check_scope).
    """
    try:
        args = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return {"error": "Arguments were not valid JSON."}, False
    if not isinstance(args, dict):
        return {"error": "Arguments must be a JSON object."}, False
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
            # Models sometimes leave out a field the schema requires; a missing
            # proposal means "no changes", a missing summary goes back to them.
            proposed = args.get("proposed_changes") or []
            if not isinstance(proposed, list):
                raise ToolError("proposed_changes must be a list.")
            if len(proposed) > MAX_PROPOSED_CHANGES:
                raise ToolError(f"Propose at most {MAX_PROPOSED_CHANGES} changes.")
            # Reject proposals that would not even validate, so the user
            # is never shown a change the app would refuse to save.
            apply_changes(snapshot, proposed, today)
            # Only in final_answer: simulate_plan may still explore anything.
            check_scope(proposed, snapshot, question)
            if args.get("verdict") not in ("yes", "trade_off", "no"):
                raise ToolError("verdict must be yes, trade_off or no.")
            summary = args.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                raise ToolError("summary is required: explain the answer to the user.")
            if len(summary) > MAX_SUMMARY_LENGTH:
                raise ToolError(f"summary must be {MAX_SUMMARY_LENGTH} characters or fewer.")
            return {"verdict": args["verdict"], "summary": summary,
                    "proposed_changes": proposed}, True
        return {"error": f"Unknown tool {name!r}."}, False
    except ToolError as exc:
        return {"error": str(exc)}, False
