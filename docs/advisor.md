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
| `advisor/tools.py` | 3 tools: `get_finances`, `simulate_plan`, `final_answer`. No writes. Validation errors go back to the model so it can retry |
| `advisor/loop.py` | The loop: model picks a tool, sees the result, repeats; `max_steps=8`; every step traced |
| `advisor/openai_client.py` | OpenAI Chat Completions adapter, `temperature=0` (left out for `gpt-5*` models, which only accept the default) |
| `advisor/apply.py` | The only write path, run after the user confirms; re-validates against fresh data and keeps a Goal's Dependencies |
| `advisor/evals.py` | 20 tasks over 8 fixtures (Dependencies, Saved Amounts, over-committed, no Income, Top-ups, injection, off-topic). Code-only graders: finished, verdict, outcome, collateral, warned, grounded, wording. Reports pass^1 and pass^k over `--runs`; `--out` saves every trace as JSONL |
| `tests/test_advisor.py` | Loop tests with a scripted fake model; every task's reference answer passes every grader; the graders catch the failures seen in real traces |

## Running the evals

```bash
python -m advisor.evals --model gpt-5-nano --runs 3 --workers 6 --out traces.jsonl
```

Needs `OPENAI_API_KEY` (read from `.env`). It calls the real model, so it costs money and takes minutes; `pytest` never runs it. Read the traces of failed runs before changing the prompt: a failure can be the agent's or the task's.

## Safety by structure, not by prompt

The model has no write tool at all. Even a model that ignores the prompt can
only return `proposed_changes`; saving needs a confirm request, which
`apply_confirmed` re-validates.

## Flask wiring (sketch, not yet in app.py)

```python
from advisor.loop import run_advisor
from advisor.tools import load_snapshot, ToolError
from advisor.openai_client import make_openai_caller
from advisor.apply import apply_confirmed

call_model = make_openai_caller(os.environ["ADVISOR_MODEL"])

@app.route("/advisor", methods=["GET", "POST"])
@login_required
@csrf_protect
def advisor():
    result = None
    if request.method == "POST":
        result = run_advisor(request.form["question"],
                             load_snapshot(session["user_id"]), call_model, date.today())
        # Keep proposals server side; never trust changes posted back by the browser.
        session["advisor_changes"] = result.proposed_changes
    return render_template("advisor.html", result=result)

@app.route("/advisor/confirm", methods=["POST"])
@login_required
@csrf_protect
def advisor_confirm():
    try:
        apply_confirmed(session["user_id"], session.pop("advisor_changes", []))
    except ToolError as exc:
        flash(f"Your data changed since the advice was given: {exc}")
    return redirect(url_for("plan"))
```

Flask's default session is a signed cookie (about 4 KB). That is enough for a
few changes; move to a server-side store if proposals grow.

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
