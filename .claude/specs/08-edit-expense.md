# Spec: Edit Expense

## Overview
Step 8 replaces the `/expenses/<id>/edit` placeholder with a working form that
lets a logged-in user change an existing expense (amount, category, date,
description). The form is pre-filled with the stored values and reuses the
validation from Step 7. Users can only edit their own expenses. Each row in the
profile's transaction list gets an "Edit" link so the feature is reachable. This
is the "U" in the expense CRUD steps, following Add (Step 7) and preceding
Delete (Step 9).

## Depends on
- Step 1: Database setup (`expenses` table, `CATEGORIES` in `database/db.py`)
- Step 3: Login and logout (`session["user_id"]`, `login_required`)
- Step 5: Profile page wired to real data (`get_recent_transactions`)
- Step 7: Add expense (`_validate_expense_form`, `add_expense.html`, `add_expense.css`,
  `create_expense`)

## Routes
- `GET /expenses/<int:id>/edit` — render the form pre-filled with the expense — logged-in (owner only)
- `POST /expenses/<int:id>/edit` — validate and update the expense, redirect to `/profile` — logged-in (owner only)

Unauthenticated requests redirect to `/login`. If the expense does not exist or
belongs to another user, respond with `404` (do not reveal that the id exists).

## Database changes
No database changes. The existing `expenses` table already fits.

## Templates
- **Create:** none — reuse the Step 7 form template so add and edit stay in sync.
- **Modify:**
  - `templates/add_expense.html` — make the form reusable for both modes: the
    heading, subtitle, submit label and form `action` depend on whether an
    expense is being edited (e.g. an `expense_id`/`editing` variable). Consider
    renaming it to `expense_form.html` and updating the Step 7 route and tests
    accordingly (optional; keep the rename small and consistent).
  - `templates/profile.html` — add an "Actions" column with an "Edit" link
    (`url_for('edit_expense', id=t.id)`) to each transaction row. Update the
    empty-state `colspan`.

## Files to change
- `app.py` — replace the `edit_expense` stub with a `login_required` GET/POST
  view; load the expense with ownership check (404 otherwise); reuse
  `_validate_expense_form`; call the new update helper
- `database/queries.py`
  - add `get_expense(expense_id, user_id)` — returns a dict or `None`
    (`WHERE id = ? AND user_id = ?`)
  - add `update_expense(expense_id, user_id, amount, category, expense_date, description)`
    — `UPDATE ... WHERE id = ? AND user_id = ?`; return `True` if a row changed
  - `get_recent_transactions` — include `"id"` in each returned dict
- `templates/add_expense.html` — see Templates
- `templates/profile.html` — see Templates
- `static/css/profile.css` — style the Edit link/column using CSS variables
- `static/css/add_expense.css` — only if the shared form needs adjustments

## Files to create
- `tests/test_08-edit-expense.py` (created later via `/test-feature 08-edit-expense`)

## New dependencies
No new dependencies.

## Rules for implementation
- No SQLAlchemy or ORMs — raw `sqlite3` via `get_db()`
- Parameterised queries only
- Passwords hashed with werkzeug (no auth changes in this step)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- No inline styles
- Ownership is enforced in SQL (`AND user_id = ?` using `session["user_id"]`) for
  both the read and the update; never trust the URL id alone
- Missing or foreign expense → `abort(404)` for GET and POST
- Reuse `_validate_expense_form` and `MAX_*` constants; do not duplicate validation
- On validation error re-render the form (HTTP 200) with the submitted values
  preserved and no update
- Redirect after successful POST (Post/Redirect/Get) to `url_for("profile")`
- Use `url_for` for all links and the form action
- Keep the Step 7 add flow working unchanged (existing tests must still pass)

## Definition of done
- [ ] Logged out, `/expenses/<id>/edit` (GET and POST) redirects to `/login`
- [ ] Each transaction row on `/profile` has an "Edit" link to its edit page
- [ ] The edit page shows the form pre-filled with the expense's amount, category, date and description
- [ ] Submitting valid changes redirects to `/profile` and the updated values appear in the list, totals and category breakdown
- [ ] Only the targeted expense changes; other expenses are untouched
- [ ] Invalid amount, category, date or over-long description shows an error, keeps the entered values and does not update the row
- [ ] Clearing the description is accepted and stored as empty (NULL)
- [ ] Editing a non-existent id returns 404
- [ ] Editing another user's expense (GET or POST) returns 404 and does not modify it
- [ ] The Add expense page (Step 7) still works and its heading/label are unchanged
- [ ] Styling uses CSS variables and matches the existing pages
