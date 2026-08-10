# Workflow — standard

How **one task is worked**, start to finish. The tracker doc owns task state and says which
phase a task enters at; this doc owns the phases themselves.

**Review instruction:** `review.md`, beside this file. Pass review workers its resolved
absolute path.
**Execute instruction:** `exec.md`, beside this file — read by the orchestrator, and only when
phase 3 finds a plan with task checkboxes.

## Phases

| # | Phase | Who runs it | Done when |
|---|---|---|---|
| 1 | Brainstorm | task worker | the approach is settled |
| 2 | Plan | task worker, checked by a reviewer that did not write it, then the user | the plan is approved |
| 3 | Execute | task worker — with a plan, **one fresh worker per plan task** | the change is committed |
| 4 | Code Review | **separate review sessions**; the task worker answers as author | every finding is terminal |
| 5 | Push | task worker; the orchestrator merges | delivered, then merged |
| 6 | Deploy | ship worker | shipped and verified |

**This column is the dispatch rule, not a description.** The task worker runs 1, 2, 3 and 5.
Phase 4 is the orchestrator's to dispatch into its own session(s), and a task worker that
reaches it **stops and reports** instead of reviewing itself.

**Reviewing a plan and running the checks are not phases.** Plan review happens inside phase 2,
in a context that did not write the plan. The checks live in the config's `# Checks` section,
run by whoever needs them: the author before committing, the review gate, the ship worker before
merging.

**The project decides which phases it runs.** The config's `# Workflow` section lists them: a
phase with no heading there is skipped; a heading not in this table is a custom phase, run in
the position its number gives it. A heading's body is the project's detail for that phase — it
sharpens the description below, it does not replace it.

**The project decides which phases run; the worker decides whether an enabled phase applies to
this task.** Two conditions:

- **Declare it in advance, with the reason** — in the plan if there is one, in the delivery if not.
- **Only ever "does this phase apply?"** Never who runs it, never a gate.

**Never a judgment call**, because these exist precisely because judgment fails under pressure:
the author never reviews its own work · checks green before a merge · the ship approval · the
3-round dispute escalation · every Safety rule in the skill.

| Rationalization | Reality |
|---|---|
| "The diff is small, I'll review it myself" | Size was never the reason for the separation. |
| "A reviewer would find nothing here" | Then it costs one session and finds nothing. |
| "The user is watching anyway" | Watching is not approving. |
| "This project is different" | The config is where a project says how it differs. If it doesn't say so, it doesn't. |

**A phase body tunes HOW, never WHO.** It may name commands, conventions, depth, checkpoints —
never a different owner: `use skill X` under `## 4. Code Review` names what **the reviewer**
runs, it does not make the author its own reviewer. Apply it inside the owner the table gives,
and say in the plan how you read it.

**A phase body may set the model** — `model: opus:xhigh` on a line of its own, effort one of
`low` `medium` `high` `xhigh` `max`. No line → the driver's default; never guess. **A session
runs its own phase's model**: the inner loop's per-task reviewer takes phase 4's, not phase 3's.

## What each phase means

**1. Brainstorm** — settle the approach before any code: the real options and their trade-offs.
Skippable for trivial work (a typo, a one-liner with an obvious fix) — say so in the delivery
when skipped. When the project runs this **with the user**, the worker presents its
recommendation and the 2–3 real decisions, then waits in its session; tell the user which
session to join.

**2. Plan** — write the plan down where a human can read it, then get it reviewed and approved.
Three parts, and the phase is not done until all three are:

1. **Write it.** Where phase 3 runs the inner loop, the plan is also its state machine, so it
   must follow the format in `exec.md`: `### Task N:` sections, checkboxes **only** inside them,
   and a `## Validation Commands` section.
2. **Review it in a context that did not write it** — a subagent is enough, no session of its
   own. Then apply what survives.
3. **The user approves it**, before any code. `--auto` skips this third part — **never the
   second**.

**3. Execute** — implement the approved plan. A deviation from it gets named in the delivery,
not absorbed silently. **If the plan has task checkboxes this phase is a loop, not one worker**:
follow `exec.md` beside this file — a fresh author per task, reviewed per task or per group,
with the checkboxes as the only progress state. Read that doc only then; a task with no plan
runs this phase as a single worker.

**4. Code Review** — the legs below. The reviewer never fixes; the author never judges its own
verdict.

**5. Push** — deliver through the tracker's review mechanism. Never force-push, never push to
the default branch. **Merging is the orchestrator's**, and only after the ship approval.

**6. Deploy** — always project-specific. No `## 6. Deploy` in the config → merge and close, but
do not deploy, and report "deploy not configured".

## Phase 4 — review legs

Three of them; the config's `## 4. Code Review` says how deep this project goes.

| # | Leg | What |
|---|---|---|
| 1 | **internal** | the bundled agents (`.tasks-loop/agents/*.txt`) over the diff, one session, per `review.md` |
| 2 | **external** | a second model (codex) over the same diff. It cannot reach the tracker — read its output and post it yourself as "External review (codex)". Costs its own slot. |
| 3 | **final** | one pass over the finished state, carrying **the completion gate**. Always runs. |

| Depth | Runs |
|---|---|
| **full** | 1 + 2 + 3 — internal, external codex, final |
| **standard** | 1 + 3 — no external model |
| **simple** | 3 only — one pass and the gate |

**The orchestrator dispatches this phase — the author never does.** When the task worker reports
it has reached phase 4:

1. **Park the author.** Leave its session alive and idle; it is about to be the fixer.
2. **Spawn `review-<slug>-<id>`** on a worktree of the delivery branch and `say` it the resolved
   absolute path of `review.md`, plus which delivery to review. It reads `.tasks-loop/agents/*.txt`
   itself — never inline agent prompts into the message.
3. **At `full`, spawn the external leg in parallel**, as its own session.

Legs 1 and 2 run in parallel over the same diff. A task may ask for a different depth — the tracker
doc says how it expresses that, and the plan must say what was applied. **The gate is not
negotiable at any depth**, which is why leg 3 always runs.

**Then the fix cycle. The fixer is whoever has the knowledge**, which is not always the author:

- **One worker wrote the whole delivery** → that session, still parked and alive.
- **A plan's inner loop wrote it** → close the last task's author and spawn a **fresh fixer**: it
  knows only its own task, and partial context produces confident wrong disputes.

Either way: "Findings are on the thread. Verify each against the code: fix confirmed ones and
push; dispute false positives as replies with evidence. No blind obedience." **Evidence is the
code, the plan and the thread — never recall.** The reviewer then answers every dispute in-thread
(WITHDRAW / MAINTAIN / COMPROMISE) and reviews only the new commits. **Max 3 rounds** — still unresolved, stop and
hand the thread to the user; agents don't make that call.

**Gate**, decided inside the reviewer's final pass and not by the orchestrator: every finding
terminal (fixed, withdrawn, or dismissed with rationale) **and** the project's checks green →
`DONE NOTHING-BLOCKING gate=pass`.

## Worker prompt

Write it to a file and `say --file`:

```
Work on <task ref>: "<title>".

<the task body verbatim>

<the comment thread, each entry attributed (author, date). Comments refine the body: where
they conflict, the newest user comment wins. Omit if there is none.>

Rules:
- You are in <cwd>. Follow the project's CLAUDE.md.
- Phases you run: <the enabled task-worker phases, from <entry> onward — 1, 2, 3, 5 — each
  with the project's detail from its config section>.
- Phases you do NOT run: <any enabled phase the table gives to someone else>. Stop at each and
  report WAITING. **Phase 4 especially: you never review your own work.** A separate session
  reviews it and posts findings; you answer them as the author — fix what you confirm against
  the code, dispute what you don't, with evidence. Never run a review skill or review agents
  on your own diff.
- Report WAITING plus what you need at any human checkpoint; do not proceed past it.
- If you file a new task, freeze it unless the user chose otherwise, and end its body with
  "_Filed by agent while working on <task ref>._" — agents post under the user's account.
- Your final message is DONE plus the deliverable's link, or WAITING plus the question.
```

One worktree per task — `git worktree add .worktrees/<id> -b issue/<id>-<slug>`, `.worktrees/`
gitignored so ripgrep skips it. A review leg gets a worktree on the **existing** branch. A
read-only task runs in the main checkout.

**Remote mode** — if the user asks for "remote", or says they will answer from their phone:
once the worker has presented its context, `say <session> "/rc worker:<session-name>"`, then
tell them. **Always label it with the session name, never a bare `/rc`** — otherwise the
sessions are indistinguishable on the phone. Claude-specific; skip it for other agents.

## Ship flow — phase 6, end of run

Collect every task the tracker reports as ship-ready and dispatch **one** ship worker (session
`ship-<id>[-<id>…]`, main checkout, takes a slot): for each delivery in order verify the checks
are green and squash-merge; after **all** merges, wait for the final artifact build, run the
config's `## 6. Deploy` **once**, verify it, close each task, report what shipped.

**Clean up each merged delivery — the branch, not just the worktree.** A squash-merge leaves the
branch un-merged by ancestry, so `git branch -d` refuses it. Verify by content, then remove:

```bash
CUT=$(git merge-base <merge-commit>^ <branch-tip>)                       # where the branch was cut
git diff <branch-tip> <merge-commit> -- $(git diff --name-only $CUT <branch-tip>)
git worktree remove .worktrees/<id>
git branch -D <branch>                    # only once that diff came back empty
git push origin --delete <branch>         # the merge authorized it; a protected branch refuses — say so and move on
```

**Scoped to the branch's own files on purpose.** A bare `git diff <branch-tip> <merge-commit>`
compares whole trees, so it reports everything *main* did since the branch was cut — which in a
batched run is always something, and reads as a lost commit on a perfectly clean merge.

**Non-empty → read it before deleting anything.** Either the branch has work main never got, or
another delivery changed the same file after the cut. Only the first is a lost commit; if it is,
keep the worktree, the branch and the session, and report it.

`--keep` skips all of it — worktree, branch and session stay for inspection.

**Batched merges go stale.** Each merge can conflict the next and invalidate the verdict it was
cleared on (`mergeable: false`, or a 405 on merge — that is this, not a flaky server).
Mechanical conflicts: merge the default branch in (never force-push), re-run checks, push,
re-verify. A **semantic** collision — feature versus deletion, both sides changing behavior —
is BLOCKED: put the call to the user. A conflict resolution is unreviewed code landing after
the gate.

## Gates `--auto` may not skip

`--auto` skips the human checkpoints the project's phase sections define. It never skips: the
ship approval, checks-green before a merge, the 3-round dispute escalation, or a semantic
merge conflict.
