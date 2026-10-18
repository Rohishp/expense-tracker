# Spec: Goals

## Overview
Step 11 is the second of four steps (10–13) that add a savings plan to SaWings.
Step 10 stored the **Income** and **Obligations**; this step lets the user record
their **Goals**: one-off things they want to pay for by a chosen **Deadline**,
each with a target amount, an **Importance** and a **Saved Amount** they have
already set aside. Nothing is calculated yet; Step 12 will use this data to
compute the Plan (Installments, Surplus, Unreachable Goals). Goals follow the same
add/edit/delete pattern as Obligations (Step 10) and the expense CRUD steps (7–9).
Importance is chosen by the user here; the AI suggestion of Importance and
**Dependencies** (see ADR 0001) is deliberately left to a later step, so Goals
carry no Dependencies yet. Terms are defined in `GLOSSARY.md`.

## Depends on
- Step 1: Database setup (`database/db.py`, `get_db()`, `init_db()`)
- Step 3: Login and logout (`session["user_id"]`, `login_required`)
- Step 10: Income and Obligations (pattern to mirror: validators, `eur` filter,
  `csrf_protect`, Post/Redirect/Get, 404 on missing or foreign records, the
  navbar link and the `obligations.css` page styles)

## Routes
- `GET /goals` — list the user's Goals (name, target, Saved Amount, Deadline, Importance) ordered by Deadline, with links to add, edit and delete — logged-in
- `GET /goals/add` — empty Goal form — logged-in
- `POST /goals/add` — validate and create a Goal, flash a success message, redirect to `/goals` — logged-in
- `GET /goals/<int:id>/edit` — Goal form pre-filled — logged-in (owner only)
- `POST /goals/<int:id>/edit` — validate and update, flash, redirect to `/goals` — logged-in (owner only)
- `GET /goals/<int:id>/delete` — confirmation page summarising the Goal — logged-in (owner only)
- `POST /goals/<int:id>/delete` — delete, flash, redirect to `/goals` — logged-in (owner only)

Unauthenticated requests redirect to `/login`. A missing or foreign Goal
responds `404` (do not reveal that the id exists). `GET` never changes data.

## Database changes
One new table, added to `init_db()` with `CREATE TABLE IF NOT EXISTS` so existing
databases pick it up without a migration:

```sql
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    target_amount REAL NOT NULL,
    saved_amount REAL NOT NULL DEFAULT 0,
    deadline TEXT NOT NULL,
    importance TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id)
)
```

`deadline` is a `YYYY-MM-DD` string (as `expenses.date`). `importance` is one of
`Must-have`, `Should-have`, `Nice-to-have`, held in a new `IMPORTANCE_LEVELS`
list in `database/db.py` next to `CATEGORIES`. `users`, `expenses`, `incomes` and
`obligations` are unchanged. `seed_db()` is not changed.

## Templates
- **Create:**
  - `templates/goals.html` — list page (Goals table, empty state, add link)
  - `templates/goal_form.html` — shared add/edit form
  - `templates/delete_goal.html` — confirmation page
- **Modify:**
  - `templates/base.html` — add a "Goals" link in the logged-in navbar after "Obligations", active for the Goal endpoints (`goals`, `add_goal`, `edit_goal`, `delete_goal`) using an explicit endpoint tuple

## Files to change
- `app.py` — add the routes above, plus `_validate_goal_form(form)` mirroring `_validate_obligation_form`; reuse `_validate_amount` and `MAX_AMOUNT`, `MAX_NAME_LENGTH`; add `MAX_GOALS = 100`
- `database/db.py` — add `IMPORTANCE_LEVELS` and the `goals` `CREATE TABLE` statement to `init_db()`
- `database/queries.py` — add `list_goals(user_id)`, `get_goal(goal_id, user_id)`, `create_goal(user_id, name, target_amount, saved_amount, deadline, importance)`, `update_goal(goal_id, user_id, name, target_amount, saved_amount, deadline, importance)`, `delete_goal(goal_id, user_id)`
- `templates/base.html` — see Templates
- `.claude/specs/` and `GLOSSARY.md` are not touched by the implementation

## Files to create
- `templates/goals.html`
- `templates/goal_form.html`
- `templates/delete_goal.html`
- `static/css/goals.css` — page styles only where `obligations.css` / `add_expense.css` cannot be reused
- `tests/test_11-goals.py` (created later via `/test-feature 11-goals`)

## New dependencies
No new dependencies.

## Rules for implementation
- No SQLAlchemy or ORMs — raw `sqlite3` via `get_db()`
- Parameterised queries only
- Passwords hashed with werkzeug (no auth changes in this step)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- No inline styles
- Use the glossary words exactly: **Goal**, **Importance**, **Deadline**, **Saved Amount**; never "wishlist item", "priority", "due date", "progress" or "balance" in the UI or code names
- Amounts are shown in € with two decimals; no currency is stored
- Every data-changing action is `POST` with `csrf_protect` and Post/Redirect/Get; `GET` never modifies data
- Ownership is enforced in SQL (`AND user_id = ?` from `session["user_id"]`) for every read, update and delete
- Goal name is required, trimmed, at most `MAX_NAME_LENGTH` characters; duplicate names are allowed
- Target amount must be finite, greater than zero and at most `MAX_AMOUNT`, rounded to 2 decimals (reuse `_validate_amount`); reject `nan` and `inf`
- Saved Amount is optional (blank means `0`); when given it must be finite, at least zero, at most the target amount, rounded to 2 decimals
- Deadline is required, a valid `YYYY-MM-DD` date, and strictly after today
- Importance must be one of `IMPORTANCE_LEVELS`; the form pre-selects `Should-have` on add
- A user may have at most `MAX_GOALS` Goals; adding beyond that shows an error
- Use `url_for` for all links and form actions
- No Dependencies, no LLM calls and no Plan calculation in this step
- Keep the existing expense and Obligation flows and tests working unchanged

## Definition of done
- [ ] Logged out, every route above redirects to `/login`
- [ ] Logged in, the navbar shows a "Goals" link that opens `/goals`, highlighted on every Goal page
- [ ] `/goals` with no data shows an empty state with a prompt to add a Goal
- [ ] Adding a Goal `Laptop` / target `1200` / Saved Amount `200` / Deadline in the future / `Should-have` adds it to the list, shows a success message and displays `€1,200.00` and `€200.00`
- [ ] Goals are listed in Deadline order, earliest first
- [ ] Leaving Saved Amount blank saves `0`
- [ ] A Goal with a blank name, an over-long name, a target of `0`, negative, `abc`, `nan`, `inf` or above `MAX_AMOUNT` shows an error and is not saved
- [ ] A negative Saved Amount, or one greater than the target, shows an error and is not saved
- [ ] A missing, malformed or past/today Deadline shows an error and is not saved
- [ ] An Importance value outside the three levels shows an error and is not saved
- [ ] Editing a Goal updates all its fields and shows a success message
- [ ] The delete link opens a confirmation page; visiting it (GET) deletes nothing; Cancel keeps the Goal; Confirm removes it
- [ ] A missing id, or another user's Goal, returns 404 on edit and delete (GET and POST) and is not changed
- [ ] User A never sees or affects user B's Goals
- [ ] Deleting the same Goal twice: the second attempt returns 404
- [ ] Adding a 101st Goal shows an error and is not saved
- [ ] Existing databases start up without error and keep their users, expenses, Income and Obligations
- [ ] Add, edit and delete expense, Income and Obligations (Steps 7–10) and the profile page still work
- [ ] Styling uses CSS variables and matches the existing pages
