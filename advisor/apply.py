"""The only place advisor changes reach the database: after the user confirms.

The proposal is re-validated against fresh data here, because the data may
have changed since the agent ran, and a confirm request is user input.
"""

from datetime import date

from advisor.tools import apply_changes, load_snapshot
from database import queries


def apply_confirmed(user_id, changes, today=None):
    today = today or date.today()
    apply_changes(load_snapshot(user_id), changes, today)  # raises ToolError if stale or bad

    for c in changes:
        op = c["op"]
        if op == "add_goal":
            queries.create_goal(user_id, c["name"], c["target_amount"],
                                c.get("saved_amount", 0), c["deadline"], c["importance"])
        elif op == "update_goal":
            goal = queries.get_goal(c["goal_id"], user_id)
            goal.update({k: c[k] for k in ("name", "target_amount", "saved_amount",
                                           "deadline", "importance") if k in c})
            queries.update_goal(c["goal_id"], user_id, goal["name"], goal["target_amount"],
                                goal["saved_amount"], goal["deadline"], goal["importance"])
        elif op == "remove_goal":
            queries.delete_goal(c["goal_id"], user_id)
        elif op == "set_income":
            queries.set_income(user_id, c["amount"])
        elif op == "add_obligation":
            queries.create_obligation(user_id, c["name"], c["amount"])
        elif op == "update_obligation":
            ob = queries.get_obligation(c["obligation_id"], user_id)
            queries.update_obligation(c["obligation_id"], user_id,
                                      c.get("name", ob["name"]), c.get("amount", ob["amount"]))
        elif op == "remove_obligation":
            queries.delete_obligation(c["obligation_id"], user_id)
