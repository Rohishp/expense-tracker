# Spendly

## Project overview

"Spendly" is a Flask-based personal expense tracker built incrementally, one numbered step at a time. Each step has a spec in `.claude/specs/` (`01-database-setup.md` … `09-delete-expense.md`); treat the spec as the source of truth for that step. Steps 1–9 are implemented: database setup, registration, login/logout, profile page wired to real data with a date filter, and add/edit/delete expense.

The next feature is a **savings plan** (Income, Obligations, Goals, and a computed Plan), split into steps 10–13. Domain terms are defined in `GLOSSARY.md`; use those words exactly. Decisions live in `docs/adr/`.

## Commands

```bash
pip install -r requirements.txt   # install dependencies
python app.py                     # run the dev server on http://localhost:5001 (debug=True)
pytest                            # run tests
pytest path/to/test_file.py::test_name   # run a single test
```

There is no linter configured; a PostToolUse hook runs `black` on any `.py` file Claude writes or edits.

Slash commands in `.claude/commands/`: `/create-spec`, `/test-feature`, `/code-review-feature`, `/ship-feature`, `/seed-user`, `/seed-expense`. Subagents in `.claude/agents/`: `spendly-test-writer`, `spendly-test-runner`, `spendly-security-reviewer`, `spendly-quality-reviewer`.

## Architecture

- **`app.py`** — single-file Flask app, no blueprints. Routes: `/`, `/register`, `/login`, `/logout`, `/terms`, `/privacy`, `/profile` (summary stats, recent transactions, category breakdown, date-range filter), `/analytics` (template only), `/expenses/add`, `/expenses/<id>/edit`, `/expenses/<id>/delete`. Protected views use `@login_required` (checks `session["user_id"]`). POST views are wrapped in `@csrf_protect`; forms include `csrf_token()`. Tests turn CSRF off with `app.config["CSRF_ENABLED"] = False`.
- **`database/db.py`** — `get_db()` (SQLite connection, `row_factory = sqlite3.Row`, `PRAGMA foreign_keys = ON` reissued per connection), `init_db()` (`CREATE TABLE IF NOT EXISTS` for `users` and `expenses`), `seed_db()` (one demo user, 8 sample expenses), and the `CATEGORIES` list. No ORM — raw `sqlite3`, parameterised queries only.
- **`database/queries.py`** — all SQL for expenses and the profile page (`create_expense`, `get_expense`, `update_expense`, `delete_expense`, `get_user_by_id`, `get_summary_stats`, `get_recent_transactions`, `get_category_breakdown`). Every expense query filters by `user_id` so users only touch their own rows.
- **`templates/`** — Jinja2 templates extending `base.html` (shared navbar/footer; blocks `title`, `head`, `content`, `scripts`). Each page has its own CSS in `static/css/` loaded via `{% block head %}`, layered on the shared `style.css`.
- **`static/css/style.css`** — shared design tokens (CSS custom properties). Use the variables; never hardcode hex values.
- **`static/js/main.js`** — shared JS (currently empty); page-specific JS is inlined in that page's `{% block scripts %}`.
- **`tests/`** — pytest, one file per step (`test_07-add-expense.py` …) plus `conftest.py`. Amounts are shown in € with no currency stored.
- The SQLite file `expense_tracker.db` and `venv/` are gitignored. Deployment is via `Procfile` (gunicorn).

## Conventions

- Missing or foreign records return 404 (never reveal that an id exists); data-changing actions are POST with Post/Redirect/Get.
- All templates extend `base.html`; no inline styles; use `url_for` for links and form actions.
- Commits and PRs carry no Claude attribution (`attribution` is blank in `.claude/settings.json`).
