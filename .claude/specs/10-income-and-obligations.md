# Spec: Income and Obligations

## Overview
Step 10 is the first of four steps (10–13) that add a savings plan to Spendly.
It gives the user two of the plan's inputs: a stable monthly **Income** and a
list of **Obligations** (recurring essential costs such as rent or groceries,
each with a fixed monthly amount typed by the user). Nothing is calculated yet;
this step only stores and manages the data that Step 12 will later split. The
Obligation pages follow the same add/edit/delete pattern as the expense CRUD
steps (7–9). Terms are defined in `GLOSSARY.md`.

## Depends on
- Step 1: Database setup (`database/db.py`, `get_db()`, `init_db()`)
- Step 3: Login and logout (`session["user_id"]`, `login_required`)
- Steps 7–9: Add, edit and delete expense (pattern to mirror: `csrf_protect`,
  Post/Redirect/Get, 404 on missing or foreign records, confirmation page)

## Routes
- `GET /obligations` — list the user's Obligations with their total, show the current monthly Income (or a prompt to set it) and links to add, edit and delete — logged-in
- `GET /income` — form to set or change the monthly Income, pre-filled if one exists — logged-in
- `POST /income` — validate and save the monthly Income, flash a success message, redirect to `/obligations` — logged-in
- `GET /obligations/add` — empty Obligation form — logged-in
- `POST /obligations/add` — validate and create an Obligation, redirect to `/obligations` — logged-in
- `GET /obligations/<int:id>/edit` — Obligation form pre-filled — logged-in (owner only)
- `POST /obligations/<int:id>/edit` — validate and update, redirect to `/obligations` — logged-in (owner only)
- `GET /obligations/<int:id>/delete` — confirmation page summarising the Obligation — logged-in (owner only)
- `POST /obligations/<int:id>/delete` — delete, flash, redirect to `/obligations` — logged-in (owner only)

Unauthenticated requests redirect to `/login`. A missing or foreign Obligation
responds `404` (do not reveal that the id exists). `GET` never changes data.

## Database changes
Two new tables, added to `init_db()` with `CREATE TABLE IF NOT EXISTS` so
existing databases pick them up without a migration:

```sql
CREATE TABLE IF NOT EXISTS incomes (
    user_id INTEGER PRIMARY KEY,
    monthly_amount REAL NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id)
)

CREATE TABLE IF NOT EXISTS obligations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id)
)
```

One Income row per user (the primary key is `user_id`), saved with an upsert.
`users` and `expenses` are unchanged. `seed_db()` is not changed.

## Templates
- **Create:**
  - `templates/obligations.html` — list page (Income summary, Obligations table, total, add link)
  - `templates/income.html` — Income form
  - `templates/obligation_form.html` — shared add/edit form
  - `templates/delete_obligation.html` — confirmation page
- **Modify:**
  - `templates/base.html` — add an "Obligations" link in the logged-in navbar, active when `request.endpoint` starts with `obligation` or equals `income`

## Files to change
- `app.py` — add the routes above, plus `_validate_obligation_form(form)` and `_validate_income_form(form)` mirroring `_validate_expense_form`; reuse `MAX_AMOUNT`; add `MAX_NAME_LENGTH = 60`
- `database/db.py` — add the two `CREATE TABLE` statements to `init_db()`
- `database/queries.py` — add `get_income(user_id)`, `set_income(user_id, amount)` (upsert), `list_obligations(user_id)`, `get_obligation(obligation_id, user_id)`, `create_obligation(user_id, name, amount)`, `update_obligation(obligation_id, user_id, name, amount)`, `delete_obligation(obligation_id, user_id)`
- `templates/base.html` — see Templates
- `.claude/specs/` and `GLOSSARY.md` are not touched by the implementation

## Files to create
- `templates/obligations.html`
- `templates/income.html`
- `templates/obligation_form.html`
- `templates/delete_obligation.html`
- `static/css/obligations.css` — page styles (reuse `add_expense.css` for the forms where it fits)
- `tests/test_10-income-and-obligations.py` (created later via `/test-feature 10-income-and-obligations`)

## New dependencies
No new dependencies.

## Rules for implementation
- No SQLAlchemy or ORMs — raw `sqlite3` via `get_db()`
- Parameterised queries only
- Passwords hashed with werkzeug (no auth changes in this step)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- No inline styles
- Use the glossary words exactly: **Income**, **Obligation**; never "bill", "salary" or "fixed expense" in the UI or code names
- Amounts are shown in € with two decimals; no currency is stored
- Every data-changing action is `POST` with `csrf_protect` and Post/Redirect/Get; `GET` never modifies data
- Ownership is enforced in SQL (`AND user_id = ?` from `session["user_id"]`) for every read, update and delete
- Income and Obligation amounts must be finite, greater than zero and at most `MAX_AMOUNT`, rounded to 2 decimals; reject `nan` and `inf`
- Obligation name is required, trimmed, at most `MAX_NAME_LENGTH` characters; duplicate names are allowed
- Use `url_for` for all links and form actions
- Keep the existing expense flows and tests working unchanged

## Definition of done
- [ ] Logged out, every route above redirects to `/login`
- [ ] Logged in, the navbar shows an "Obligations" link that opens `/obligations`
- [ ] `/obligations` with no data shows an empty state with prompts to set Income and add an Obligation
- [ ] Setting an Income of `2500` at `/income` saves it, shows a success message and displays `€2,500.00` on `/obligations`
- [ ] Setting an Income again replaces the previous value (still one row for the user)
- [ ] Income of `0`, a negative number, `abc`, `nan`, `inf` or a value above `MAX_AMOUNT` shows an error and saves nothing
- [ ] Adding an Obligation `Rent` / `900` adds it to the list and the total updates
- [ ] An Obligation with a blank name, an over-long name, or an invalid amount shows an error and is not saved
- [ ] Editing an Obligation updates its name and amount and the total
- [ ] The delete link opens a confirmation page; visiting it (GET) deletes nothing; Cancel keeps the Obligation; Confirm removes it and updates the total
- [ ] A missing id, or another user's Obligation, returns 404 on edit and delete (GET and POST) and is not changed
- [ ] User A never sees or affects user B's Income or Obligations
- [ ] Deleting the same Obligation twice: the second attempt returns 404
- [ ] Existing databases start up without error and keep their users and expenses
- [ ] Add, edit and delete expense (Steps 7–9) and the profile page still work
- [ ] Styling uses CSS variables and matches the existing pages
