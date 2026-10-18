# Spec: Advisor

## Overview
The advisor agent (`advisor/`) was built and evaluated after step 14, but nothing in
`app.py` uses it. This step puts it in the app. On a new Advisor page the user asks a
what-if question ("Can I get the camera by March?"). The agent answers by running the
deterministic Plan on hypothetical changes, and returns a verdict, a summary and
proposed changes. The user reviews the proposed changes and confirms them before
anything is saved. One question takes about a minute with `gpt-5-nano` (median 60 s,
p90 about 130 s, max 230 s in the saved eval traces), which is longer than
gunicorn's 30-second worker timeout. So a question runs in a background thread, is
stored in the database, and the page refreshes itself until the answer is ready.
The model comes only from the `ADVISOR_MODEL` environment variable. The advisor
ships with its known quality problems (latest eval: pass^1 0.68, pass^3 0.35; see
`docs/advisor-next-steps.md`). Fixing them is the next step, not this one. Terms are
defined in `GLOSSARY.md`.

## Depends on
- Step 3: Login and logout (`session["user_id"]`, `login_required`, `csrf_protect`)
- Steps 10–12: Income, Obligations, Goals and the Plan (`planner.py`, `/plan`)
- Step 13: Dependencies (`apply_confirmed` keeps a Goal's Dependencies)
- Step 14: Top-ups and the Savings Pot (in the advisor's snapshot so its Plan matches `/plan`)
- The advisor package: `advisor/loop.py`, `advisor/tools.py`, `advisor/apply.py`, `advisor/openai_client.py`

## Routes
- `GET /advisor` — the question form, a notice that answers come from AI and must be checked before confirming, and the user's 5 most recent questions (question, status, verdict, link to each) — logged-in
- `POST /advisor` — validate the question; if the advisor is configured and the user has no question still running, store a new run, start it in the background and redirect to `/advisor/runs/<id>` (Post/Redirect/Get). Otherwise re-render `/advisor` with the reason. Saves no Income, Obligation or Goal — logged-in
- `GET /advisor/runs/<int:id>` — one run. **Running:** "Thinking… this usually takes about a minute", and the page refreshes itself every 3 seconds. **Done:** the verdict, the summary, the proposed changes in plain words, and a Confirm button when there are changes not yet saved. **Failed:** "The Advisor could not answer. Try asking again." Never calls the model — logged-in (owner only)
- `POST /advisor/runs/<int:id>/confirm` — save the run's proposed changes, read from the database (never from the form), through `apply_confirmed`; then flash "Changes saved." and redirect to `/plan` — logged-in (owner only)

Unauthenticated requests redirect to `/login`. A run that is missing or belongs to
another user responds `404` on both run routes. Every POST is wrapped in
`@csrf_protect`. `GET /advisor/runs/<id>/confirm` returns 405.

## Database changes
One new table, added to `init_db()` with `CREATE TABLE IF NOT EXISTS`:

```sql
CREATE TABLE IF NOT EXISTS advisor_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',       -- running | done | failed
    verdict TEXT,                                 -- yes | trade_off | no; NULL unless final_answer was called
    summary TEXT,
    proposed_changes TEXT NOT NULL DEFAULT '[]',  -- JSON list, written only by the server
    stopped_reason TEXT,                          -- final_answer | max_steps | error
    created_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT,
    applied_at TEXT,                              -- set once, when the user confirms
    FOREIGN KEY (user_id) REFERENCES users (id)
)
```

No other table changes. `seed_db()` is not changed.

A run still `running` 15 minutes after `created_at` is stale (the process restarted
or the thread died). It is shown as failed and does not block a new question.

## Background runs
- `POST /advisor` loads the snapshot (`advisor.tools.load_snapshot`) and `date.today()` in the request, creates the run row, then starts the run.
- The run executes in a daemon `threading.Thread`: `run_advisor(question, snapshot, call_model, today)`. Its result is written to the run row. Any exception marks the run `failed` with `stopped_reason = 'error'` and logs only the error type (an API error message can quote part of the key). The thread never raises.
- `stopped_reason = 'max_steps'` is stored as `done` with no verdict and no proposed changes. The page shows the summary ("I could not finish within the step limit.").
- `get_db()` opens a new connection per call, so the thread uses its own connections and needs no Flask app context.
- `app.config["ADVISOR_INLINE"]` (default `False`) runs the job in the request instead of a thread. Tests set it to `True`.
- The model caller is built in one function in `app.py`, `_advisor_caller()`. It returns `make_openai_caller(os.environ["ADVISOR_MODEL"])`, or `None` when `OPENAI_API_KEY` or `ADVISOR_MODEL` is missing. Tests replace it with a scripted fake (as in `tests/test_advisor.py`).
- `advisor/openai_client.py` sets a per-call timeout of 90 seconds and `max_retries=0` (OpenAI SDK 3.32 defaults: 600 s and 2 retries). The longest single call in the eval traces was 70 s, and 8 steps × 90 s stays under the 15-minute stale limit.

## Confirming
- Allowed only when the run is the user's, `status = 'done'`, it has at least one proposed change, and `applied_at` is NULL.
- `apply_confirmed(user_id, changes, run_id=id)` does everything in **one transaction** (`BEGIN IMMEDIATE`): claim the run (`UPDATE … SET applied_at = datetime('now') WHERE id = ? AND user_id = ? AND status = 'done' AND applied_at IS NULL`), re-validate the changes against fresh data, and write them. Either every change and the claim are saved, or nothing is.
- Claim fails (already saved): flash "These changes were already saved." and redirect to the run. A double click therefore saves once.
- `ToolError` (the data changed): flash "Your Income, Obligations or Goals changed since this answer, so nothing was saved. Ask again." and redirect to the run. Never show the `ToolError` text: it is written for the model.
- Any other error: log only its type, flash "These changes could not be saved, so nothing was saved. Ask again." and redirect to the run. Never a 500, and the run can be confirmed again.

## Security review fixes
From `/code-review-feature 15-advisor`:
- **Validation (S1):** the Advisor's checks (`advisor/tools.py`) accept only what the forms accept, using the same limits, which move to `database/db.py` (`MAX_AMOUNT`, `MAX_NAME_LENGTH`, `MAX_GOALS`, `MAX_OBLIGATIONS`, `MAX_DEADLINE_YEARS`). Names must be text, non-blank, at most 60 characters. Amounts must be finite numbers (not `NaN`, `Infinity` or a boolean), at most €10,000,000. Deadlines must be within 50 years. A change must be an object, and tool arguments must be a JSON object. `final_answer` allows at most 10 proposed changes and a summary of at most 4,000 characters.
- **Daily limit (S2):** at most `app.config["ADVISOR_DAILY_LIMIT"]` (10) questions per account in any 24 hours, enforced in the same single `INSERT` as the one-running-question rule. Above it, `/advisor` shows "You have asked 10 questions in the last 24 hours. Try again later." and creates no run.
- **Logs (S3):** failed runs and failed confirms log the error type and run id only.
- **Session key (S4):** without `SECRET_KEY`, the app uses a random key per process and logs a warning. It never uses a fixed fallback string. The dev server reads `SECRET_KEY` from `.env`.

## Showing proposed changes
`app.py` turns each change into one plain sentence, using the user's current Goal
and Obligation names. For example:
- `add_goal` → Add Goal "Trip": €2,000.00 by 2027-06-01, Should-have
- `update_goal` → Change Goal "Laptop": Deadline 2027-03-01, Importance Nice-to-have (only the fields the change sets)
- `remove_goal` → Remove Goal "Sofa"
- `set_income` → Set Income to €3,200.00
- `add_obligation` / `update_obligation` / `remove_obligation` → the same pattern

If an id no longer exists, the sentence says "(no longer exists)", and Confirm will
fail as described above. Verdicts show as "Yes, it fits", "Fits with a trade-off"
and "Does not fit".

## Templates
- **Create:**
  - `templates/advisor.html` — question form (textarea, 500-character limit, CSRF token), the AI notice, an "unavailable" notice when `_advisor_caller()` is `None` (form disabled), and the recent questions list
  - `templates/advisor_run.html` — one run as described in Routes. While `running`, `{% block head %}` adds `<meta http-equiv="refresh" content="3">`. The Confirm form posts to `url_for('advisor_confirm', id=run.id)`
- **Modify:**
  - `templates/base.html` — add an "Advisor" nav link after Plan, active for `advisor`, `advisor_run` and `advisor_confirm`
  - `templates/plan.html` — a link "Ask the Advisor a what-if question" to `/advisor`
  - `templates/privacy.html` — in "Third Party Services", add that a question asked on the Advisor page is sent to an AI provider (OpenAI) together with the user's Income, Obligations (names and amounts), Goals (names, target amounts, Saved Amounts, Deadlines, Importance), Top-ups and Savings Pot amounts. This only happens when the user asks, and nothing is saved until they confirm. In "Data Storage", add that questions and answers are stored with the account. Update "Last updated"

## Files to change
- `app.py` — the four routes, `_advisor_caller()`, the change sentences, the stale-run rule, `app.config["ADVISOR_INLINE"] = False`, `app.config["ADVISOR_DAILY_LIMIT"] = 10`, the `SECRET_KEY` handling; the shared limits are imported from `database/db.py`
- `database/db.py` — the `advisor_runs` `CREATE TABLE` in `init_db()`; the limits shared by the forms and the Advisor
- `database/queries.py` — `create_advisor_run(user_id, question, daily_limit)` (returns None, storing nothing, while another non-stale run is running or the daily limit is reached; one statement, so a double click cannot start two runs), `count_recent_advisor_runs`, `get_advisor_run`, `list_advisor_runs` (newest 5), `expire_stale_advisor_runs`, `finish_advisor_run`, `fail_advisor_run`, `claim_advisor_run`; every query filters by `user_id`. The Goal, Obligation and Income writers, `get_goal`, `get_obligation` and `claim_advisor_run` take an optional `conn`, so `apply_confirmed` can use one transaction
- `advisor/apply.py` — claim, re-validate and write in one transaction
- `advisor/tools.py` — the stricter checks (see Security review fixes)
- `advisor/openai_client.py` — client timeout 90 s, `max_retries=0`
- `advisor/tracing.py` — docstring: the privacy page now names Anthropic and OpenAI
- `templates/base.html`, `templates/plan.html`, `templates/privacy.html` — see Templates
- `Procfile` — `web: gunicorn app:app --bind 0.0.0.0:$PORT --threads 4`, so page loads are not queued behind each other while runs are in flight
- `.env.example` — add `ADVISOR_MODEL=gpt-5-nano` under the OpenAI key. Its comment says the app's advisor needs both. Update the LangSmith comment (Anthropic and OpenAI are the third parties)
- `GLOSSARY.md` — add **Advisor** (the AI that answers what-if questions by running the Plan on hypothetical changes, and proposes changes the user confirms; _Avoid_: chatbot, assistant, AI coach)
- `docs/advisor.md` — replace "Flask wiring (sketch, not yet in app.py)" with how it is wired (background runs, `advisor_runs`, confirm)
- `CLAUDE.md` — steps 1–15 implemented; the new routes; `advisor/` in Architecture; `OPENAI_API_KEY` / `ADVISOR_MODEL`; ADR 0003 next to ADR 0001 in the overview

## Files to create
- `templates/advisor.html`
- `templates/advisor_run.html`
- `static/css/advisor.css` — CSS variables only
- `advisor/jobs.py` — `start_run(...)` (thread or inline) and `execute_run(...)` (runs the agent, writes the result, never raises)
- `docs/adr/0003-advisor-proposes-user-confirms.md` — The advisor may propose Income, Obligation and Goal changes, which extends ADR 0001 for this one feature. It only proposes. Every number comes from `planner.plan()`. `final_answer` rejects Income and Obligation amounts the question did not give, and removals it did not name (`check_scope`). Nothing is saved until the user confirms, and `apply_confirmed` re-validates against fresh data. Rejected: letting the agent write directly (no undo, and a model can ignore a prompt rule), and an answers-only advisor (the user would re-enter every change by hand)
- `tests/test_15-advisor.py` (created later via `/test-feature 15-advisor`)

## New dependencies
No new dependencies. `openai` is already in `requirements.txt`.
- Deployment: set `OPENAI_API_KEY` and `ADVISOR_MODEL` on Railway. Without either, the app runs unchanged and the Advisor page says "The Advisor is unavailable right now.". Never set `LANGSMITH_TRACING` on Railway.

## Rules for implementation
- No SQLAlchemy or ORMs — raw `sqlite3` via `get_db()`
- Parameterised queries only
- Passwords hashed with werkzeug (no auth changes in this step)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- No inline styles; use `url_for` for all links and form actions
- ADR 0001 and ADR 0003 are binding: the model only proposes, `planner.plan()` does all money arithmetic, and only `POST /advisor/runs/<id>/confirm` writes Income, Obligations or Goals
- The model is called only by a run started from `POST /advisor`; never by a `GET`, by confirm, or by the Plan
- No model name is hardcoded in app code; it comes only from `ADVISOR_MODEL`
- Proposed changes live only in `advisor_runs`; confirm never reads changes from the request
- The summary and the user's question are user/model text: render them escaped (never `|safe`)
- At most one running question per user, and at most `ADVISOR_DAILY_LIMIT` questions per user in any 24 hours; a question is 1–500 characters after trimming
- `OPENAI_API_KEY` is read only from the environment; never stored, logged, rendered or sent to the browser
- Tests never make a network call and never start a thread: they set `ADVISOR_INLINE = True` and replace `_advisor_caller`
- Do not change `advisor/loop.py` or the prompt in this step (quality fixes are the next step); `advisor/tools.py` only gets the stricter checks from the security review
- Use the glossary words exactly: **Advisor**, **Income**, **Obligation**, **Goal**, **Deadline**, **Importance**, **Installment**, **Surplus**, **Unreachable Goal**, **Top-up**, **Plan**
- Keep the existing expense, Income, Obligation, Goal, Suggest, Plan and redirect flows and tests working unchanged

## Definition of done
- [ ] Logged out, `/advisor`, `/advisor/runs/1` and `POST /advisor/runs/1/confirm` redirect to `/login`
- [ ] The navbar shows "Advisor" for logged-in users and highlights it on the advisor pages; `/plan` links to `/advisor`
- [ ] Without `OPENAI_API_KEY` or `ADVISOR_MODEL`, `/advisor` says "The Advisor is unavailable right now.", the form is disabled, and a POST creates no run
- [ ] An empty question or one over 500 characters shows an error and creates no run
- [ ] With a scripted fake model and `ADVISOR_INLINE`, asking a question redirects to its run page, which shows the verdict, the summary and each proposed change as a sentence
- [ ] A run in `running` state shows "Thinking…" and the page refreshes itself; once done it shows the answer without the refresh
- [ ] Asking while another question is still running shows "Your last question is still running" and creates no run; a run older than 15 minutes counts as failed and does not block
- [ ] A model error, timeout or exception marks the run failed and the page shows "The Advisor could not answer. Try asking again."; no 500
- [ ] Confirm saves exactly the stored changes (e.g. a new Goal appears on `/goals` and in `/plan`), flashes "Changes saved." and redirects to `/plan`
- [ ] Confirming twice saves once; the second time shows "These changes were already saved."
- [ ] If a Goal or Obligation in the proposal was deleted or changed since the answer, confirm saves nothing and shows the "changed since this answer" message; the run can still be viewed
- [ ] A run with no proposed changes, or a `max_steps` run, shows no Confirm button, and a confirm POST to it saves nothing
- [ ] Another user's run, or a missing run, returns 404 on view and on confirm
- [ ] POSTs without a valid CSRF token return 400, start no run and save nothing
- [ ] Changes posted in the confirm form body are ignored
- [ ] Updating a Goal through confirm keeps its Dependencies
- [ ] A summary containing HTML is shown as text
- [ ] `/advisor` lists the user's 5 most recent questions, newest first, and never another user's
- [ ] The privacy page names OpenAI and lists what the advisor sends
- [ ] No test or page load makes a real network call; the app starts and every other page works without `OPENAI_API_KEY`
- [ ] Existing databases start up without error and keep all their data
- [ ] The 11th question within 24 hours shows the daily-limit message and creates no run; questions older than 24 hours, and another user's, do not count
- [ ] If any write fails during confirm, nothing is saved (not even earlier changes), there is no 500, and the run can be confirmed again
- [ ] A stored change with a value the forms would reject (a non-text or 61-character name, `NaN`, `Infinity`, over €10,000,000, a Deadline over 50 years away) is refused and nothing is saved
- [ ] Failed runs and confirms log the error type, never the error message
- [ ] `app.secret_key` is never the old fixed fallback string
- [ ] Steps 1–14 and their tests still pass
- [ ] Styling uses CSS variables and matches the existing pages
