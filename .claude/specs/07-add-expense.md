# Spec: Add Expense

## Overview
Step 7 replaces the `/expenses/add` placeholder with a working form that lets a
logged-in user record a new expense (amount, category, date, optional
description). On success the expense is inserted into the existing `expenses`
table and the user is redirected to `/profile`, where it appears in the
transaction list and updates the summary stats and category breakdown. This is
the first write path for expense data and unblocks the edit (Step 8) and delete
(Step 9) steps.

## Depends on
- Step 1: Database setup (`expenses` table, `CATEGORIES` list in `database/db.py`)
- Step 3: Login and logout (`session["user_id"]`, `login_required`)
- Step 4/5: Profile page wired to real data (to see the new expense)

## Routes
- `GET /expenses/add` — render the add-expense form — logged-in
- `POST /expenses/add` — validate and insert the expense, redirect to `/profile` — logged-in

Unauthenticated requests to either method redirect to `/login`.

## Database changes
No database changes. The existing `expenses` table
(`user_id, amount REAL, category, date TEXT YYYY-MM-DD, description`) already
fits. Category values must come from `CATEGORIES` in `database/db.py`.

## Templates
- **Create:** `templates/add_expense.html` — form with fields `amount`
  (`type="number"`, `step="0.01"`, `min="0.01"`), `category` (`<select>` built
  from `CATEGORIES`), `date` (`type="date"`, defaults to today), `description`
  (optional text); inline error block; Cancel link back to `/profile`
- **Modify:** `templates/profile.html` — add an "Add expense" button linking to
  `url_for('add_expense')`

## Files to change
- `app.py` — replace the `add_expense` stub with a `login_required` GET/POST
  view; validate input; call the query helper; import `CATEGORIES`
- `database/queries.py` — add `create_expense(user_id, amount, category, date, description)`
- `templates/profile.html` — add "Add expense" link
- `static/css/profile.css` — style for the add button (only if needed)

## Files to create
- `templates/add_expense.html`
- `static/css/add_expense.css` (page-specific styles, loaded via `{% block head %}`)

## New dependencies
No new dependencies.

## Rules for implementation
- No SQLAlchemy or ORMs — raw `sqlite3` via `get_db()`
- Parameterised queries only
- Passwords hashed with werkzeug (no auth changes in this step)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- No inline styles
- `user_id` comes from `session`, never from form data
- Server-side validation (never trust the browser):
  - `amount` parses as a number, is finite, and is > 0; store rounded to 2 decimals
  - `category` must be in `CATEGORIES`
  - `date` must parse with `datetime.strptime(value, "%Y-%m-%d")`
  - `description` is stripped and optional; store `None` when empty; cap length (e.g. 200)
- On validation error re-render the form with an error message and the
  previously entered values preserved (HTTP 200, no insert)
- Follow the existing error pattern (`render_template(..., error=...)`)
- Redirect after successful POST (Post/Redirect/Get) to `url_for("profile")`
- Use `url_for` for all links and the form action

## Definition of done
- [ ] Logged out, visiting `/expenses/add` redirects to `/login`
- [ ] Logged in, `/expenses/add` shows the form with today's date prefilled and all 7 categories
- [ ] Submitting valid data redirects to `/profile` and the new expense appears in the transaction list
- [ ] Profile total spent, transaction count and category breakdown reflect the new expense
- [ ] Submitting an empty amount, zero, negative or non-numeric amount shows an error and inserts nothing
- [ ] Submitting a category not in `CATEGORIES` (e.g. via edited HTML) shows an error and inserts nothing
- [ ] Submitting a malformed or empty date shows an error and inserts nothing
- [ ] After a validation error the entered values are still in the form
- [ ] An empty description is accepted
- [ ] The expense is saved under the logged-in user; another user does not see it
- [ ] An "Add expense" button on the profile page leads to the form
- [ ] Styling uses CSS variables and matches the existing pages
