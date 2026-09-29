# Spec: Delete Expense

## Overview
Step 9 replaces the `/expenses/<id>/delete` placeholder with a working delete
flow. Each row in the profile's transaction list gets a "Delete" control; the
user confirms on a dedicated confirmation page and the expense is then removed.
Deletion changes data, so it is a `POST` (never a link-triggered `GET`) and only
the owner can delete their own expenses. This is the "D" in the expense CRUD
steps, following Add (Step 7) and Edit (Step 8).

## Depends on
- Step 1: Database setup (`expenses` table in `database/db.py`)
- Step 3: Login and logout (`session["user_id"]`, `login_required`)
- Step 5: Profile page wired to real data (`get_recent_transactions`)
- Step 8: Edit expense (`get_expense`, the per-row action column in `profile.html`)

## Routes
- `GET /expenses/<int:id>/delete` — show a confirmation page summarising the expense (amount, category, date, description) with Confirm and Cancel actions — logged-in (owner only)
- `POST /expenses/<int:id>/delete` — delete the expense, flash a success message, redirect to `/profile` — logged-in (owner only)

Unauthenticated requests redirect to `/login`. If the expense does not exist or
belongs to another user, respond with `404` (do not reveal that the id exists).
The existing `delete_expense` stub is `GET`-only; it becomes `GET`/`POST`.

## Database changes
No database changes. The existing `expenses` table already fits.

## Templates
- **Create:**
  - `templates/delete_expense.html` — confirmation page (extends `base.html`) showing the expense details, a `POST` form (Confirm delete button) and a Cancel link back to `/profile`
- **Modify:**
  - `templates/profile.html` — add a "Delete" link next to the existing "Edit" link in each row's action cell (`url_for('delete_expense', id=t.id)`)

## Files to change
- `app.py` — replace the `delete_expense` stub with a `login_required` GET/POST view; load the expense with ownership check (404 otherwise); on POST call the new delete helper, flash, redirect
- `database/queries.py`
  - add `delete_expense(expense_id, user_id)` — `DELETE FROM expenses WHERE id = ? AND user_id = ?`; return `True` if a row was deleted
- `templates/profile.html` — see Templates
- `static/css/profile.css` — style the Delete link (danger colour via CSS variable) alongside Edit

## Files to create
- `templates/delete_expense.html`
- `static/css/delete_expense.css` — confirmation card styles (only if `add_expense.css` cannot be reused cleanly)
- `tests/test_09-delete-expense.py` (created later via `/test-feature 09-delete-expense`)

## New dependencies
No new dependencies.

## Rules for implementation
- No SQLAlchemy or ORMs — raw `sqlite3` via `get_db()`
- Parameterised queries only
- Passwords hashed with werkzeug (no auth changes in this step)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- No inline styles
- Deletion happens only on `POST`; `GET` must never modify data
- Ownership is enforced in SQL (`AND user_id = ?` using `session["user_id"]`) for both the read and the delete; never trust the URL id alone
- Missing or foreign expense → `abort(404)` for GET and POST
- If the delete affects no row (vanished since the read) → `abort(404)`
- Redirect after successful POST (Post/Redirect/Get) to `url_for("profile")`
- Use `url_for` for all links and the form action
- Keep the Step 7 add and Step 8 edit flows working unchanged (existing tests must still pass)

## Definition of done
- [ ] Logged out, `/expenses/<id>/delete` (GET and POST) redirects to `/login`
- [ ] Each transaction row on `/profile` has a "Delete" link next to "Edit"
- [ ] The Delete link opens a confirmation page showing that expense's amount, category, date and description
- [ ] Visiting the confirmation page (GET) does not delete anything
- [ ] Cancel returns to `/profile` with the expense still listed
- [ ] Confirming redirects to `/profile`, shows a success message, and the expense is gone from the list, totals and category breakdown
- [ ] Only the targeted expense is removed; other expenses are untouched
- [ ] Deleting a non-existent id returns 404 (GET and POST)
- [ ] Deleting another user's expense (GET or POST) returns 404 and does not remove it
- [ ] Deleting the same expense twice: the second attempt returns 404
- [ ] The Add (Step 7) and Edit (Step 8) pages still work
- [ ] Styling uses CSS variables and matches the existing pages
