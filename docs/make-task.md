# make-task

One development task, from idea to a reviewed branch, in one Claude Code session.

```
/make-task [--profile=<name>] [--update] [task text]
```

It runs brainstorm, plan, plan review, task-by-task execution, a multi-agent review loop and finalize.
You answer a few questions at the start and come back to a branch with a final status on it.

---

## The problem

The pieces exist as separate skills: brainstorm, planning, plan execution, revmux review. Run them by
hand and you are the glue:

- you start each phase and carry its output into the next one
- each skill asks its own questions at its own time, so you stay at the keyboard for the whole run
- the review loop (review, fix, review again) is yours to drive and to stop
- after an auto-compact or a crash you work out where things were

make-task is the orchestrator for that chain. The session that runs it writes no code. It drives the
phases, hands the work to subagents and keeps a journal of every step.

---

## Five phases

```mermaid
flowchart LR
    B["Brainstorm<br/>design.md"] --> P["Plan<br/>docs/plans/*.md"]
    P --> PR["Plan review<br/>read-only agent"]
    PR --> E["Execute<br/>one subagent per task"]
    E --> R["Review<br/>revmux rounds + fixer"]
    R --> F["Finalize<br/>rebase, squash, verify"]
```

| phase | who does the work | output |
|---|---|---|
| Brainstorm | the orchestrator, following the brainstorm companion | `design.md` |
| Plan | the orchestrator with the planning companion; a read-only `Plan` agent then reviews it | a plan in `docs/plans/` with `### Task N` sections and checkboxes |
| Execute | one `general-purpose` subagent per task, one at a time | commits on the feature branch, a progress log |
| Review | revmux (claude and codex reviewers in parallel), then a fixer subagent | review rounds, fix commits |
| Finalize | a finalizer subagent, then a stats subagent | rebased branch, final status, stats |

---

## What it looks like

![make-task dashboard in agterm](images/make-task-dashboard.png)

On the left is the Claude session running make-task. On the right is the dashboard make-task opens in
an agterm split. This run is issue #64 of a browser extension, about 30 minutes in, executing the first
of five tasks.

---

## Reading the dashboard

- **Header.** Task title, repo, branch, worktree or in place, the profile and its autonomy level.
- **Artifact buttons.** Design, Plan, Progress log, Review, Stats, Changes. Each shows how long ago it
  changed. Design to Stats open the viewer (below) on that tab over the session. Changes runs `revdiff`
  from the commit where the task's work starts, so you can read the task's commits while it runs. A
  button stays grey until its artifact exists (Review and Stats in the screenshot). While a review round
  runs, Review shows `● live`.
- **Now strip.** What the orchestrator is doing right now, and the run's total clock (29m 33s).
- **Phase rail.** Each phase with its duration: Brainstorm 8m 28s, Plan 13m 28s. Execute lists the
  plan's tasks with checkbox progress (`0/6`) and a live timer on the running one. During review, a
  compact block under the running round shows its stage, the findings so far, how many reviewers are
  done and the latest activity, with a link to the live Review tab.
- **Assumed for you, You decided.** Every choice the run made on its own, with its reason, and every
  answer you gave. Here it is 23 assumptions and 0 questions: under the `default` autonomy, brainstorm
  found nothing in this task that needed the user.
- **Timeline.** The last seven events, below what the screenshot shows.
- **Footer.** The run's state directory and two buttons, also below the screenshot. **Agent** moves
  the keyboard focus to the pane the agent runs in. It turns yellow when the agent has stopped and red
  while it asks you something: a permission prompt, or a make-task question waiting for your answer.
  The colour follows the session's agterm sidebar status. **Close** closes the dashboard, and the split
  pane with it when make-task opened that split for the dashboard. A split you had before stays.

The dashboard is an HTML page that reloads a `state.js` file every 1.5 s. `scripts/mt` writes that file,
and it is the only part of make-task that talks to agterm.

---

## The viewer

The artifact buttons open one page, the viewer, with five tabs:

| tab | shows |
|---|---|
| Design | `design.md` with its table of contents |
| Plan | the plan's tasks with status, checkbox progress and duration. The running task's checklist is open, every other one opens on a click, and the plan document sits behind "Show the plan document" |
| Progress | the progress log as a timeline, newest entry first, with decisions, deviations and review lines marked and a filter for each |
| Review | the review rounds: the running one live, a finished one with its verdict and findings |
| Stats | time, tokens and tool calls, once the run has ended |

The viewer reloads every 1.5 s and redraws a tab only when its data changed, so your scroll position and
what you expanded stay. A tab whose artifact does not exist yet says "not yet". The viewer only reads
files `scripts/mt` writes: closing it never affects the run, also in the middle of a review round.

<img src="images/make-task-viewer-review.png" width="640" alt="The viewer's Review tab with round 1 picked">

The Review tab of the run that built go-ask's `recap` tool, opened during finalize, so Stats is still
empty. Each round has a button with its verdict: round 1 found one major and two minors, rounds 2 and 3
came back clean. With round 1 picked, the strip shows its verdict and how long it took, and the list
shows its findings as that round recorded them. The major is still `unresolved` there, because its fix
landed between rounds 1 and 2. The minors are grouped under their count. Each finding opens to its body
and the suggested fix.

---

## Who gets asked what

Brainstorm sorts every question into three classes:

1. Trivial. The orchestrator decides and records an assumption.
2. A recommendation a reasonable engineer would accept. Also decided and recorded.
3. Needs your knowledge or taste. Asked, one question at a time.

The profile's autonomy level picks which classes reach you:

| autonomy | asked during brainstorm | design | gates |
|---|---|---|---|
| `autonomous` | nothing | written once | none |
| `default` | class 3 | written once | none |
| `interactive` | classes 2 and 3 | approved section by section | design, plan, each review round |

After brainstorm nothing is asked at any level, apart from the gates, the leftovers question at the end
and the error questions. Every other judgment call becomes an assumption with a reason, and the dashboard
lists all of them. Under `autonomous` the worktree and leftovers questions are answered from the profile's
`defaults`.

---

## Questions

Every question is an `AskUserQuestion` in the terminal, inside agterm or outside it, also in a remote
session:

- 2 to 4 options, the recommended one first and marked "(Recommended)", each described by what it gets you
  and what it costs
- a free-text answer through "Other"
- a short context in the question itself. Long material (the design, the plan, the review's findings) is
  named by its artifact, which the viewer shows

While a question waits, the dashboard's Now strip says so. Your answer is recorded with the run's
decisions.

---

## Execute: one subagent per task

The plan is a checklist, and the loop works through it:

1. Find the first `### Task N` with an unchecked box.
2. Start one subagent with the task prompt and wait for it.
3. Re-read the plan. All boxes checked, go to the next task. Not checked, retry once with a fresh agent
   and the error appended. A second failure asks an error question (retry or stop).

Only the checkboxes count. The orchestrator ignores what the agent says it did, and it never fixes or
debugs code itself.

Every subagent gets the same rules appended to its prompt:

- ask the user nothing, take judgment calls from the project's CLAUDE.md, lint rules and conventions
- log each judgment call in the progress file as a `[decision]` or `[deviation]` line
- commit only the paths you changed, with an explicit pathspec, never `git add -A` or `git stash`
- never rewrite a commit from before the task started, never push

The `[decision]` and `[deviation]` lines end up in the final report.

---

## Review: the revmux loop

Each round:

1. revmux runs claude and codex reviewers in parallel over the task's commits.
2. `critical` and `major` findings gate the round. A finding outside the change's scope is recorded as
   not gating.
3. If gating findings are left, a fixer subagent fixes them and the next round starts.

The fixer gets three extra rules. Sweep the repo for the same construct, not only the reported line.
List every case the fix touches and write tests that tell them apart. Re-read the finding against the
fix before committing.

The loop stops when a round is clean, when the gating count stops dropping and a finding repeats, or
after five rounds. Whatever is still open goes to the leftovers question. Open majors can get one fixer
round and a confirming round with revmux's `final` profile, or stay, and then the run ends `not clean`.
Minors left on their own do not stop a run from ending `clean`.

---

## Review in progress

<img src="images/make-task-review.png" width="640" alt="Dashboard during review round 2">

All five tasks are done. Round 1 found one critical and two major findings, a fixer subagent fixed
them, and round 2 is in revmux's verify stage with 3 of 4 reviewers finished. The Stats button stays
dashed until the run ends.

The viewer's Review tab follows the running round as revmux works: its stages, each reviewer with its
state and latest activity, and the findings so far by severity, each one opening to its body and fix.
`mt review wait` keeps the tab current from `events.jsonl`, the event log revmux writes for the round.
After 30 s without an event the tab marks its data stale. Once the round has a result, the tab shows its
verdict and the final findings, each gating one marked unresolved, not gating or deferred as the round
recorded it. A round that ended without a result says so. If a later round or the end of the run cut it
off, the tab also shows the last stage it reached. A round whose revmux process ended without a report
shows no stage.

---

## The leftovers question

When the loop ends with findings open, the run asks once more before finalize. With open majors the
options read "Fix the remaining majors" and "Leave them and finalize" (the run then ends `not clean`).
With only minors or open questions left they read "Fix the listed minors" and "Leave them", and typed
text answers the open questions or narrows the list. The findings themselves are in the viewer's
Review tab.

---

## Finalize and the final status

A finalizer subagent rebases onto `origin/<default>`, squashes the task's commits and runs the tests.
`mt review verify-final` then checks that HEAD holds exactly the reviewed content, replayed onto the new
base. A stats subagent reads the session logs and reports time, tokens and tool calls per task, fixer
and finalizer.

The run ends with one of five statuses:

| status | meaning |
|---|---|
| `clean` | no open findings, the last round reviewed the final content, rebase and tests ok |
| `not clean` | findings left open, a degraded last round, commits after the last round, or a failed rebase or test run |
| `review stale` | the finalize step changed what the review saw |
| `review skipped` | no review ran: the profile skips it, or revmux was unavailable |
| `stopped` | the run stopped on an error question |

`mt done` refuses `clean` unless the journal backs it, so the orchestrator cannot claim it on its own
word.

---

## A finished run

<img src="images/make-task-done.png" width="560" alt="Dashboard of a run that finished clean">

Three review rounds and 1h 20m in total. The user answered three questions, and the run took six decisions
on its own, each with its reason. After the run the artifact buttons show the time each artifact last
changed, and Stats is filled in. The timeline ends with the `verify-final` check and the final status.

---

## Your working tree stays yours

The last brainstorm question asks where the run happens:

- **worktree.** A new branch under `.claude/worktrees/`. Your checkout is not touched.
- **in place.** The feature branch is created in your checkout. Your uncommitted edits are stashed
  before execution and restored at the end.

In place, the stash is guarded at every step:

- the stash commit gets a private ref, `refs/make-task/<run_id>`, so `git gc` cannot drop it
- the restore applies by sha and never drops a stash entry, so a stash from another terminal is safe
- before applying, a trial merge checks for conflicts. On a conflict nothing is applied, and the report
  gives the command to run by hand
- every stop, errors included, gives the stash back

Started inside a linked worktree, the run skips the question and stays in place. That is the case in the
screenshot (`mode inplace` is the first assumption). make-task never pushes.

---

## Recovery after a compact or a crash

`scripts/mt` keeps a journal in the run's state directory
(`~/.local/state/make-task/<repo>-<slug>-<date>/`): the phase, a checkpoint per task and per fix,
every review round, every assumption and answer.

After an auto-compact, a crash or a long wait, the orchestrator runs `mt status`, re-reads the part of
`SKILL.md` for the printed phase and reconciles:

- a task checkpoint begun but not ended: re-read the plan's checkboxes, rerun the task if they are open
- a review round started without a result: check the revmux process and its report
- a question already answered: not asked again

---

## Profiles

A profile is a directory, `~/.config/make-task/profiles/<name>/`:

```
profile.md        autonomy, gates, defaults, the companion for each phase, ## <phase> sections
UPDATE.md         where each copy came from, its version, your own edits to the copies
companions/<id>/  the skill copies a run reads
upstream/<id>/    each source as last imported, the merge base for updates
data/<id>/        prompt and rule overrides
```

The `default` profile ships copies of Umputun's brainstorm and planning plugins and of the revmux skill,
with make-task's edits in them: no menus, no questions outside the three classes, a fixed review scope.
A run reads these copies, never the installed plugins, so a plugin update cannot change a run under you.

Your own instructions for a phase go into `profile.md` as `## brainstorm`, `## plan`, `## exec` or
`## review`. The order of precedence is make-task's own rules, then your section, then the companion's
text. Neither of the last two can relax a safety rule.

`/make-task --update` diffs the copies against their sources, merges upstream changes with your edits
and offers an interview to adjust the workflow.

---

## Requirements

Every run needs:

| tool | what uses it |
|---|---|
| Claude Code | runs the orchestrator and its subagents. The plan review uses the built-in `Plan` agent type; a Claude Code without it skips the plan review and records that |
| git | the branch, the commits, the stash and the review range. Outside a git repo make-task stops before it starts |
| [`uv`](https://docs.astral.sh/uv/) | runs `scripts/mt` (`uv run -q --script`) and installs its Python dependencies, `markdown-it-py`, `mdit-py-plugins` and `pyyaml` |

The review needs:

| tool | what uses it |
|---|---|
| [`revmux`](https://github.com/umputun/revmux) | runs the review rounds. Install with `brew install umputun/apps/revmux` |
| `jq` | the revmux preflight reads `revmux config` with it |
| `claude`, `codex` | the reviewers revmux starts, as its review profile names them |

When the preflight fails, a question during brainstorm offers to run without review or to stop. A run
without review ends `review skipped`, never `clean`.

The brainstorm, planning and revmux plugins are not needed. The profile carries its own copies of them.

Optional:

| tool | adds | without it |
|---|---|---|
| [agterm](https://agterm.com) | the dashboard, the viewer, sidebar status | no dashboard or viewer; artifacts are still rendered in the state directory |
| [`revdiff`](https://github.com/umputun/revdiff) | the Changes button | the button stays disabled |
| `gh` | files an issue proposal from the `--update` interview | no proposal is filed |
| network access | `--update` checks a companion with a `git:` source | that source is not checked |

---

## Getting started

Link the skill into Claude Code:

```
ln -s <repo>/skills/make-task ~/.claude/skills/make-task
```

The first run installs the `default` profile and asks one question, the autonomy level:

```
/make-task add a --json flag to the export command
```

Other forms:

```
/make-task --profile=<name> <task>   run with another profile (a new name installs one)
/make-task --update                  check the profile's copies against their sources
```

---

## Read more

- [`skills/make-task/SKILL.md`](../skills/make-task/SKILL.md), the orchestrator's rulebook
- [`skills/make-task/README.md`](../skills/make-task/README.md), usage, requirements, what it changes on disk
- [`skills/make-task/references/profiles.md`](../skills/make-task/references/profiles.md), profile setup, updates and the interview
- [`skills/make-task/CLAUDE.md`](../skills/make-task/CLAUDE.md), notes and tests for editing the skill
