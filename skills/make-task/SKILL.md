---
name: make-task
description: Run one development task end to end in this session — brainstorm, plan, automatic plan review, task-by-task execution in subagents, a revmux review-fix loop, leftovers question, finalize and stats — through a profile that holds a copy of each phase's companion skill and sets how autonomous the run is (by default it asks the user only during brainstorm), with a live agterm dashboard; every question is an AskUserQuestion in the terminal. `--profile=<name>` picks the profile, `--update` checks its copies against their sources. Triggers on "make-task", "/make-task", "make a task", "run the full dev workflow", "brainstorm, plan, execute and review", "take this from idea to reviewed branch".
allowed-tools: Agent, AskUserQuestion, EnterWorktree, TaskCreate, TaskUpdate, Bash, Read, Write, Edit, Glob, Grep
---

# make-task

One orchestrator (this session) runs a task through five phases: **brainstorm → plan → exec → review →
finalize**. A **profile** says which companion skill runs each phase and how autonomous the run is, and
holds its own copies of those skills (the `default` profile: brainstorm, planning and revmux, with
make-task's changes already in them). For brainstorm, plan and review the orchestrator reads the
companion's entry file and follows it (Running a companion). The task loop and finalize/stats/completion
are this skill's own playbook on the exec companion's scripts and prompts. The exec companion's own skill
text (planning's `skills/exec/SKILL.md`) is never followed: it runs its own review and asks its own
worktree question.

Invocation: `/make-task [--profile=<name>] [--update] [task text]`. Without `--profile` the profile is
`default`. Profiles live in `<config root>/profiles/<name>/` (`~/.config/make-task` unless
`MT_CONFIG_HOME` or `XDG_CONFIG_HOME` says otherwise; `mt profile resolve` prints the directory).

Paths below are relative to this skill's directory (the one containing this SKILL.md) — resolve them against it, not the current working directory.

## How to run `mt`

`scripts/mt` holds the run state, the checkpoint journal, the dashboard, the viewer's data and the review
waits.
It is the only thing that calls `agtermctl`. In this file **`mt` stands for**

```bash
uv run -q --script <skill dir>/scripts/mt
```

with `<skill dir>` the absolute "Base directory for this skill" the harness announced: the path the
resolution line above resolves `scripts/mt` against. Write the absolute path into every call: the calls
run from the project's directory, and after an auto-compact the absolute paths in earlier calls are what
still say where the skill lives.

- **Global `--run DIR` goes before the command**: `mt --run <state_dir> status`. After `review start`
  the word `--run` means the revmux run name: `mt review start --round 1 --run 01-initial --round-dir D`.
- Without `--run`, `mt` finds the run through the pointer `current-<AGTERM_SESSION_ID or default>`
  that `mt init` wrote. Inside agterm that is enough. Outside agterm see "Outside agterm".
- `mt --help` and `mt <command> --help` (also `mt review wait --help`) show the exact flags.
- Exit codes: `0` ok, `1` failure with the message on stderr.
- **Workflow-state commands** (`init`, `set`, `phase`, `activity`, `checkpoint`, `dirty`, `plan`, `refresh`,
  `review …`, `fail`, `done`) exiting non-zero → stop the run and report it. `set` refuses an empty
  value (a failed `$(…)`) for every key but `stash_ref` and `worktree_path`, so a failed substitution stops
  the run instead of clearing the key. `reviewed_sha` is written once: `set` refuses a second, different
  value. Never continue without the journal. UI problems (dashboard, artifact pages, sidebar status) only print `mt: warning:` and exit 0.
- Run `mt review wait` with the Bash tool's `timeout: 600000`. It returns before 600 s.
- `mt revmux aside` and `mt revmux put-back` (Finalize step 2, Completion step 3) take `--state-dir` and
  `--toplevel`, read no run state and print one `revmux_tree=<outcome>` line; their outcomes and exit codes
  are described where they run. They are not workflow-state commands.
- `mt profile` (`resolve`, `check` and `install-default` at Start step 0, the rest in
  `references/profiles.md`) runs before `mt init` and never takes `--run`: it takes `--profile <name>`
  (or `--dir DIR` for a template), prints one JSON line and exits 1 with a message on failure.
  `mt profile check` also exits 1 when the profile has errors, and still prints its line (`ok`, `errors`,
  `warnings`, `updates`). It is not a workflow-state command.

Names in angle brackets are values. Most are `mt init`/`mt status` fields by their key (`<toplevel>`,
`<main_checkout>`, `<state_dir>`, `<default_branch>`, …). The profile's values are flat keys too:
absolute paths, null for a phase the profile skips (`skip`). The commands below use these profile paths
unquoted: a profile path with a space or a shell metacharacter fails `mt profile install-default`,
`mt profile check` and `mt init`, with a message that says to move it (`MT_CONFIG_HOME`), or to rename it
when the part below `companions/<id>/` holds it (a `phases` path in `profile.md`).

| key | value |
|---|---|
| `<profile_name>`, `<profile_dir>` | the profile and its directory (`profile.md`, `companions/`, `data/`) |
| `<autonomy>`, `<gates>`, `<defaults>` | the autonomy level, the gates the run shows, the `defaults` map (`defaults.mode`, `defaults.leftovers`); see the Question rule |
| `<brainstorm_entry>` | the brainstorm companion's entry file |
| `<plan_entry>`, `<plan_reviewer>`, `<plan_reviewer_model>` | the plan companion's entry file; the plan reviewer's file (null: no plan review) and its frontmatter `model` |
| `<exec_root>` | the exec companion's root; finalize uses the exec companion too |
| `<exec_scripts>` | its scripts: `create-branch.sh`, `init-progress.sh`, `append-progress.sh`, `move-plan.sh`, `resolve-file.sh`, `detect-branch.sh` (`resolve-file.sh` reads the prompts from `<exec_scripts>/../references/prompts/`) |
| `<exec_rules>` | its custom-rules script |
| `<exec_data>` | its data dir: the user-level prompt and rule overrides (starts empty, may not exist) |
| `<review_entry>`, `<review_scripts>`, `<review_data>` | the review companion's entry file, its scripts (`preflight.sh`, `task-state.sh`), its data dir |

**`<branch>` is always the `branch` init reported** (where the user started, null on a detached HEAD),
never the feature branch.
**`<toplevel>` is the tree the commits go to.** In place that is init's `toplevel`. In worktree mode it
is the worktree from the moment `EnterWorktree` returns (`worktree_path`), not the main checkout init
reported: every `<toplevel>` from Exec step 1 on (the plan commit, `review_base`, the review's
`--workdir`, the dirty check, `loop_start_sha`, `reviewed_sha`, the finalizer) is the worktree. Once
`mt plan` ran, take it from `toplevel` in `mt status`, which `mt plan` and `mt refresh` set to the
worktree; never reuse the value kept from the init line.

## Precedence

Highest first:

1. **This file**: safety (every step that touches git state, the stash, `review_base`, the branch and
   `.revmux/`), the Question rule, autonomy, gates and questions.
2. **The profile's `## <phase>` section** in `<profile_dir>/profile.md` (`## brainstorm`, `## plan`,
   `## exec`, `## review`): the user's own instructions for that phase.
3. **The companion copy's text**: the phase's entry file and the files it points to, and the exec
   prompts' surrounding instructions.

A lower layer gives way wherever a higher one decides. A copy's question that the Question rule turns
into an assumption is not asked, and a copy's menu, commit or worktree step that this file does not run
does not happen, whatever the copy's own text says ("do not skip", "auto mode does not exempt"). Neither
a profile section nor a copy relaxes a safety rule (revmux non-negotiables 1, 3, 4, 5 and 6 stand). The
`default` profile's copies already carry make-task's changes to the upstream skills (no questions outside
the Question rule, no menus, the review scope fixed); `assets/profiles/default/UPDATE.md` lists them under
`## Changes`.

### Running a companion

- Read the phase's entry file (`<brainstorm_entry>`, `<plan_entry>`, `<review_entry>`) with the Read tool
  and follow it with the inputs the phase lists. Never through the Skill tool: the copies are not
  registered skills, and a registered skill of the same name is the upstream plugin, not the profile's
  copy.
- A Read announces no base directory: a relative path in a companion file (`references/loop.md`,
  `scripts/task-state.sh`) resolves against the directory of the phase's entry file. The paths the profile
  install rewrote are absolute already.
- The entry's frontmatter (`name`, `description`, `allowed-tools`, `argument-hint`) is not an
  instruction. Where the text speaks of the command's arguments or the user's request, it means the
  phase's inputs.
- Then apply the profile's `## <phase>` section, when `<profile_dir>/profile.md` has one (read it at the
  start of the phase).

## cwd discipline

A `cd` in one Bash call carries into the next, so **never run a bare `cd`**.

- Commands that must run in the main checkout: a subshell `(cd <main_checkout> && …)` or `git -C <main_checkout> …`.
- The session's cwd changes only through `EnterWorktree`.
- In worktree mode `mt refresh` (and `mt plan`, `mt artifact changes`) compare cwd's toplevel with the
  stored `worktree_path` and fail when they differ. That failure means the session left its tree: stop.

## Recovery

When unsure where the run is (after an auto-compact, a crash, a long wait), run `mt status` and re-read
this file's section for the printed `phase` **before doing anything with side effects**. `mt status`
prints `run`, `phase`, `phase_status`, `activity`, `error`, `warnings`, the stored `values` (`mode` is the
worktree answer), the checkpoints still `begun` and the `ended` ones with their result, every review round
(`rounds`: run, round_dir, report, head, verdict, started, finished, confirming), the unresolved and deferred
counts, and every assumption and decision so far (a gate's or question's answer among the decisions). The
profile keys (`<profile_dir>`, the entries, `<exec_scripts>`, `<autonomy>`, `<gates>`, `<defaults>`, …) are
in `values` too: take them from there and never resolve the profile again mid-run. A `warnings` line
saying the run was started before make-task profiles (its state has no profile keys) → stop: tell the user
to finish it with the make-task version that started it, or to start a new run. Reconcile each `begun`
checkpoint by the recipe of its phase:

| checkpoint | reconcile |
|---|---|
| `exec.task.<n>` | Exec step 5 |
| `review.fix.<n>`, `review.fix.leftovers` | the fixer verifies each finding before it fixes it, so run it again with `mt checkpoint begin <key> --resume`, end the checkpoint, then go on (round `<n+1>`, or the confirming round) |
| `finalize` | "Finalize" in The end of the run |

A round in `rounds` with `started` and no `finished` (and no later round) → "Reconciling a round" in Review.
A `finished` round with a `<k> gating` verdict, no `review.fix.<n>` checkpoint and no `stop_reason` →
Each round step 8 of that round (its `review-round` gate again, unless it passed: Gates). Ended
checkpoints say how far the review got:

| ended, and nothing after it | go to |
|---|---|
| `review.fix.<n>`, no round `<n+1>` in `rounds` | round `<n+1>` (Each round, step 1) |
| `review.fix.leftovers`, no `confirming` round after it | the confirming round (Leftovers question); never Finalize first: `mt done` refuses `clean` when `reviewed_sha` is not the `head` of the last finished round |
| `finalize`, `verified_sha` unset or not `reviewed_sha` | Finalize step 3 |
| `finalize`, `verified_sha` equal to `reviewed_sha` | Finalize step 4, then Stats |

Other phases: brainstorm running with no `<state_dir>/design.md` → resume at Brainstorm step 2, building
on the assumptions and decisions recorded so far (under `interactive` also on `<state_dir>/design-draft.md`,
the design sections written so far); a question whose answer is recorded (`mode` set, the preflight
question's answer in `decisions`) is not asked again. Brainstorm `skipped` with no
`<state_dir>/design.md` → the questions whose answer is not recorded yet, then Brainstorm step 5. Brainstorm
with `<state_dir>/design.md` present → finish Brainstorm step 5 (`mt artifact design`, then the `design` gate
when `<gates>` has it and it has not passed), then go to Plan. Plan with a plan file in `docs/plans/`
and `plan_rel` unset → finish the plan review and the `plan` gate (unless it passed), then Exec step 1.
Exec step 1 half done → read
`git branch --show-current`, `git stash list`, `worktree_path` and `stash_ref` and do only the missing
part (in place, a current branch other than the `branch` init reported → the branch step ran: keep that
branch, never pick a second name; the `.revmux/` step is idempotent, run it again; `review_base` unset
and the plan committed → it is the commit before the plan commit; not committed yet → it is set at that step, after the branch exists; `stash_ref` unset and the `stash_paths` clean → look for this run's stash, never another
run's: `<run_id>` is the last path component of `run`):

```bash
r=refs/make-task/$(printf '%s' "<run_id>" | tr -c 'A-Za-z0-9_-' -)
s=$(git -C <toplevel> rev-parse -q --verify "$r") ||
  { s=$(git -C <toplevel> stash list --format='%H %gs' | M=': make-task <run_id>' awk '{ m = ENVIRON["M"] } length($0) > length(m) && substr($0, length($0) - length(m) + 1) == m {print $1; exit}')
    [ -z "$s" ] || git -C <toplevel> update-ref "$r" "$s"; }
echo "$s"
```

It reads the private ref the push recipe wrote, then this run's entry by its message (writing the
private ref when only the entry has it). It prints a sha → the push ran: `mt set stash_ref=<that sha>`.
It prints nothing → nothing was stashed. The entry stays in `git stash list` after the restore, so a
sha found here after `stash_restored` is set names a stash already back in the tree; the restore
recipe's `stash-applied` marker then keeps it from being applied twice.
`<state_dir>/revmux-tree/list` non-empty (the directory not yet renamed `revmux-tree.restored/`) → the
`.revmux/` edits are set aside (Finalize step 2) and only the copies under `<state_dir>/revmux-tree/files/`
hold them: never `git checkout -- .revmux` outside `mt revmux aside`, never delete or edit that directory;
Completion step 3 or a stop puts them back (`mt revmux put-back`).
`final_status` set → the run is over; only the final report is left. A stop that left the session
without `mt done` (a failing workflow-state command) still owes the stash: see "Every stop gives the
stash back" in Errors.

Checkpoint rules: `begin` on a key that is `begun` fails unless `--resume`; `begin` on an `ended` key
starts a new attempt; `end` on an `ended` key is a harmless no-op. Keys match `[A-Za-z0-9][A-Za-z0-9._-]*`.

## Start

0. **Profile.** No run exists yet, so this step's questions are plain `AskUserQuestion`s with no `mt`
   call before or after them (no dashboard, nothing recorded). Parse the invocation
   `/make-task [--profile=<name>] [--update] [task text]`: `<name>` is `default` when `--profile` is
   absent; what follows the flags is the task text.
   1. `mt profile resolve --profile <name>` prints `{"name", "dir", "status"}`; `dir` is `<profile_dir>`.
   2. `status` is `missing` and `<name>` is `default` → first use. Tell the user make-task is configuring
      itself for first use, run `mt profile install-default`, and ask one `AskUserQuestion`, the autonomy:
      `default` (recommended: during brainstorm it asks only what needs the user's knowledge or taste),
      `autonomous` (asks nothing, records every choice as an assumption, answers the worktree and
      leftovers questions from the profile's `defaults`) or `interactive` (asks more during brainstorm,
      validates the design section by section, and adds the `design`, `plan` and `review-round` gates).
      Write the answer into the `autonomy:` line of the frontmatter of `<profile_dir>/profile.md` with
      Edit, and tell the user that `/make-task --update` changes the profile later (the interview in
      `references/profiles.md`). When `~/.claude/plugins/data/planning-umputun-cc-thingz/` holds `prompts/`
      or `planning-rules.md`, or `~/.claude/plugins/data/brainstorm-umputun-cc-thingz/` holds
      `brainstorm-rules.md` (`ls` them), add one line: make-task no longer reads them, and
      `/make-task --update` offers to carry them into the profile.
   3. `status` is `missing` for another name, or `--update` is given → follow `references/profiles.md`:
      its new-profile setup and interview, or its update flow. `--update` without task text → stop after it,
      with no run.
   4. `mt profile check --profile <name>`. `ok` false (exit 1) → stop with its `errors` and how to fix
      them: `/make-task --update` (it takes the copies' updates and raises an older `schema`; its interview
      fixes the rest), or edit `<profile_dir>/profile.md` and the copies by hand, never the `schema:` line.
      No `mt init`, no run. `warnings` → tell the user, one line each.
      `updates` non-empty (it lists only the sources it can check without the network) → one line:
      "updates are available for <ids>; run `/make-task --update` to review them". Then go on.
1. `git rev-parse --is-inside-work-tree` — not a git repo → stop and tell the user make-task needs git.
2. `mt init --title "<short task title>" --profile-dir <profile_dir>`. Read its one JSON line and keep:
   `state_dir`, `main_checkout`, `toplevel` (the worktree replaces it in worktree mode, see the names
   above), `branch`, `default_branch`, `has_origin`, `linked_worktree`, `dirty_paths`, `agterm`,
   the profile keys (the table above), `revdiff`, `warnings`. Init runs step 0's check again
   (without the update lookups) and fails (exit 1) on a profile with errors or a template profile,
   creating no run: stop as the Errors row "profile check fails" says. Its `warnings` carry the check's
   warnings, each prefixed `profile <name>: ` (a review `requires` command missing on PATH among them),
   and one for a review phase the profile skips (step 3 handles both). Inside agterm it opens the
   dashboard over the other pane; when that fails it reports `agterm: false` and the run goes on without
   it. Questions do not depend on agterm: every one is an `AskUserQuestion` in the terminal (Questions).
3. **Review check.**
   - `review_entry` is null (the profile's `phases.review` is `skip`): the profile chose to run without
     review, so there is nothing to check and nothing to ask.
     `mt assume "no revmux review" --why "profile <profile_name> skips review"`, then go on: no preflight
     and no preflight question, at every autonomy level. The review phase is skipped (Review).
   - Otherwise the revmux preflight: `(cd <main_checkout> && bash <review_scripts>/preflight.sh)`.
     It fails, or init's `warnings` name a review `requires` command missing on PATH (that counts as a
     failed preflight: preflight.sh does not read `requires`) →
     `mt assume "no revmux review" --why "<what preflight or the warning said>"` and the preflight question
     during brainstorm (Errors row "revmux preflight fails at Start").
4. `TaskCreate` one entry per phase (Brainstorm, Plan, Execute, Review, Finalize) and move them with
   `TaskUpdate` as the phases change.

Phase changes go through `mt phase <id> [--activity A] [--note N] [--skipped]` (ids: `brainstorm`, `plan`,
`exec`, `review`, `finalize`). It finishes the running phase, clears an error and re-asserts the sidebar
status `active`. Smaller steps: `mt activity "<what is happening now>"`. Timeline lines: `mt event "<text>"`.

## Question rule

`<autonomy>` decides which questions reach the user. During brainstorm, sort every question:

1. **Trivial** → decide it and `mt assume "<what>" --why "<reason>"`.
2. **A recommendation a reasonable engineer would accept** → take it and `mt assume`.
3. **Needs the user's knowledge or taste** → ask it (Questions). One question at a time.

Each autonomy level is a preset. The classes it asks go to the user (Questions); every other class is
`mt assume`:

| `<autonomy>` | brainstorm questions asked | the design | gates (preset) | worktree question, leftovers question |
|---|---|---|---|---|
| `autonomous` | none: class 3 too becomes `mt assume` with the reason | written once | none | not asked: answered from `defaults` |
| `default` | class 3 | written once | none | asked |
| `interactive` | classes 2 and 3 (class 2 with the recommendation marked) | validated section by section (Brainstorm step 2) | `design`, `plan`, `review-round` | asked |

`<gates>` is the list this run shows: the profile's `gates` when its `profile.md` sets them, else the
preset's. Under `autonomous` the two questions are answered from `<defaults>` and each answer is recorded
with `mt assume`: the worktree question from `defaults.mode` (`worktree` or `inplace`; it never overrides
the linked-worktree rule, which always runs in place), the leftovers question from `defaults.leftovers`
(`fix` or `leave`).

After brainstorm every question becomes `mt assume` with the reason, at every level. The only later
questions are the error questions (Errors), which are asked at every level, the gates in `<gates>` and the
leftovers question.

### Questions

Every question the run asks (a brainstorm question, the preflight question, the worktree question, a gate,
an error question, the leftovers question) is one `AskUserQuestion`, one at a time, with the options listed
where it is asked:

- **Text.** The question carries a short context: what is at stake and the recommendation (the worktree
  question lists the dirty paths). Long material is named by its artifact, registered before the question
  so the dashboard opens it: `mt artifact design <state_dir>/design.md` before the `design` gate (and, under
  `interactive`, `mt artifact design <state_dir>/design-draft.md` with the design written so far before
  each design-section question), `mt artifact plan <absolute plan path>` before the `plan` gate, and the
  Review view, which `mt review start` registers, for the `review-round` gate and the leftovers question.
- **Options.** 2–4 options. An option's pro and con become its description. The recommended option comes
  first and its label ends in "(Recommended)". There is no delegate option: the user picks an option or
  types an answer in "Other".
- **Before and after.** Before it, `mt activity "Waiting for your answer · <topic>" --wait`, except before
  an error question (`mt fail` already shows the error). After it, `mt decide "<question>" "<answer>"`,
  then a normal `mt activity` (after an error question, the Errors row's next step).
- **Gates.** A gate's question is its exact title, and `mt decide` records `Approve`, `Revise` or the
  typed text, so "a gate has passed" (Gates) reads the decisions as it always did.
- **"Other".** Typed text answers a brainstorm question, a gate (the change wanted) and the leftovers
  question. For every other question, an "Other" answer that maps to no option asks the same question
  again.
- **One option.** An error question with one option (`stop`, `keep`) is not asked (`AskUserQuestion`
  needs two): the run takes that option and says why in its report.

### Gates

A gate is a question the run asks at a fixed point when `<gates>` names it, at any autonomy level. Its
title is fixed: `Gate: design`, `Gate: plan`, `Gate: review-round <n>` (round `<n>`). Its options are
"Approve (Recommended)" and "Revise"; typed text is the change the user wants. "Revise" without text → ask
the same gate again, its "Revise" description now asking for the change as typed text. "Approve" → go on.
Each answer is recorded with `mt decide "<the gate's title>" "<Approve, Revise or the text>"`. A gate
**has passed** when the last entry in `decisions` with its title is `Approve`, or `assumptions` hold
`<its title> → Approve` (a delegated answer, which older make-task versions recorded); Recovery shows a
gate that has not passed again.

| gate | when | the question names | a revision |
|---|---|---|---|
| `design` | Brainstorm step 5: after `design.md` is written and `mt artifact design` registered it | the Design view | change `design.md` per the text, `mt artifact design <state_dir>/design.md` again, then ask the gate again |
| `plan` | Plan step 4: after the plan reviewer's findings are applied and `mt artifact plan` registered the plan, before Exec | the plan's path and goal, the Plan view | change the plan per the text (still before Exec, so still the orchestrator's only plan-editing window), one `mt assume "<change made>" --why "plan gate: <text>"` per change, `mt artifact plan <absolute plan path>` again, then ask the gate again |
| `review-round` | Each round step 8: after the round's one-line report, before its fixer | how many gating findings the fixer would get, the Review view | the text narrows or changes which findings the fixer gets (`FINDINGS_LIST`); a gating finding it takes away is recorded for the round, and one it rules out is excluded from every later round (Each round step 8); then the fixer runs, with no second gate |

## Subagent prompts

Every subagent is an Agent call with `subagent_type: "general-purpose"` and `mode: "bypassPermissions"`,
except the plan reviewer (Plan step 3: `Plan`, without `bypassPermissions`). One subagent at a time.

### Resolving and substituting

1. Resolve the template from the main checkout (project and user overrides apply):
   `(cd <main_checkout> && bash <exec_scripts>/resolve-file.sh prompts/<name>.md <exec_data>)`
   for `task`, `fixer`, `finalizer` and `stats`. Custom rules:
   `(cd <main_checkout> && bash <exec_rules> planning-rules.md <exec_data>)`.
   `resolve-file.sh` takes the project's `.claude/exec-plan/prompts/<name>.md`, else
   `<exec_data>/prompts/<name>.md`, else the exec companion's own prompt; `<exec_data>` starts empty, so
   a run uses the profile copy's prompts unless the project overrides them.
   The resolved file is a wrapper (a heading and a "Use this … replace …" line) around the prompt in a
   fence. Keep only the prompt: the lines between the first line that is exactly ```` ``` ```` and the
   last line that is exactly ```` ``` ```` (stats has fences inside its prompt; the outer pair is the first
   and the last). The wrapper names `USER_RULES` and `FINDINGS_LIST` itself and must not be passed on.
2. Append the addenda: task → the profile's `## exec` section (when `<profile_dir>/profile.md` has one),
   then harness; fixer → harness + fixer; finalizer → harness, plus no-origin when `has_origin` is false.
   Stats gets none (it is read-only). The harness comes after the profile section, so its rules win.
3. Substitute every structural placeholder:

   | token | value |
   |---|---|
   | `PLAN_FILE_PATH` | the plan's absolute path in the current toplevel (`plan_path` from `mt status` once `mt plan` ran) |
   | `PROGRESS_FILE_PATH` | the progress file (`progress_path`) |
   | `DEFAULT_BRANCH` | `default_branch` |
   | `${CLAUDE_PLUGIN_ROOT}` | `exec_root` (the profile's copies have it rewritten already; a project override in `.claude/exec-plan/prompts/` may still carry it) |
   | `RESOLVE_SCRIPT` | `<exec_scripts>/resolve-file.sh` |
   | `PLUGIN_DATA_DIR` | `exec_data` |

4. Write the result to `<state_dir>/prompts/<name>.md` and check it:
   `grep -nE 'PLAN_FILE_PATH|PROGRESS_FILE_PATH|DEFAULT_BRANCH|RESOLVE_SCRIPT|PLUGIN_DATA_DIR|REVIEW_PHASE|DIFF_COMMAND|\$\{CLAUDE_PLUGIN_(ROOT|DATA)\}|<exec_scripts>|<review_base>|<pre-existing paths>' <state_dir>/prompts/<name>.md`
   must print nothing (the last three are the harness addendum's fill-ins: an unfilled `<exec_scripts>`
   would make every `[decision]` line a failed redirection). A hit →
   `mt fail "unsubstituted placeholder in <name>.md"` and stop (Errors).
5. Only then insert the free-text values, so a finding or a rule that quotes a token cannot trip the
   check: `USER_RULES` = `ADDITIONAL CUSTOM RULES:` + newline + the rules output (empty string when there are
   none); `FINDINGS_LIST` = the findings (fixer only). Pass that final text as the prompt.

### Harness addendum (task, fixer, finalizer)

Append verbatim, then fill `<review_base>` with the stored `review_base`, `<exec_scripts>` with the
profile's `exec_scripts`, and `<pre-existing paths>` with `stash_paths` in place (the user's edits stashed
at Exec step 1), or `none` in worktree mode (those edits stay in the main checkout and are not in the
worktree) and when nothing was stashed:

```text
MAKE-TASK HARNESS RULES (they override anything above that conflicts):
- Never move, rename or delete the plan file PLAN_FILE_PATH. If a checkbox says to move it to a
  completed/ directory, mark it [x] and move nothing: the orchestrator moves the plan at the very end.
- No human is available. Never ask the user a question (no AskUserQuestion, no pausing for input).
  Decide judgment calls the plan does not settle from the project's CLAUDE.md, lint rules and code
  conventions.
- Log every judgment call as one line in the progress file that starts with [decision] or [deviation]
  followed by the reason, using only the append script:
  printf '%s\n' '[decision] <what> - <why>' | bash <exec_scripts>/append-progress.sh PROGRESS_FILE_PATH
- Stage and commit only the paths you changed for this work, named with an explicit pathspec on both
  commands, as file names (--literal-pathspecs: no globs):
  git --literal-pathspecs add -- <path> ... && git --literal-pathspecs commit -m "<message>" -- <path> ...
  Do not use stage-and-commit.sh: it ends in a bare git commit, which commits everything staged, not
  only your paths. Never git add -A, git add ., git commit -a, git stash or git checkout -- on
  paths you did not change. Never stage anything under .revmux/.
- The user's own uncommitted edits to these paths are stashed and come back at the end: <pre-existing paths>.
  Never git stash, git stash pop/apply/drop or git checkout them; edit and commit one only when the plan
  needs that file changed.
- This task's commits are the ones after <review_base>. Never squash, reword, reorder or drop
  <review_base> or any commit before it.
- Never push.
```

### Fixer addendum

```text
MAKE-TASK FIXER RULES - before committing each fix, do these three checks in this order:
1. Sweep for the shape, not the site. Name the defect the finding demonstrates as a construct (for
   example "a switch over a three-value enum that only tests one end"), grep the repo for that construct
   - the pattern, not the literal string - and fix every occurrence in the same commit.
2. Enumerate what the fix touched: every value of an enum or set of string constants you branched on and
   every transition between them where direction matters; every field of a struct you folded, copied or
   serialized; every platform, filesystem or executor the code branches on; every error class (absent,
   unreadable, malformed, present-but-empty); both sides of a ratio drawn from one population. A test
   must tell the cases apart, not only pin the case that prompted the fix.
3. Re-read the finding, then read your fix against it: fix the mechanism the finding names, not the
   example it uses.
Then run the tests and the linter. A fix that breaks the build is not committed. Never edit code to
settle an open question; only the findings listed are in scope.
```

### No-origin addendum (finalizer, only when `has_origin` is false)

```text
THIS REPOSITORY HAS NO origin REMOTE: skip every git fetch and every rebase step above. Wherever the
instructions say origin/DEFAULT_BRANCH, use DEFAULT_BRANCH instead, in every command. A rebase skipped
for this reason is not a failure; report it as "rebase skipped (no origin)".
```

## Brainstorm

Inputs: the user's task text (the invocation without `--profile` and `--update`) and the repository at
`<toplevel>`. Output: the design, which step 5 writes to `<state_dir>/design.md`.

1. `mt phase brainstorm --activity "Brainstorm · understanding the task"`.
2. Read `<brainstorm_entry>` and follow it (Running a companion) with the task text as its input, then the
   profile's `## brainstorm` section. Every question goes through the Question rule for `<autonomy>`:
   asked as Questions says, one at a time, and only for the classes the preset asks. Under `interactive`
   the design is validated section by section before step 5, whatever the copy says: sections of 200–300
   words, each one a question shaped like a gate (Gates: "Approve" / "Revise", typed text), its question
   "Design section <k> of <m>: <name>". Before it, write the sections validated so far and this one to
   `<state_dir>/design-draft.md` and run `mt artifact design <state_dir>/design-draft.md`, so the Design
   view shows the section. A revision changes the section and asks its question again.
3. The preflight question, only when Start step 3's preflight failed (Errors row "revmux preflight fails at
   Start"). A review the profile skips has no question.
4. **Last question — worktree or in place**, only when `linked_worktree` is false. Under `autonomous` there
   is no question: `mt set mode=<defaults.mode>` and
   `mt assume "mode <defaults.mode>" --why "autonomy autonomous: defaults.mode"` (on a dirty tree name in
   `--why` which paths stay behind in worktree mode, or are stashed in place; in worktree mode with
   `branch` set and not `default_branch`, say in `--why` that the worktree starts where
   `worktree.baseRef` puts it, so `<branch>`'s own commits may be absent from it). Otherwise the question:
   - question "Run the task in a worktree or in place?"; options "Worktree (Recommended)" and "In place";
   - clean tree: worktree = a new branch under `.claude/worktrees/`, this checkout stays as it is;
     in place = the feature branch is created in this checkout;
   - `branch` is not `default_branch` (the user started on a branch of their own): say so in the question.
     In place make-task keeps a non-default branch, so the task's commits land on `<branch>` itself.
     A worktree starts where `EnterWorktree` puts it: the `worktree.baseRef` setting decides
     (`fresh`, the default, branches from `origin/<default_branch>`; `head` from this checkout's HEAD), so `<branch>`'s own commits may be absent from it. Either way the review covers only the
     commits after the plan commit's parent (`review_base`, set at Exec step 1);
   - dirty tree (`dirty_paths` without `.revmux/` and the paths under it is non-empty): list those paths
     in the question and explain both options in their descriptions: **worktree** — those edits stay in
     this checkout and are absent from the worktree; **in place** — make-task stashes those paths before
     execution and restores them at the end;
   - `.revmux/` is never stashed: the review needs the project profile in place. In place, its staged
     changes are saved and unstaged for the run (so no commit takes them) and staged again at the end;
     its edits to tracked files are set aside while the finalizer rebases (`git rebase` refuses a tree with
     unstaged changes) and put back at the end;
   - record the answer: `mt set mode=worktree` or `mt set mode=inplace`.

   In a linked worktree there is no question, at any autonomy level: the run stays in place (on a dirty
   linked worktree, with the stash): `mt set mode=inplace`.
5. Write `<state_dir>/design.md`: goal, chosen approach and why, the design (components, data flow,
   error handling, testing), decisions and assumptions so far, out of scope. Then
   `mt artifact design <state_dir>/design.md`, then the `design` gate when `<gates>` has it (Gates).

**Brainstorm skipped** (`<brainstorm_entry>` null: the profile's `phases.brainstorm` is `skip`):
`mt phase brainstorm --skipped --note "skipped by profile"`, then steps 3 and 4 as written (the preflight
question when the preflight failed, and the worktree question, are still asked), then step 5 with a minimal
`design.md`: the task text as the goal and the design, "brainstorm skipped by profile `<profile_name>`" as
the approach, the assumptions so far.

## Plan

Inputs: the absolute path of `<state_dir>/design.md`, the assumptions and decisions recorded so far (from
`mt status` or your notes), and the plan directory `<toplevel>/docs/plans/`. Output: one plan file there.

1. `mt phase plan --activity "Plan · writing the plan"`.
2. Read `<plan_entry>` and follow it (Running a companion) with those inputs, then the profile's
   `## plan` section. Record the testing approach it settles:
   `mt assume "testing approach: <x>" --why "<reason>"`. No commit, whatever the companion's last step
   says: the plan stays uncommitted until Exec step 1 has its branch.
3. Plan review, when `<plan_reviewer>` is set: Read `<plan_reviewer>`, drop its frontmatter, and run the
   body as one Agent call with `subagent_type: "Plan"` (Claude Code's built-in read-only type: no Edit,
   Write or NotebookEdit tool) and `model: <plan_reviewer_model>` (left out when null or not a model the
   Agent tool takes), without `bypassPermissions`, description "Review plan", prompt: the body, then
   "Review the implementation plan at `<absolute plan path>` against the design at
   `<state_dir>/design.md`. Report findings only; change no files. No human is available: ask no
   questions." Its Bash tool can still write, so guard the plan: before the call
   `cp <plan> <state_dir>/plan.before-review.md`; after it, the plan differs from the saved copy (`cmp`) →
   copy the saved one back and
   `mt assume "reverted the plan reviewer's edit to the plan" --why "the reviewer changes no files"`.
   Compare nothing else (no HEAD or `git status` check): during Plan `<toplevel>` is still the user's
   checkout, and the user may edit or commit there while the reviewer runs. Apply its findings to the plan
   yourself without asking: one
   `mt assume "<change made>" --why "plan-review: <finding>"` per applied change, and one
   `mt assume "kept <x>" --why "<reason>"` per finding you reject. The Agent tool refuses the `Plan` type
   (this Claude Code has none) → no plan review:
   `mt assume "no plan review" --why "the Agent tool has no Plan agent type"`, then step 4.
   `<plan_reviewer>` null → `mt assume "no plan review" --why "profile <profile_name> names no plan reviewer"`.
4. The `plan` gate when `<gates>` has it: first `mt artifact plan <absolute plan path>`, so the Plan view
   shows the plan, then the gate (Gates). Steps 3 and 4 are the only time the orchestrator edits the plan;
   from Exec on only subagents do.

## Exec

Inputs: the reviewed plan file, the exec companion's scripts `<exec_scripts>` and its prompts (Subagent
prompts), the profile's `## exec` section (it goes into the task prompt). The exec companion has no entry
file: this section is the playbook.

`mt phase exec --activity "Exec · branch and plan commit"`.

1. **Branch and plan commit**. `review_base` is recorded last, right before the plan commit (below): in
   worktree mode the branch HEAD is only known after `EnterWorktree` (`worktree.baseRef` decides it).
   - **Branch name**, for every branch this step creates: worktree mode, and in place when `branch` is
     null (detached HEAD) or `default_branch`. In place on any other branch there is no new branch: the
     commits land on `<branch>`; skip this recipe. Pick a name no branch and no worktree has yet. The
     plan's name without its date can repeat an earlier task's (same topic); an old branch of that name
     may be stale, merged or checked out in a worktree, and `git branch -m` refuses an existing branch:

     ```bash
     base=$(bash <exec_scripts>/create-branch.sh --print-name <plan>) || exit 1
     name=$base; i=2
     while git -C <main_checkout> show-ref --verify --quiet "refs/heads/$name" ||
       git -C <main_checkout> show-ref --verify --quiet "refs/heads/worktree-$name" ||
       [ -e <main_checkout>/.claude/worktrees/"$name" ]; do
       name=$base-$i; i=$((i + 1))
     done
     echo "base=$base name=$name"
     ```

     `name` differs from `base` → `mt assume "branch <name>" --why "<base> is taken by an earlier branch or
     worktree"`. Never run `create-branch.sh <plan>` itself: it checks out an existing branch of that name.
   - worktree chosen → the `EnterWorktree` tool with `name` (the session moves into
     `.claude/worktrees/<name>/` on branch `worktree-<name>`); `git -C <wt> branch -m <name>` (never `-M`:
     it would overwrite a branch of the user's; failing → Errors row "worktree branch"); verify
     `git -C <main_checkout> branch --show-current` still prints the `branch` init reported (changed →
     `mt fail` and stop: isolation breach); `mt set worktree_path=<wt>`; then `mkdir -p` the plan's
     directory in the worktree and `mv` the untracked plan file there from the main checkout;
   - in place (or dirty linked worktree), in this order, so that nothing that can fail runs while the
     user's edits sit only in the stash:
     1. **Branch first.** When the name recipe ran: `git -C <toplevel> checkout -b <name>`. It starts at the
        current HEAD and keeps the index and the working tree as they are. Failing → Errors row "in-place
        branch"; nothing is stashed yet.
     2. **Staged `.revmux/` changes.** The review needs `.revmux/` in the tree, so it is never stashed, but
        what the user staged there must not ride along in a task commit, nor in the stash's index commit
        (`stash apply --index` would then fail at the end). Save it and unstage it, one Bash call
        (idempotent: a rerun keeps the first save):

        ```bash
        p=<state_dir>/revmux-index.patch
        mt_git() { git -C <toplevel> -c diff.noprefix=false -c diff.mnemonicPrefix=false -c diff.relative=false "$@"; }
        mt_diff() { mt_git diff --no-textconv --no-ext-diff --no-color --binary -U3 --src-prefix=a/ --dst-prefix=b/ "$@"; }
        new=0
        [ -e "$p" ] || { mt_diff --cached -- .revmux > "$p.tmp" && mv "$p.tmp" "$p" && new=1; } || exit 1
        [ -s "$p" ] || { echo "revmux_saved=no"; exit 0; }
        if ! mt_diff --cached --quiet -- .revmux; then
          t=$(mktemp -d) || exit 1; ok=0
          GIT_INDEX_FILE="$t/i" mt_git read-tree HEAD &&
            GIT_INDEX_FILE="$t/i" mt_git apply --cached --whitespace=nowarn --no-ignore-whitespace "$p" &&
            GIT_INDEX_FILE="$t/i" mt_git ls-files -s -z -- .revmux > "$t/want" &&
            mt_git ls-files -s -z -- .revmux > "$t/have" && cmp -s "$t/want" "$t/have" && ok=1
          rm -rf "$t"
          if [ "$ok" != 1 ]; then [ "$new" = 1 ] && mv "$p" "$p.unsafe"; echo "revmux_saved=unsafe"; exit 1; fi
          mt_git reset -q -- .revmux || exit 1
        fi
        echo "revmux_saved=yes"
        ```

        The working copies under `.revmux/` stay as they are; Completion step 3 stages the patch again.
        The diff ignores the user's git config (textconv drivers, external diff, color, prefixes, context
        size), and the unstage runs only after the patch round-trips: HEAD plus the patch, in a scratch
        index, must give exactly the staged `.revmux/` entries (same blob ids, so the same bytes). A patch
        is safe here, unlike for the working copies: `diff --cached` compares blobs and `apply --cached`
        writes blobs, so no clean or smudge filter, `core.autocrlf` or `eol` attribute runs on either side. `revmux_saved=unsafe` (it does not, so the
        reset would lose staged content) leaves the index as it was and renames a patch made by this call
        to `revmux-index.patch.unsafe`, so no later recipe applies it.
        A non-zero exit → `mt fail "saving staged .revmux changes failed"` and stop (Errors).
     3. **Stash.** Re-read the dirty paths now, not the init snapshot (paths
        deleted since init would fail the push, paths dirtied since would be missed):
        `mt dirty --exclude <plan>`. It drops `.revmux/` and the plan, lists every untracked file on its own
        (so a stash of an untracked directory cannot take the plan with it), stores the list as `stash_paths`
        and prints `{"paths": [...], "quoted": "...", "unstage": [...], "unstage_quoted": "..."}`. `<paths>` =
        `quoted`, `<unstage>` = `unstage_quoted` (may be empty). When `paths` is non-empty, run this in one
        Bash call, with `<run_id>` the last path component of `state_dir` (the stash message names the run, so
        recovery finds this run's stash and no other). `mt init` builds that name from `[A-Za-z0-9_-]` only
        (other characters of the checkout's directory name become `-`), so `<run_id>` goes into the recipes
        as it is, never quoted:

        ```bash
        unstage=(<unstage>)
        if [ ${#unstage[@]} -gt 0 ]; then
          for f in "${unstage[@]}"; do
            if [ -e <toplevel>/"$f" ] || [ -L <toplevel>/"$f" ]; then printf '%s\0' "$f"; fi
          done >> <state_dir>/stash-rm-cached || { echo "push=unsaved"; exit 1; }
          printf '%s\0' "${unstage[@]}" >> <state_dir>/stash-unstaged || { echo "push=unsaved"; exit 1; }
          git -C <toplevel> --literal-pathspecs reset -q -- "${unstage[@]}"
        fi
        r=refs/make-task/$(printf '%s' "<run_id>" | tr -c 'A-Za-z0-9_-' -)
        ours() { git -C <toplevel> stash list --format='%H %gs' | M=': make-task <run_id>' awk '{ m = ENVIRON["M"] } length($0) > length(m) && substr($0, length($0) - length(m) + 1) == m {print $1; exit}'; }
        before=$(ours)
        git -C <toplevel> --literal-pathspecs stash push -u -m "make-task <run_id>" -- <paths>; echo "push=$?"
        after=$(ours)
        if [ -n "$after" ] && [ "$after" != "$before" ]; then git -C <toplevel> update-ref "$r" "$after" || echo "ref=failed"; fi
        left=$(git -C <toplevel> --literal-pathspecs status --porcelain --untracked-files=all -- <paths> | wc -l | tr -d ' ')
        echo "before=$before after=$after left=$left"
        ```

        `before` and `after` are this run's entry, found by its message, never the tip of `refs/stash`:
        another terminal can store a stash between the push and the lookup, and the tip is then its stash.
        `ours` hands the message to awk through `ENVIRON` and compares whole strings: `awk -v` would read
        backslash escapes in it, and a lookup that misses this run's entry reads as "nothing stashed".
        The recipe copies the sha to the private ref `refs/make-task/<run_id>` (characters a ref name cannot
        hold become `-`), so the stash commit survives `git gc` even when the entry leaves `git stash list`;
        the restore applies that sha and never touches the stash list. `ref=failed` is not a stop: the
        stash entry still holds the edits and the restore takes the sha from `stash_ref`.

        `--literal-pathspecs` makes git read every path as a file name, never a pattern: an untracked file
        named `*.md` must not pull the excluded plan into the stash. `unstage` holds only the paths in HEAD
        that the index no longer has (staged deletions, rename sources): `stash push -- <paths>` cannot match
        them and exits 128 half done. Resetting them puts HEAD's version back in the index, where there was
        nothing, so no staged content is lost; the recipe first appends them to `<state_dir>/stash-unstaged`
        (a rerun adds nothing: they are no longer staged), and the ones whose file is still in the tree (a
        `git rm --cached`: the deletion is in the index only) also to `<state_dir>/stash-rm-cached`. Such a path
        can come out of the reset clean, so no stash holds it; the restore stages every recorded deletion
        again whether or not a stash was made (a staged rename comes back a rename). Every other path
        stays staged: the stash's index commit keeps staged content the tree no longer has (a staged hunk
        overwritten or reverted since), and the restore puts it back. Never reset the other paths.
        - `after` non-empty, different from `before`, and `left=0` → `mt set stash_ref=<after>`. `push` may
          be non-zero here: when every listed path's working copy equals HEAD (a file staged, then deleted or
          reverted in the tree), git saves the stash, finds nothing to clean and exits 128. The stash holds
          the staged content and the paths are clean.
        - `push=0` and `after` empty → nothing was stashed; set nothing.
        - anything else → Errors row "stash push fails".
   - then, in both modes and in the tree the commits go to (`<toplevel>` is the worktree in worktree
     mode), record where the task's work starts and commit the plan (only that path):
     `mt set review_base=$(git -C <toplevel> rev-parse HEAD)` (the review, the Changes view and the
     finalizer's squash limit all start there; the next `mt plan` turns the Changes button on, so the
     user can follow the task's work while it runs), then
     `git -C <toplevel> --literal-pathspecs add -- <plan> && git -C <toplevel> --literal-pathspecs commit -m "docs: add <topic> plan" -- <plan>`.
2. **Progress**: `bash <exec_scripts>/init-progress.sh /tmp/progress-<plan-stem>.txt <plan> "$(git -C <toplevel> branch --show-current)"`
   (the feature branch the commits go to, not `<branch>`), then
   `mt plan <absolute plan path in the current toplevel> --progress /tmp/progress-<plan-stem>.txt`.
3. **Prompt**: build the task prompt from `prompts/task.md` per Subagent prompts (task → the profile's
   `## exec` section, then the harness addendum).
4. **Loop**: re-read the plan; first Task section (`### Task N:` or `### Iteration N:`) with `[ ]` →
   `mt checkpoint begin exec.task.<n>` (add `--resume` when `mt status` lists it `begun`), `mt refresh`,
   `mt activity "Task <n> · <title>"`, show the task's
   open checkboxes to the user, then one Agent (`general-purpose`, `bypassPermissions`, description
   "Execute task <n>") with the prompt; wait for it. Re-read the plan: the section's boxes checked →
   `mt checkpoint end exec.task.<n> --result done`, `mt refresh`; not checked → one retry with a fresh agent
   and the error details appended to the prompt (the checkpoint stays `begun`), then `mt fail "Task <n> failed
   after one retry"` and the error question (its `retry` reruns the task with
   `mt checkpoint begin exec.task.<n> --resume`). Stop when no `[ ]` remains in any Task section (max 50 iterations).
   One task agent at a time. Never fix or debug code yourself, and never stop the loop on an agent's
   return text — only the plan's checkboxes count.
5. **Reconciliation**: `exec.task.<n>` begun but not ended → re-read the plan; its boxes checked → end it;
   else rerun that task from step 4 with `mt checkpoint begin exec.task.<n> --resume` (the task prompt is
   idempotent on a partially done section).

## Review

Inputs: the range `<review_base>..HEAD` (`review_base` is the commit where the task's work starts: hand it
over as the review's base), the tree under review `<toplevel>`, the main checkout `<main_checkout>` and
the tasks dir `<D>`.

The review phase is skipped in two cases, and then no round runs and no leftovers question is asked; go to The
end of the run (Finalize), and the run ends `review skipped`:

- `review_entry` is null (the profile skips review; Start step 3 recorded the assumption and asked
  nothing): `mt phase review --skipped --note "skipped by profile"`. No question, at every autonomy level.
- the preflight failed and the user chose `noreview` on the preflight question:
  `mt phase review --skipped --note "revmux unavailable"`.

revmux runs as one loop: Before round 1 below reads `<review_entry>` and follows it (Running a companion),
then the profile's `## review` section; this file's rules below win over both. `<D>` is
`revmux_tasks_dir`; the review companion's scripts are in `<review_scripts>`. `<profile>` in this section
is the revmux review profile (`comprehensive`, `final`, …) the copy's Step 3 picks, not the make-task
profile.

### Where revmux runs

- **Every revmux command** (config, `task-state.sh`, `revmux new`, the launch, re-runs) runs as
  `(cd <main_checkout> && revmux … --tasks-dir <D> …)`, so the archive, the project profile and the normal
  config layers are the main checkout's. The launch adds `--workdir <toplevel>`.
- Fill the flags slot of revmux's round-preparation brief (Step 2) with `--tasks-dir <D>`, and add this
  line to the brief: "`<D>` is an allowed write location; run every revmux command as
  `(cd <main_checkout> && revmux … --tasks-dir <D>)`". Expand every path in the brief (no `${CLAUDE_SKILL_DIR}`).
- Once per run: `(cd <main_checkout> && revmux config --tasks-dir <D>)` reports the expected profile and
  lenses.

### revmux step overrides

These steps and rules stay here, whatever the review companion's copy says. The `default` profile's
copy already carries make-task's other changes to revmux (`assets/profiles/default/UPDATE.md`
`## Changes`); the two that touch the user's data are repeated here, so a replaced or merged copy cannot
drop them:

- **Step 0**: before round 1 run preflight again with the profile round 1 will use:
  `(cd <main_checkout> && bash <review_scripts>/preflight.sh <profile>)`; failure → Errors row for revmux
  exit 2. The linked-worktree notice becomes `mt event "<notice>"`.
- **Step 0.5**: never create or offer the project profile (`.revmux/profile.md`) and never write a
  round-local `input/profile.md`: either would set the review standard of the user's repository unasked.
  If `revmux config` shows no `paths.profile_fallback`,
  `mt assume "no project profile; generic calibration" --why "no .revmux/profile.md in the main checkout"`;
  otherwise record nothing.
- **Archive housekeeping** never runs: no `revmux stats` check and no cleanup question, whatever the
  copy says about running it after the loop (removing archive tasks deletes the user's review history).
  The final report says it was skipped.
- **Step 2** task id: round 1 hands none (the subagent matches or derives one); store the returned
  `task` with `mt set revmux_task=<task>`. Later rounds hand `revmux_task` verbatim.
- **Step 4**: the launch and the wait are this file's (Each round, steps 2–4).
- **Step 7**: the fixes run in a fixer subagent, never in this session.
- Non-negotiable 2 holds through `mt review wait`: it blocks in one foreground call, it does not poll from
  this session. No Monitor, so the session does not yield during a round.

### Before round 1

1. `mt phase review --activity "Review · preparing round 1"`.
2. If `git -C <main_checkout> check-ignore -q .revmux/tasks/x` fails, append `.revmux/tasks/` to
   `$(git -C <main_checkout> rev-parse --path-format=absolute --git-common-dir)/info/exclude` (local, never
   committed, shared by worktrees; mention it in the final report).
3. `git -C <toplevel> status --porcelain -- . ':(exclude).revmux'` must print nothing, else
   `mt fail "working dir dirty before review"` and the error question. `.revmux/` stays out of the check: the
   project profile lives there, untracked or edited, and is never committed.
4. `mt set loop_start_sha=$(git -C <toplevel> rev-parse HEAD) revmux_tasks_dir=<main_checkout>/.revmux/tasks`
   (`review_base` was set at Exec step 1), then `mt artifact changes`.
5. Read `<review_entry>` and follow it (Running a companion) for a review loop over `<review_base>..HEAD`
   in `<toplevel>`, then the profile's `## review` section, with the overrides above: the `revmux config`
   check, the Step 0.5 check, the preflight with round 1's profile.

### Each round `<n>`

1. Prepare the round with revmux's Step 2 subagent (brief as overridden above; its "Exclusions the user
   has stated" lists every finding an earlier `review-round` gate ruled out, step 8). It returns `task`,
   `run` (`NN-label`) and `round_dir`; a missing `round_dir`, an empty `wrote` or a refusal in `notes`
   blocks the launch (revmux's own check).
2. `mt review start --round <n> --run <run> --round-dir <round_dir>` (prints the report path). The round
   entry is the journal: it holds run, round_dir, report and `head` (the HEAD this round reviews) before
   the launch, and `mt status` lists it.
3. Launch in the background (Bash `run_in_background: true`), these two flags first and in this order so
   `mt review wait` can match the process:
   `(cd <main_checkout> && revmux --task <revmux_task> --run <run> --tasks-dir <D> --workdir <toplevel> --profile <profile> --no-tui > <state_dir>/revmux/round-<n>.json 2> <state_dir>/revmux/round-<n>.log)`.
   Never `2>&1` into the report.
4. Foreground `mt review wait --round <n>` with `timeout: 600000`, repeated while it prints `running`.
   Each call keeps the Review tab live: as the round's `events.jsonl` grows, it folds it into the
   `review` artifact and the round's `live` summary, which the viewer and the dashboard show.
   `done` → go on. `failed` (process gone, report does not parse) → Errors row for exit 2.
5. Read the report: gating is `critical`/`major` in `findings` (loop.md "What gates"); note whether
   `sources.degraded` is non-empty.
6. `mt review result <state_dir>/revmux/round-<n>.json --round <n> [--not-gating <id>=<reason> ...] [--deferred <id>=<reason> ...]`:
   the loop's own gating decision; `mt` computes the verdict (`degraded`, `<k> gating` or `clean`). Scope-ruled findings (the round's `scope.md`/`goal.md` puts them outside
   the change) are `--not-gating`; prose-only majors, which loop.md says do not drive another round but are
   still worth fixing, are `--deferred` and reach the leftovers question. Ids must be ids of `critical` or
   `major` findings in the report and appear in one list only. Deferred findings stay open across later
   rounds (even when a later round does not report them again) until the leftovers fix's confirming round.
   This also rebuilds the `review` artifact, registered at `mt review start`. Record a degraded round too
   (its computed verdict is `degraded`), then go to the Errors row for a degraded round.
7. Append a one-line round summary:
   `printf '%s\n' 'review round <n>: <counts> <verdict>' | bash <exec_scripts>/append-progress.sh <progress_path>`.
8. Stop per loop.md "Stopping", recording the reason: zero gating → `mt set stop_reason=clean`; the code
   gating count not strictly lower and a gating finding repeats → `not-converging`; five rounds →
   `round-cap`. A degraded round or exit 2 is not a verdict: it goes to the Errors row (its `skip` sets
   `stop_reason=skipped`). Otherwise: the `review-round` gate when `<gates>` has it (Gates). When its text
   takes gating findings away from the fixer, record that before the fixer runs, or the next round gates
   on them again: run step 6's call for round `<n>` again (a second call replaces the round's result) with
   the same lists plus, per finding taken away, `--not-gating <id>="gate: <text>"` when the user rules it
   out of the change or `--deferred <id>="gate: <text>"` when the user wants it fixed at the end (the
   leftovers question):
   `mt review result <state_dir>/revmux/round-<n>.json --round <n> --not-gating <id>="gate: <text>" ...`.
   Every later round's Step 2 brief lists each ruled-out one under "Exclusions the user has stated", so it
   is written into that round's `scope.md`; a later round that reports any of them again records it with
   the same flag and reason (after a restart, `<state_dir>/review.md` marks them `not gating: gate: …` and
   `deferred: gate: …`). Its verdict now `clean` (the text took every gating finding away) →
   `mt set stop_reason=clean` and no fixer. With gating findings left: `mt checkpoint begin review.fix.<n>`,
   one fixer subagent (description "Fix review round <n>") with the resolved `prompts/fixer.md`
   (placeholders substituted, `FINDINGS_LIST` = the full JSON of the gating findings, as the gate left
   them, harness and fixer addenda appended); it verifies, fixes, commits with a pathspec and logs to the
   progress file. `mt checkpoint end review.fix.<n>`, then round `<n+1>`.

**Re-running a round** (degraded, exit 2, crashed): a round name cannot be reused, so open a new round
`<n+1>`: `(cd <main_checkout> && revmux new --task <revmux_task> --run <NN-label> --tasks-dir <D>)` with the
next `NN-label`, then copy the failed round's `input/` files into the round dir it prints, and continue at
step 2 with round `<n+1>`, that run name and round dir (`mt review start` refuses a second name for a
started round). A failed or degraded round does not count toward the five-round cap.

### Reconciling a round

A round started but without a result → `mt review wait --round <n> --deadline 0` (it checks the process
with its own anchored pattern and the report at the round's `report` path, and returns at once):
`done` → step 5; `running` → continue with `mt review wait --round <n>` (step 4); `failed` → re-run the
round as above. Never `pgrep` from this session: the Bash call's own shell carries the pattern.

## The end of the run

### Leftovers question

After the loop, if anything is open, ask one question (Questions). Its text: the stop reason and what is
open — unresolved gating findings, deferred (prose-only) majors, minors, open questions — with their counts
and short titles; each finding with file:line and its mechanism is in the Review view.

- Gating or deferred findings open → options `fix` "Fix the remaining majors (Recommended)" (one fixer
  round and one confirming round) and `leave` "Leave them and finalize" (the run ends `not clean`); typed
  text allowed.
- Only minors or open questions → options `fix` "Fix the listed minors" / `leave` "Leave them"; typed text
  answers open questions or narrows the list.

Under `autonomous` there is no question: the answer is `defaults.leftovers` (`fix` or `leave`), recorded with
`mt assume "leftovers: <defaults.leftovers>" --why "autonomy autonomous: defaults.leftovers"`; open
questions stay unanswered and go to the final report.

The fix answer runs one fixer subagent between `mt checkpoint begin review.fix.leftovers` and
`mt checkpoint end review.fix.leftovers` (`FINDINGS_LIST` = the open gating and deferred findings, or the
listed minors, narrowed by the free text) and one confirming round with revmux profile `final` (its
`goal.md` is the merge gate), recorded with
`mt review result <state_dir>/revmux/round-<n>.json --round <n> --confirming`: that settles the deferred
findings carried from earlier rounds.
Whatever that round reports is final: no further loop; new gating findings or a degraded round make the run
`not clean` (a degraded confirming round: `mt set stop_reason=degraded`).

### Finalize

1. `mt phase finalize --activity "Finalize · rebase and clean up"`. Then, only while `reviewed_sha` is unset
   in `mt status` (a rerun after a compact skips this; `set` refuses a second value):
   `mt set reviewed_sha=$(git -C <toplevel> rev-parse HEAD)`. It names the tree the review vouches for,
   so it must be the `head` of the last finished round in `rounds` (the HEAD `mt review start` recorded
   for it). When HEAD is not that `head`, a commit landed after the last round: a leftovers fix whose
   confirming round has not run → run that round first (Recovery), then set it. Any other cause → set it
   anyway; `mt done` then refuses `clean` and names why.
2. **In place only, set the `.revmux/` edits aside first.** The finalizer rebases onto `origin/<default>`
   and squashes with `git rebase -i`, and `git rebase` refuses to start while a tracked file has unstaged
   changes. In place, `.revmux/` is the only dirt left in the tree, and an edited tracked profile (or a
   staged edit, unstaged at Exec step 1) would fail every rebase. The review is over, so save those edits
   as raw copies and put HEAD's version back for the finalizer, in one Bash call (idempotent: a rerun keeps
   the first save; worktree mode skips this, its tree has no `.revmux/` edits):

   ```bash
   mt revmux aside --state-dir <state_dir> --toplevel <toplevel>
   ```

   The save is a copy of the files, not a git diff, so no user git config reaches it: clean and smudge
   filters, `core.autocrlf`, `text`/`eol` attributes, textconv drivers and diff settings all shape what git
   shows or writes, never the bytes kept. `git checkout-index` writes, into a scratch directory, exactly what
   the checkout would write (same filters, same line endings); every tracked `.revmux/` path whose working
   copy differs from that in bytes, type or executable bit is recorded in `<state_dir>/revmux-tree/list`
   (its whole `git ls-files -s -z` record, NUL-terminated) and copied to `<state_dir>/revmux-tree/files/<path>`
   with its mode, a symlink as a symlink with the same target bytes (a path missing from the tree is
   recorded with no copy). `mt` handles paths, records and symlink targets as raw bytes and never hands
   one to a shell, so a name with a newline or a leading `-`, or a target such as `-f` or one ending in a
   newline, is kept as it is. Each copy is compared byte for byte before the save counts: the copies are
   made in `revmux-tree.new/`, renamed `revmux-tree/` only once all match, and only then are the recorded
   paths removed and checked out again from the index, so each holds HEAD's version whatever git thinks of
   the old one. Untracked files under `.revmux/` stay (they do not block a rebase), and so does the index.
   A rerun keeps the first save: each path edited now must be in its list with the same record and the
   same bytes (or be missing from the tree; the copy holds it), and every path in the list, edited now or
   not, must still have its recorded index record and hold that index version, the saved copy or nothing;
   all are checked before any path is removed, so a listed path dropped from or restaged in the index
   since the save stops as `differs` with its working copy untouched. It prints `revmux_tree=aside` or
   `revmux_tree=none` (nothing to set aside) with exit 0. A non-zero exit → Errors row
   "setting `.revmux/` aside fails": `revmux_tree=differs` (the tree holds `.revmux/` edits other than the
   saved ones), `revmux_tree=unsafe` (an unmerged `.revmux/` entry, or a symlink or non-directory in a
   parent component of a `.revmux/` path in the tree or under `revmux-tree/`: nothing is touched; or a copy
   that does not match its file: the tree is left as it was, and the partial copies move to
   `revmux-tree.unsafe/`, so no later `mt revmux put-back` restores them) or `revmux_tree=failed` (a git or file operation failed; before the
   save is complete the tree is untouched, after it the copies hold the edits).
   Completion step 3, and every stop, puts the edits back.

   Then `mt checkpoint begin finalize`; resolve `prompts/finalizer.md`, append the harness addendum (plus the
   no-origin addendum when `has_origin` is false), substitute every placeholder; one subagent (description
   "Finalize"); `mt checkpoint end finalize --result <ok|rebase failed|tests failed>` (`ok` also for a
   rebase skipped for lack of `origin`; `mt done` accepts `clean` only after `ok`).
3. `mt review verify-final` → prints `unchanged` or `changed` (`changed` sets `review_stale`). It also
   stamps the checked `reviewed_sha`; `clean` needs that stamp. HEAD must hold exactly the reviewed
   content replayed onto its new base (whitespace, binary files and line positions count; a replay that
   conflicts is `changed`). A missing `origin/<default>` falls back to the local `<default>`; with
   neither it compares the trees as they are. Neither is an error. Uncommitted changes are `changed`
   too (the finalizer tested a tree HEAD does not hold): anything `git status` lists outside `.revmux/`,
   whose untracked files and set-aside edits are the user's. The user's own paths are still in the stash
   here (or, when nothing was stashed, clean in the tree) until Completion step 3 sets `stash_restored`,
   so a change to one is the task's.
4. A failed rebase or a test failure reported by the finalizer → the final status cannot be `clean`. A
   rebase skipped for lack of `origin` is not a failure.

Reconciliation: `finalize` begun → a rebase in progress (`git status`) → `git rebase --abort`, rerun
finalize from step 2 (`mt revmux aside` first; the rerun keeps the first save) with
`mt checkpoint begin finalize --resume`; a finalize entry already in the progress file → end the
checkpoint with the result it reports; neither → rerun finalize the same way.

### Stats (best-effort)

Resolve `prompts/stats.md`, substitute every placeholder (no addendum: stats is read-only). Tell the agent
how to find its session log instead of deriving it from `pwd`. Encode `start_cwd` by turning every character
that is not a letter or digit into `-`. The log is a `*.jsonl` directly under
`~/.claude/projects/<encoded start_cwd>*/` — the trailing `*` matters: after `EnterWorktree`, Claude Code
logs the whole session under the worktree's path (`<encoded start_cwd>--claude-worktrees-<name>`), and the
start directory then holds only older sessions. Take the candidates newest first and use the first one that
contains `<state_dir>` (every `mt` call of this run names it), so a parallel session in the same repo is never
picked. Its subagent logs are in `<session-id>/subagents/` beside it. Its task agents are "Execute task <n>", fixers "Fix
review round <n>", finalizer "Finalize"; rows it cannot attribute are reported as n/a. Save its report as
`<state_dir>/stats.md`, `mt artifact stats <state_dir>/stats.md`, and show it to the user verbatim. A failure
here is reported and does not block completion.

### Completion

1. Decisions report: `grep -E '^\[(decision|deviation)\]' <progress_path>` → a section "Decisions made
   autonomously / Deviations from the plan", one bullet each (or "none were logged").
2. `bash <exec_scripts>/append-progress.sh <progress_path> "completed"`, then
   `bash <exec_scripts>/move-plan.sh <plan_path>` (best-effort), then `mt refresh`.
3. In place, once Exec step 1 ran the push recipe, restore in one Bash call, with `<stash_ref>` the
   `stash_ref` sha, or `''` when it is unset (nothing was stashed, but staged deletions may still need
   staging again), and `<run_id>` as in Exec step 1. The recipe applies the sha and never drops a stash
   entry: `git stash drop` takes only a `stash@{n}` position, and another terminal's `git stash push` can
   shift that position between any check and the drop, which would delete that terminal's stash. Nothing
   in git drops an entry by sha.

   ```bash
   s=<stash_ref>
   a=<state_dir>/stash-applied
   h=<state_dir>/stash-partial
   r=refs/make-task/$(printf '%s' "<run_id>" | tr -c 'A-Za-z0-9_-' -)
   snap() { { git -C <toplevel> diff --no-ext-diff --no-textconv --no-color --binary HEAD --
     git -C <toplevel> ls-files -s -z; git -C <toplevel> ls-files -o -z --exclude-standard; } | git hash-object --stdin; }
   if [ -z "$s" ]; then echo "restore=none"
   elif [ "$(cat "$a" 2>/dev/null)" = "$s" ]; then echo "restore=ok"
   elif [ "$(cat "$h" 2>/dev/null)" = "$s" ]; then echo "restore=partial"; exit 0
   elif ! git -C <toplevel> cat-file -e "$s^{commit}" 2>/dev/null; then echo "restore=gone"
   else
     clash=0
     if git -C <toplevel> rev-parse -q --verify "$s^3" > /dev/null; then
       while IFS= read -r -d '' f; do
         d=$f; [ -e <toplevel>/"$f" ] || [ -L <toplevel>/"$f" ] && { printf 'exists: %s\n' "$f"; clash=1; continue; }
         while [ "$d" != "${d%/*}" ]; do d=${d%/*}
           if [ -L <toplevel>/"$d" ] || { [ -e <toplevel>/"$d" ] && [ ! -d <toplevel>/"$d" ]; }; then
             printf 'exists: %s\n' "$d"; clash=1; break; fi
         done
       done < <(git -C <toplevel> ls-tree -r -z --name-only "$s^3")
     fi
     m=$(git -C <toplevel> merge-tree --write-tree --no-messages --name-only --merge-base="$s^1" HEAD "$s" 2>/dev/null)
     [ $? != 1 ] || { printf '%s\n' "$m" | sed '1d; s/^/conflicts: /'; clash=1; }
     if [ "$clash" = 1 ]; then echo "restore=conflict"; exit 0; fi
     staged_only=$(comm -12 <(git -C <toplevel> diff --name-only "$s^1" "$s^2" | sort) \
       <(git -C <toplevel> diff --name-only "$s^2" "$s" | sort))
     pre=$(snap); res=no
     if git -C <toplevel> stash apply -q --index "$s"; then res=ok
     elif [ -z "$staged_only" ] && [ "$(snap)" = "$pre" ] && git -C <toplevel> stash apply -q "$s"; then res=ok; fi
     if [ "$res" = ok ]; then printf '%s\n' "$s" > "$a"; echo "restore=ok"
     elif [ "$(snap)" != "$pre" ]; then printf '%s\n' "$s" > "$h"; echo "restore=partial"; exit 0
     else echo "restore=conflict"; exit 0; fi
   fi
   if [ -n "$s" ]; then
     [ "$(git -C <toplevel> rev-parse -q --verify "$r")" != "$s" ] || git -C <toplevel> update-ref -d "$r" "$s"
     n=$(git -C <toplevel> stash list --format='%gd %H' | S="$s" awk '$2 == ENVIRON["S"] {print $1; exit}')
     echo "entry=${n:-none}"
   fi
   u=<state_dir>/stash-unstaged
   k=<state_dir>/stash-rm-cached
   [ ! -s "$k" ] || while IFS= read -r -d '' f; do
     git -C <toplevel> rm -q --cached --ignore-unmatch -- ":(literal)$f" || echo "restage=failed"
   done < "$k"
   [ ! -s "$u" ] || while IFS= read -r -d '' f; do
     [ -e <toplevel>/"$f" ] || [ -L <toplevel>/"$f" ] ||
       git -C <toplevel> rm -q --cached --ignore-unmatch -- ":(literal)$f" || echo "restage=failed"
   done < "$u"
   ```

   `git stash apply` is not all or nothing: a merge conflict leaves conflict markers and the rest of the
   stash in the tree, and an untracked file that already exists stops it after the tracked changes are in.
   So the recipe checks before it applies anything: every untracked file of the stash (`$s^3`) must be
   missing from the tree, its parent directories real directories or missing (`exists: <path>` names one
   that is not), and a trial merge of the stash onto HEAD (`git merge-tree --write-tree`, base `$s^1`)
   must be clean (`conflicts: <path>` names the paths it conflicts on). Either check failing →
   `restore=conflict` with the tree untouched. A git without `merge-tree --merge-base` skips the trial.
   `--index` puts the staged content back too. When it cannot (the branch changed a file the user had
   staged), a plain `apply` follows only when it loses nothing: `staged_only` (paths whose staged content
   is in neither HEAD nor the working copy) is empty, and the failed `--index` left the tree as it was.
   Otherwise nothing more is applied. The recipe hashes the tree before applying (`snap`: the diff against
   HEAD, the index entries, the untracked files); a failed apply that changed it prints `restore=partial`
   and writes the sha to `<state_dir>/stash-partial`, so a rerun prints `partial` again and never applies
   on top of a half-restored tree. A failed apply that changed nothing prints `restore=conflict`. A
   successful apply writes the sha to `<state_dir>/stash-applied`, so a rerun never applies the same stash
   twice. The sha
   comes from `stash_ref` and the private ref keeps its commit, so the restore does not depend on the
   stash list: an entry another terminal dropped or shifted changes nothing. `restore=gone` means the
   commit itself is missing (its entry and the private ref were deleted outside make-task and git pruned
   it). After `ok` or `gone` the recipe deletes the private ref and prints `entry=`: the position
   `git stash list` showed this run's entry at (`none` once someone dropped it). The loops stage the
   deletions Exec step 1 unstaged again (`restore=none` and `restore=gone` run them too, so a run that
   stashed nothing loses nothing, and a rerun stages what is still unstaged). Each path in
   `stash-rm-cached` leaves the index whatever the tree holds: its file was in the tree when it was
   unstaged, and stays there, untracked. Each other path in `stash-unstaged` leaves it only while it is
   still missing from the working copy (a path the tree has again, because the user put it back, is
   left alone). `restage=failed` is not a stop: the path is tracked again, unstaged, and the final report
   says so.

   Each outcome but `conflict` and `partial` is recorded in `stash_restored`, in the same call that clears `stash_ref`
   so a compact cannot split the two; from then on `mt review verify-final` leaves the restored
   `stash_paths` to the user.
   `restore=none` → `mt set stash_restored=none`. `restore=ok` → `mt set stash_ref= stash_restored=ok`
   (a rerun after a compact then prints `restore=none`). `restore=gone` → the edits cannot come back from
   the stash: `mt event`, `mt set stash_ref= stash_restored=gone`, and the final report says which
   `stash_paths` were not restored. `entry=stash@{n}` → the final report names the entry (message
   `make-task <run_id>`, the sha) and says the edits are already back in the tree, so it is a spare copy
   the user drops by hand once checked (`git stash list`, then `git stash drop` at the position it shows
   then). `restore=conflict` → Errors row "stash restore conflicts"; nothing was applied, the tree is as
   it was, the entry and the private ref stay, and so do the recorded deletions: the final report names
   the sha, the `exists:`/`conflicts:` paths the recipe printed, `stash-unstaged` and `stash-rm-cached`,
   and the command to run once those paths are dealt with (an existing file moved away; a conflicting
   path is left to the merge, which then marks it for the user to resolve):
   `git -C <toplevel> stash apply --index <sha>`. `restore=partial` → the same Errors row, but the tree
   holds part of the stash and may hold conflict markers: the final report says so, names the sha (it
   holds all of the stashed edits: `git -C <toplevel> stash show --include-untracked -p <sha>`), the
   paths `git -C <toplevel> status --porcelain` lists and the unmerged ones `git -C <toplevel> ls-files -u`
   lists, `stash-unstaged` and `stash-rm-cached`, and says not to run `git stash apply` on that tree. The
   user either finishes the restore in place (resolve the unmerged paths, take what is missing from
   `git show <sha>:<path>`, or `git show <sha>^3:<path>` for an untracked file), or puts the listed paths
   back to HEAD (`git -C <toplevel> checkout -f HEAD -- <tracked paths>`, the restored untracked files
   removed) and then runs `git -C <toplevel> stash apply --index <sha>`.

   Then, whatever the stash outcome, put the user's `.revmux/` changes back, in two Bash calls: first the
   working copies Finalize step 2 set aside, then the staged changes Exec step 1 saved. In worktree mode
   there is nothing saved and both print `none`. The working copies:

   ```bash
   mt revmux put-back --state-dir <state_dir> --toplevel <toplevel>
   ```

   It copies the saved files back as they are, with their mode (a symlink with its exact target bytes),
   and compares each byte for byte; a path saved as missing is removed again. It always exits 0. It changes
   all or nothing: first every recorded path must still have the index entry it had at the aside (its whole
   `git ls-files -s -z` record, compared as bytes; the rebase did not bring a new `.revmux/` version),
   and its working copy must be HEAD's version as `git checkout-index` writes it, the saved copy already
   (a rerun after a compact mid-copy), or missing; and no parent component of the path, in the tree or
   under `revmux-tree/files/`, may be a symlink or non-directory. Then the directory is renamed `revmux-tree.restored/`,
   so a rerun prints `none`. `revmux_tree=conflict` (a path fails that check) is not a stop: nothing was
   copied, and the final report names `<state_dir>/revmux-tree/files/` (the user's edits, byte for byte)
   and `<state_dir>/revmux-tree/list` for the user to copy back by hand. `revmux_tree=failed` (a copy
   failed or does not match) is reported the same way and is not a stop either: the saved copies stay.
   Then the staged changes:

   ```bash
   p=<state_dir>/revmux-index.patch
   if [ ! -s "$p" ]; then echo "revmux_index=none"
   elif git -C <toplevel> apply --cached --whitespace=nowarn --no-ignore-whitespace "$p"; then
     mv "$p" "$p.restored" && echo "revmux_index=ok"
   else echo "revmux_index=conflict"; fi
   ```

   `git apply` changes all or nothing. `revmux_index=conflict` (HEAD's `.revmux/` changed under it, for
   example by the rebase) is not a stop: the working copies were never touched, and the final report names
   `<p>` and the command that stages it again, `git -C <toplevel> apply --cached <p>`.
4. `mt done --status "<S>" [--note "<one line>"]` with S:
   - `stopped` — the run stopped on an error question;
   - `review skipped` — the review phase was skipped or the user chose to skip it (`stop_reason=skipped`);
   - `review stale` — `verify-final` printed `changed`;
   - `not clean` — unresolved or deferred findings left, the last round degraded, commits after the last
     round, or the finalizer reported a failed rebase or failing tests;
   - `clean` — none of the above.
   `mt done` refuses `clean` (exit 1, message names why) while findings are unresolved or deferred, the
   review is stale, skipped or never ran, no round has a result, the latest round has no result or was
   degraded, `reviewed_sha` is not the `head` of the latest finished round (commits after it were never
   reviewed), `stop_reason` is `skipped`, `verify-final` has not checked `reviewed_sha`, the `finalize`
   checkpoint did not end `ok`, or an error is set; then pick the status the message names. A second
   `mt done` is harmless.
5. Final report: final status, the branch and (worktree mode) `worktree_path`, which tree was reviewed
   (`reviewed_sha`) and the final HEAD, the `info/exclude` edit if one was made, the stash outcome of
   step 3 (the spare `make-task <run_id>` entry when `entry=` named one), skipped archive housekeeping,
   the stats, and the decisions report.

## Errors

| condition | mt call | question options | next |
|---|---|---|---|
| not a git repo | none (stop before `mt init`) | — | tell the user make-task needs git |
| profile check fails: Start step 0's `mt profile check` prints `ok: false`, or `mt init` exits 1 on the profile (errors, or a template) | none (no run) | — | stop with the errors and how to fix them: `/make-task --update` (the only fix for an older `schema`), or edit `<profile_dir>/profile.md` and the copies; a profile path that is not one plain shell word: set `MT_CONFIG_HOME` to a path without spaces or shell metacharacters (a `phases` path: rename the file or dir and the path); no `mt init` |
| another `mt profile` command fails at Start step 0 or in `references/profiles.md` | none (no run) | — | stop and report its message; no `mt init`. Except `another make-task command is changing …` (another session held the profile's lock longer than mt waits; nothing changed): run the same command once more first |
| revmux preflight fails at Start, or init's `warnings` name a review `requires` command missing on PATH | `mt assume "no revmux review" --why …` | asked during brainstorm: `noreview` run without review (recommended) / `stop` | review phase `--skipped`, or `mt done --status stopped` |
| `revdiff` missing | none (init reports it) | — | Changes button disabled |
| task fails after one retry | `mt fail` | `retry` retry the task / `stop` stop and keep the branch | retry → `mt activity` resumes; stop → `mt done --status stopped` |
| working dir dirty before round 1 | `mt fail` | `commit` commit the listed changes and continue / `stop` | commit them with a literal pathspec (`git --literal-pathspecs add -- <path> ...` and the same `commit`; never a path under `.revmux/`), `mt activity`; or stop as above |
| revmux exit 2, `mt review wait` prints `failed`, or degraded round | `mt fail` | `rerun` re-run the round / `skip` skip review and finalize (`review skipped`) / `stop` | rerun → `mt activity`, re-run the round; skip → `mt set stop_reason=skipped`, Finalize; stop as above |
| loop not converging or 5 rounds | `mt set stop_reason=<not-converging or round-cap>` | none here; the leftovers question names it | leftovers question |
| worktree branch: `EnterWorktree` or `git -C <wt> branch -m <name>` fails | `mt fail` | `stop` | `mt done --status stopped`; the report names the worktree and its `worktree-<name>` branch; never `branch -M` |
| in-place branch: `git -C <toplevel> checkout -b <name>` fails | `mt fail` | `stop` | `mt done --status stopped`; it runs before the `.revmux/` save and the stash push, so there is nothing to give back; never fall back to `create-branch.sh <plan>` or `git checkout <name>` |
| stash push fails (`push` and `left` not as in Exec step 1) | `mt fail` | `stop` | `mt done --status stopped`; when `after` is non-empty, the final report names that stash and `refs/make-task/<run_id>` (the edits are in it and may still be in the tree) |
| stash restore conflicts (`restore=conflict` or `restore=partial`) | `mt fail` | `keep` keep the stash and stop (user resolves) | `mt done --status stopped`; the stash entry and the private ref stay; `conflict` left the tree untouched, `partial` left part of the stash in it (Completion step 3 says what the report names) |
| setting `.revmux/` aside fails (Finalize step 2 `mt revmux aside` exits non-zero: `revmux_tree=differs`, `revmux_tree=unsafe` or `revmux_tree=failed`) | `mt fail` | `stop` | `mt done --status stopped`; never `git stash`, `--autostash` or `git checkout -- .revmux` by hand; the stop runs both Completion step 3 `.revmux/` steps (`mt revmux put-back`, then the index recipe), and the final report names `<state_dir>/revmux-tree/`, `<state_dir>/revmux-tree.new/` and `<state_dir>/revmux-tree.unsafe/` when they exist (each holds raw copies of the user's `.revmux/` files under `files/`) |
| unsubstituted placeholder in a prompt | `mt fail` | `stop` | `mt done --status stopped` |
| `agtermctl` fails in `mt init` | init sets `agterm: false` | — | run continues without dashboard |
| a workflow-state `mt` command fails | none | — | give the stash back (below), then stop and report; never continue without the journal |
| a UI-only `mt` failure | none | — | continue |

Error questions are the exception to the Question rule: they show at every autonomy level, during
brainstorm (the preflight question) and after it. A review the profile skips (`review_entry` null) is not
an error and asks no question: Start step 3 records the assumption and the review phase is skipped.
`mt fail "<msg>"` sets the error state and the sidebar status `blocked`; the run stays open, and the next
`mt phase` or `mt activity` clears the error. A row with one option (`stop`, `keep`) asks nothing: the run takes that
option and says why in its report (Questions).

**Every stop gives the stash back**, the error-question stops before `mt done --status stopped` and the
stops on a failing workflow-state `mt` command alike. `stash_restored` already set → the stash is back:
run the recipe with `''`. Otherwise take the sha from `stash_ref` in `mt status`; when `mt status` fails
too, or `stash_ref` is unset after a push ran, take it from the Recovery lookup of this run's stash (the
`stash-applied` marker keeps a stash already applied from being applied again). Run the Completion step 3
restore recipe with it (with `''` when no sha is found, so the staged deletions come back) and report the
outcome as Completion step 3 does (`entry=` included); when it is not `restore=ok` or `restore=none`, the
stop report names the stash sha and the paths in it
(`git -C <toplevel> stash show --include-untracked --name-only <sha>`). The exceptions are
the rows "stash push fails" (the push did not finish: run the recipe with `''`, which only stages the
recorded deletions again; the final report names that stash) and "stash
restore conflicts" (the stash stays for the user). Every stop, these two included, then runs both
Completion step 3 `.revmux/` steps in order (`mt revmux put-back` for the working copies, then the index
recipe), so the `.revmux/` edits set
aside at Finalize step 2 are back in the tree and the user's staged `.revmux/` changes are staged again.
Both print `none` when there is nothing to put back.

## Outside agterm

The flow is the same. `mt init` reports `agterm: false`: no dashboard and no sidebar status. Questions are
the same `AskUserQuestion`s (Questions). Artifacts are still rendered under `<state_dir>/artifacts/` for
opening by hand.

Outside agterm the run pointer is `current-default`, which two concurrent make-task runs share (accepted).
Pass `--run <state_dir>` before the command on every `mt` call after init (`mt --run <state_dir> refresh`)
so a second run cannot redirect this one; `mt status` prints `run` for recovery. `mt profile` takes no
`--run`: it reads the profile, never a run.
