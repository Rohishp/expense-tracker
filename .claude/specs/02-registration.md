# Spec: Registration

## Overview
This feature implements account creation for SaWings. The `/register` route currently only
renders `register.html` on GET; it needs a POST handler that validates the submitted form,
hashes the password, inserts a new row into the `users` table, and handles the duplicate-email
case. This is the first step that writes real user data through the app (rather than via the
`seed_db()` demo data), and every later step (login, profile, expenses) depends on real accounts
existing in the `users` table.

## Depends on
- Step 01 — Database setup (`get_db()`, `init_db()`, and the `users` table must already exist)

## Routes
- `GET /register` — renders the registration form (already implemented, unchanged) — public
- `POST /register` — validates input, creates the user, redirects to `/login` on success — public

## Database changes
No database changes. The existing `users` table (`id`, `name`, `email`, `password_hash`,
`created_at`) already supports registration as defined in `database/db.py`.

## Templates
- **Create:** none
- **Modify:** none — `templates/register.html` already posts to `/register` with `name`,
  `email`, `password` fields and already has an `{% if error %}` block for validation messages

## Files to change
- `app.py` — change the `/register` route to accept `["GET", "POST"]`; on POST, validate the
  form, hash the password, insert the user, and either redirect to `/login` (success) or
  re-render `register.html` with an `error` message (failure)

## Files to create
None.

## New dependencies
No new dependencies.

## Rules for implementation
- No SQLAlchemy or ORMs
- Parameterised queries only
- Passwords hashed with werkzeug (`generate_password_hash`)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- Validate `name`, `email`, and `password` are non-empty before inserting
- Catch the `sqlite3.IntegrityError` from a duplicate email (UNIQUE constraint) and show
  "An account with this email already exists." rather than a 500 error
- Do not create a session or log the user in on successful registration — redirect to `/login`
  instead, since session-based login is implemented in Step 3

## Definition of done
- [ ] `GET /register` still renders the form with no errors
- [ ] Submitting valid name/email/password creates a new row in `users` with a hashed
      (not plaintext) password
- [ ] A successful registration redirects to `/login`
- [ ] Submitting an already-registered email shows an inline error and does not create a
      duplicate row
- [ ] Submitting with a blank name, email, or password shows an inline error and does not
      insert a row
- [ ] The app starts and runs without errors (`python app.py`)
