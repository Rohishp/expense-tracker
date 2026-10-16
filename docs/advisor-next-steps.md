# Advisor agent: results and next steps

Hand-off for the next session. Written 2026-10-16, after the fixes on
`feature/advisor-eval-fixes`. The advisor is built but not wired into
`app.py` yet (see `docs/advisor.md`).

## What this branch changed

1. **Grader:** `_wrong_importance` (`advisor/evals.py`) only checks an
   Importance word attached to a Goal name: `Laptop (Nice-to-have)`,
   `Nice-to-have Laptop`, `Nice-to-have concern (Laptop)`. Nine summaries the
   old grader flagged are tests that must pass.
2. **Diff:** `diff_plans` reports `top_ups_changed`, e.g. `{"goal": "Car
   licence", "amount": 300.0, "status": ["active", "paused"]}`.
3. **Guard:** `final_answer` rejects Income and Obligation amounts the
   question did not give, and removals of a Goal or Obligation it did not
   name (`check_scope` in `advisor/tools.py`).
4. **Prompt** (`advisor/loop.py`): no changes for off-topic questions; no
   Income or Obligation change the user did not mention; only change Goals
   the user mentioned; say which Top-ups a change pauses. `trade_off` now
   covers a paused Top-up.
5. `pytest.ini`, so bare `pytest` works.

## Results

`python -m advisor.evals --model gpt-5-nano --runs 3 --workers 6`, 20 tasks:

| | Baseline (raw) | Baseline (grader false positives removed) | This branch |
|---|---|---|---|
| pass^1 | 0.52 | 0.58 | **0.72** |
| pass^3 | 0.25 | 0.30 | **0.50** |

| Grader (of 60) | finished | verdict | outcome | collateral | warned | grounded | wording |
|---|---|---|---|---|---|---|---|
| Baseline | 58 | 46 | 43/45 | 46 | 55 | 60 | 51 |
| This branch | 58 | 49 | 42/45 | 56 | 59 | 60 | 58 |

The four problems from the baseline:

| Task | Before | After |
|---|---|---|
| `off_topic` | 0/3 | 0/3 |
| `trip_impossible` | 0/3 | 2/3, no invented Income |
| collateral overall | 46/60 | 56/60 |
| `new_goal_pauses_top_up` | 0/3 | 1/3 |

The guard rejected one proposal (in `why_no_sofa`); the model answered again
and passed. No final answer proposed Income.

Read these numbers with care. Each task runs 3 times at temperature 1, so a
move of 1/3 on one task can be noise. All changes landed together, so the
gain cannot be split by change. Traces:
`evals/results/advisor/2026-10-16_184250_gpt-5-nano_3d82fc6-dirty.jsonl`.

## Still failing: 17 of 60 runs, read from the traces

1. **"yes" when something else pays (7 runs).** In `licence_sooner`,
   `car_sooner` ×2, `tight_weekend`, `phone_sooner` and
   `new_goal_pauses_top_up` ×2, the model answers "yes" while another Goal
   becomes Unreachable, a Top-up is paused or the target is cut. Most of these
   summaries name the cost correctly; only the verdict is wrong.
2. **Ignored fields look like changes (`drop_laptop` 0/3).** For "stop
   saving for the laptop", the model sends `update_goal` with `amount: 0`.
   `apply_changes` ignores fields an op does not use, so this validates and
   changes nothing. Other runs also sent `amount`, `installment` or `status`
   on Goals. The baseline doc did not list `drop_laptop` as a problem. The
   baseline traces are gone, so we cannot tell whether the new "only change
   what was asked" rule made it worse.
3. **Off-topic questions still get Goals (`off_topic` 0/3).** The model
   ignores the new rule and adds a "Stock investments" Goal. The guard cannot
   catch this: the question says "stocks", so a name check would pass.
4. **Step limit (2 runs).** In `trip_impossible` and `camera_too_soon`, the
   model keeps editing "Goal 3", which it added in an earlier call. In
   `final_answer` the "No Goal with id 3" error talks about `simulate_plan`,
   so it does not tell the model how to fix the answer.
5. **Unasked Deadline move (1 run).** In `rent_rise`, the model moved the Car
   licence Deadline to 2028. The user named the Car licence, so the prompt
   rule allows the move, but the question only asked whether it is on track.
6. **Grader false positive (1 run).** In `tight_weekend`, the summary called
   one simulated option "Weekend away (Nice-to-have)" and proposed the other
   one, a Should-have. The grader only knows the proposed Importance.

## Candidate next steps (not started)

Structure before prompts, in order of runs affected:

- **Check "yes" in `final_answer`.** Run the Plan before and after the
  proposal. If a funded Goal becomes Unreachable or a Top-up is paused,
  reject "yes" and ask for `trade_off`. Covers 6 of the 7 runs in item 1
  (the cut target needs the question's amount).
- **Reject unknown fields per op in `apply_changes`** (e.g. `amount` on
  `update_goal`). Covers item 2 and the noise in `off_topic`.
- **Better `final_answer` error for a Goal added in the same answer:** "send
  its add_goal with the final values instead". Covers item 4.
- **Grader:** also allow Importance levels the model simulated (item 6).
- **`off_topic`:** no structural check found yet. Options are to accept it,
  to classify the question first, or to accept that a savings Goal for
  investing is fine.

## How to check progress

```bash
pytest tests/test_advisor.py    # graders, guard and tasks (no network)
python -m advisor.evals --model gpt-5-nano --runs 3 --workers 6 --note "what changed"
```

Compare pass^1, pass^3 and the per-grader counts with `evals/results/runs.csv`, and
read the traces of failed runs before changing anything else. Keep the model
and `--runs` the same between runs.

## Other open items

- **The suggestions eval has never run against a real model.**
  `suggestions_evals.py` has 30 cases for the live suggestions feature. It
  needs `ANTHROPIC_API_KEY`. Run: `python -m suggestions_evals --runs 3`. Its
  `--model` flag compares models.
- **`feature/redirect-surplus` is pushed but not merged.** It holds step 14
  and the evals, and this branch is built on it. `/ship-feature` opens the PR
  and merges it.
- **A local branch `chore/glossary-adr-claude-md` was never merged.** It holds
  one commit, `ebe9504`. Check it and delete or merge it.
