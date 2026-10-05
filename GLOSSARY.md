# Spendly

A personal expense tracker. Beyond recording spending, it helps a user plan how a stable income is split across what they need to pay for and save towards.

## Language

**Expense**:
A record of money the user has already spent.
_Avoid_: Transaction, payment

**Income**:
The user's stable amount of money received per pay period, stated by the user rather than derived from their records.
_Avoid_: Salary, earnings

**Obligation**:
A recurring essential cost, such as rent or groceries, that is funded before any Goal and has no deadline.
_Avoid_: Bill, essential, fixed expense

**Goal**:
A one-off thing the user wants to pay for by a chosen deadline, with a target amount.
_Avoid_: Wishlist item, target, saving

**Importance**:
The user-confirmed ranking of a Goal (Must-have, Should-have, Nice-to-have) that decides which Goals are funded first.
_Avoid_: Priority, urgency

**Dependency**:
A link stating that one Goal is useless until another is fully funded, such as a car depending on its licence.
_Avoid_: Prerequisite, blocker

**Deadline**:
The date by which a Goal must be fully funded. Every Goal has one.
_Avoid_: Timeline, due date

**Saved Amount**:
The amount the user says they have already set aside for a Goal, entered and edited by hand.
_Avoid_: Progress, balance, saved so far

**Installment**:
The even amount set aside from each pay period for a Goal, the minimum needed to fund it by its Deadline.
_Avoid_: Payment, contribution

**Surplus**:
Income left after every Obligation and Installment is funded, shown as its own line for the user to redirect.
_Avoid_: Leftover, extra, free money

**Unreachable Goal**:
A Goal the Plan cannot fund by its Deadline, shown with a strong warning and the earliest date it could be met.
_Avoid_: Failed goal, overdue goal

**Plan**:
A hypothetical split of Income across Obligations and Goals. It does not alter recorded Expenses.
_Avoid_: Budget, allocation
