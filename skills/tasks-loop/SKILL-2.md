---
name: tasks-loop
description: Use when asked to work through the Gitea issue backlog autonomously — "run the tasks loop", "tasks-loop", "process open issues", "dispatch agents on the issues", "work the backlog". Requires running inside agterm, in a git repo with a Gitea remote.
argument-hint: "[--help] [--slots 1-5] [--keep]"
---

# Tasks Loop

## Overview

Use task tracker to work on tasks.
The orchestrator (you) supervises workers, answers their prompts, and
updates issue labels as work completes.

## Requirement

Config md file in project.

Config defines:
Task Tracker - Issue tracker
Agent Driver - tool to manage workers
Workflow - Task workflow

Important: Run install if project not contains config file ./.tasks-loop/config.md, then follow instruction in references/install.md


## Config
### Parameters
Tracker instruction
 task-tracker: .tasks-loop/tracker.md
 task-status: .tasks-loop/tracker-status.md
Agent Driver name
 agent-driver: bash-claude-agterm
Agent Driver command, run with skill parameter to read how to use it
 agent-driver-cmd: /Users/pkoptilin/workspace/repos/agent-driver/bash-claude-agterm/claude-agterm skill
Workflow instruction, how to move task, task phases
task-workflow: .tasks-loop/workflow.md


## Arguments

`/tasks-loop [--slots N] [--keep] [--auto] [--list]`

| Parameter | Meaning | Default |
|---|---|---|
| `--slots 1..5` | Max **busy** worker sessions at once. Idle-alive sessions (parked authors awaiting review findings, `--keep`-retained workers) do NOT consume a slot — only sessions actively working do. A review pair (reviewer + codex) takes 2 slots. | 2 |
| `--keep` | Don't close worker sessions on completion — leave them idle so the user can review their transcripts. Their worktrees are kept too (a kept session lives in its worktree). Cleanup happens when the user says "cleanup" or on the next run's Recovery reconcile. | off |
| `--auto` | Fully-automatic preference, applicable to any flow: project config sections may define human checkpoints and how `--auto` changes them (typically: skip the checkpoint). Never overrides the skill's own safety gates (done+pending approval, CI-green, dispute escalation). Pass the flag's state to workers. | off |
| `--list` | Manual selection: don't dispatch automatically. Show the actionable issues (`#idx  labels — title`, plus what would be dispatched for each) and ask the user which to process; dispatch only the chosen ones, then run those to completion as normal. Issues not chosen are left untouched, not relabeled. | off |
| '--status' | Status command for the **tasks-loop orchestrator**. 

A bare number (`/tasks-loop 3`) is shorthand for `--slots 3`. Unknown parameters →
don't guess: show the table above and ask.


## Task Tracker
Read instruction from task-tracker path in config

## Task Status
Read instruction from task-status path in config if --status parameter is used

## Agent Driver
Run command from config 'agent-driver-cmd' to read instruction how to use driver

## Workflow

Task workflow, read  instruction from task-workflow path in config

## The loop
... the loop instruction from legacy skill...  abstract, depends on tracker config

## Review→fix cycle
... from legacy skill...
