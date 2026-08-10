# Tracker — none

There is no tracker. Tasks come from the user, in the session, one at a time. The sections
below are the same interface every tracker doc answers — this one answers most of them with
"you can't; do this instead".

**Status instruction:** `none-status.md`, beside this file.
**Preflight identity fragment:** `no tracker · tasks from you`.

## Install

Nothing to set up, no config keys.

## Read

There is no list. **Ask the user for the task**, and keep asking as each one finishes.

- Started with no task → ask for one before anything else. Don't invent work and don't go
  hunting for it in the repo.
- The task is what the user wrote plus what they answer when you ask. **Settle the open
  questions in chat before dispatching**: there is no thread for a worker to read, so anything
  left unasked becomes a guess made inside the worker.
- `--list` has nothing to select from. Say so and run normally.

## State

There is none, and this is the real cost of `none`: a task exists only inside this run. Track
its phase in your own run state and report it under `--status`.

**Tell the user up front that the bookkeeping does not survive a crash.** A worker's commits
and pushed branches do; which task was at which phase does not. Recovery is limited to what the
driver's `list` still shows.

Every task enters at the workflow's first enabled phase, unless the user says otherwise.

## Claim

Nothing to claim — you are the only orchestrator and the user is the only owner. Skip it.

## Write

Nowhere to comment. Deliverable reports, findings and summaries go **to the user in chat**;
review threads live in the delivery itself — the PR, or the branch — never on a task.

**`tasks-add` cannot run against this tracker.** There is nothing to file into: say so, and
offer to configure a real one.

## Verify a deliverable

The rule holds — a worker's claim is verified outside the channel — but the evidence is git and
the filesystem:

| Claim | Check |
|---|---|
| Work done | `git log` / `git diff` on the branch |
| Delivered | the branch exists and is pushed; the PR too, if the host has them |
| Tests green | run the project's `# Checks` commands yourself |
