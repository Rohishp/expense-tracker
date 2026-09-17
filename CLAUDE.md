# 
## Project overview

"Spendly" is a Flask-based expense tracker built incrementally as a step-by-step learning exercise. Many routes and the entire database layer are intentionally left as stubs, marked with comments like `# Students will write this file in Step 1` or `return "Logout — coming in Step 3"`. When asked to implement one of these, treat the stub's comment as the spec for that step rather than removing the surrounding scaffolding style.

## Commands

```bash
pip install -r requirements.txt   # install dependencies
python app.py                     # run the dev server on http://localhost:5001 (debug=True)
pytest                            # run tests
pytest path/to/test_file.py::test_name   # run a single test
```

There is no lint/format tooling configured in this repo.

## Architecture

- **`app.py`** — single-file Flask app; all routes are currently defined directly on `app` (no blueprints). Fully implemented routes render templates only (`/`, `/register`, `/login`, `/terms`, `/privacy`); a second block of routes (`/logout`, `/profile`, `/expenses/add`, `/expenses/<id>/edit`, `/expenses/<id>/delete`) are placeholders returning plain strings, pending auth and expense-CRUD implementation.
- **`database/db.py`** — not yet implemented. Intended to hold `get_db()` (SQLite connection with `row_factory` and foreign keys enabled), `init_db()` (creates tables with `CREATE TABLE IF NOT EXISTS`), and `seed_db()` (sample data for development). No ORM is used — raw `sqlite3` is the expected approach.
- **`templates/`** — Jinja2 templates extending `base.html`, which defines the shared navbar/footer and exposes `{% block title %}`, `{% block head %}`, `{% block content %}`, `{% block scripts %}`. Page-specific styles live in a dedicated CSS file per page (e.g. `landing.css`) loaded via a page's `{% block head %}`, layered on top of the shared `static/css/style.css`.
- **`static/css/style.css`** — defines the shared design tokens (CSS custom properties for color, font, radius, max-width) that other stylesheets build on; keep new styles consistent with these variables rather than hardcoding values.
- **`static/js/main.js`** — currently empty; shared/global JS goes here, while page-specific JS (e.g. modal behavior on the landing page) is inlined in that page's `{% block scripts %}`.
- The SQLite database file (`expense_tracker.db`) and `venv/` are gitignored and not present until `init_db()` is implemented and run.
