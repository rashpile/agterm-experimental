# Workflow — standard

How **one task is worked**, start to finish. The tracker doc owns task state and says which
phase a task enters at; this doc owns the phases themselves.

**Review instruction:** `review.md`, beside this file. Pass review workers its resolved
absolute path.
**Execute instruction:** `exec.md`, beside this file — read by the orchestrator, and only when
phase 4 finds a plan with task checkboxes.

## Phases

| # | Phase | Who runs it | Done when |
|---|---|---|---|
| 1 | Brainstorm | task worker | the approach is settled |
| 2 | Plan | task worker | a written plan exists |
| 3 | Plan Review | a reviewer that did not write the plan, then the user | the plan is approved |
| 4 | Execute | task worker — with a plan, **one fresh worker per plan task** | the change is committed |
| 5 | Code Review | **separate review sessions**; the task worker answers as author | every finding is terminal |
| 6 | Test | task worker | the project's checks pass |
| 7 | Push | task worker; the orchestrator merges | delivered, then merged |
| 8 | Deploy | ship worker | shipped and verified |

**This column is the dispatch rule, not a description.** The task worker runs 1, 2, 4, 6 and 7.
Phase 5 is the orchestrator's to dispatch into its own session(s), and a task worker that
reaches it **stops and reports** instead of reviewing itself.

**The project decides which phases it runs.** The config's `# Workflow` section lists them: a
phase with no heading there is skipped; a heading not in this table is a custom phase, run in
the position its number gives it. A heading's body is the project's detail for that phase — it
sharpens the description below, it does not replace it.

**A phase body tunes HOW, never WHO.** It may name the commands, the conventions, the depth, the
checkpoints. It may not hand a phase to a different owner: `use skill X` under
`## 5. Code Review` does not make the author its own reviewer — it names what **the reviewer**
should run. A body that reads as reassigning a phase is detail written loosely, not a dispatch
instruction. Apply it inside the owner the table gives, and say in the plan how you read it.

## What each phase means

**1. Brainstorm** — settle the approach before any code: the real options and their trade-offs.
Skippable for trivial work (a typo, a one-liner with an obvious fix) — say so in the delivery
when skipped. When the project runs this **with the user**, the worker presents its
recommendation and the 2–3 real decisions, then waits in its session; tell the user which
session to join.

**2. Plan** — write the plan down where a human can read it. It is what phase 3 reviews and what
the work is checked against later. Where phase 4 runs the inner loop, the plan is also its state
machine, so it must follow the format in `exec.md`: `### Task N:` sections, checkboxes **only**
inside them, and a `## Validation Commands` section.

**3. Plan Review** — a separate session reviews the plan against the task; never the session
that wrote it. Then the human checkpoint: the plan reaches the user before any code.
`--auto` skips the human half, not the review half.

**4. Execute** — implement the approved plan. A deviation from it gets named in the delivery,
not absorbed silently. **If the plan has task checkboxes this phase is a loop, not one worker**:
follow `exec.md` beside this file — a fresh author per task, reviewed per task or per group,
with the checkboxes as the only progress state. Read that doc only then; a task with no plan
runs this phase as a single worker.

**5. Code Review** — the legs below. The reviewer never fixes; the author never judges its own
verdict.

**6. Test** — run the project's own checks (its config section names them). Red is not a phase
outcome; it is a return to phase 4.

**7. Push** — deliver through the tracker's review mechanism. Never force-push, never push to
the default branch. **Merging is the orchestrator's**, and only after the ship approval.

**8. Deploy** — always project-specific. No `## 8. Deploy` in the config → merge and close, but
do not deploy, and report "deploy not configured".

## Phase 5 — review phases

Three of them; the config's `## 5. Code Review` says how deep this project goes.

| # | Review phase | What |
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
it has reached phase 5:

1. **Park the author.** Leave its session alive and idle; it is about to be the fixer.
2. **Spawn `review-<slug>-<id>`** on a worktree of the delivery branch and `say` it the resolved
   absolute path of `review.md`, plus which delivery to review. It reads `.tasks-loop/agents/*.txt`
   itself — never inline agent prompts into the message.
3. **At `full`, spawn the external leg in parallel**, as its own session.

1 and 2 run in parallel over the same diff. A task may ask for a different depth — the tracker
doc says how it expresses that, and the plan must say what was applied. **The gate is not
negotiable at any depth**, which is why phase 3 always runs.

**Then the fix cycle. The fixer is whoever has the knowledge** — which is not always the author:

- **One worker wrote the whole delivery** → that session, still parked and alive. It is a real
  advocate: it knows why the code is the way it is.
- **A plan's inner loop wrote it** → a **fresh fixer**. The last task's author has no privileged
  knowledge of the other tasks, only the look of continuity, and partial context produces
  confident wrong disputes. Close it; spawn a reader with a clean slate.

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
- Phases you run: <the enabled task-worker phases, from <entry> onward — 1, 2, 4, 6, 7 — each
  with the project's detail from its config section>.
- Phases you do NOT run: <any enabled phase the table gives to someone else>. Stop at each and
  report WAITING. **Phase 5 especially: you never review your own work.** A separate session
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

## Ship flow — phase 8, end of run

Delegated like everything else: merges, image builds and deploys are minutes of wall time that
would stop the orchestrator answering the user.

Collect every task the tracker reports as ship-ready and dispatch **one** ship worker (session
`ship-<id>[-<id>…]`, main checkout, takes a slot): for each delivery in order verify the checks
are green and squash-merge; after **all** merges, wait for the final artifact build, run the
config's `## 8. Deploy` **once**, verify it, close each task, report what shipped.

**Clean up each merged delivery — the branch, not just the worktree.** A squash-merge leaves the
branch behind and **not** an ancestor of the default branch, so `git branch --merged` will not
list it and `git branch -d` will refuse it. Verify by content instead, then remove:

```bash
git diff <branch-tip> <merge-commit>      # empty = the squash captured the branch exactly
git worktree remove .worktrees/<id>
git branch -D <branch>                    # only once that diff came back empty
git push origin --delete <branch>         # the merge authorized it; a protected branch refuses — say so and move on
```

**A non-empty diff means something on that branch did not land. Stop: keep the worktree, the
branch and the session, and report it.** That is a lost commit, not a cleanup problem.

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
