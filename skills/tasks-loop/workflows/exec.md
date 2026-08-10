# Execute — the plan-driven inner loop

Read this **only when phase 4 starts and the plan from phase 2 has task checkboxes.** A task
with no plan runs phase 4 as a single worker and none of this applies.

**The orchestrator runs this loop.** Workers never dispatch — see the workflow's phase table.

## Why the author restarts

A plan's tasks are small and ordered. One long-lived worker accumulates every earlier task's
context and ends up reviewing its own work at a size where honest review is impossible. A fresh
session per task keeps each diff small enough to review and each reviewer's job narrow. The
plan file carries everything a fresh session needs.

## State

**The plan's checkboxes are the state**, committed in the repo. Nothing else tracks progress —
no signals, no run file, no memory held in the orchestrator. Re-read the plan at the top of
every iteration, because the last author changed it. A restarted orchestrator resumes at the
first `[ ]` it finds.

## Review granularity

| Level | Review runs | Over |
|---|---|---|
| **per task** (default) | after every task | that task's diff |
| **per group** | after the last task of a group | the group's diff |
| **at the end** | once, after all tasks | the whole plan's diff |

A group is whatever the plan says it is — the tasks under one `## Stage` / `## Group` heading.
The config's `## 4. Execute` may set the level outright and wins over the plan.

Whatever the level, **phase 5 still runs over the whole delivery afterwards.** The inner loop
catches what a small diff shows; phase 5 catches what only appears across tasks.

## The loop

Repeat until no `[ ]` remains in any task section:

1. **Re-read the plan.** Take the first task section with unchecked boxes. None left → phase 4
   is done; hand back to the workflow.
2. **Record the range start** — `git rev-parse HEAD` on the delivery branch. The review is that
   SHA to HEAD, never wider.
3. **Announce it**: task number, title, and its unchecked items, in one block. The board should
   be readable without opening the plan.
4. **Spawn a fresh author** `task<N>-<slug>-<id>` in the task's worktree. Its prompt carries
   this task's items only, the config's `## 4. Execute` detail, and the project's checks from
   `## 6. Test` to run **before** committing. It commits its own work and ticks its own boxes.
5. **Wait, then re-read the plan.** Boxes ticked → continue. Not ticked → **one retry** with a
   fresh session carrying what the last one left behind. Still not ticked → stop the loop and
   report. Never start the next task on top of a half-finished one.
6. **Review** — only if this task closes a review unit (see the table above). Spawn
   `review<N>-<slug>-<id>` on **its own worktree** of the delivery branch, running `review.md`
   over `<start-sha>...HEAD` with one internal leg. Two sessions never share a checkout.
7. **Fix** — findings go to the author, still parked and alive. It fixes what it confirms
   against the code and disputes the rest with evidence. **Max 2 inner rounds**; anything still
   unresolved is not an inner-loop problem — leave it for phase 5 and say so.
8. **Close both sessions**, then the next task.

**One task at a time. Never spawn two authors.** Plan tasks are ordered and interdependent:
later ones build on files earlier ones create, and every author edits the same plan file and
overlapping source. Parallel authors corrupt both. This is a correctness rule, not a speed
preference.

**Stop when**: no boxes remain · a task failed its retry · the iteration count exceeds the
plan's task count plus retries. A loop that outlives its own plan is a bug, not progress.

## Rules

- **The orchestrator writes no code and reads no diff in order to fix one.** A task that left
  broken code gets a fresh session with the breakage in its prompt — never a repair by hand.
- **Never tick a checkbox yourself.** Authors tick their own; that is the only signal this loop
  reads. An orchestrator that ticks one has destroyed its own evidence.
- **A failing check is not a finished task.** Each author runs the project's checks before
  committing, so task N+1 never starts on a red tree. Phase 6 is the final pass over the whole
  delivery, not a substitute for this.
- **Findings reach the author verbatim** — not summarized, filtered, or pre-judged. The author
  decides what is real, against the code.
- A task that turns out to be wrong, already done, or impossible is **reported, not skipped
  silently**: say so, leave its boxes unticked, and let the user rule.
