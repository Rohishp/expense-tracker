# Advisor agent: problems found and next steps

Hand-off for the next session. Written 2026-10-16, after the first real
baseline of the rebuilt advisor eval (`advisor/evals.py`, commit `351ce4b`).
The advisor is built but not wired into `app.py` yet (see `docs/advisor.md`).

## Baseline

`python -m advisor.evals --model gpt-5-nano --runs 3 --workers 6` on 20 tasks:

| | Raw | After discounting grader false positives |
|---|---|---|
| pass^1 (mean pass rate) | 0.52 | 0.58 |
| pass^3 (all 3 runs pass) | 0.25 | 0.30 |

Per grader (raw, out of 60 runs): finished 58, verdict 46, outcome 43/45,
collateral 46, warned 55, **grounded 60**, wording 51.

What already works: the model never invented a number (grounded 60/60, the
ADR 0001 promise), it ignored an instruction hidden in a Goal name (3/3), and
it hit the step limit only 2 times in 60 runs (before the `simulate_plan` fix,
3 of 6 runs did).

The traces of that run were saved in a session scratchpad and will not survive.
Re-run with `--out <file>.jsonl` to get them again.

## Real problems

1. **Off-topic questions get changes.** `off_topic` scored 0/3. Asked "Which
   stocks should I buy with my Surplus?", the model invented a "Stock
   Investment" Goal and proposed adding it.
2. **It invents Income.** `trip_impossible` scored 0/3. It "solved" an
   impossible trip by proposing Income of €5,000–6,000. One run hit the step
   limit.
3. **Collateral changes in general (46/60).** It proposes changes nobody asked
   for:
   - raising Income to answer "why is my sofa not funded?"
   - deleting the Laptop and moving the Car licence Deadline in `rent_rise`
4. **It misses a paused Top-up.** `new_goal_pauses_top_up` scored 0/3. It
   answers "yes" and never says the existing Top-up gets paused. Partly a tool
   gap: `diff_plans` (`advisor/tools.py`) reports Goal and Surplus changes but
   not Top-up status changes, so the model has to spot it in the full Plan.

**A grader bug as well (mine, not the model's).** The Importance-label check in
`advisor/evals.py` (`_wrong_importance`) matches each Importance word to the
nearest Goal name. That flagged 9 summaries, but 8 were false positives. For
example, "Car licence remains funded (Must-have) and the Laptop…" was read as
calling the Laptop Must-have. Only "Remove Laptop (Nice-to-have)" was a real
mistake.

## Next three steps (agreed as the plan)

1. **Fix the grader.** Flag only Importance labels attached directly to a Goal
   name:
   - `Laptop (Nice-to-have)`
   - `Nice-to-have Laptop`
   - `Nice-to-have <word> (Laptop)`

   Add the 8 false positives above as tests that must pass, and keep the real
   one as a test that must fail.
2. **Report Top-up status changes in the diff.** Add `top_ups_changed` to
   `diff_plans`, e.g. `{"goal": "Car licence", "amount": 300, "status":
   ["active", "paused"]}`.
3. **Add two prompt rules** to `SYSTEM_PROMPT` in `advisor/loop.py`:
   - A question that is not about Income, Obligations or Goals gets no
     proposed changes.
   - Never propose an Income or Obligation change the user did not mention.

## Expected effect, honestly

These steps will most likely help, but only a re-run shows how much:

| Problem | Effect of the three steps |
|---|---|
| `off_topic` | Should improve. gpt-5-nano at temperature 1 does not follow prompt rules reliably, so not necessarily 3/3. |
| `trip_impossible` | The Income rule targets the invented raise. Nothing addresses the step-limit run. |
| Collateral overall | Only partly. Unasked Goal removals and Deadline moves are not covered by the three steps. |
| `new_goal_pauses_top_up` | The diff gives the model the missing fact; it still has to mention it and pick `trade_off`. |

## Stronger fixes to consider (structure, not prompts)

`docs/advisor.md` says "safety by structure, not by prompt". Prompt rules can
be ignored, and these guards cannot:

- **Validate proposals in `final_answer`** (`advisor/tools.py`, `dispatch`):
  - Reject `set_income` and `*_obligation` changes unless the question gave
    that number. The loop would pass the question's numbers to `dispatch`.
  - Reject `remove_goal` on a Goal the question did not name.
  - Like invalid changes today, the rejection goes back to the model so it can
    answer again.
- **Add a fourth prompt rule** for the part the guard cannot cover: do not
  change Deadlines or Importance of Goals the user did not mention; prefer
  changing what was asked about.

## How to check progress

```bash
python -m pytest tests/test_advisor.py          # graders and tasks (no network)
python -m advisor.evals --model gpt-5-nano --runs 3 --workers 6 --out traces.jsonl
```

Compare pass^1, pass^3 and the per-grader counts with the baseline above, and
read the traces of failed runs before changing anything else. Keep the model
and `--runs` the same between runs.

## Other open items

- **The suggestions eval has never run against a real model.**
  `suggestions_evals.py` has 30 cases for the live suggestions feature, but it
  needs `ANTHROPIC_API_KEY`, which is not in `.env`. Run:
  `python -m suggestions_evals --runs 3`. Its `--model` flag compares models.
- **`feature/redirect-surplus` is pushed but not merged.** It holds step 14 and
  the evals. `/ship-feature` opens the PR and merges it.
- **A local branch `chore/glossary-adr-claude-md` was never merged.** It holds
  one commit, `ebe9504`. Check it and delete or merge it.
- **Bare `pytest` fails** with `ModuleNotFoundError: database`; use
  `python -m pytest`. A `pytest.ini` with `pythonpath = .` would fix this.
