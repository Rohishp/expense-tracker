# Spec: Plan

## Overview
Step 12 is the third of four steps (10–13) that add a savings plan to SaWings.
Steps 10 and 11 stored the user's **Income**, **Obligations** and **Goals**. This
step computes the **Plan**: a hypothetical split of the monthly Income across the
Obligations and the Goals. It shows each Goal's **Installment**, the **Surplus**
left over, and flags every **Unreachable Goal** with a strong warning and the
earliest date it could be met. The split is done by a fixed, deterministic
algorithm (see ADR 0001); no LLM is involved, and the Plan works with no network.
The Plan is calculated on demand from the stored data and is never saved, and it
does not alter recorded Expenses. Terms are defined in `GLOSSARY.md`.

## Depends on
- Step 3: Login and logout (`session["user_id"]`, `login_required`)
- Step 10: Income and Obligations (`get_income`, `list_obligations`, the `eur` filter)
- Step 11: Goals (`list_goals`, `IMPORTANCE_LEVELS`)

## Routes
- `GET /plan` — show the computed Plan for the logged-in user: Income, Obligations total, one row per Goal with its Installment or Unreachable warning, and the Surplus — logged-in

Unauthenticated requests redirect to `/login`. The view only reads data; it never
changes anything. Only `GET` is allowed.

## Database changes
No database changes. The Plan is computed from `incomes`, `obligations` and
`goals` and is not stored.

## The algorithm
Implemented as pure functions in a new module `planner.py` at the project root
(no Flask, no database access), so it can be tested exactly. The route fetches the
data and passes it in, along with `today`.

Inputs: `income` (float or `None`), `obligations` (list of dicts with `amount`),
`goals` (list of dicts as returned by `list_goals`), `today` (a `date`).

1. **Available** = `income` − sum of Obligation amounts. Round to 2 decimals.
2. **Months left** for a Goal = `max(1, ceil((deadline − today).days / 30))`.
3. **Remaining** for a Goal = `target_amount − saved_amount`. If it is zero or
   less, the Goal is **fully funded**: Installment `0.00`, status `funded`.
4. **Installment** = `remaining / months_left`, rounded **up** to the next cent
   (so the Goal is never underfunded by rounding).
5. **Order** Goals by Importance (`Must-have`, then `Should-have`, then
   `Nice-to-have`), then earliest Deadline, then lowest id.
6. Walk the Goals in that order with a running `capacity` that starts at
   `Available` (never below 0):
   - fully funded Goals take nothing;
   - if `Installment <= capacity`, the Goal is **funded**: subtract the
     Installment from `capacity`;
   - otherwise the Goal is an **Unreachable Goal**. It is not partly funded and
     takes nothing from `capacity`, and later (lower-ranked) Goals are still
     considered with what is left.
7. **Earliest date** for an Unreachable Goal = `today + (n × 30 days)` where
   `n = ceil(remaining / capacity)` using the `capacity` left at that point in
   the walk. If `capacity` is `0`, there is no date (shown as "not reachable with
   your current Income").
8. **Surplus** = `Available` − sum of funded Installments, never shown as
   negative. If `Available` is negative (Obligations exceed Income), the Plan
   flags that and every unfunded Goal is unreachable with no date.

`plan(income, obligations, goals, today)` returns a dict:
`{"income", "obligations_total", "available", "items", "surplus", "overcommitted"}`
where `items` is one dict per Goal in the walk order with the Goal's fields plus
`installment`, `months_left`, `status` (`funded`, `fully_funded` or `unreachable`)
and `earliest_date` (ISO string or `None`). If `income` is `None`, `plan` is not
called; the route shows the "set your Income" prompt instead.

## Templates
- **Create:**
  - `templates/plan.html` — the Plan page (extends `base.html`): an Income / Obligations / Available summary, a table of Goals with Importance, Deadline, Installment, status, a warning block for each Unreachable Goal (with its earliest date or "not reachable with your current Income"), the Surplus line, and an over-committed banner when Obligations exceed Income
- **Modify:**
  - `templates/base.html` — add a "Plan" link in the logged-in navbar after "Goals", active on the `plan` endpoint
  - `templates/obligations.html` and `templates/goals.html` — no change needed; do not touch them

## Files to change
- `app.py` — add the `plan` route (login_required, GET only); import `planner`
- `templates/base.html` — see Templates

## Files to create
- `planner.py` — pure Plan algorithm as described above
- `templates/plan.html`
- `static/css/plan.css` — page styles (reuse `obligations.css` where it fits; warning styles use `--danger` / `--danger-light`)
- `tests/test_12-plan.py` (created later via `/test-feature 12-plan`)

## New dependencies
No new dependencies. Use only the standard library (`math`, `datetime`).

## Rules for implementation
- No SQLAlchemy or ORMs — raw `sqlite3` via the existing `queries` functions; no new SQL is needed
- Parameterised queries only
- Passwords hashed with werkzeug (no auth changes in this step)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- No inline styles
- Use the glossary words exactly: **Plan**, **Installment**, **Surplus**, **Unreachable Goal**, **Income**, **Obligation**, **Goal**; never "budget", "allocation", "payment", "leftover" or "failed goal" in the UI or code names
- `planner.py` is pure: it imports nothing from Flask or `database`, takes `today` as an argument and never calls `date.today()` itself
- The Plan is never written to the database, and the route never modifies data
- All data is read with the logged-in user's id from `session["user_id"]`; a user never sees another user's Plan
- Amounts are shown in € with two decimals using the `eur` filter; no currency is stored
- Money arithmetic is done in cents (integers) or with explicit rounding so that Installments never come out a cent short
- No LLM calls, no network access and no Dependencies (the AI suggestion of Importance and Dependencies is a later step)
- Use `url_for` for all links; the empty states link to `/income` and `/goals/add`
- Keep the existing expense, Income, Obligation and Goal flows and tests working unchanged

## Definition of done
- [ ] Logged out, `/plan` redirects to `/login`
- [ ] Logged in, the navbar shows a "Plan" link that opens `/plan`, highlighted on the Plan page
- [ ] With no Income set, `/plan` shows a prompt linking to `/income` and no Goal table
- [ ] With an Income but no Goals, `/plan` shows Income, the Obligations total, Available, a prompt linking to `/goals/add`, and a Surplus equal to Available
- [ ] Income `2500` with Obligations totalling `1000` shows Available `€1,500.00`
- [ ] A Goal with `1200` remaining and a Deadline 6 months (180 days) away shows an Installment of `€200.00`
- [ ] A Goal's Installment is rounded up to the next cent (for example `100.00` over 3 months is `€33.34`)
- [ ] A Goal whose Saved Amount equals its target shows as fully funded with an Installment of `€0.00`
- [ ] A Goal whose Deadline is less than 30 days away uses 1 month
- [ ] Goals are funded in Importance order, then earliest Deadline: when Available cannot cover everything, the `Must-have` Goal is funded and the `Nice-to-have` Goal is the Unreachable Goal
- [ ] An Unreachable Goal shows a strong warning and the earliest date it could be met, or "not reachable with your current Income" when nothing is left over
- [ ] An Unreachable Goal takes nothing from Available, and a lower-ranked Goal that fits in what is left is still funded
- [ ] Surplus equals Available minus the funded Installments and is shown as its own line
- [ ] When Obligations exceed Income, an over-committed banner is shown, Surplus is `€0.00` and every Goal that is not fully funded is an Unreachable Goal with no date
- [ ] Changing Income, an Obligation or a Goal and reloading `/plan` updates the Plan immediately; nothing is stored
- [ ] Two users each see only their own Plan
- [ ] `planner.py` has no import of Flask or `database` and gives the same output for the same inputs on every run
- [ ] `POST /plan` returns 405
- [ ] Existing databases start up without error and Steps 1–11 and the profile page still work
- [ ] Styling uses CSS variables and matches the existing pages
