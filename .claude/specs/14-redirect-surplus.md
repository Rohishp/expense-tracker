# Spec: Redirect Surplus

## Overview
Step 14 follows the savings plan (steps 10–13). The Plan gives each Goal an even
**Installment** and leaves the rest as **Surplus**, "shown as its own line for the
user to redirect", but until now there was no way to redirect it, and the Plan
never told the user when a Goal's Installment would stop being needed. This step
tells the user when money is free (today's Surplus, a Goal that has just become
fully funded, and **Freed Installments** forecast for future months) and lets the
user decide what to do with it: give one or more Goals a monthly **Top-up**, put
it in the **Savings Pot**, add a new Goal, or leave it as Surplus. Separately, a
user can add a one-off **Lump Sum** to a Goal, which lowers its Installment. The
Plan never redirects money on its own; every change is the user's choice (ADR
0001, and the new ADR 0002). Terms are defined in `GLOSSARY.md`.

## Decisions taken with the user
- Free money is shown in three places: today's Surplus on `/plan`, a banner when
  a Goal becomes fully funded, and a forecast of Freed Installments.
- A Goal's Installment is freed from the period after its last Installment: its
  Deadline, or earlier if Top-ups fund it sooner.
- Two ways to add to a Goal, both chosen by the user:
  - **Top-up**: a recurring amount per month that the user picks, starting from a
    chosen period. The Installment is unchanged, so the Goal is funded before its
    Deadline.
  - **Lump Sum**: a one-off amount added to the Goal's Saved Amount. The Deadline
    stays the same, so the Installment shrinks.
  - The user described the recurring kind as "a random amount" without a name.
    "Top-up" is a proposed name; confirm it on review.
- The **Savings Pot** is a monthly amount kept separate from Goals. It has no
  Deadline and is never a Goal. When money runs short it is cut first, before any
  Top-up or Goal. Like Top-ups, it is made of amounts that each start on a date,
  so a future Freed Installment can go to the Pot as well as to Top-ups.
- A redirect can never use the same money twice: the limit is what is still free
  from its start date onwards, after every Top-up and Pot amount already saved.
- "Ask the advisor" is out of scope until the advisor is wired into `app.py`.

## Depends on
- Step 3: Login and logout (`login_required`)
- Step 10: Income and Obligations
- Step 11: Goals (Saved Amount, Deadline)
- Step 12: Plan (`planner.plan`, Installment, Surplus, Unreachable Goal)
- Step 13: Dependencies (Goal ordering is unchanged by this step)

## Routes
- `GET /plan`: existing. Adds the Freed Installment forecast, Top-ups, the
  Savings Pot line, the "fully funded" banner and a "Redirect" link next to the
  Surplus and next to each Freed Installment. Logged-in.
- `GET /plan/redirect?source=surplus` or `?source=goal&goal_id=<id>`: shows the
  amount to redirect and when it is free (today for Surplus, the Freed date for a
  Goal). The form has one Top-up amount field per eligible Goal (funded, not the
  source Goal, still paying after the start date), a Savings Pot amount, and
  links to `/goals/add` and back to `/plan` ("leave it as Surplus").
  An unknown `source`, or a `goal_id` that is missing, someone else's or not
  funded, returns 404. Logged-in.
- `POST /plan/redirect`: saves the chosen Top-ups and Savings Pot amount, all
  starting on the source's start date. Logged-in, CSRF.
- `GET /top-ups/<id>/delete`: confirmation page. `POST` removes the Top-up.
  Logged-in, CSRF on POST, 404 if the Top-up is missing or someone else's.
- `GET /savings-pot/<id>/delete`: confirmation page. `POST` removes that Savings
  Pot amount. Logged-in, CSRF on POST, 404 if missing or someone else's.
- `GET /goals/<id>/lump-sum`: form for a one-off amount. `POST` adds it to the
  Goal's Saved Amount. Logged-in, CSRF on POST, 404 if the Goal is missing or
  someone else's.

Unauthenticated requests redirect to `/login`. Every POST uses Post/Redirect/Get
back to `/plan`.

## Database changes
Two new tables in `init_db()`, both `CREATE TABLE IF NOT EXISTS`, so existing
databases keep working:

```sql
CREATE TABLE IF NOT EXISTS top_ups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    goal_id INTEGER NOT NULL,
    amount REAL NOT NULL,            -- per period, > 0
    start_date TEXT NOT NULL,        -- YYYY-MM-DD, first period it applies
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id),
    FOREIGN KEY (goal_id) REFERENCES goals (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS savings_pot_amounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount REAL NOT NULL,            -- per period, > 0
    start_date TEXT NOT NULL,        -- YYYY-MM-DD, first period it applies
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id)
);
```

A Lump Sum needs no table. It updates `goals.saved_amount`. Deleting a Goal
removes its Top-ups through the cascade.

## The algorithm
These are additions to `planner.py`. It stays pure: integer cents, `today` passed
in, no Flask or database imports. `plan()` with no Top-ups and no Savings Pot
must return exactly what it returns today.

1. **Period of a date** = `max(0, ceil((date − today).days / 30))`, using the
   same 30-day period as step 12.
2. **Funding order is unchanged.** Goals are funded as in steps 12–13 and take
   their Installments first.
3. **Top-ups** are then funded from the remaining capacity in the current period
   (period 0), in Goal walk order, then by id. Status: `finished` if the Goal is
   fully funded; `paused` if the Goal is not funded; `scheduled` if its start
   period is after 0; `active` if it fits (and is subtracted); otherwise `paused`.
   Paused and finished Top-ups take nothing; later Top-ups are still tried.
4. **Savings Pot**: `set` = the sum of Pot amounts whose start period is 0;
   `funded = min(set, capacity left)`. If it is cut, the Plan shows both.
5. **Surplus** = capacity left after steps 2–4.
6. **Freed Installment forecast.** For each funded Goal, simulate periods
   `k = 0, 1, …` with `paid_k = Installment + the Goal's Top-ups whose start
   period ≤ k`, until the running total reaches `remaining`. The finish period `f`
   is the last period it pays. Its Freed date = `today + 30 × (f + 1)` days, and
   its Freed amount = `Installment + the Top-ups active in period f`. Fully funded
   Goals free nothing (their Installment is already `0` and part of the Surplus).
   The forecast is sorted by Freed date.
7. **Free money per period.** `free_at(k)` = Available − Installments of funded
   Goals still paying in period `k` − active and scheduled Top-ups started by `k`
   on Goals still paying − Pot amounts started by `k` (funded amount in period 0).
   `free_from(p)` = the minimum of `free_at(k)` for every `k ≥ p` up to the last
   Freed period, never below 0.
8. **Redirect limit.** The most that can be redirected is:
   - for `source=surplus`: `free_from(0)`;
   - for a Goal's Freed Installment with Freed period `p`:
     `min(Freed amount, free_from(p) − free_at(p − 1))`, floored at 0. That is,
     only money that becomes free in `p` and stays free, so the Surplus (free
     earlier) cannot also be redirected through a Goal's link.

   This stops the same money being redirected twice, and stops a new redirect
   breaking a later scheduled one. The server always recomputes the limit from the
   current Plan and never trusts an amount posted by the browser. Top-ups and Pot
   amounts created from a Goal's Freed Installment start on its Freed date; those
   created from Surplus start today.

`plan()` gains optional `top_ups=()` and `pot_amounts=()` arguments. Its result
gains `top_ups` (each with `status` `active`, `scheduled`, `paused` or
`finished`), `savings_pot` (`{"set", "funded", "amounts"}`) and `freed` (the
forecast list). Funded items gain `freed_period`, `freed_date` and
`freed_amount`. The existing keys keep their meaning.

## Templates
- **Create:**
  - `templates/redirect.html`: the redirect form. It shows the amount and the
    date it is free, one Top-up field per funded Goal, a Savings Pot field, a
    running "left to redirect" total (inline JS in `{% block scripts %}`), and
    links to add a Goal or leave it as Surplus.
  - `templates/lump_sum.html`: the Lump Sum form. It shows the Goal's remaining
    amount, its current Installment and the Installment it would have after the
    Lump Sum.
  - `templates/delete_top_up.html` and `templates/delete_pot_amount.html`:
    delete confirmations, matching `delete_goal.html`.
- **Modify:**
  - `templates/plan.html`: a "Redirect" link on the Surplus line; a "Coming up"
    section listing Freed Installments (Goal, date, amount, "Redirect" link);
    Top-ups under each Goal (amount, start date, status, delete link); a Savings
    Pot section listing its amounts (start date, delete link) with a warning when
    it is cut; and a
    banner for each fully funded Goal ("<Goal> is fully funded; redirect your
    Surplus").
  - `templates/goals.html`: a "Add Lump Sum" link per Goal that is not fully
    funded.

## Files to change
- `app.py`: the new routes, `/plan` passing Top-ups and the Savings Pot to
  `planner.plan`.
- `database/db.py`: the two tables.
- `database/queries.py`: `list_top_ups`, `get_top_up`, `delete_top_up`,
  `list_pot_amounts`, `get_pot_amount`, `delete_pot_amount`, `create_redirect`
  (Top-ups and Pot amount in one transaction), `add_lump_sum`. All filter by
  `user_id`.
- `planner.py`: see The algorithm.
- `templates/plan.html`, `templates/goals.html`: see Templates.
- `static/css/plan.css`: styles for the forecast, Top-ups and Savings Pot.
- `GLOSSARY.md`: add **Freed Installment**, **Top-up**, **Lump Sum** and
  **Savings Pot**; add "Top-up" and "Savings Pot" to Surplus's redirect wording.
  Installment stays "even", because Top-ups are a separate amount.
- `CLAUDE.md`: steps 1–14 and the new routes.

## Files to create
- `docs/adr/0002-user-redirects-freed-money.md`: the Plan forecasts Freed
  Installments but never moves money by itself. The user redirects it through
  Top-ups, a Lump Sum or the Savings Pot. Rejected alternative: automatically
  re-spreading freed money across Goals month by month.
- `templates/redirect.html`, `templates/lump_sum.html`,
  `templates/delete_top_up.html`, `templates/delete_pot_amount.html`
- `tests/test_14-redirect-surplus.py` (created later via
  `/test-feature 14-redirect-surplus`)

## New dependencies
No new dependencies.

## Rules for implementation
- No SQLAlchemy or ORMs
- Parameterised queries only
- Passwords hashed with werkzeug (no auth changes in this step)
- Use CSS variables. Never hardcode hex values.
- All templates extend `base.html`
- No inline styles. Use `url_for` for every link and form action.
- Use the glossary words exactly: **Surplus**, **Freed Installment**,
  **Top-up**, **Lump Sum**, **Savings Pot**, **Installment**, **Goal**. Never
  "free money", "allocation", "leftover", "budget" or "payment" in the UI or in
  code names.
- `planner.py` stays pure, and with no Top-ups and no Savings Pot its output is
  identical to step 13.
- No LLM calls. Nothing is redirected automatically; every Top-up, Lump Sum and
  Savings Pot change comes from a user POST.
- The redirect limit is recomputed on the server from the current Plan, and the
  sum of posted Top-ups plus the Savings Pot increase must not exceed it.
- Amounts must be positive numbers with at most 2 decimals; empty redirect fields
  are skipped, and at least one must be filled. A Lump Sum must not exceed the
  Goal's remaining amount. Invalid input
  re-renders the form with an error and saves nothing.
- Missing or foreign Goals and Top-ups return 404.
- A user has at most 100 Top-ups and 20 Savings Pot amounts (every Plan loops
  over them); a redirect over the cap shows an error and saves nothing.
- The redirect is rechecked inside the database write lock before it is saved,
  so two redirects at the same moment cannot both use the same money.

## Definition of done
- [ ] Logged out, every new route redirects to `/login`
- [ ] With no Top-ups and no Savings Pot, `/plan` shows the same Installments, Unreachable Goals and Surplus as before this step
- [ ] `/plan` shows a "Redirect" link next to a Surplus above `€0.00`, and none when the Surplus is `€0.00`
- [ ] With the step 12 example data (Income 2000, Obligations 1200, Car licence 2400 by +180 days, Laptop 1200 by +360 days), "Coming up" lists Car licence freeing `€400.00` and Laptop freeing `€100.00`, each with its Freed date
- [ ] Redirecting the Surplus as a `€300.00` Top-up to the Car licence shows it under that Goal as active, makes the Surplus `€0.00`, and moves the Car licence's Freed date earlier
- [ ] Redirecting the Car licence's Freed Installment as a Top-up to the Laptop creates a Top-up that is scheduled from the Car licence's Freed date, and does not change today's Surplus
- [ ] After the Car licence's whole Freed Installment has been redirected, redirecting it again is refused (its limit is `€0.00` and "Coming up" shows it as already redirected)
- [ ] A redirect whose Top-ups plus Savings Pot add up to more than the amount being redirected shows an error and saves nothing, even if the form's amount was edited in the browser
- [ ] `/plan/redirect` with an unknown source, or a `goal_id` that is missing, someone else's or not funded, returns 404
- [ ] Redirecting `€100.00` of the Surplus to the Savings Pot shows it in the Savings Pot section and lowers the Surplus by `€100.00`; deleting that amount (confirm page, then POST) restores the Surplus
- [ ] Redirecting a Goal's Freed Installment to the Savings Pot creates a Pot amount scheduled from the Freed date that does not change today's Surplus
- [ ] When Income drops so that not everything fits, the Savings Pot is cut first (showing set and funded amounts), then Top-ups are paused with a warning, before any Goal becomes Unreachable
- [ ] A Lump Sum of `€600.00` on a Goal with `€1200.00` remaining over 6 periods lowers its Installment from `€200.00` to `€100.00` and raises its Saved Amount by `€600.00`
- [ ] A Lump Sum larger than the Goal's remaining amount, zero, negative or non-numeric shows an error and saves nothing
- [ ] When a Goal's Saved Amount reaches its target, `/plan` shows a banner saying it is fully funded, with a link to redirect the Surplus
- [ ] Deleting a Top-up (confirm page, then POST) removes it, and the Surplus and Freed dates update
- [ ] Deleting a Goal removes its Top-ups
- [ ] A Top-up or Goal id belonging to another user returns 404 on every route
- [ ] Every new POST without a valid CSRF token returns 400 and changes nothing
- [ ] `planner.py` still imports nothing from Flask or `database`, and gives the same output for the same inputs
- [ ] Existing databases start up without error and keep all their data
- [ ] Steps 1–13 and the profile page still work
- [ ] Styling uses CSS variables and matches the existing pages
