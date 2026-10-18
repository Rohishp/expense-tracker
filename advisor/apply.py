"""The only place advisor changes reach the database: after the user confirms.

The proposal is re-validated against fresh data here, because the data may
have changed since the agent ran, and a confirm request is user input. All
changes are saved in one transaction: either every change is saved or none.
"""

from datetime import date

from advisor.tools import apply_changes, load_snapshot
from database import queries
from database.db import get_db


def apply_confirmed(user_id, changes, today=None, run_id=None):
    """Save `changes` for `user_id`. Returns True if they were saved.

    With `run_id`, the run is claimed in the same transaction first; if it was
    already claimed, nothing is saved and this returns False. Raises ToolError
    if the changes no longer fit the user's data; any error saves nothing.
    """
    today = today or date.today()
    conn = get_db()
    try:
        # Take the write lock first: the data cannot change between the check
        # and the writes, and a second confirm of the same run waits for this one.
        conn.execute("BEGIN IMMEDIATE")
        if run_id is not None and not queries.claim_advisor_run(run_id, user_id, conn=conn):
            conn.rollback()
            return False
        apply_changes(load_snapshot(user_id), changes, today)  # raises ToolError if stale or bad

        for c in changes:
            op = c["op"]
            if op == "add_goal":
                queries.create_goal(user_id, c["name"], c["target_amount"],
                                    c.get("saved_amount", 0), c["deadline"], c["importance"],
                                    conn=conn)
            elif op == "update_goal":
                goal = queries.get_goal(c["goal_id"], user_id, conn=conn)
                goal.update({k: c[k] for k in ("name", "target_amount", "saved_amount",
                                               "deadline", "importance") if k in c})
                # Pass the Dependencies through: update_goal replaces them, and the
                # advisor never changes them.
                queries.update_goal(c["goal_id"], user_id, goal["name"], goal["target_amount"],
                                    goal["saved_amount"], goal["deadline"], goal["importance"],
                                    goal["depends_on"], conn=conn)
            elif op == "remove_goal":
                queries.delete_goal(c["goal_id"], user_id, conn=conn)
            elif op == "set_income":
                queries.set_income(user_id, c["amount"], conn=conn)
            elif op == "add_obligation":
                queries.create_obligation(user_id, c["name"], c["amount"], conn=conn)
            elif op == "update_obligation":
                ob = queries.get_obligation(c["obligation_id"], user_id, conn=conn)
                queries.update_obligation(c["obligation_id"], user_id,
                                          c.get("name", ob["name"]), c.get("amount", ob["amount"]),
                                          conn=conn)
            elif op == "remove_obligation":
                queries.delete_obligation(c["obligation_id"], user_id, conn=conn)
        conn.commit()
        return True
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
