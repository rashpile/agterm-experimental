---
companions:
  brainstorm: {source: 'plugin:brainstorm@umputun-cc-thingz', version: 2.2.2+c35eda1c9910}
  planning: {source: 'plugin:planning@umputun-cc-thingz', version: 3.8.4+dd01fa7f1815}
  revmux: {source: 'plugin:revmux@revmux:.claude-plugin/skills/revmux', version: 0.5.5+a85cbb8d57e0}
---

## Preferences

## Changes

### brainstorm

- `skills/brainstorm/SKILL.md` Phase 1 step 2: every question goes to the orchestrator's question handling (an assumption or one of make-task's questions), never straight to AskUserQuestion. make-task decides how and whether the user is asked.
- `skills/brainstorm/SKILL.md` Phase 2, new step 4: the approach is asked only when the choice needs the user's knowledge or taste, else the recommended one is taken and recorded as an assumption. Most approach picks are a recommendation a reasonable engineer would accept, which make-task never asks about.
- `skills/brainstorm/SKILL.md` Phase 3 steps 1-2 and the closing line: the whole design is written once, with no section-by-section validation. make-task writes it to its design file in one pass; the `interactive` autonomy adds the validation back from make-task's side.
- `skills/brainstorm/SKILL.md` Phase 4 first line: no next-step menu, the question below it is not asked. make-task's next phase is always the plan.
- `skills/brainstorm/SKILL.md` Key Principles "Incremental validation" became "Whole design at once". It contradicted the Phase 3 change.
- `skills/brainstorm/SKILL.md` Key Principles "Lead with recommendation": "let user decide" became "ask only when it needs their knowledge or taste, else record an assumption". Same rule as Phase 1 and 2.
- `skills/brainstorm/SKILL.md` Key Principles "Duplication vs abstraction": decided and recorded as an assumption unless it needs the user's knowledge or taste, instead of always asking. Same rule as Phase 1 and 2.

### planning

- `commands/make.md` step 0 CRITICAL line: missing context comes from the design file in step 1, not from asking the user. Step 1 asks nothing under make-task.
- `commands/make.md` step 1, new first paragraph: no questions; purpose, scope, constraints and title come from the design file, and the testing approach is decided (Regular unless the design or project rules call for TDD) and recorded as an assumption. make-task asks the user only during brainstorm.
- `commands/make.md` step 1.5, new first line: skipped. Brainstorm already chose the approach.
- `commands/make.md` step 3 menu line: no menu, the plan path goes back to the orchestrator. make-task runs the plan review and execution itself and decides when the plan is committed.
- `commands/make.md` key principles "one question at a time" became "no questions". It contradicted the step 1 change.
- `commands/make.md` key principles "lead with recommendation": "let user decide" became "decide and record the choice as an assumption". Same rule as step 1.
- `commands/make.md` key principles "explore alternatives" became "approach from the design". Brainstorm explored them; same rule as step 1.5.
- `commands/make.md` key principles "duplication vs abstraction": decided and recorded as an assumption instead of asking. Same rule as step 1.

### revmux

- `SKILL.md` non-negotiable 0, new last lines: under make-task the answer the user gives is make-task's own question, not this skill's; no round ends on a `present.md` turn and the leftovers question replaces the loop's closing question. make-task asks the user only through its own questions, and Step 6 no longer ends a round on a question.
- `SKILL.md` Step 0, new first paragraph: preflight already ran, bare, when the orchestrator started; it runs once more before round 1 with round 1's profile. make-task checks revmux at its start so a missing tool is settled during brainstorm, and the profile run catches a roster binary the bare check does not cover.
- `SKILL.md` Step 0.5, new paragraph: never create or offer the project profile; an empty `paths.profile_fallback` is recorded as an assumption (generic calibration), and no round-local `input/profile.md` is written. make-task asks nothing during review, and a profile written mid-run would set the review standard of the user's repository unasked.
- `SKILL.md` Step 1, new first paragraph: the scope is fixed to `git diff <review_base>..HEAD` with every exclusion earlier rounds carried, never asked; `review_base` comes from the orchestrator. The review covers the whole task's work, not a range picked from the table.
- `SKILL.md` Step 3, new first paragraph: the profile is chosen without asking, round 1 by the Step 3 table and later rounds by `references/loop.md`; the questions in Step 3 and Step 7 are not asked. make-task asks nothing during review.
- `SKILL.md` Step 4, new first paragraph: headless only, never the overlay, and no question; the orchestrator launches and waits with its own commands. make-task's dashboard is the progress view and its wait blocks until the round ends.
- `SKILL.md` Step 6, new first paragraph: no question and no `present.md` turn; each round is reported in one plain line with the combined tags. make-task authorized the loop, and its leftovers question is the one question after it.
- `references/loop.md` "Before starting", new bullet: the rounds review the diff from `review_base`, not from the starting commit. Same rule as SKILL.md Step 1.
- `references/loop.md` "The iteration" step 4, new last sentence: the cumulative diff starts at `review_base`, not at the starting commit. Same rule as SKILL.md Step 1.
- `references/loop.md` "Stopping", new paragraph before the closing question: it is not asked, make-task's leftovers question replaces it at whichever stop leaves something open. make-task asks the user only through its own questions.
- `references/loop.md` "Stopping", new paragraph after the closing question: when the leftovers question's answer is to fix, one confirming round under `final` follows the fix, deliberately overriding "no review round runs after that question"; its report is final. A fix nobody reviewed would end the run on an unreviewed tree.
- `references/loop.md` "Stopping", new last paragraph: archive housekeeping is skipped, and the final report says so. Cleaning the user's archive is a question make-task does not ask.

## Wanted
