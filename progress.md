# Spendly: progress and decisions

How the app grew, step by step, and what led to each change. Sources:
- the git history and the step specs in `.claude/specs/` (the "why" for steps
  1–13 comes from them);
- `GLOSSARY.md` and the ADRs in `docs/adr/`;
- the 2026-10-16 working sessions (steps 13–14, evals, advisor fixes).

## Timeline

### 1. Landing page and foundation (2026-09-10 → 09-17)
- Landing page with terms and privacy pages, then a hero redesign "to match the
  mockup" with a demo video modal.
- **Step 1, database setup:** SQLite through raw `sqlite3`, no ORM, and
  parameterised queries only. These rules have held ever since.

### 2. Accounts and the profile page (09-27 → 09-29)
- **Steps 2–3, registration and login/logout:** the first steps to write real
  user data. Passwords are hashed with werkzeug, and the session is signed.
- **Step 4, profile UI with static data:** built UI-first "to validate the
  design in isolation" before wiring any queries.
- **Step 5, real data:** the profile was wired to real queries.
- **Step 6, date filter:** a filter driven by the query string, with no new
  routes.
- **Tooling added along the way:**
  - slash commands for specs and seed data;
  - review and test subagents;
  - a project UI skill.
- The landing page's currency moved from rupee to euro. Amounts are shown in €,
  with no currency stored.

### 3. Expenses and deployment (09-29)
- **Steps 7–9, add, edit and delete expenses:** delete is a POST behind a
  confirmation page, never a GET link.
- **Analytics:** a "coming soon" page.
- **Project rules:**
  - `/ship-feature` (PR, squash merge, branch cleanup);
  - `black` and rm-guard hooks;
  - no Claude attribution in commits or PRs.
- **Deployment:** gunicorn and a Procfile for Railway.

### 4. The savings plan (10-05), steps 10–12
The app moved from recording spending to planning how a stable Income is split.
- **Step 10, Income and Obligations:** inputs only, nothing calculated yet.
- **Step 11, Goals:** each Goal has a Deadline, a target, an Importance and a
  Saved Amount.
- **Step 12, the Plan:** a fixed algorithm splits Income into Installments.
  Goals are funded by Importance, then Deadline. A Goal is funded in full or not
  at all, and the rest is the Surplus. An Unreachable Goal gets a warning and
  the earliest date it could be met. Money is in integer cents, and `today` is
  passed in, so the Plan is exactly testable.
- **Domain language fixed in `GLOSSARY.md`:** Plan, Installment, Surplus,
  Unreachable Goal, and the words to avoid.

### 5. AI, the careful way (10-16), step 13
- **ADR 0001:** the LLM only *suggests* a Goal's Importance and Dependencies,
  and the user confirms. The money split stays a deterministic algorithm. We
  rejected LLM allocation because it varies between runs, can't be tested
  exactly and costs an API call. The Plan must also work with no LLM.
- **Step 13:**
  - Dependencies: a Goal waits until its prerequisite is funded.
  - `suggestions.py` (Anthropic API) validates replies as untrusted input and
    never raises.
- **The advisor agent** (`advisor/`) was built: what-if questions answered by
  calling the planner in a loop.
  - It has no write tool. Proposals need the user's confirmation, and
    `apply_confirmed` re-validates them.
  - It is not wired into `app.py` yet.
- **Shipped as PR #12.** A leftover `tools.py` duplicate was removed.

### 6. Redirecting the Surplus (10-16), step 14
**What led to it.** The Plan had four known limitations:
1. Money freed when a Goal is funded was never reused.
2. Cash saved in earlier months was not counted.
3. Funding is all or nothing.
4. Months are 30-day periods.

The user chose to start with the first. Rather than letting the algorithm
re-spread freed money, the user wanted to be told and then decide.

**What it added:**
- A forecast of each **Freed Installment**, plus a banner when a Goal becomes
  fully funded.
- **Top-ups:** extra money per month on one or more Goals, from a start date,
  so a Goal finishes early.
- A **Lump Sum:** a one-off amount added to the Saved Amount, which lowers the
  Installment.
- A **Savings Pot:** amounts with start dates, cut first when money is short.
- **ADR 0002:** the Plan forecasts but never redirects money itself.

**Hardened by the code review:**
- Redirect limits, so the same money can't be redirected twice; the limit is
  rechecked under the database write lock.
- Caps of 100 Top-ups and 20 Savings Pot amounts.
- A planner speed-up, about 100× faster.
- The Lump Sum Installment rendered on the server.

### 7. Evaluating the AI (10-16)
**What led to it.** The first advisor eval (5 scenarios, 1 run) looked weak at
3/5. The traces showed it was mostly measuring the wrong things:
- a correct "no change needed" answer was marked as failed;
- harmful advice (an invented raise, a deleted Goal) passed;
- a tool bug made the model waste its steps.

**What changed:**
- **Advisor eval rebuilt:**
  - 20 tasks over 8 fixtures;
  - only code-based graders;
  - pass^k over several runs;
  - a reference answer per task that must pass every grader.
- **New suggestions eval:** 30 cases, weighted towards false Dependencies,
  because a wrong Dependency blocks a Goal's funding.
- **Advisor bugs found along the way:**
  - `simulate_plan` restarts from the real data on each call, and the model
    wasn't told;
  - the snapshot missed Top-ups;
  - confirming an edit wiped a Goal's Dependencies;
  - a missing `final_answer` field crashed the loop.
- **First real baseline:** pass^1 0.58, pass^3 0.30, after discounting grader
  false positives. Next steps are in `docs/advisor-next-steps.md`.

### 8. Fixing the advisor (10-16), `feature/advisor-eval-fixes`
**What led to it.** The baseline traces showed four real problems:
- an off-topic question got a new Goal;
- the model invented Income to "solve" an impossible trip;
- it proposed changes nobody asked for;
- it never mentioned a paused Top-up.

The grader also flagged 8 summaries that were actually correct.

**What changed:**
- **Grader:** an Importance word is checked only when it is attached to a Goal
  name, e.g. `Laptop (Nice-to-have)`. It used to be matched to the nearest
  name.
- **Diff:** `simulate_plan` now reports Top-up status changes, so the model
  sees a paused Top-up.
- **Guard (structure, not prompt):** `final_answer` rejects Income and
  Obligation amounts the question did not give. It also rejects removing a
  Goal or Obligation the question did not name.
- **Prompt rules:**
  - off-topic questions get no changes;
  - no Income or Obligation change the user did not mention;
  - only Goals the user mentioned may change.
- **`pytest.ini`:** bare `pytest` works.

**Result:** pass^1 0.58 → 0.72, pass^3 0.30 → 0.50.
- Collateral 46 → 56 of 60.
- No answer proposed Income.
- `off_topic` is still 0/3.

The biggest remaining failure is a "yes" verdict while another Goal becomes
Unreachable or a Top-up is paused (7 runs). Next steps are in
`docs/advisor-next-steps.md`.

## Decision log

| Decision | Why | Recorded in |
|---|---|---|
| Raw `sqlite3`, parameterised queries, every query filtered by `user_id` | Simple, safe, and users only touch their own rows | `CLAUDE.md` |
| Missing or foreign records return 404 | Never reveal that an id exists | `CLAUDE.md` |
| Data changes are POST, with Post/Redirect/Get | No changes from links or reloads | `CLAUDE.md` |
| One numbered spec per step; the spec is the source of truth | Build in small, reviewable steps | `.claude/specs/` |
| Glossary words used exactly | One language in UI, code and docs | `GLOSSARY.md` |
| The algorithm allocates; the LLM only suggests; the user confirms | Deterministic, testable, works offline | ADR 0001 |
| Money in integer cents, `today` passed in | An Installment is never a cent short; the Plan is reproducible | `planner.py` |
| Freed money is forecast, and the user redirects it | The user wanted to decide, not an automatic re-spread | ADR 0002 |
| Installment stays "even"; a Top-up is a separate amount | Keeps the glossary meaning of Installment | ADR 0002, `GLOSSARY.md` |
| Savings Pot is cut first, then Top-ups paused, before any Goal | Goals keep priority over optional saving | spec 14 |
| Step 14 names avoid "free money" and "allocation" | The glossary lists them as words to avoid | spec 14 |
| A redirect is checked against money still free from its start date | The same money can't be redirected twice | spec 14 |
| The advisor has no write tool | Safety by structure, not by prompt | `docs/advisor.md` |
| `final_answer` rejects Income or Obligation amounts the question did not give, and removals it did not name | A model can ignore a prompt rule but not a guard; `simulate_plan` stays unchecked so the model can still explore | `docs/advisor.md` |
| The Importance grader checks only words attached to a Goal name | Nearest-name matching flagged 9 summaries, 8 of them wrongly | `advisor/evals.py` |
| Evals use code graders, pass^k and a reference answer per task | Scores mean the same every time; users need consistency; a failing task must be fair | `advisor/evals.py` |
| `temperature` left out for `gpt-5*` models | The API rejects 0 for them (seen as a 400 error) | `advisor/openai_client.py` |
| Real-model evals never run in `pytest` | They cost money and take minutes | `CLAUDE.md` |
| Commits carry no Claude attribution | Project preference | `CLAUDE.md`, `.claude/settings.json` |

## Still open

- **The other three planner limitations:**
  - carried-over cash is only partly covered, by the Lump Sum;
  - funding is still all or nothing;
  - periods are still 30 days.
- **The advisor's real problems and next steps:** see
  `docs/advisor-next-steps.md`.
- **The advisor is not wired into `app.py`.**
- **The analytics page** is still "coming soon".
- **`feature/redirect-surplus`** is pushed but not merged;
  `feature/advisor-eval-fixes` is built on it.
