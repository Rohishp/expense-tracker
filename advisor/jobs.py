"""Runs an advisor question in the background and stores the answer.

One question takes about a minute, longer than gunicorn's 30 s worker
timeout, so the request only starts the run; the run page reads the answer
from advisor_runs. The thread never raises: any failure marks the run failed.
"""

import logging
import threading

from advisor.loop import run_advisor
from database import queries

logger = logging.getLogger(__name__)


def execute_run(run_id, user_id, question, snapshot, today, call_model):
    """Answer `question` and store the result on the run row."""
    try:
        result = run_advisor(question, snapshot, call_model, today)
        queries.finish_advisor_run(
            run_id,
            user_id,
            result.verdict,
            result.summary,
            result.proposed_changes,
            result.stopped_reason,
        )
    except Exception as exc:
        # Only the error type: an API error message can quote part of the key.
        logger.error("advisor run %s failed: %s", run_id, type(exc).__name__)
        try:
            queries.fail_advisor_run(run_id, user_id)
        except Exception as exc:  # the stale-run rule marks it failed later
            logger.error("could not mark advisor run %s failed: %s", run_id, type(exc).__name__)


def start_run(run_id, user_id, question, snapshot, today, call_model, inline=False):
    """Start execute_run in a daemon thread, or run it now if `inline` (tests)."""
    args = (run_id, user_id, question, snapshot, today, call_model)
    if inline:
        execute_run(*args)
        return
    threading.Thread(target=execute_run, args=args, daemon=True).start()
