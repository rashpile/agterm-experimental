# Config template — `.tasks-loop/config.md`

The project's tasks-loop file. Copy it in, fill it, commit it.

```markdown
---
task-tracker: gitea          # bundled name, or a path to the project's own doc
task-workflow: standard      # bundled name, or a path
agent-driver: bash-claude-agterm
agent-driver-cmd: ~/.config/agent-driver/bash-claude-agterm/claude-agterm
slots: 2
---

# Workflow

The phases this project runs, in order. **A phase with no heading here is skipped.** A heading
not in the workflow doc is a custom phase, run in the position its number gives it. Each body
is this project's detail — it sharpens the workflow's description, it does not replace it.

## 1. Brainstorm
Settle the approach first. Skip for typos and obvious one-liners; say so in the delivery.

## 2. Plan
model: opus:xhigh
Write the plan to `docs/plans/` with `### Task N:` sections and a `## Validation Commands`
section; move it to `docs/plans/completed/` in the final commit. A context that did not write it
reviews it, then the user approves — commit the plan, open the delivery early (plan only),
report WAITING with its URL and hold. `--auto` skips the user's approval, never the review.

## 3. Execute
model: sonnet:high
Implement the approved plan. Name any deviation from it — in the commit and back in the plan.
Inner-loop review follows the plan's own stage boundaries, or runs at the end. Write
`per task` here to force a review after every task; expect roughly triple the wall time.

## 4. Code Review
model: opus:high
full          # full (1 internal + 2 external codex + 3 final) · standard (1 + 3) · simple (3 only)

## 5. Push
Delivery only — the orchestrator merges after the ship approval.

## 6. Deploy
Always project-specific. Omit this heading entirely and the loop merges and closes without
deploying, and says so.

# Checks

What must pass before any change counts as done. Not a phase — a predicate the author runs
before committing, the review gate requires, and the ship worker re-verifies before merging.
A plan may add its own in `## Validation Commands`; both run.

`cd app && npx tsc --noEmit; npm run lint; npx vitest run`

# Notes

Anything else a worker or the orchestrator needs: repo quirks, `.worktrees/` already
gitignored, conventions.
```

**Name vs path.** A bare `gitea` / `standard` resolves inside the skill — the project rides
skill updates and stores nothing. A value with a `/` or a `.md` is a path from the project
root: the escape hatch for a project that needs its own or a frozen copy.

**`model: <model>[:effort]`** on a line of its own in any phase body sets that phase's model —
effort is `low` `medium` `high` `xhigh` `max`. Omit it to inherit the driver's default. It only
takes effect if the driver's contract documents a model option; the run says so if it does not.

**Write `agent-driver-cmd` with `~`**, not an expanded home — `~/.config/agent-driver/…` is the
default install location, so the tilde form reads correctly on any machine. A bare command on
`PATH` works too.

**Resolved centrally, copied locally.** Tracker and workflow docs are read where they live, so
a fix reaches every project. Review agent prompts are **copied** into `.tasks-loop/agents/` at
install — they are meant to be tuned per project.
