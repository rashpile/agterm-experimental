---
name: tasks-add
description: Use when a task has just been discussed with the user and should become a Gitea issue — "add this task", "file this as an issue", "create a gitea task from this", "tasks add". Distills the discussion into an issue, then asks the user whether to mark it pending (frozen) or implement (ready for the tasks-loop).
---

# Tasks Add

## Overview

Companion to **tasks-loop**: turns a just-finished discussion into a Gitea issue with the
right lifecycle label. Runs in whatever session the discussion happened in — no
orchestrator needed. `tea` resolves the repo from the origin remote, so run it inside the
project repo.

## Flow

1. **Draft from the discussion.** Title: imperative, one line. Body: the outcome of the
   discussion, not a transcript — what to build/change, key decisions already made,
   constraints agreed, acceptance criteria if any surfaced. Anything the user decided
   during the discussion goes in the body so the implementing worker doesn't re-ask.
2. **Show the draft** (title + body) to the user in the same message as step 3 — they
   see what will be filed before choosing.
3. **Ask how to mark it** (AskUserQuestion):
   - `pending` — frozen; the loop skips it until the user removes the label.
   - `implement` — actionable; the next tasks-loop run dispatches a worker for it.
   - Other labels (`discuss`, `investigate`, `bug`+`implement`) are valid answers too if
     the user types them — apply whatever they choose.
4. **Create** with the chosen label in one step:
   `tea issues create --title "<title>" --description "<body>" --labels <label>`
   End the body with `_Filed from a discussion with the user._` — issues post under the
   user's account, so attribution lives in the body (same convention as tasks-loop).
5. **Report** the issue URL. If labeled `implement`, remind the user the loop is a queue
   drain, not a daemon — the issue is picked up on the next tasks-loop run.

## Rules

- One issue per invocation; if the discussion produced several distinct tasks, say so and
  ask which to file (or file them one at a time, each with its own label question).
- Never label `done` or add assignees — this skill only files work, it never advances the
  lifecycle. Label semantics are owned by tasks-loop.
- If the label the user wants doesn't exist in the repo yet, create it first
  (`tea labels create --name <label>`) rather than silently dropping it.
