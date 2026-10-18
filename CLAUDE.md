# Spendly

## Project overview

"Spendly" is a Flask-based personal expense tracker built incrementally, one numbered step at a time. Each step has a spec in `.claude/specs/` (`01-database-setup.md` … `15-advisor.md`); treat the spec as the source of truth for that step. Steps 1–15 are implemented: database setup, registration, login/logout, profile page wired to real data with a date filter, add/edit/delete expense, the savings plan, redirecting the Surplus, and the Advisor.

The **savings plan** (steps 10–13) is Income, Obligations, Goals (with Importance and Dependencies), and a computed Plan; an LLM may only suggest a Goal's Importance and Dependencies, and the user confirms them (ADR 0001). Step 14 forecasts each Freed Installment and lets the user redirect the Surplus or freed money into Top-ups, a Lump Sum or the Savings Pot; the Plan never redirects money itself (ADR 0002). Step 15 adds the **Advisor**: an LLM agent answers what-if questions by running the Plan on hypothetical changes and may propose Income, Obligation and Goal changes, saved only when the user confirms (ADR 0003, which extends ADR 0001 for this feature). Domain terms are defined in `GLOSSARY.md`; use those words exactly. Decisions live in `docs/adr/`.

## Commands

```bash
pip install -r requirements.txt   # install dependencies
python app.py                     # run the dev server on http://localhost:5001 (debug is off unless FLASK_DEBUG=1)
pytest                            # run tests
pytest path/to/test_file.py::test_name   # run a single test
python -m advisor.evals --model gpt-5-nano --runs 3   # real-model eval of the advisor agent (OPENAI_API_KEY)
python -m suggestions_evals --runs 3                  # real-model eval of suggestions.py (ANTHROPIC_API_KEY)
```

The two evals call real models (cost, minutes) and are never run by `pytest`; their graders and cases are tested in `tests/test_advisor.py` and `tests/test_suggestions_evals.py`. Every run is kept (`eval_results.py`): its traces in `evals/results/<eval>/` and one row in `evals/results/runs.csv` with model, commit and scores, all committed. Label a run with `--note "..."`; `--no-save` keeps nothing.

Tracing: set `LANGSMITH_TRACING=true` in `.env` to send advisor and suggestion runs to LangSmith (`advisor/tracing.py`; off in tests and never set on Railway). The Claude Code workflow is traced separately via the LangSmith plugin. Setup in `docs/observability.md`.

There is no linter configured; a PostToolUse hook runs `black` on any `.py` file Claude writes or edits.

Slash commands in `.claude/commands/`: `/create-spec`, `/test-feature`, `/code-review-feature`, `/ship-feature`, `/seed-user`, `/seed-expense`. Subagents in `.claude/agents/`: `spendly-test-writer`, `spendly-test-runner`, `spendly-security-reviewer`, `spendly-quality-reviewer`.

## Architecture

- **`app.py`** — single-file Flask app, no blueprints. Routes: `/`, `/register`, `/login`, `/logout`, `/terms`, `/privacy`, `/profile` (summary stats, recent transactions, category breakdown, date-range filter), `/analytics` (template only), `/expenses/add`, `/expenses/<id>/edit`, `/expenses/<id>/delete`, `/income`, `/obligations` (+ `/add`, `/<id>/edit`, `/<id>/delete`), `/goals` (+ `/add`, `/suggest`, `/<id>/edit`, `/<id>/delete`, `/<id>/lump-sum`), `/plan` (read-only), `/plan/redirect` (`?source=surplus` or `?source=goal&goal_id=`), `/top-ups/<id>/delete`, `/savings-pot/<id>/delete`, `/advisor` (ask) and `/advisor/runs/<id>` (+ `/confirm`). `_current_plan()` loads everything the Plan needs. Advisor questions run in a background thread (`app.config["ADVISOR_INLINE"]` runs them in the request; tests set it), at most `app.config["ADVISOR_DAILY_LIMIT"]` (10) per user in 24 hours. Protected views use `@login_required` (checks `session["user_id"]`). POST views are wrapped in `@csrf_protect`; forms include `csrf_token()`. Tests turn CSRF off with `app.config["CSRF_ENABLED"] = False`.
- **`database/db.py`** — `get_db()` (SQLite connection, `row_factory = sqlite3.Row`, `PRAGMA foreign_keys = ON` reissued per connection), `init_db()` (`CREATE TABLE IF NOT EXISTS` for `users`, `expenses`, `incomes`, `obligations`, `goals`, `goal_dependencies`, `top_ups`, `savings_pot_amounts` and `advisor_runs`), `seed_db()` (one demo user, 8 sample expenses), the `CATEGORIES` and `IMPORTANCE_LEVELS` lists, and the `MAX_*` limits shared by the forms and the Advisor's checks. No ORM — raw `sqlite3`, parameterised queries only.
- **`database/queries.py`** — all SQL for expenses, the profile page, Income, Obligations, Goals, Top-ups and the Savings Pot (`create_expense` … `get_category_breakdown`, `get_income`/`set_income`, `*_obligation`, `*_goal`, `get_dependency_edges`, `add_lump_sum`, `*_top_up`, `*_pot_amount`, `create_redirect`, `*_advisor_run`). Every query filters by `user_id` so users only touch their own rows.
- **`planner.py`** — the Plan: pure functions in integer cents (no Flask, no database, `today` passed in). Funds Goals by Importance then Deadline, after their Dependencies; Unreachable Goals take nothing. Then Top-ups, then the Savings Pot (cut first). Forecasts each Freed Installment and the redirect limits (`surplus_limit`, `freed[].limit`) so the same money is never redirected twice. Without Top-ups or Savings Pot amounts the Plan is the same as step 13.
- **`advisor/`** — the Advisor agent (OpenAI): `loop.py` (the tool loop), `tools.py` (read-only tools and the `check_scope` guard), `apply.py` (the only write path, after the user confirms), `jobs.py` (background runs), `openai_client.py`, `evals.py`. The app reads `OPENAI_API_KEY` and `ADVISOR_MODEL` (required, no default model in code); without them the Advisor page says it is unavailable. Tests replace `app._advisor_caller` with a scripted model and never touch the network. Details in `docs/advisor.md`.
- **`suggestions.py`** — the Goal suggestion LLM code (Anthropic). `suggest()` asks the Anthropic API for a Goal's Importance and Dependencies, validates the reply as untrusted input and returns `None` (never raises) when unavailable. Reads `ANTHROPIC_API_KEY` and optional `SPENDLY_LLM_MODEL` from the environment; tests replace `suggestions._call_llm` and never touch the network.
- **`templates/`** — Jinja2 templates extending `base.html` (shared navbar/footer; blocks `title`, `head`, `content`, `scripts`). Each page has its own CSS in `static/css/` loaded via `{% block head %}`, layered on the shared `style.css`.
- **`static/css/style.css`** — shared design tokens (CSS custom properties). Use the variables; never hardcode hex values.
- **`static/js/main.js`** — shared JS (currently empty); page-specific JS is inlined in that page's `{% block scripts %}`.
- **`tests/`** — pytest, one file per step (`test_07-add-expense.py` …) plus `conftest.py`. Amounts are shown in € with no currency stored.
- The SQLite file `expense_tracker.db` and `venv/` are gitignored. Deployment is via `Procfile` (gunicorn with `--threads 4`).

## Conventions

- Missing or foreign records return 404 (never reveal that an id exists); data-changing actions are POST with Post/Redirect/Get.
- All templates extend `base.html`; no inline styles; use `url_for` for links and form actions.
- Commits and PRs carry no Claude attribution (`attribution` is blank in `.claude/settings.json`).
