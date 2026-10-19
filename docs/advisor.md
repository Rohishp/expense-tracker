# Savings advisor agent

A what-if advisor: the user asks "Can I get the camera by March?", and an LLM
answers by calling the existing deterministic planner in a loop.

## Why this is an agent and not an LLM feature

The steps are not fixed in advance. The model decides what to simulate next
based on what the last simulation returned: try the Deadline as asked, see the
Laptop become an Unreachable Goal, try a later Deadline, compare, then answer.

## How it keeps ADR 0001

| Who | Does what |
|---|---|
| LLM | Chooses which what-ifs to try, explains the trade-off, drafts changes |
| `planner.plan()` | All money arithmetic, unchanged |
| User | Confirms every change before it is saved |

## Files

| File | Role |
|---|---|
| `advisor/tools.py` | 3 tools: `get_finances`, `simulate_plan`, `final_answer`. No writes. `simulate_plan`'s diff reports Goal, Top-up and Surplus changes. `final_answer` rejects changes the question did not open up (`check_scope`). Validation errors go back to the model so it can retry |
| `advisor/loop.py` | The loop: model picks a tool, sees the result, repeats; `max_steps=8`; every step traced |
| `advisor/openai_client.py` | OpenAI Chat Completions adapter, `temperature=0` (left out for `gpt-5*` models, which only accept the default) |
| `advisor/apply.py` | The only write path, run after the user confirms; re-validates against fresh data and keeps a Goal's Dependencies |
| `advisor/jobs.py` | Runs one question in a background thread (or inline in tests) and stores the answer in `advisor_runs`; never raises |
| `advisor/evals.py` | 20 tasks over 8 fixtures (Dependencies, Saved Amounts, over-committed, no Income, Top-ups, injection, off-topic). Code-only graders: finished, verdict, outcome, collateral, warned, grounded, wording. Reports pass^1 and pass^k over `--runs`; every run's traces and scores are saved under `evals/results/` |
| `tests/test_advisor.py` | Loop tests with a scripted fake model; every task's reference answer passes every grader; the graders catch the failures seen in real traces |

## Running the evals

```bash
python -m advisor.evals --model gpt-5-nano --runs 3 --workers 6 --note "what changed"
```

Needs `OPENAI_API_KEY` (read from `.env`). It calls the real model, so it costs money and takes minutes; `pytest` never runs it. Read the traces of failed runs before changing the prompt: a failure can be the agent's or the task's. Each run adds its traces to `evals/results/advisor/` and a row to `evals/results/runs.csv`, so compare against earlier rows there.

## Safety by structure, not by prompt

The model has no write tool at all. Even a model that ignores the prompt can
only return `proposed_changes`; saving needs a confirm request, which
`apply_confirmed` re-validates.

`final_answer` also checks each proposal against the question
(`check_scope` in `advisor/tools.py`):

- An Income or Obligation amount must appear in the question, as the new
  amount or as the change ("rent goes up by 200").
- An Obligation or Goal may be removed only if the question names it.

It also rejects a summary that shows the user a field or tool name, such as
"target_amount 800" copied from a tool result (`check_summary`). Names the
user chose and words from the question are allowed.

A rejection goes back to the model so it can answer again. `simulate_plan`
is not checked, so the model can still explore. The guard cannot catch a
number that the question uses for something else: asked for "6000 euros for
a trip", Income of 6000 passes. A prompt rule covers that case, and also
Deadline and Importance edits to Goals the user did not mention.

## How the app uses it (step 15, ADR 0003)

One question takes about a minute (median 60 s, max 230 s in the eval
traces), longer than gunicorn's 30 s worker timeout, so it runs in the
background:

1. `POST /advisor` checks the question (1–500 characters) and that
   `_advisor_caller()` is configured (`OPENAI_API_KEY` and `ADVISOR_MODEL`).
   `create_advisor_run` stores a `running` row, unless the user already has
   one running or has asked `ADVISOR_DAILY_LIMIT` (10) questions in the last
   24 hours; one `INSERT` checks both, so a double click cannot start two
   runs. Then `advisor/jobs.py` runs `run_advisor` in a daemon thread
   on a snapshot read in the request, and the user goes to
   `/advisor/runs/<id>`.
2. That page refreshes itself every 3 s while the run is `running`, then
   shows the verdict, the summary and each proposed change as a sentence.
   Any exception marks the run `failed` and logs only its type (an API
   error message can quote part of the key). A run still `running` after
   15 minutes is stale and shown as failed.
3. `POST /advisor/runs/<id>/confirm` reads the changes from `advisor_runs`
   (never from the form) and calls `apply_confirmed(..., run_id=id)`. In one
   `BEGIN IMMEDIATE` transaction it claims the run (so it saves only once),
   re-checks the changes against fresh data and writes them. Any error,
   including a `ToolError` (the data changed since the answer), rolls back
   everything, the claim too, so the user can confirm again.

The checks in `tools.py` accept only what the forms accept, with the limits
from `database/db.py` (`MAX_AMOUNT`, `MAX_NAME_LENGTH`, `MAX_GOALS`,
`MAX_OBLIGATIONS`, `MAX_DEADLINE_YEARS`): a model can propose anything, and
the confirm saves what it proposed.

Each model call times out after 90 s with no retries, so a run of 8 steps
ends before it counts as stale. Tests set `ADVISOR_INLINE` and replace
`_advisor_caller` with a scripted model, so they start no thread and make
no network call.

## Not covered yet

- **Dependencies** are honoured through `planner.plan()` (step 13), but the
  agent has no change op to add or remove one.
- **Top-ups and the Savings Pot** (step 14) are in the snapshot, so the
  agent's Plan matches `/plan`, but it cannot propose them.
- `simulate_plan` starts from the real data on every call; the tool
  description and the "No Goal with id" error say so, because models
  otherwise try to edit a Goal they added in an earlier call.
- Traces are returned, not persisted. Log them per request to show what the
  agent did.
