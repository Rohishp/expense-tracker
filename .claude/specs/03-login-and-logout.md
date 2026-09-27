# Spec: Login and Logout

## Overview
This feature adds session-based authentication to Spendly. The `/login` route currently only
renders `login.html` on GET; it needs a POST handler that checks the submitted email/password
against the `users` table (created in Step 01) and, on success, starts a signed session. The
`/logout` placeholder needs to clear that session. This is the first step that introduces the
concept of a "logged-in" user, which every later step (profile, add/edit/delete expenses) will
depend on to know who the current user is.

## Depends on
- Step 01 — Database setup (`get_db()`, `init_db()`, and the `users` table)
- Step 02 — Registration (so real accounts exist to log in with; the seeded demo user
  `demo@spendly.com` / `demo123` also works)

## Routes
- `GET /login` — renders the login form (already implemented, unchanged) — public
- `POST /login` — validates credentials, starts the session, redirects to `/profile` on
  success — public
- `GET /logout` — clears the session, redirects to `/login` — logged-in (safe to call even
  with no active session; it simply has nothing to clear)

## Database changes
No database changes. Authentication is checked against the existing `users` table
(`email`, `password_hash`) — no new tables, columns, or session-storage table. The session
itself is Flask's built-in signed-cookie session, not server-side storage.

## Templates
- **Create:** none
- **Modify:** none — `templates/login.html` already posts to `/login` with `email` and
  `password` fields and already has an `{% if error %}` block for validation messages

## Files to change
- `app.py`:
  - Set `app.secret_key` (required for Flask sessions to work; use a hardcoded dev-only
    value, e.g. `app.secret_key = "dev-secret-key-change-in-production"` — this is a learning
    project with no deployment config, so a hardcoded key matches the existing scaffolding
    style rather than introducing environment-variable handling)
  - Change the `/login` route to accept `["GET", "POST"]`; on POST, look up the user by email,
    verify the password with `check_password_hash`, and either set `session["user_id"]` and
    redirect to `/profile`, or re-render `login.html` with an `error` message
  - Change `/logout` to clear the session (`session.clear()`) and redirect to `/login`, instead
    of returning the placeholder string

## Files to create
None.

## New dependencies
No new dependencies. `session` comes from Flask itself; `check_password_hash` comes from
`werkzeug.security` (the counterpart to `generate_password_hash`, already used in
`database/db.py` and `app.py`'s registration handler).

## Rules for implementation
- No SQLAlchemy or ORMs
- Parameterised queries only
- Passwords verified with werkzeug's `check_password_hash` — never compare plaintext passwords
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- Look up the user by email first; if no user with that email exists, or the password doesn't
  match, show the same generic error either way (e.g. "Invalid email or password.") — do not
  reveal whether the email exists, to avoid leaking which addresses are registered
- Do not implement a `login_required` decorator or protect any other route in this step —
  that belongs to the specific steps that need it (Step 4 profile, Step 7-9 expenses)

## Definition of done
- [ ] `GET /login` still renders the form with no errors
- [ ] Logging in with a valid, registered email/password redirects to `/profile` and a
      session cookie is set
- [ ] Logging in with an unregistered email shows "Invalid email or password." and does not
      set a session
- [ ] Logging in with a registered email but wrong password shows the same error and does not
      set a session
- [ ] Visiting `/logout` after logging in clears the session and redirects to `/login`
- [ ] Visiting `/logout` with no active session still redirects to `/login` without erroring
- [ ] The app starts and runs without errors (`python app.py`)
