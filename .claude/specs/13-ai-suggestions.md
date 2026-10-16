# Spec: AI Suggestions

## Overview
Step 13 is the last of four steps (10–13) that add a savings plan to Spendly. Until
now the user chose every Goal's **Importance** by hand and Goals had no
**Dependencies**. This step adds both ideas fully, following ADR 0001: an LLM may
only *suggest* a Goal's Importance and Dependencies, the user always confirms them,
and the money split stays a fixed algorithm. A "Suggest" button on the Goal form
asks the LLM for an Importance and for which of the user's other Goals this Goal
depends on, and fills the form for the user to review and save. Dependencies are
then stored, and the Plan honours them: a Goal whose prerequisite is not funded
cannot be funded. The Plan must keep working when the LLM is unavailable, so the
Suggest button is optional and every failure falls back to manual entry. Terms are
defined in `GLOSSARY.md`.

## Depends on
- Step 3: Login and logout (`session["user_id"]`, `login_required`, `csrf_protect`)
- Step 11: Goals (`goals` table, the Goal form and queries, `IMPORTANCE_LEVELS`)
- Step 12: Plan (`planner.py`, `plan.html`)

## Routes
- `POST /goals/suggest` — read the Goal form fields, ask the LLM for an Importance and Dependencies, and re-render the Goal form with the suggestion filled in and a notice to review it. Saves nothing — logged-in
- `GET /goals/add`, `POST /goals/add`, `GET /goals/<int:id>/edit`, `POST /goals/<int:id>/edit` — existing routes; the form gains a Dependencies field and the POST handlers validate and save it — logged-in (owner only)

Unauthenticated requests redirect to `/login`. `POST /goals/suggest` is wrapped in
`@csrf_protect`, and `GET /goals/suggest` returns 405. It takes an optional hidden
`goal_id` (set on the edit form) so the Goal being edited is excluded from its own
Dependency choices; a `goal_id` that is missing or not the user's own responds `404`.

## Database changes
One new table, added to `init_db()` with `CREATE TABLE IF NOT EXISTS`:

```sql
CREATE TABLE IF NOT EXISTS goal_dependencies (
    goal_id INTEGER NOT NULL,
    depends_on_goal_id INTEGER NOT NULL,
    PRIMARY KEY (goal_id, depends_on_goal_id),
    FOREIGN KEY (goal_id) REFERENCES goals (id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_goal_id) REFERENCES goals (id) ON DELETE CASCADE
)
```

A row means "`goal_id` depends on `depends_on_goal_id`" (the prerequisite).
`PRAGMA foreign_keys = ON` is already set per connection, so deleting a Goal removes
every row that mentions it. `goals` and the other tables are unchanged. `seed_db()`
is not changed.

## The suggestion module
A new `suggestions.py` at the project root keeps all LLM code out of `app.py` and
`planner.py`.

- `suggest(name, target_amount, deadline, other_goals)` returns
  `{"importance": <one of IMPORTANCE_LEVELS>, "depends_on": [<ids>]}` or `None`.
  `other_goals` is a list of the user's other Goals (id, name, target amount, Deadline).
- It calls the Anthropic Messages API through the official `anthropic` package,
  with the key from the `ANTHROPIC_API_KEY` environment variable, the model from
  `SPENDLY_LLM_MODEL` (default `claude-haiku-4-5-20251001`), a short timeout
  (10 seconds), no retries and a small `max_tokens`. The call is isolated in one
  function `_call_llm(prompt)` so tests can replace it.
- The prompt asks for JSON only. The reply is **parsed and validated**, never
  trusted: `importance` must be in `IMPORTANCE_LEVELS`; `depends_on` is cut down to
  ids that belong to `other_goals`. Anything unparseable, missing, or out of range
  is discarded (a bad Importance gives `None` for the whole suggestion; bad ids are
  just dropped).
- It returns `None`, and never raises, when the key is missing, the network fails,
  the call times out, the API errors, or the reply is invalid.
- The Goal name and the other Goals' names are user text, so they are passed to the
  LLM only as quoted data, and nothing from the reply is ever used except the
  validated Importance and ids.

## Dependency rules
Applied wherever Dependencies are saved (add and edit), in `app.py`:
- every id must be one of the logged-in user's own Goals (anything else is an error, not a 404)
- a Goal cannot depend on itself
- a Dependency may not create a cycle (A needs B and B needs A, or a longer loop); this is checked against the user's existing Dependencies before saving
- duplicates are ignored

## Plan changes (`planner.py`)
`planner.py` stays pure. Goal dicts now carry `depends_on` (a list of prerequisite
ids; a missing key means none).

1. **Order:** start from the existing order (Importance, then Deadline, then id), then
   place Goals so that every prerequisite comes before its dependents: repeatedly take
   the first Goal in the base order whose prerequisites are all already placed. (If a
   cycle somehow exists, the remaining Goals keep the base order.)
2. **Walk:** when a Goal is reached, if any of its prerequisites is not `funded` or
   `fully_funded`, the Goal is an **Unreachable Goal**: it takes nothing, has no
   earliest date, and its item has `blocked_by` set to the name of the first
   prerequisite that is not funded. Otherwise it is handled exactly as in Step 12.
3. Every item gains `blocked_by` (a Goal name or `None`). All other Step 12 rules and
   outputs are unchanged, and Plans for Goals without Dependencies are identical to
   before.

## Templates
- **Create:** none
- **Modify:**
  - `templates/goal_form.html` — add a "Depends on" checkbox group listing the user's other Goals; add a "Suggest Importance and Dependencies" button (a second submit button using `formaction="{{ url_for('suggest_goal') }}"`) and a hidden `goal_id` on the edit form; show a notice area for the suggestion result ("Suggested by AI — review before saving", or "Suggestions are unavailable right now. Choose manually.")
  - `templates/goals.html` — show each Goal's prerequisites ("Depends on: …") under its name
  - `templates/plan.html` — for a blocked Unreachable Goal show "Waiting for <Goal name>" instead of the earliest-date or Income message

## Files to change
- `app.py` — add `suggest_goal` route; load the user's other Goals for the form; validate and save Dependencies in `add_goal` and `edit_goal`; pass `depends_on` through to `queries`; `_validate_goal_form` returns the validated Dependency ids
- `database/db.py` — add the `goal_dependencies` `CREATE TABLE` to `init_db()`
- `database/queries.py` — `list_goals` and `get_goal` also return `depends_on` (list of ids); `create_goal` and `update_goal` accept `depends_on` and replace the Goal's rows in the same transaction; add `get_dependency_edges(user_id)` returning all `(goal_id, depends_on_goal_id)` pairs for the cycle check
- `planner.py` — Dependency ordering, `blocked_by`, as above
- `templates/goal_form.html`, `templates/goals.html`, `templates/plan.html` — see Templates
- `static/css/goals.css` — small styles for the Dependency checkboxes and the suggestion notice (CSS variables only)
- `requirements.txt` — add `anthropic`
- `CLAUDE.md` — update the overview and architecture (steps 1–13 implemented; add `planner.py`, `suggestions.py`, the savings-plan routes, the `ANTHROPIC_API_KEY` / `SPENDLY_LLM_MODEL` variables, and that debug now defaults off)

## Files to create
- `suggestions.py`
- `tests/test_13-ai-suggestions.py` (created later via `/test-feature 13-ai-suggestions`)

## New dependencies
- `anthropic` — the official Anthropic SDK, pinned to the current release in `requirements.txt` at implementation time. It is used only by `suggestions.py`.
- Deployment: set `ANTHROPIC_API_KEY` on Railway to enable suggestions. Without it the app runs unchanged and the Suggest button reports that suggestions are unavailable.

## Rules for implementation
- No SQLAlchemy or ORMs — raw `sqlite3` via `get_db()`
- Parameterised queries only
- Passwords hashed with werkzeug (no auth changes in this step)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- No inline styles
- Use the glossary words exactly: **Importance**, **Dependency**, **Goal**, **Plan**, **Unreachable Goal**; never "priority", "prerequisite" as a UI label for a Dependency, "blocker" or "suggestion engine" in the UI. (In code and comments "prerequisite" may be used only to name the Goal depended on.)
- ADR 0001 is binding: the LLM only suggests Importance and Dependencies, the user confirms by saving the form, and the Plan's money split stays in the deterministic `planner.py`
- The LLM is never called by the Plan, by any `GET`, or when a Goal is saved; only by `POST /goals/suggest`
- A suggestion changes nothing in the database; only saving the Goal form does
- The Plan and every Goal form must work with no API key, no network and no `anthropic` call succeeding
- Tests never make a network call: they replace `suggestions._call_llm` (or `suggestions.suggest`)
- The API key is read only from the environment, never stored in the repo, the database, a template or a log, and never sent to the browser
- Validate the LLM reply as untrusted input (allow-listed Importance, ids restricted to the user's own other Goals)
- `POST /goals/suggest` uses `@login_required` then `@csrf_protect`; ownership of any `goal_id` is checked in SQL and a miss is a 404
- Dependency ids submitted with the form are checked against the user's own Goals in SQL (`AND user_id = ?`); cycles and self-dependencies are rejected with an error
- `planner.py` stays pure (no Flask, no `database`, no `suggestions`, no `date.today()`)
- Use `url_for` for all links and form actions
- Keep the existing expense, Income, Obligation, Goal and Plan flows and tests working unchanged

## Definition of done
- [ ] Logged out, `/goals/suggest` redirects to `/login`, and `GET /goals/suggest` returns 405
- [ ] The Goal form (add and edit) shows a "Depends on" checkbox group listing the user's other Goals (never the Goal being edited) and a Suggest button
- [ ] With no other Goals, the "Depends on" group shows a hint instead of checkboxes
- [ ] Saving a Goal with a Dependency stores it, and `/goals` shows "Depends on: <Goal name>" for it
- [ ] Editing a Goal's Dependencies replaces the old ones; unchecking all removes them
- [ ] Deleting a Goal also removes every Dependency that mentions it, and the Goals that depended on it no longer show it
- [ ] A Dependency on another user's Goal, on a missing Goal, or on the Goal itself shows an error and saves nothing
- [ ] A Dependency that would create a cycle (A needs B, then B needs A) shows an error and saves nothing
- [ ] With the LLM call replaced by a fake returning `Must-have` and a Dependency, clicking Suggest re-renders the form with that Importance selected, that Dependency checked, the other fields kept, and the "review before saving" notice; nothing is saved
- [ ] A fake reply with an invalid Importance, or ids that are not the user's other Goals, is discarded and the form shows the "unavailable" notice (or only the valid ids are kept)
- [ ] With no `ANTHROPIC_API_KEY`, a network error, a timeout or an unparseable reply, clicking Suggest shows "Suggestions are unavailable right now. Choose manually." and the form still saves normally
- [ ] On the edit form, Suggest excludes the Goal being edited, and a `goal_id` that is missing or someone else's returns 404
- [ ] Without Dependencies, `/plan` is identical to Step 12
- [ ] In `/plan`, a Goal is funded after its prerequisite even if it has higher Importance, and its Installment is computed as in Step 12
- [ ] In `/plan`, a Goal whose prerequisite is not funded shows as an Unreachable Goal with "Waiting for <Goal name>" and no earliest date, and takes nothing from Available
- [ ] In `/plan`, a Goal whose prerequisite is fully funded (nothing left to save) is not blocked
- [ ] `POST /goals/suggest` without a valid CSRF token returns 400 and makes no LLM call
- [ ] No test or page load makes a real network call, and the app starts and works without the `anthropic` call succeeding
- [ ] `suggestions.py` never raises on API or parse failure and returns `None`
- [ ] Existing databases start up without error and keep their users, expenses, Income, Obligations and Goals
- [ ] Steps 1–12 and the profile page still work
- [ ] Styling uses CSS variables and matches the existing pages
