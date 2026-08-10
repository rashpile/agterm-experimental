# Execute — the plan-driven inner loop

Read this **only when phase 3 starts and the plan from phase 2 has task checkboxes.** A task
with no plan runs phase 3 as a single worker and none of this applies.

**The orchestrator runs this loop.** Workers never dispatch — see the workflow's phase table.

## Plan format

The loop reads the plan mechanically, so phase 2 has to write it that way:

- **`### Task N:`** headings (or `### Iteration N:`). `N` need not be an integer — `2.5` and
  `2a` are how a review round inserts a task without renumbering the rest.
- **Checkboxes only inside task sections.** A `- [ ]` in Overview or Success criteria reads as
  unfinished work and buys an extra iteration that implements nothing.
- **A `## Validation Commands` section** — what must pass before a task counts as done. These
  are the plan's own checks; the project's live in the config's `# Checks`, and an author runs
  both.

## State

**The plan's checkboxes are the state**, committed in the repo. Nothing else tracks progress —
no signals, no run file, no memory held in the orchestrator. Re-read the plan at the top of
every iteration, because the last author changed it. A restarted orchestrator resumes at the
first `[ ]` it finds.

## Review granularity

**The plan declares its own review points, and the default is the end.**

| The plan has | Review runs | Over |
|---|---|---|
| `## Stage` / `## Group` headings | at each group's last task | that group's diff |
| a flat task list | once, after the last task | the whole plan's diff |

The config's `## 3. Execute` may force **per task**, and wins over the plan.

**The gate that always runs per task is validation, not review** — correctness is caught
immediately, design is batched. Grouping belongs to the plan, not to this loop.

Whatever the level, **phase 4 still runs over the whole delivery afterwards.**

## The loop

Repeat until no `[ ]` remains in any task section:

1. **Re-read the plan.** Take the first task section with unchecked boxes. None left → phase 3
   is done. **Close the last author** and hand back to the workflow: phase 4's findings span
   every task, and the session that wrote the last one has no privileged knowledge of the
   rest — only the look of continuity.
2. **Record the range start** — `git rev-parse HEAD` on the delivery branch. The review is that
   SHA to HEAD, never wider.
3. **Announce it**: task number, title, and its unchecked items, in one block. The board should
   be readable without opening the plan.
4. **Spawn a fresh author** `task<N>-<slug>-<id>` in the task's worktree. Its prompt carries
   this task's items only, the config's `## 3. Execute` detail, and the checks to run **before**
   committing — the plan's `## Validation Commands` and the project's `# Checks`. It commits
   its own work and ticks its own boxes.
5. **Wait, then re-read the plan.** Boxes ticked → continue. Not ticked → **one retry** with a
   fresh session carrying what the last one left behind. Still not ticked → stop the loop and
   report. Never start the next task on top of a half-finished one.
6. **Review** — only if this task closes a review unit (see the table above). Spawn
   `review<N>-<slug>-<id>` on **its own worktree** of the delivery branch, running `review.md`
   over `<start-sha>...HEAD` with one internal leg. Two sessions never share a checkout.
7. **Fix** — findings go to the author, still parked and alive. It fixes what it confirms
   against the code and disputes the rest with evidence. **Max 2 inner rounds**; anything still
   unresolved is not an inner-loop problem — leave it for phase 4 and say so.
8. **Close both sessions**, then the next task.

**One task at a time. Never spawn two authors.** Every author edits the same plan file and
overlapping source; parallel authors corrupt both. A correctness rule, not a speed preference.

**Stop when**: no boxes remain · a task failed its retry · the iteration count exceeds the
plan's task count plus retries.

**The user may edit the plan between iterations.** Every iteration re-reads the file, so an
edited task takes effect on the next one, and unticking a finished task's boxes re-runs it. If
the user asks to change something mid-task, finish or stop the current author first; never edit
the plan under a running one.

## Rules

- **The orchestrator writes no code and reads no diff in order to fix one.** A task that left
  broken code gets a fresh session with the breakage in its prompt — never a repair by hand.
- **Never tick a checkbox yourself.** Authors tick their own; that is the only signal this loop
  reads. An orchestrator that ticks one has destroyed its own evidence.
- **A failing check is not a finished task.** Each author runs the project's checks before
  committing, so task N+1 never starts on a red tree. The review gate re-runs them over the
  whole delivery later; that is not a substitute for this.
- **Findings reach the author verbatim** — not summarized, filtered, or pre-judged. The author
  decides what is real, against the code.
- **Every author externalizes its reasoning before it closes.** The commit message says why this
  code is the way it is; a judgment call the plan did not settle goes back into the plan as a
  note. A session's memory dies with it, and the later fix cycle can only dispute a false
  positive with what was written down.
- A task that turns out to be wrong, already done, or impossible is **reported, not skipped
  silently**: say so, leave its boxes unticked, and let the user rule.
