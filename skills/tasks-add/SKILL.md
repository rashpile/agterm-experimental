---
name: tasks-add
description: Use when a discussion has settled into work that should be filed — "add this task", "file this as an issue", "create a task from this", "tasks add". Reads the project's .tasks-loop/config.md for where tasks live.
---

# Tasks Add

## Overview

Companion to **tasks-loop**: turns a just-finished discussion into a task in the project's
tracker. Runs in whatever session the discussion happened in — no orchestrator needed.

It knows nothing about any particular tracker. Where tasks live, and how one is created,
come from the project's config — the same source the loop reads.

## Startup

1. Read `./.tasks-loop/config.md`. **Missing** → tasks-loop is not configured here. Say so and
   offer to run its install (`/tasks-loop` does it on first use), or to file the task however
   the user tells you. Don't guess a tracker.
2. Resolve and read the `task-tracker` doc — a bare name is the tasks-loop skill's
   `task-trackers/<name>.md`, a path is resolved from the project root. Two of its sections
   matter here: **State** (what to file it as) and **Write** (how to create it). Skip
   `## Install`.

## Flow

1. **Draft from the discussion.** Title: imperative, one line. Body: the *outcome*, not a
   transcript — what to build or change, the decisions already made, the constraints agreed,
   acceptance criteria if any surfaced. Everything the user settled during the discussion goes
   in the body, so the worker that picks it up doesn't re-ask what was decided.
2. **Show the draft** — title and body — in the same message as the question below, so the
   user sees exactly what will be filed before choosing.
3. **Ask which state** (AskUserQuestion), options taken from the tracker doc's State table: its
   frozen state, and the states that enter the workflow. Describe each by what it *causes*, not
   by its name — "frozen, the loop skips it until you unfreeze" / "the next run dispatches a
   worker for it".
4. **Create it** the way the tracker doc's Write section says, in the chosen state. End the body
   with `_Filed from a discussion with the user._` — agents file under the user's account, so
   attribution belongs in the body.
5. **Report** where it landed. If it was filed actionable, remind the user the loop is a queue
   drain, not a daemon: it is picked up on the next tasks-loop run.

## Rules

- One task per invocation. If the discussion produced several distinct ones, say so and ask
  which to file — or file them one at a time, each with its own state question.
- **File work, never advance it.** Never file something as done or ship-ready, and never assign
  it. State transitions belong to the loop.
- A chosen state the tracker doesn't have yet gets created the way the tracker doc's Install
  section describes — never silently dropped.
